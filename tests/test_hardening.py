"""Loopholes found in the audit, one test each: unknown sessions, cross-site sockets, sensor-only endpoints,
bad vitals, foreign session ids, the calibration minimum, replay of answered questions, memory pruning."""

import math
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import ws as hub
from backend.brain import interviewer, state
from backend.brain.state import SessionMeta, SessionState
from backend.contracts import VitalsReading
from backend.data import fake_queries, metrics, queries as data
from backend.main import app
from backend.sensing.routes import MAX_BATCH, clean


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def create(client, interview_type="Quick Demo") -> str:
    return client.post("/api/sessions", json={"interview_type": interview_type}).json()["session_id"]


# ---------------------------------------------------------------- /start

def test_start_refuses_a_session_that_was_never_created(client):
    assert client.post("/api/sessions/totally-made-up/start").status_code == 404
    assert "totally-made-up" not in state.tasks


def test_start_after_a_restart_rebuilds_setup_from_data(client):
    sid = create(client, "Behavioral")
    state.metas.pop(sid)                     # what a backend restart does to Brain's memory
    assert client.post(f"/api/sessions/{sid}/start").json()["status"] == "started"
    assert client.get(f"/api/sessions/{sid}/status").json()["type"] == "Behavioral"   # not the SWE default
    state.states[sid].end_requested = True   # let the loop finish


# ---------------------------------------------------------------- WebSocket origin

def test_websocket_refuses_other_websites(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/x", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()


def test_websocket_accepts_the_app_and_non_browser_clients(client):
    with client.websocket_connect("/ws/x", headers={"origin": "http://localhost:5173"}):
        pass
    with client.websocket_connect("/ws/x"):   # no Origin: scripts/fake_frontend.py, tests
        pass
    assert hub.origin_allowed("http://127.0.0.1:4173")
    assert not hub.origin_allowed("http://localhost.evil.example")


# ---------------------------------------------------------------- sensor-only endpoints

def test_sensor_endpoints_are_local_only():
    with TestClient(app, client=("192.168.1.50", 5555)) as remote:
        assert remote.get("/api/sessions/active").status_code == 403
        assert remote.post("/api/vitals", json={"readings": []}).status_code == 403
        assert remote.post("/api/sensor/status", json={"state": "idle"}).status_code == 403
        assert remote.get("/health").status_code == 200          # everything else is unaffected


# ---------------------------------------------------------------- vitals cleaning

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def test_future_dated_readings_are_dropped():
    assert clean(VitalsReading(ts=NOW + timedelta(hours=1), heart_rate=70), NOW) is None
    assert clean(VitalsReading(ts=NOW + timedelta(seconds=2), heart_rate=70), NOW) is not None   # jitter is fine


def test_impossible_values_become_null_and_fractions_are_clamped():
    r = clean(VitalsReading(ts=NOW, heart_rate=math.nan, breathing_rate=500, focus=5, confidence=9), NOW)
    assert r.heart_rate is None and r.breathing_rate is None
    assert r.focus == 1.0 and r.confidence == 1.0
    assert clean(VitalsReading(ts=NOW, heart_rate=-10, confidence=-1), NOW).confidence == 0.0


def test_oversized_batches_are_refused(client):
    ts = datetime.now(timezone.utc).isoformat()
    body = {"readings": [{"ts": ts, "heart_rate": 70}] * (MAX_BATCH + 1)}
    assert client.post("/api/vitals", json=body).status_code == 413


def test_future_readings_never_reach_the_live_window(client):
    sid = create(client)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = client.post("/api/vitals", json={"readings": [{"session_id": sid, "ts": future, "heart_rate": 70,
                                                       "confidence": 0.9}]})
    assert r.json() == {"stored": 0}
    assert client.portal.call(data.get_window_stats, sid, 10).n_samples == 0


# ---------------------------------------------------------------- foreign / malformed session ids

def test_real_path_drops_readings_for_foreign_or_malformed_session_ids(client, monkeypatch):
    class Pool:
        inserted = []

        async def executemany(self, _sql, records):
            Pool.inserted += records

    async def pool():
        return Pool()

    async def active():
        return "11111111-1111-1111-1111-111111111111"

    async def no_baseline(_sid):
        return None

    mine = "22222222-2222-2222-2222-222222222222"
    monkeypatch.setattr(data, "FAKE", False)
    monkeypatch.setattr(data.db, "get_pool", pool)
    monkeypatch.setattr(data, "get_active_session", active)
    monkeypatch.setattr(data, "_cached_baseline", no_baseline)
    monkeypatch.setattr(data, "_mine", {mine})
    now = datetime.now(timezone.utc)
    rows = client.portal.call(data.insert_vitals, [
        VitalsReading(session_id="typo-123", ts=now, heart_rate=70),                              # malformed
        VitalsReading(session_id="33333333-3333-3333-3333-333333333333", ts=now, heart_rate=70),  # not ours
        VitalsReading(session_id=mine, ts=now, heart_rate=70),
    ])
    assert [r["session_id"] for r in rows] == [mine]


# ---------------------------------------------------------------- calibration minimum

def test_quick_small_talk_still_gets_the_calibration_minimum(client, monkeypatch):
    monkeypatch.setattr(interviewer, "CALIBRATION_MIN_S", 1.0)
    monkeypatch.setattr(interviewer, "CALIBRATION_MAX_WAIT_S", 5.0)
    sid = create(client)
    with client.websocket_connect(f"/ws/{sid}") as ws:
        client.post(f"/api/sessions/{sid}/start")
        t0 = time.monotonic()
        while True:
            ev = ws.receive_json()
            if ev["type"] != "question":
                continue
            d = ev["data"]
            if d["kind"] == "main":
                break
            ws.send_json({"type": "answer_started", "data": {"question_id": d["question_id"]}})
            ws.send_json({"type": "answer_done", "data": {"question_id": d["question_id"], "transcript": "Good."}})
        assert time.monotonic() - t0 >= 0.9   # both answers were instant, yet calibration lasted ~1 s
        ws.send_json({"type": "end_session", "data": {}})


def test_calibration_wait_is_capped(monkeypatch):
    monkeypatch.setattr(interviewer, "CALIBRATION_MIN_S", 999.0)
    monkeypatch.setattr(interviewer, "CALIBRATION_MAX_WAIT_S", 0.2)
    s = SessionState("x", SessionMeta("x", "", "demo", ""))
    import asyncio
    t0 = time.monotonic()
    asyncio.run(interviewer._top_up_calibration(s))
    assert time.monotonic() - t0 < 1.0


# ---------------------------------------------------------------- replay + memory

def test_answered_question_is_not_replayed_to_reconnecting_tabs(client):
    sid = create(client)
    with client.websocket_connect(f"/ws/{sid}") as ws:
        client.post(f"/api/sessions/{sid}/start")
        while (ev := ws.receive_json())["type"] != "question":
            pass
        qid = ev["data"]["question_id"]
        ws.send_json({"type": "answer_started", "data": {"question_id": qid}})
        ws.send_json({"type": "answer_done", "data": {"question_id": qid, "transcript": "Pretty good so far."}})
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            replayed = hub._last.get(sid, {}).get("question")
            if replayed is None or replayed["data"]["question_id"] != qid:
                break
            time.sleep(0.01)
        else:
            pytest.fail("the answered question is still queued for replay")
        ws.send_json({"type": "end_session", "data": {}})


def test_finished_sessions_are_pruned_after_the_ttl():
    s = SessionState("old", SessionMeta("old", "", "demo", ""))
    s.finished_at = time.monotonic() - state.FINISHED_TTL_S - 1
    state.states["old"], state.metas["old"] = s, s.meta
    hub._last["old"] = {"state": {"type": "state", "data": {}}}
    gone = state.prune_finished()
    assert "old" in gone and "old" not in state.states and "old" not in state.metas
    hub.forget("old")
    assert "old" not in hub._last


def test_inbox_for_a_session_that_never_runs_is_bounded(client):
    with client.websocket_connect("/ws/nobody-reads-this") as ws:
        for _ in range(hub.INBOX_MAX + 20):
            ws.send_json({"type": "answer_started", "data": {}})
        ws.send_json({"type": "end_session", "data": {}})
    time.sleep(0.2)
    assert hub.inbox["nobody-reads-this"].qsize() <= hub.INBOX_MAX


# ---------------------------------------------------------------- config parsing

@pytest.mark.parametrize("raw, expected", [
    (None, 0.4), ("", 0.4), ("0.55", 0.55), ("40", 0.40), ("abc", 0.4), ("-1", 0.4), ("250", 0.4),
])
def test_min_confidence_setting_is_parsed_safely(raw, expected):
    assert metrics._min_confidence(raw) == pytest.approx(expected)


def test_fake_and_real_get_session_agree_on_shape(client):
    sid = create(client)
    row = client.portal.call(data.get_session, sid)
    assert set(row) == {"id", "user_name", "interview_type", "role_title", "ended"}
    assert client.portal.call(fake_queries.get_session, "nope") is None
