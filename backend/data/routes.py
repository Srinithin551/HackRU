"""Data's REST endpoints. main.py mounts `router` automatically when this file exists."""
from fastapi import APIRouter

from backend.data import queries

router = APIRouter(tags=["data"])


@router.get("/api/sessions/active")
async def active_session():
    """{session_id} of the live session, or null. The sensing client polls this every 2 s."""
    sid = await queries.get_active_session()
    return {"session_id": sid} if sid else None
