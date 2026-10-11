"""Data's REST endpoints. main.py mounts `router` automatically when this file exists.

    GET /api/sessions/active          {session_id} or null; for the sensor only (this machine only)
    GET /api/sessions/{id}/replay     the replay chart's data (series + questions + stats)
"""
from fastapi import APIRouter, Depends, HTTPException

from backend.data import queries
from backend.guards import local_only

router = APIRouter(tags=["data"])


@router.get("/api/sessions/active", dependencies=[Depends(local_only)])
async def active_session():
    """{session_id} of the live session, or null. The sensing client polls this every 2 s. Local
    requests only: the id is all it takes to send events to a session (see backend/guards.py)."""
    sid = await queries.get_active_session()
    return {"session_id": sid} if sid else None


@router.get("/api/sessions/{session_id}/replay")
async def replay(session_id: str):
    """Replay chart payload: session + baseline, 5 s series from vitals_5s, questions with stats."""
    payload = await queries.get_replay(session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="session not found")
    return payload
