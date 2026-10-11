"""End-to-end: drive whole sessions over the real WebSocket, like the browser does."""

import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.brain import fake, gemini, lines, voice
from backend.brain.models import Decision
from backend.brain.state import states
from backend.brain.specs import FALLBACK_BANK
from backend.contracts import VitalsReading
from backend.data import queries as data
from backend.main import app

OFF_TOPIC = "I really like pizza and playing basketball with my friends on the weekends."


def on_topic(q) -> str:
    """An answer that shares words with the question, so fake mode doesn't call it off-topic.
    Fake rules: 8 to 24 words and no vitals → "next"."""
    return f"So for that, {q['data']['text']} I handled this once."


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


ANSWERED = {"type": "_client_answered"}   # marker we put in event logs


def answer(ws, q, transcript=None, log=None):
    if transcript is None:
        transcript = on_topic(q)
    qid = q["data"]["question_id"]
    ws.send_json({"type": "answer_started", "data": {"question_id": qid}})
    ws.send_json({"type": "answer_done", "data": {"question_id": qid, "transcript": transcript}})
    if log is not None:
        log.append(ANSWERED)


def assert_one_voice(log):
    """The server never sends a second clip until the candidate has answered the first one, so the
    interviewer can't talk over itself (each question's audio ends before the answer can start)."""
    audio = [i for i, e in enumerate(log) if e["type"] in ("question", "filler") and e["data"].get("audio_url")]
    for prev, cur in zip(audio, audio[1:]):
        assert ANSWERED in log[prev + 1:cur], f"two clips without an answer between them: {log[prev]} / {log[cur]}"


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
        answer(ws, q, log=log)
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

        assert cal["data"]["text"] == "Thanks for joining me today. To start, tell me a little about yourself."
        answer(ws, cal, "I'm a backend engineer, I like building fast APIs and I'm at a hackathon this weekend.", log)
        questions = run_to_end(ws, log)
    assert_one_voice(log)
    fillers = [e for e in log if e["type"] == "filler"]
    assert len(fillers) == 1                                   # only the closing line; no "Okay."/"Got it."

    assert [q["data"]["kind"] for q in questions] == ["main"] * 3
    assert [q["data"]["index"] for q in questions] == [1, 2, 3]
    assert [q["data"]["text"] for q in questions] == [q.text for q in fake.FAKE_QUESTIONS[:3]]
    types = [e["type"] for e in log]
    assert types[-1] == "session_ended"
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
        ws.send_json({"type": "answer_done", "data": {"question_id": q1["data"]["question_id"], "transcript": on_topic(q1)}})

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


def test_off_topic_answer_gets_coaching_then_the_next_question(client):
    sid = create(client)
    log = []
    with client.websocket_connect(f"/ws/{sid}") as ws:
        client.post(f"/api/sessions/{sid}/start")
        answer(ws, recv_until(ws, "question", log), log=log)          # calibration
        q1 = recv_until(ws, "question", log)
        answer(ws, q1, OFF_TOPIC, log)

        coached = recv_until(ws, "question", log)
        d = coached["data"]
        assert d["kind"] == "main" and d["index"] == 2                  # coaching moves on
        assert d["text"] == fake.FAKE_QUESTIONS[1].text                 # the screen shows the question...
        assert d["coaching"] == fake.FAKE_COACH                         # ...and the coaching separately
        assert d["audio_url"]                                           # both spoken in one clip
        questions = [coached] + run_to_end(ws, log, first_question=coached)[1:]
    assert_one_voice(log)

    report = client.get(f"/api/sessions/{sid}/report").json()
    assert report["adaptations"][0]["action"] == "coach"
    q1_feedback = next(p for p in report["per_question"] if p["question_id"] == q1["data"]["question_id"])
    assert "missed what the question was asking" in q1_feedback["what_happened"]


def test_empty_answer_is_asked_again(client):
    sid = create(client)
    log = []
    with client.websocket_connect(f"/ws/{sid}") as ws:
        client.post(f"/api/sessions/{sid}/start")
        answer(ws, recv_until(ws, "question", log), log=log)
        answer(ws, recv_until(ws, "question", log), "", log)          # nothing came through
        again = recv_until(ws, "question", log)
        assert again["data"]["text"] == fake.FAKE_LINES["redirect"]
        assert again["data"]["kind"] == "follow_up" and again["data"]["index"] == 1
        run_to_end(ws, log, first_question=again)
    assert_one_voice(log)


def test_turns_after_small_talk_are_written_by_gemini(client, monkeypatch):
    """Real mode with Gemini stubbed: the opening bridge, Gemini-worded next questions, the redirect cap,
    and parting words before the closing line."""
    monkeypatch.setattr(config, "BRAIN_FAKE", False)
    spoken = []

    async def fake_tts(text):
        spoken.append(text)
        return f"/audio/{len(spoken)}.mp3"

    async def opening(s, first):
        assert [e["kind"] for e in s.exchanges] == ["calibration"]    # bridge sees the small talk
        return "Love that you build APIs. Speaking of which, what project are you proudest of?"

    script = [
        Decision(action="redirect", spoken_text="Ha, fair. But back to it: which project are you proudest of?",
                 answer_quality=1, rationale="off-topic"),
        Decision(action="redirect", spoken_text="Still off topic. Once more?", answer_quality=1, rationale="again"),
        Decision(action="next", spoken_text="Makes sense. Bigger picture now: how would you design a chat app for a million users?",
                 answer_quality=3, rationale="fine"),
        Decision(action="wrap_up", spoken_text="Thanks, that was a thoughtful answer.", answer_quality=4, rationale="done"),
    ]

    async def decide(s, kind, question, transcript, stats, window):
        return script.pop(0)

    monkeypatch.setattr(voice, "tts", fake_tts)
    monkeypatch.setattr(gemini, "open_interview", opening)
    monkeypatch.setattr(gemini, "decide_next", decide)

    sid = create(client)
    log = []
    with client.websocket_connect(f"/ws/{sid}") as ws:
        client.post(f"/api/sessions/{sid}/start")
        answer(ws, recv_until(ws, "question", log), "I build backend APIs and I love hiking.", log)
        questions = run_to_end(ws, log)
    assert_one_voice(log)

    texts = [q["data"]["text"] for q in questions]
    assert texts == [
        "Love that you build APIs. Speaking of which, what project are you proudest of?",   # bridge
        "Ha, fair. But back to it: which project are you proudest of?",                     # redirect
        FALLBACK_BANK["demo"][1][0],         # second redirect capped → planned text, unworded
        "Makes sense. Bigger picture now: how would you design a chat app for a million users?",
    ]
    assert [q["data"]["index"] for q in questions] == [1, 1, 2, 3]
    assert spoken[-1] == f"Thanks, that was a thoughtful answer. {lines.CLOSING_LINE}"   # one closing clip
    assert [a["action"] for a in states[sid].adaptations] == ["redirect", "next", "next", "wrap_up"]


def test_coaching_mid_interview_and_on_the_last_question(client, monkeypatch):
    """Real mode with Gemini stubbed: a coaching turn carries the next question in the same clip, and
    coaching on the last answer is said right before the closing line, also as one clip."""
    monkeypatch.setattr(config, "BRAIN_FAKE", False)
    spoken = []

    async def fake_tts(text):
        spoken.append(text)
        return f"/audio/{len(spoken)}.mp3"

    coach_mid = ("Let me step out of the interview for a second. In a real interview that answer would sink you. "
                 "Name a project and what you shipped. Okay, back in.")
    next_q = "How would you design a chat app for a million users?"
    coach_last = ("Stepping out for a second: in a real interview, refusing to answer ends things. "
                  "A strong answer owns one real failure and what you changed.")
    coach_last_raw = coach_last + " Okay, back in the interview. So, what's your biggest failure?"   # ignored the prompt

    async def decide(s, kind, question, transcript, stats, window):
        return {1: Decision(action="coach", coaching=coach_mid, spoken_text=next_q, answer_quality=1, rationale="wrong"),
                2: Decision(action="next", spoken_text="", answer_quality=4, rationale="ok"),
                3: Decision(action="coach", coaching=coach_last_raw, spoken_text="", answer_quality=1,
                            rationale="refused")}[s.planned_number]

    async def opening(s, first):
        return None

    monkeypatch.setattr(voice, "tts", fake_tts)
    monkeypatch.setattr(gemini, "open_interview", opening)
    monkeypatch.setattr(gemini, "decide_next", decide)

    sid = create(client)
    log = []
    with client.websocket_connect(f"/ws/{sid}") as ws:
        client.post(f"/api/sessions/{sid}/start")
        answer(ws, recv_until(ws, "question", log), log=log)
        questions = run_to_end(ws, log)
    assert_one_voice(log)

    assert [q["data"]["text"] for q in questions] == [FALLBACK_BANK["demo"][0][0], next_q, FALLBACK_BANK["demo"][2][0]]
    assert [q["data"]["coaching"] for q in questions] == [None, coach_mid, None]
    assert f"{coach_mid} {next_q}" in spoken                         # coaching + question: one clip
    assert [q["data"]["kind"] for q in questions] == ["main", "main", "main"]
    report = client.get(f"/api/sessions/{sid}/report").json()
    assert report["adaptations"][0]["coaching"] == coach_mid
    assert spoken[-1] == f"{coach_last} {lines.CLOSING_LINE}"
    assert [a["action"] for a in states[sid].adaptations] == ["coach", "next", "coach"]


def test_session_does_not_end_until_the_closing_clip_has_played(client, monkeypatch):
    from backend.brain import interviewer
    monkeypatch.setattr(interviewer, "CLOSING_SLACK_S", 0.0)
    monkeypatch.setattr(voice, "clip_seconds", lambda url: 0.8 if url else 0.0)
    sid = create(client)
    log = []
    with client.websocket_connect(f"/ws/{sid}") as ws:
        client.post(f"/api/sessions/{sid}/start")
        answer(ws, recv_until(ws, "question", log), log=log)
        for _ in range(3):
            answer(ws, recv_until(ws, "question", log), log=log)
        recv_until(ws, "filler", log)                              # closing clip starts
        started = time.monotonic()
        recv_until(ws, "session_ended", log)
        assert time.monotonic() - started >= 0.75


@pytest.mark.parametrize("gemini_line,expected", [
    ("Turtles, huh? Respect. So what got you into computer science in the first place?", None),
    (None, "fallback"),   # Gemini unavailable: the fixed line
])
def test_second_small_talk_turn_reacts_to_the_first_answer(client, monkeypatch, gemini_line, expected):
    from backend.brain import interviewer
    monkeypatch.setattr(config, "BRAIN_FAKE", False)
    monkeypatch.setattr(interviewer, "CALIBRATION_MIN_S", 999.0)     # first answer too short → second turn
    seen = []

    async def small_talk(s):
        seen.append([e["transcript"] for e in s.exchanges])
        return gemini_line

    async def no_audio(text):
        return None

    monkeypatch.setattr(gemini, "small_talk_follow_up", small_talk)
    monkeypatch.setattr(voice, "tts", no_audio)
    sid = create(client)
    log = []
    with client.websocket_connect(f"/ws/{sid}") as ws:
        client.post(f"/api/sessions/{sid}/start")
        first = recv_until(ws, "question", log)
        assert first["data"]["text"] == lines.CALIBRATION_LINE
        answer(ws, first, "I like turtles.", log)
        second = recv_until(ws, "question", log)
        ws.send_json({"type": "end_session", "data": {}})
        recv_until(ws, "session_ended", log)

    assert seen == [["I like turtles."]]                              # it saw what they actually said
    assert second["data"]["kind"] == "calibration" and second["data"]["index"] == 0
    want = gemini_line if expected is None else lines.CALIBRATION_EXTRA_LINES["demo"]
    assert second["data"]["text"] == want


def test_database_down_at_the_end_still_ends_the_session(client, monkeypatch):
    """If Tiger Data is unreachable when the session wraps up, the browser still gets session_ended
    and GET /report serves the report from memory."""
    async def down(*_a, **_k):
        raise ConnectionError("Tiger Data unreachable")

    sid = create(client)
    log = []
    with client.websocket_connect(f"/ws/{sid}") as ws:
        client.post(f"/api/sessions/{sid}/start")
        answer(ws, recv_until(ws, "question", log), log=log)
        q = recv_until(ws, "question", log)
        monkeypatch.setattr(data, "save_report", down)
        monkeypatch.setattr(data, "end_session", down)
        monkeypatch.setattr(data, "get_report", down)
        run_to_end(ws, log, first_question=q)
    assert log[-1]["type"] == "session_ended"
    r = client.get(f"/api/sessions/{sid}/report")
    assert r.status_code == 200 and r.json()["generated_by"] == "fake"


def test_database_down_mid_interview_still_ends_the_session(client, monkeypatch):
    async def down(*_a, **_k):
        raise ConnectionError("Tiger Data unreachable")

    sid = create(client)
    log = []
    with client.websocket_connect(f"/ws/{sid}") as ws:
        client.post(f"/api/sessions/{sid}/start")
        answer(ws, recv_until(ws, "question", log), log=log)
        q = recv_until(ws, "question", log)
        for name in ("insert_question", "get_question_stats", "get_session_history", "save_report",
                     "end_session", "get_report", "get_window_stats"):
            monkeypatch.setattr(data, name, down)
        answer(ws, q, log=log)
        recv_until(ws, "session_ended", log)
    assert client.get(f"/api/sessions/{sid}/report").json()["generated_by"] == "fallback"


def test_setup_fields_are_tidied_and_capped(client):
    r = client.post("/api/sessions", json={"user_name": "  Ada   Lovelace  ", "interview_type": "demo",
                                            "role_title": "x" * 500})
    sid = r.json()["session_id"]
    from backend.brain.state import metas
    assert metas[sid].user_name == "Ada Lovelace" and len(metas[sid].role_title) == 120


def test_health_reports_data_store_and_sensor(client):
    from backend import ws as hub
    h = client.get("/health").json()
    assert h["data_store"] in ("in_memory", "tiger_data") and h["sensor"]["live"] is False
    import asyncio as _a
    client.portal.call(hub.broadcast, "nobody", "vitals", {"ts": "t", "heart_rate": None})   # no face
    h = client.get("/health").json()
    assert h["sensor"]["live"] is True and h["sensor"]["seconds_since_reading"] < 1
    assert h["sensor"]["signal"] is False
    client.portal.call(hub.broadcast, "nobody", "vitals", {"ts": "t", "heart_rate": 72.0})
    assert client.get("/health").json()["sensor"]["signal"] is True


def test_two_interviews_at_once_never_see_each_others_events(client):
    a, b = create(client), create(client, "Behavioral")
    with client.websocket_connect(f"/ws/{a}") as wa, client.websocket_connect(f"/ws/{b}") as wb:
        client.post(f"/api/sessions/{a}/start")
        client.post(f"/api/sessions/{b}/start")
        qa, qb = recv_until(wa, "question", []), recv_until(wb, "question", [])
        answer(wa, qa)
        answer(wb, qb)
        qa2, qb2 = recv_until(wa, "question", []), recv_until(wb, "question", [])
        assert qa2["data"]["total"] == 3 and qb2["data"]["total"] == 4       # demo vs behavioral (fake: 4)
        ids_a = {q["data"]["question_id"] for q in (qa, qa2)}
        ids_b = {q["data"]["question_id"] for q in (qb, qb2)}
        assert not ids_a & ids_b
        wa.send_json({"type": "end_session", "data": {}})
        recv_until(wa, "session_ended", [])
        # b is unaffected by a ending
        assert client.get(f"/api/sessions/{b}/status").json()["end_requested"] is False
        wb.send_json({"type": "end_session", "data": {}})
        recv_until(wb, "session_ended", [])


def test_vitals_endpoint_edges(client):
    assert client.post("/api/vitals", json={"readings": [{"heart_rate": 70}]}).status_code == 422   # no ts
    assert client.post("/api/vitals", json={"nope": 1}).status_code == 422
    assert client.post("/api/vitals", json={"readings": []}).json() == {"stored": 0}
    sid = create(client)
    r = client.post("/api/vitals", json={"readings": [{"session_id": sid, "ts": "2026-10-10T20:00:00Z",
                                                       "heart_rate": 72, "confidence": 0.95}]})
    assert r.status_code == 200 and r.json()["stored"] == 1


def test_unknown_sessions_fail_cleanly(client):
    assert client.get("/api/sessions/not-a-session/report").status_code == 404
    assert client.get("/api/sessions/not-a-session/status").status_code == 404
    with client.websocket_connect("/ws/not-a-session") as ws:   # connecting is harmless; nothing is replayed
        ws.send_json({"type": "answer_done", "data": {"question_id": "x", "transcript": "hi"}})
