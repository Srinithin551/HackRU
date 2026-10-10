"""Data's REST endpoints. main.py mounts `router` automatically when this file exists."""
from fastapi import APIRouter, HTTPException

from backend.data import queries

router = APIRouter(tags=["data"])


@router.get("/api/sessions/active")
async def active_session():
    """{session_id} of the live session, or null. The sensing client polls this every 2 s."""
    sid = await queries.get_active_session()
    return {"session_id": sid} if sid else None


@router.get("/api/sessions/{session_id}/replay")
async def replay(session_id: str):
    """Replay chart payload: session + baseline, 5 s series from vitals_5s, questions with stats."""
    payload = await queries.get_replay(session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="session not found")
    return payload
