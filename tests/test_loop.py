"""End-to-end: drive whole sessions over the real WebSocket, like the browser does."""

import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.brain import fake
from backend.brain.specs import FALLBACK_BANK
from backend.contracts import VitalsReading
from backend.data import queries as data
from backend.main import app

MEDIUM = " ".join(["word"] * 20)   # fake rules: 20 words, no vitals → "next"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def create(client, interview_type="Quick Demo") -> str:
    r = client.post("/api/sessions", json={"user_name": "Ada", "interview_type": interview_type,
                                            "role_title": "Backend Engineer"})
    assert r.status_code == 200
    return r.json()["session_id"]


def recv_until(ws, type_, log):
    while True:
        ev = ws.receive_json()
        log.append(ev)
        if ev["type"] == type_:
            return ev


def answer(ws, q, transcript=MEDIUM):
    qid = q["data"]["question_id"]
    ws.send_json({"type": "answer_started", "data": {"question_id": qid}})
    ws.send_json({"type": "answer_done", "data": {"question_id": qid, "transcript": transcript}})


def stressed_readings(sid, n=5):
    now = datetime.now(timezone.utc)
    return [VitalsReading(session_id=sid, ts=now, heart_rate=100, breathing_rate=21, focus=0.3,
                          expression="frown", confidence=0.95) for _ in range(n)]


def run_to_end(ws, log, first_question=None):
    """Answer every question until session_ended. Returns the list of question events."""
    questions = []
    q = first_question
    while True:
        if q is None:
            ev = ws.receive_json()
            log.append(ev)
            if ev["type"] == "session_ended":
                return questions
            if ev["type"] != "question":
                continue
            q = ev
        questions.append(q)
        answer(ws, q)
        q = None


def test_full_fake_session(client):
    sid = create(client)
    assert client.get(f"/api/sessions/{sid}/report").status_code == 404

    log = []
    with client.websocket_connect(f"/ws/{sid}") as ws:
        assert client.post(f"/api/sessions/{sid}/start").json()["status"] == "started"
        assert client.post(f"/api/sessions/{sid}/start").json()["status"] == "already_running"

        assert recv_until(ws, "state", log)["data"]["state"] == "calibrating"
        cal = recv_until(ws, "question", log)
        assert cal["data"]["kind"] == "calibration" and cal["data"]["index"] == 0 and cal["data"]["total"] == 3
        audio = cal["data"]["audio_url"]
        assert audio.startswith("/audio/")                    # relative: the frontend proxies /audio
        assert client.get(audio).headers["content-type"] == "audio/mpeg"

        answer(ws, cal, "It's going great, thanks for asking, we built a lot this weekend.")
        questions = run_to_end(ws, log)

    assert [q["data"]["kind"] for q in questions] == ["main"] * 3
    assert [q["data"]["index"] for q in questions] == [1, 2, 3]
    assert [q["data"]["text"] for q in questions] == [q.text for q in fake.FAKE_QUESTIONS[:3]]
    types = [e["type"] for e in log]
    assert "filler" in types and types[-1] == "session_ended"
    assert {"state": "done"} in [e["data"] for e in log if e["type"] == "state"]
    assert log[-1]["data"] == {"report_url": f"/api/sessions/{sid}/report",
                               "replay_url": f"/api/sessions/{sid}/replay"}

    report = client.get(f"/api/sessions/{sid}/report").json()
    assert report["generated_by"] == "fake"
    assert [p["question_id"] for p in report["per_question"]] == [q["data"]["question_id"] for q in questions]
    assert len(report["top_tips"]) == 3
    assert [a["action"] for a in report["adaptations"]] == ["next", "next", "wrap_up"]


def test_stress_spike_triggers_coach_flag_and_ease_off(client):
    sid = create(client)
    log = []
    with client.websocket_connect(f"/ws/{sid}") as ws:
        client.post(f"/api/sessions/{sid}/start")
        answer(ws, recv_until(ws, "question", log))          # calibration

        q1 = recv_until(ws, "question", log)
        recv_until(ws, "state", log)                          # listening
        ws.send_json({"type": "answer_started", "data": {"question_id": q1["data"]["question_id"]}})
        time.sleep(0.2)
        rows = client.portal.call(data.insert_vitals, stressed_readings(sid))
        assert rows and rows[0]["stress"] > 90
        flag = recv_until(ws, "coach_flag", log)
        assert flag["data"]["level"] == "high" and "Heart rate up" in flag["data"]["message"]
        ws.send_json({"type": "answer_done", "data": {"question_id": q1["data"]["question_id"], "transcript": MEDIUM}})

        follow = recv_until(ws, "question", log)
        assert follow["data"]["kind"] == "follow_up"
        assert follow["data"]["text"] == fake.FAKE_LINES["ease_off"]
        assert follow["data"]["index"] == 1                   # follow-ups keep their parent's number
        assert follow["data"]["audio_url"]
        questions = run_to_end(ws, log, first_question=follow)

    assert [q["data"]["kind"] for q in questions] == ["follow_up", "main", "main"]
    report = client.get(f"/api/sessions/{sid}/report").json()
    assert report["adaptations"][0]["action"] == "ease_off"
    assert report["adaptations"][0]["question_id"] == q1["data"]["question_id"]
    assert report["coach_flags"] and report["coach_flags"][0]["question_id"] == q1["data"]["question_id"]
    q1_feedback = next(p for p in report["per_question"] if p["question_id"] == q1["data"]["question_id"])
    assert "peaked at 9" in q1_feedback["body_signal"]


def test_end_session_mid_interview(client):
    sid = create(client, "Software Engineering")
    log = []
    with client.websocket_connect(f"/ws/{sid}") as ws:
        client.post(f"/api/sessions/{sid}/start")
        answer(ws, recv_until(ws, "question", log))
        recv_until(ws, "question", log)                       # Q1 asked
        ws.send_json({"type": "end_session", "data": {}})
        recv_until(ws, "session_ended", log)

    assert "filler" not in [e["type"] for e in log[-4:]]      # no closing line after an explicit end
    report = client.get(f"/api/sessions/{sid}/report").json()
    assert len(report["per_question"]) == 1
    assert client.get(f"/api/sessions/{sid}/status").json()["end_requested"] is True


def test_real_mode_without_keys_falls_back_everywhere(client, monkeypatch):
    monkeypatch.setattr(config, "BRAIN_FAKE", False)
    sid = create(client, "Behavioral")
    log = []
    with client.websocket_connect(f"/ws/{sid}") as ws:
        client.post(f"/api/sessions/{sid}/start")
        cal = recv_until(ws, "question", log)
        assert cal["data"]["audio_url"] is None               # ElevenLabs unconfigured → text only
        answer(ws, cal)
        questions = run_to_end(ws, log)

    assert [q["data"]["text"] for q in questions] == [t for t, _ in FALLBACK_BANK["behavioral"]]
    report = client.get(f"/api/sessions/{sid}/report").json()
    assert report["generated_by"] == "fallback" and len(report["per_question"]) == 5
    assert all(a["action"] in ("next", "wrap_up") for a in report["adaptations"])


def test_loop_crash_still_ends_session(client, monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("database fell over")

    monkeypatch.setattr(data, "get_question_stats", boom)
    sid = create(client)
    log = []
    with client.websocket_connect(f"/ws/{sid}") as ws:
        client.post(f"/api/sessions/{sid}/start")
        answer(ws, recv_until(ws, "question", log))
        answer(ws, recv_until(ws, "question", log))
        recv_until(ws, "session_ended", log)

    assert client.get(f"/api/sessions/{sid}/report").json()["generated_by"] == "fallback"


def test_late_connect_replays_state_and_question(client):
    sid = create(client)
    client.post(f"/api/sessions/{sid}/start")
    deadline = time.time() + 5
    while client.get(f"/api/sessions/{sid}/status").json()["question"]["id"] is None and time.time() < deadline:
        time.sleep(0.02)

    with client.websocket_connect(f"/ws/{sid}") as ws:
        first, second = ws.receive_json(), ws.receive_json()
        assert first == {"type": "state", "data": {"state": "calibrating"}}
        assert second["type"] == "question" and second["data"]["kind"] == "calibration"
        ws.send_text("not json")                              # ignored, socket stays up
        ws.send_json({"type": "end_session", "data": {}})
        recv_until(ws, "session_ended", [])


def test_health_and_audio_route(client):
    h = client.get("/health").json()
    assert h["ok"] and h["fake"] is True and "brain" in h["routers"]
    assert client.get("/audio/../../main.py").status_code == 404
    assert client.get("/audio/doesnotexist.mp3").status_code == 404
