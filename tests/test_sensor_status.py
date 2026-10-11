"""The Presage sensor's status (POST /api/sensor/status) reaches the live page and /health."""

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.sensing import routes as sensing


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(sensing, "latest_status", None)
    monkeypatch.setattr(sensing, "latest_status_at", None)
    with TestClient(app) as c:
        yield c


def test_status_is_broadcast_to_the_active_interview(client):
    sid = client.post("/api/sessions", json={"user_name": "Ada", "interview_type": "Quick Demo",
                                             "role_title": "Backend Engineer"}).json()["session_id"]
    with client.websocket_connect(f"/ws/{sid}") as ws:
        r = client.post("/api/sensor/status", json={"state": "adjust", "message": "Hold still."})
        assert r.status_code == 200
        assert ws.receive_json() == {"type": "sensor",
                                     "data": {"state": "adjust", "message": "Hold still.", "simulated": False}}


def test_health_shows_the_latest_status(client):
    assert client.get("/health").json()["sensor"]["presage"] is None
    client.post("/api/sensor/status", json={"state": "measuring", "simulated": True})
    assert client.get("/health").json()["sensor"]["presage"] == {"state": "measuring", "message": None,
                                                                 "simulated": True}


def test_rejects_unknown_states(client):
    assert client.post("/api/sensor/status", json={"state": "dancing"}).status_code == 422


def test_page_camera_signal(client, monkeypatch):
    """The page reports its webcam open; the pre-warmed sensor reads it before starting Presage."""
    monkeypatch.setattr(sensing, "page_camera_open_at", None)
    assert client.get("/api/sensor/page-camera").json() == {"open": False}
    assert client.post("/api/sensor/page-camera", json={"open": True}).status_code == 200
    assert client.get("/api/sensor/page-camera").json() == {"open": True}
