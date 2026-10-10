from fastapi import APIRouter

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
