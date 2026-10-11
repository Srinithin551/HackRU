import time
from typing import Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.contracts import VitalsBatch
from backend.data import queries as data
from backend.ws import broadcast

router = APIRouter()


@router.post("/api/vitals")
async def ingest(batch: VitalsBatch):
    rows = await data.insert_vitals(batch.readings)  # Data resolves session + computes stress
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
