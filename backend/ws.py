"""WebSocket hub. One channel per session at /ws/{session_id}.

Server -> client: `await broadcast(session_id, "vitals", {...})` from anywhere in the backend.
Client -> server: every incoming message lands in `inbox[session_id]` (the interview loop reads it).

Every message on the wire is JSON: {"type": "...", "data": {...}}.

There is no login: anyone who knows a session id can watch it or send it events. Two guards keep that
safe enough for a laptop demo:
  - a browser connection must come from an allowed origin (config.CORS_ORIGINS or any localhost port), so
    a random website open in the same browser can't attach to your session (cross-site WebSocket hijacking).
    Clients without an Origin header (scripts/fake_frontend.py, tests) aren't browsers and are allowed;
  - each session's inbox is bounded (INBOX_MAX) and oversized messages are dropped (MAX_MESSAGE_CHARS),
    so messages for a session that never runs can't pile up in memory.
forget() frees a session's hub state once Brain has pruned it (brain/state.prune_finished).
"""

import asyncio
import json
import logging
import re
import time
from collections import defaultdict
from typing import Awaitable, Callable, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend import config

log = logging.getLogger("ws")
router = APIRouter()

INBOX_MAX = 200             # unread client events kept per session; the loop reads them within a second
MAX_MESSAGE_CHARS = 20_000  # a transcript is capped at 4,000 chars anyway (interviewer.MAX_TRANSCRIPT_CHARS)
_LOCAL_ORIGIN = re.compile(config.LOCAL_ORIGIN_REGEX + r"$")

connections: dict[str, set[WebSocket]] = defaultdict(set)
inbox: dict[str, asyncio.Queue] = defaultdict(lambda: asyncio.Queue(maxsize=INBOX_MAX))   # client→server events

# The latest event of these types is replayed to a client when it (re)connects, so a tab that
# opens the socket late or reloads mid-session still shows the current question and state.
REPLAY_TYPES = ("state", "question", "session_ended")
_last: dict[str, dict[str, dict]] = defaultdict(dict)

# Called for every client event as soon as it arrives (Brain uses this to react to end_session
# immediately instead of waiting for the loop to read the inbox).
ClientListener = Callable[[str, dict], Awaitable[None]]
client_listeners: list[ClientListener] = []

# When the last vitals reading was broadcast (from Sensing's /api/vitals), and when the last one that
# actually carried a heart rate was (a face in view), for /health.
last_vitals_at: Optional[float] = None
last_signal_at: Optional[float] = None

_send_locks: dict[WebSocket, asyncio.Lock] = {}
SEND_TIMEOUT_S = 2.0


async def _send(ws: WebSocket, msg: dict) -> bool:
    """Send one message to one socket. False if it failed or took longer than SEND_TIMEOUT_S.
    The per-socket lock stops two concurrent broadcasts from interleaving on the same socket; the
    timeout stops one stalled tab from holding up everyone else's broadcast."""
    lock =_send_locks.setdefault(ws, asyncio.Lock())
    try:
        async with lock:
            await asyncio.wait_for(ws.send_json(msg), SEND_TIMEOUT_S)
        return True
    except Exception:
        return False


async def broadcast(session_id: str, type_: str, data: dict) -> None:
    """Send an event to every client watching this session. Never raises."""
    global last_vitals_at, last_signal_at
    session_id = str(session_id)
    msg = {"type": type_, "data": data}
    if type_ == "vitals":
        last_vitals_at = time.monotonic()
        if data.get("heart_rate") is not None:
            last_signal_at = last_vitals_at
    if type_ in REPLAY_TYPES:
        last = _last[session_id]
        last.pop(type_, None)   # re-insert so replay keeps the original send order
        last[type_] = msg

    sockets = list(connections.get(session_id, ()))
    if not sockets:
        return
    results = await asyncio.gather(*(_send(ws, msg) for ws in sockets))
    for ws, ok in zip(sockets, results):
        if not ok:
            _drop(session_id, ws)


def _drop(session_id: str, ws: WebSocket) -> None:
    """Forget a dead or closed socket so later broadcasts skip it."""
    socks = connections.get(session_id)
    if socks is not None:
        socks.discard(ws)
        if not socks:
            connections.pop(session_id, None)   # don't keep an empty set per session id ever seen
    _send_locks.pop(ws, None)


def get_inbox(session_id: str) -> asyncio.Queue:
    """The queue of client events for a session. Shared by every tab watching it."""
    return inbox[str(session_id)]


def clear_replay(session_id: str, type_: str) -> None:
    """Stop replaying the last `type_` event to tabs that connect from now on (e.g. an answered question)."""
    _last.get(str(session_id), {}).pop(type_, None)


def forget(session_id: str) -> None:
    """Drop everything the hub keeps for a session. Open sockets stay connected but get no replay."""
    session_id = str(session_id)
    _last.pop(session_id, None)
    inbox.pop(session_id, None)


def origin_allowed(origin: Optional[str]) -> bool:
    """No Origin header = not a browser (scripts, tests): allowed. A browser must be on an allowed origin."""
    if not origin:
        return True
    return origin in config.CORS_ORIGINS or bool(_LOCAL_ORIGIN.match(origin))


@router.websocket("/ws/{session_id}")
async def session_socket(websocket: WebSocket, session_id: str):
    """One browser tab. Replays the latest state/question/session_ended so a late or reloaded tab lands
    on the right screen, then pumps every valid client message into the inbox and the client listeners
    until the tab disconnects."""
    origin = websocket.headers.get("origin")
    if not origin_allowed(origin):
        log.warning("ws session=%s refused: origin %r isn't allowed (CORS_ORIGINS)", session_id, origin)
        await websocket.close(code=1008)   # policy violation; the handshake is rejected with 403
        return
    await websocket.accept()
    connections[session_id].add(websocket)
    log.info("ws connected session=%s clients=%d", session_id, len(connections[session_id]))

    for msg in list(_last.get(session_id, {}).values()):
        await _send(websocket, msg)

    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw) > MAX_MESSAGE_CHARS:
                log.warning("ws session=%s ignored a %d-char message (limit %d)", session_id, len(raw), MAX_MESSAGE_CHARS)
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("ws session=%s ignored non-JSON message", session_id)
                continue
            if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
                log.warning("ws session=%s ignored malformed message: %.200s", session_id, raw)
                continue
            if not isinstance(msg.get("data"), dict):
                msg["data"] = {}

            try:
                inbox[session_id].put_nowait(msg)
            except asyncio.QueueFull:   # nobody is reading this session's inbox (it isn't running)
                log.warning("ws session=%s inbox full, dropped a %r event", session_id, msg["type"])
            for listener in client_listeners:
                try:
                    await listener(session_id, msg)
                except Exception:
                    log.exception("client listener failed")
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws session=%s crashed", session_id)
    finally:
        _drop(session_id, websocket)
        log.info("ws disconnected session=%s", session_id)
