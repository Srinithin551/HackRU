"""Sensing's REST endpoints: where the Presage sensor's readings enter the backend.

    POST /api/vitals          VitalsBatch -> {stored: n}. Data stores each reading (stress computed per row)
                              and every reading is broadcast to the live page as a `vitals` event.
    POST /api/sensor/status   {state, message, simulated}: what Presage is doing ("Hold still."), shown on
                              the live page as a `sensor` event and in /health.

The sender is sensing_client/presage_node/sensor.mjs (or the legacy sensing_client/client.py, or
scripts/mock_vitals.py), always on this machine, so both endpoints are local-only (backend/guards.py).

Readings are cleaned before Data sees them, because one bad value poisons every average it lands in:
  - a timestamp more than MAX_FUTURE_S ahead of this machine's clock is dropped. It would otherwise sit in
    every "last N seconds" live window (and drive coach flags) until that time arrives;
  - NaN / infinite numbers and physically impossible values become null (the metric is "not measured");
  - focus and confidence are clamped to 0-1.
"""
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.contracts import VitalsBatch, VitalsReading
from backend.data import queries as data
from backend.guards import local_only
from backend.ws import broadcast

log = logging.getLogger("sensing")
router = APIRouter(tags=["sensing"], dependencies=[Depends(local_only)])

MAX_BATCH = 120       # the sensor buffers at most 30 readings while the backend is unreachable
MAX_FUTURE_S = 5.0    # clock jitter allowance; the sensor stamps readings with this machine's clock
HR_RANGE = (25.0, 250.0)   # bpm a webcam can plausibly report
BR_RANGE = (2.0, 80.0)     # breaths per minute


def _finite(x: Optional[float]) -> Optional[float]:
    return x if x is not None and math.isfinite(x) else None


def _in_range(x: Optional[float], lo: float, hi: float) -> Optional[float]:
    x = _finite(x)
    return x if x is not None and lo <= x <= hi else None


def _clamp01(x: Optional[float]) -> Optional[float]:
    x = _finite(x)
    return None if x is None else max(0.0, min(1.0, x))


def clean(r: VitalsReading, now: datetime) -> Optional[VitalsReading]:
    """The reading with impossible values blanked, or None if it must be dropped (future timestamp)."""
    ts = r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)
    if ts > now + timedelta(seconds=MAX_FUTURE_S):
        return None
    return r.model_copy(update={
        "heart_rate": _in_range(r.heart_rate, *HR_RANGE),
        "breathing_rate": _in_range(r.breathing_rate, *BR_RANGE),
        "focus": _clamp01(r.focus),
        "confidence": _clamp01(r.confidence) or 0.0,
    })


@router.post("/api/vitals")
async def ingest(batch: VitalsBatch):
    """Store and broadcast a batch. `stored` counts the readings accepted for a session (including those
    below the confidence cutoff, which are broadcast as "no signal" but not written to the database)."""
    if len(batch.readings) > MAX_BATCH:
        raise HTTPException(status_code=413, detail=f"at most {MAX_BATCH} readings per batch")
    now = datetime.now(timezone.utc)
    readings = [c for c in (clean(r, now) for r in batch.readings) if c is not None]
    if len(readings) < len(batch.readings):
        log.warning("dropped %d reading(s) stamped more than %.0fs in the future: is the sensor's clock right?",
                    len(batch.readings) - len(readings), MAX_FUTURE_S)
    rows = await data.insert_vitals(readings)  # Data resolves session + computes stress
    for r in rows:
        await broadcast(r["session_id"], "vitals", {
            "ts": r["time"].isoformat(),
            "heart_rate": r["heart_rate"],
            "breathing_rate": r["breathing_rate"],
            "focus": r["focus"],
            "stress": r["stress"],
        })
    return {"stored": len(rows)}


class SensorStatus(BaseModel):
    """What Presage is doing right now, from sensing_client/presage_node/sensor.mjs."""
    state: Literal["idle", "starting", "warming_up", "measuring", "adjust", "error"]
    message: Optional[str] = Field(default=None, max_length=300)   # e.g. "Hold still."
    simulated: bool = False                                        # artificial vitals (--simulate)


# Latest status and when it arrived (time.monotonic()), for /health.
latest_status: Optional[dict] = None
latest_status_at: Optional[float] = None


@router.post("/api/sensor/status")
async def sensor_status(status: SensorStatus):
    """The sensor posts this on every change and every 3 s; the live page shows it as a `sensor` event."""
    global latest_status, latest_status_at
    latest_status = status.model_dump()
    latest_status_at = time.monotonic()
    sid = await data.get_active_session()
    if sid:
        await broadcast(sid, "sensor", latest_status)
    return {"ok": True}
