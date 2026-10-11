"""FastAPI app. Run from the repo root:  uvicorn backend.main:app --reload

Mounts every role's router. Data's and Sensing's routers are optional so the app boots before
they're pushed; once the files exist they're picked up with no changes here.
"""

import asyncio
import importlib
import logging
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

if __package__ in (None, ""):   # also support `cd backend && uvicorn main:app`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend import config, ws
from backend.brain import routes as brain_routes
from backend.brain import voice

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("main")


class _QuietPolling(logging.Filter):
    """Drops uvicorn's access lines for the sensor's once-a-second traffic and the launcher's health checks,
    so the terminal stays readable. Failed requests (4xx/5xx) are still logged."""
    QUIET = {("GET", "/api/sessions/active"), ("POST", "/api/vitals"), ("POST", "/api/sensor/status"),
             ("GET", "/health")}

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:   # (client, method, path, http_version, status)
            _, method, path, _, status = args
            return not ((method, str(path).split("?")[0]) in self.QUIET and int(status) < 400)
        return True


logging.getLogger("uvicorn.access").addFilter(_QuietPolling())

OPTIONAL_ROUTERS = ["backend.data.routes", "backend.sensing.routes"]
mounted: list[str] = []


def _optional_module(name: str):
    """Import a teammate's module if it exists. Real errors inside it still surface."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as e:
        if e.name and name.startswith(e.name):
            log.warning("%s not found yet, skipping", name)
            return None
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Data: expose `async def startup()` / `async def shutdown()` in backend/data/db.py (asyncpg pool).
    db = _optional_module("backend.data.db")
    if db is not None and hasattr(db, "startup"):
        await db.startup()
    log.info("%s backend up. BRAIN_FAKE=%s gemini=%s elevenlabs=%s routers=%s", config.APP_NAME,
             int(config.BRAIN_FAKE), "on" if config.GEMINI_API_KEY else "off",
             "on" if voice.configured() else "off", mounted)
    warm = asyncio.create_task(voice.warm_up())   # don't block startup on TTS
    yield
    warm.cancel()
    if db is not None and hasattr(db, "shutdown"):
        await db.shutdown()


app = FastAPI(title=config.APP_NAME, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_origin_regex=config.LOCAL_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ws.router)
app.include_router(brain_routes.router)
mounted.append("brain")
for _name in OPTIONAL_ROUTERS:
    _mod = _optional_module(_name)
    if _mod is not None:
        app.include_router(_mod.router)
        mounted.append(_name.split(".")[1])


SENSOR_STALE_S = 5.0


@app.get("/health")
async def health():
    db = sys.modules.get("backend.data.db")
    since = None if ws.last_vitals_at is None else round(time.monotonic() - ws.last_vitals_at, 1)
    signal_since = None if ws.last_signal_at is None else round(time.monotonic() - ws.last_signal_at, 1)
    return {
        "ok": True,
        "fake": config.BRAIN_FAKE,
        "gemini": bool(config.GEMINI_API_KEY),
        "elevenlabs": voice.configured(),
        "routers": mounted,
        # "tiger_data" when TIGER_DATABASE_URL is set and in use, "in_memory" otherwise
        "data_store": "unknown" if db is None else ("in_memory" if db.use_fake() else "tiger_data"),
        # Is Presage → sensing client → /api/vitals feeding readings right now, and are they passing the
        # confidence cutoff? (stored vs. below_cutoff since startup)
        # live = readings are arriving; signal = they carry a heart rate (a face is in view)
        "sensor": {"live": since is not None and since < SENSOR_STALE_S, "seconds_since_reading": since,
                   "signal": signal_since is not None and signal_since < SENSOR_STALE_S, **_ingest_counts(),
                   "presage": _presage_status()},
    }


PRESAGE_STATUS_STALE_S = 10.0   # the sensor re-posts its status every 3 s


def _presage_status() -> Optional[dict]:
    """Latest status from the Presage sensor (sensing_client/presage_node), or None if it isn't running."""
    sensing = sys.modules.get("backend.sensing.routes")
    at = getattr(sensing, "latest_status_at", None)
    if at is None or time.monotonic() - at > PRESAGE_STATUS_STALE_S:
        return None
    return sensing.latest_status


def _ingest_counts() -> dict:
    metrics = sys.modules.get("backend.data.metrics")
    if metrics is None or not hasattr(metrics, "INGEST"):
        return {}
    return {"min_confidence": metrics.MIN_CONFIDENCE, "stored": metrics.INGEST["stored"],
            "below_cutoff": metrics.INGEST["below_cutoff"]}


_AUDIO_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}\.mp3$")


@app.get("/audio/{filename}")
async def audio(filename: str):
    """Interviewer audio. Generated clips first, then the committed fake-mode clips."""
    if not _AUDIO_NAME.match(filename):
        raise HTTPException(status_code=404)
    for d in (config.AUDIO_DIR, config.FAKE_AUDIO_DIR):
        path = d / filename
        if path.is_file():
            return FileResponse(path, media_type="audio/mpeg",
                                headers={"Cache-Control": "public, max-age=86400"})
    raise HTTPException(status_code=404)
