"""Every function in PLAN.md section 3 exists with the agreed signature, in both the Tiger Data
version (queries.py) and the in-memory version (fake_queries.py), and returns the agreed types."""
import asyncio
import inspect
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.contracts import Baseline, QuestionStats, VitalsReading, WindowStats
from backend.data.metrics import MIN_CONFIDENCE
from backend.data import fake_queries, queries

# name -> parameters exactly as written in PLAN.md section 3 (name, default or None)
CONTRACT = {
    "create_session": [("user_name", None), ("interview_type", None), ("role_title", None)],
    "get_active_session": [],
    "get_session": [("session_id", None)],          # additive (not in PLAN.md): Brain's /start checks ids
    "end_session": [("session_id", None)],
    "insert_vitals": [("readings", None)],
    "compute_baseline": [("session_id", None)],
    "get_baseline": [("session_id", None)],
    "get_window_stats": [("session_id", None), ("seconds", 15)],
    "insert_question": [("session_id", None), ("idx", None), ("kind", None), ("text", None)],
    "mark_answer_started": [("question_id", None), ("ts", None)],
    "mark_answered": [("question_id", None), ("ts", None), ("transcript", None)],
    "get_question_stats": [("question_id", None)],
    "get_session_history": [("session_id", None)],
    "save_report": [("session_id", None), ("report", None)],
    "get_report": [("session_id", None)],
}


@pytest.mark.parametrize("module", [queries, fake_queries], ids=["queries", "fake_queries"])
def test_signatures_match_plan(module):
    for name, expected in CONTRACT.items():
        fn = getattr(module, name)
        assert inspect.iscoroutinefunction(fn), f"{module.__name__}.{name} must be async"
        params = [(p.name, None if p.default is inspect.Parameter.empty else p.default)
                  for p in inspect.signature(fn).parameters.values()]
        assert params == expected, f"{module.__name__}.{name}{params} != PLAN.md {expected}"


def test_pytest_never_touches_the_shared_db():
    assert queries.FAKE, "under pytest, queries.py must route to fake_queries unless DATA_FAKE=0"


def test_fake_end_to_end_shapes():
    """A whole session through the queries.py facade (in-memory under pytest)."""
    async def run():
        t0 = datetime.now(timezone.utc) - timedelta(seconds=60)
        sid = await queries.create_session("Likhil", "demo", "SWE Intern")
        assert isinstance(sid, str) and await queries.get_active_session() == sid
        fake_queries._sessions[sid]["started_at"] = t0      # pretend the session began a minute ago

        calm = [VitalsReading(ts=t0 + timedelta(seconds=i), heart_rate=72, breathing_rate=14, focus=0.8,
                              expression="neutral", confidence=0.95) for i in range(40)]
        lost = [VitalsReading(ts=t0 + timedelta(seconds=40), confidence=0.0),
                VitalsReading(ts=t0 + timedelta(seconds=41), heart_rate=72, confidence=MIN_CONFIDENCE - 0.01)]
        rows = await queries.insert_vitals(calm + lost)
        assert len(rows) == 42                                  # returned for the live chart...
        assert len(fake_queries._vitals[sid]) == 40             # ...but below the cutoff is never stored
        for r in rows:
            assert set(r) == {"time", "session_id", "heart_rate", "breathing_rate", "focus",
                              "expression", "confidence", "stress"}
            assert r["session_id"] == sid and r["stress"] is None        # no baseline yet

        b = await queries.compute_baseline(sid)
        assert isinstance(b, Baseline) and b.n_samples == 30 and b.heart_rate == pytest.approx(72)
        assert await queries.get_baseline(sid) == b

        qid = await queries.insert_question(sid, 1, "main", "Tell me about a bug you fixed.")
        start = t0 + timedelta(seconds=45)
        await queries.mark_answer_started(qid, start)
        spike = [VitalsReading(ts=start + timedelta(seconds=i), heart_rate=90, breathing_rate=18.2, focus=0.5,
                               expression="frown", confidence=0.95) for i in range(10)]
        rows = await queries.insert_vitals(spike)
        assert rows[0]["stress"] == pytest.approx(84.5, abs=0.1)
        await queries.mark_answered(qid, start + timedelta(seconds=12), "I once...")

        w = await queries.get_window_stats(sid)
        assert isinstance(w, WindowStats) and w.seconds == 15 and w.stress == pytest.approx(84.5, abs=0.1)
        assert w.hr_delta_pct == pytest.approx(25, abs=0.1) and w.dominant_expression == "frown"

        qs = await queries.get_question_stats(qid)
        assert isinstance(qs, QuestionStats) and qs.question_id == qid and qs.n_samples == 10
        assert qs.duration_s == 12 and qs.stress_peak == pytest.approx(84.5, abs=0.1)

        hist = await queries.get_session_history(sid)
        assert [set(h) for h in hist] == [{"question_id", "idx", "kind", "text", "transcript", "stats"}]
        assert hist[0]["question_id"] == qid and isinstance(hist[0]["stats"], QuestionStats)

        assert await queries.get_report(sid) is None
        await queries.save_report(sid, {"composure_score": 80})
        assert await queries.get_report(sid) == {"composure_score": 80}

        await queries.end_session(sid)
        assert await queries.get_active_session() is None
        assert await queries.insert_vitals([VitalsReading(ts=t0, heart_rate=70)]) == []

        assert_replay_shape(await queries.get_replay(sid))
        assert await queries.get_replay(str(uuid.uuid4())) is None

    asyncio.run(run())


# The replay JSON shape agreed with Frontend (docs/roles/data.md, "Replay endpoint")
REPLAY_KEYS = {
    "top": {"session", "series", "questions"},
    "session": {"id", "user_name", "started_at", "ended_at", "baseline"},
    "baseline": {"heart_rate", "breathing_rate", "focus"},
    "series": {"t", "hr", "br", "focus", "stress", "stress_peak"},
    "question": {"id", "idx", "kind", "text", "transcript", "answer_started_at", "answered_at", "stats"},
}


def assert_replay_shape(r: dict) -> None:
    assert set(r) == REPLAY_KEYS["top"]
    assert set(r["session"]) == REPLAY_KEYS["session"]
    assert r["session"]["baseline"] is None or set(r["session"]["baseline"]) == REPLAY_KEYS["baseline"]
    assert r["series"], "series should not be empty"
    assert all(set(p) == REPLAY_KEYS["series"] and p["t"].endswith("Z") for p in r["series"])
    assert [p["t"] for p in r["series"]] == sorted(p["t"] for p in r["series"])
    for q in r["questions"]:
        assert set(q) == REPLAY_KEYS["question"]
        assert set(q["stats"]) == set(QuestionStats.model_fields)
    json.dumps(r)                                   # serializable as-is


def test_routes_through_brains_app():
    """GET /api/sessions/active and /replay, mounted by Brain's main.py (in-memory under pytest)."""
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as client:
        sid = asyncio.run(queries.create_session("Route test", "demo", "SWE"))
        assert client.get("/api/sessions/active").json() == {"session_id": sid}
        now = datetime.now(timezone.utc)
        asyncio.run(queries.insert_vitals([VitalsReading(ts=now, heart_rate=72, confidence=0.9)]))
        r = client.get(f"/api/sessions/{sid}/replay")
        assert r.status_code == 200
        assert_replay_shape(r.json())
        assert client.get(f"/api/sessions/{uuid.uuid4()}/replay").status_code == 404
        asyncio.run(queries.end_session(sid))
        assert client.get("/api/sessions/active").json() is None


def test_new_session_closes_older_open_ones():
    """One sensor, one person: starting an interview closes this backend's older open sessions, so stale
    sessions can never soak up Presage readings between interviews."""
    async def run():
        a = await queries.create_session("A", "demo", "x")
        b = await queries.create_session("B", "demo", "x")
        assert await queries.get_active_session() == b
        await queries.end_session(b)
        assert await queries.get_active_session() is None      # not the leftover A
    asyncio.run(run())


def test_ingest_counts_track_the_cutoff():
    from backend.data import metrics
    before = dict(metrics.INGEST)
    async def run():
        sid = await queries.create_session("C", "demo", "x")
        now = datetime.now(timezone.utc)
        await queries.insert_vitals([VitalsReading(session_id=sid, ts=now, heart_rate=72, confidence=0.9),
                                     VitalsReading(session_id=sid, ts=now, heart_rate=None, confidence=0.0)])
    asyncio.run(run())
    assert metrics.INGEST["stored"] - before.get("stored", 0) == 1
    assert metrics.INGEST["below_cutoff"] - before.get("below_cutoff", 0) == 1
