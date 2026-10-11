"""Request guards shared by the routers.

The sensor endpoints (POST /api/vitals, POST /api/sensor/status, GET /api/sessions/active) have no login,
because the Presage sensor runs on the same laptop as the backend. Without a guard, anyone who can
reach the backend could read the live session id or feed it fake vitals. `local_only` limits them to
requests from this machine. The browser never calls them (the live page gets vitals over the WebSocket),
and requests through the Vite dev proxy come from 127.0.0.1 anyway.

Set ALLOW_REMOTE_SENSOR=1 in .env if the sensor really has to run on another machine.
"""

from fastapi import HTTPException, Request

from backend import config

# "testclient" is the host Starlette's TestClient reports: tests run in-process, on this machine.
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def local_only(request: Request) -> None:
    """FastAPI dependency: 403 unless the request comes from this machine (or ALLOW_REMOTE_SENSOR=1)."""
    if config.ALLOW_REMOTE_SENSOR:
        return
    host = request.client.host if request.client else ""
    if host not in LOCAL_HOSTS and not host.startswith("127."):
        raise HTTPException(status_code=403, detail="sensor endpoints only accept requests from this machine "
                                                    "(set ALLOW_REMOTE_SENSOR=1 to allow others)")
