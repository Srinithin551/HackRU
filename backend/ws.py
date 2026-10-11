"""WebSocket hub. One channel per session at /ws/{session_id}.

Server -> client: `await broadcast(session_id, "vitals", {...})` from anywhere in the backend.
Client -> server: every incoming message lands in `inbox[session_id]` (the interview loop reads it).

Every message on the wire is JSON: {"type": "...", "data": {...}}.
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Awaitable, Callable, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger("ws")
router = APIRouter()

connections: dict[str, set[WebSocket]] = defaultdict(set)
inbox: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)   # client→server events per session

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
    lock = _send_locks.setdefault(ws, asyncio.Lock())
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
    connections.get(session_id, set()).discard(ws)
    _send_locks.pop(ws, None)


def get_inbox(session_id: str) -> asyncio.Queue:
    return inbox[str(session_id)]


@router.websocket("/ws/{session_id}")
async def session_socket(websocket: WebSocket, session_id: str):
    await websocket.accept()
    connections[session_id].add(websocket)
    log.info("ws connected session=%s clients=%d", session_id, len(connections[session_id]))

    for msg in list(_last.get(session_id, {}).values()):
        await _send(websocket, msg)

    try:
        while True:
            raw = await websocket.receive_text()
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

            await inbox[session_id].put(msg)
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
