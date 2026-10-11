"""Brain's REST endpoints: create a session, start the interview, fetch the report.

    POST /api/sessions                 {user_name, interview_type, role_title} -> {session_id}
    POST /api/sessions/{id}/start      launches the interview loop (idempotent); 404 for an unknown id
    GET  /api/sessions/{id}/report     the saved report, 404 until it's written
    GET  /api/sessions/{id}/status     debug view of the running loop (not used by the frontend)

It also listens for `end_session` on the WebSocket (_on_client_event) so ending is instant.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from backend import config, ws
from backend.brain import fake
from backend.brain.interviewer import run_session
from backend.brain.state import SessionMeta, SessionState, metas, prune_finished, states, tasks
from backend.data import queries as data

log = logging.getLogger("brain.routes")
MAX_FIELD_CHARS = {"user_name": 60, "interview_type": 40, "role_title": 120}
router = APIRouter(prefix="/api/sessions", tags=["brain"])


class CreateSessionBody(BaseModel):
    """The setup page's form. Every field is optional; get_spec() maps any interview_type label."""
    user_name: str = ""
    interview_type: str = "Software Engineering"
    role_title: str = ""

    @field_validator("user_name", "interview_type", "role_title", mode="before")
    @classmethod
    def _tidy(cls, v, info):
        """Collapse whitespace and cap length: these go into prompts, the database, and spoken lines."""
        v = " ".join(str(v or "").split())
        return v[:MAX_FIELD_CHARS[info.field_name]]


@router.post("")
async def create_session(body: CreateSessionBody):
    """Store the session in Data and remember the setup choices in memory. Doesn't start anything: the
    live page calls /start once its WebSocket is open, so it can't miss the first question. Also the
    cheap moment to free sessions that finished long ago."""
    for old in prune_finished():
        ws.forget(old)
    sid = await data.create_session(body.user_name, body.interview_type, body.role_title)
    sid = str(sid)
    metas[sid] = SessionMeta(sid, body.user_name, body.interview_type, body.role_title)
    log.info("session %s created: %s / %r", sid, body.interview_type, body.role_title)
    return {"session_id": sid}


@router.post("/{session_id}/start")
async def start_session(session_id: str):
    """Launch the interview loop as a background task. Idempotent: a repeat call reports the existing
    task's status instead of starting a second loop.

    The session must exist in Data. Starting a made-up id used to launch a whole interview (one Gemini
    call each, and with Tiger Data it crashed on the first insert); now it's a 404. After a backend
    restart the setup choices are rebuilt from the sessions row instead of falling back to defaults.
    """
    task = tasks.get(session_id)
    if task is not None:
        return {"session_id": session_id, "status": "finished" if task.done() else "already_running"}

    try:
        row = await data.get_session(session_id)
    except Exception:
        log.exception("get_session failed for %s", session_id)
        raise HTTPException(status_code=503, detail="database unavailable")
    if row is None:
        raise HTTPException(status_code=404, detail="no such session")
    # A session closed in Data because a newer one was created can still run (two interviews at once is
    # supported); it just won't receive sensor vitals, which always go to the newest session.

    meta = metas.get(session_id)
    if meta is None:   # the backend restarted between create and start
        log.warning("start for session %s created before a restart: rebuilding its setup from Data", session_id)
        meta = metas[session_id] = SessionMeta(session_id, row["user_name"], row["interview_type"] or
                                               "Software Engineering", row["role_title"])

    s = SessionState(session_id, meta)
    s.total_planned = len(fake.plan(meta.spec)) if config.BRAIN_FAKE else meta.spec.n_questions
    states[session_id] = s
    tasks[session_id] = asyncio.create_task(run_session(s))
    return {"session_id": session_id, "status": "started"}


@router.get("/{session_id}/report")
async def get_report(session_id: str):
    """The saved report. Falls back to the in-memory copy if the database save failed; 404 while it's
    still being written (the frontend polls on 404)."""
    try:
        report = await data.get_report(session_id)
    except Exception:
        log.exception("get_report failed for %s", session_id)
        report = None
    if isinstance(report, str):   # asyncpg returns JSONB as text unless a codec is set
        report = json.loads(report)
    if report is None and session_id in states:
        report = states[session_id].report   # saved in memory if the database save failed
    if report is None:
        raise HTTPException(status_code=404, detail="report not ready")
    return report


@router.get("/{session_id}/status")
async def get_status(session_id: str):
    """Debug view of the interview loop. Not part of the frontend contract."""
    s = states.get(session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="session not started")
    return {
        "session_id": session_id, "phase": s.phase, "type": s.spec.label, "role": s.meta.role,
        "question": {"id": s.current_qid, "kind": s.current_kind, "text": s.current_text,
                     "index": s.planned_number, "total": s.total_planned},
        "turns": s.turns, "elapsed_s": round(s.elapsed(), 1), "remaining_planned": len(s.queue),
        "baseline": s.baseline.model_dump() if s.baseline else None,
        "live_window": s.live_window.model_dump() if s.live_window else None,
        "actions": s.actions, "flags": len(s.flags), "end_requested": s.end_requested,
        "fake": config.BRAIN_FAKE,
    }


async def _on_client_event(session_id: str, msg: dict) -> None:
    """React to end_session immediately, even while the loop is busy thinking or speaking."""
    if msg.get("type") != "end_session":
        return
    s = states.get(session_id)
    if s is not None:
        s.end_requested = True
    elif session_id in metas:
        await data.end_session(session_id)   # never started: just close it


ws.client_listeners.append(_on_client_event)
