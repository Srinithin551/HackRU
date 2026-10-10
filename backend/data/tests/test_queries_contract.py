"""Every function in PLAN.md section 3 exists with the agreed signature, in both the Tiger Data
version (queries.py) and the in-memory version (fake_queries.py), and returns the agreed types."""
import asyncio
import inspect
from datetime import datetime, timedelta, timezone

import pytest

from backend.contracts import Baseline, QuestionStats, VitalsReading, WindowStats
from backend.data import fake_queries, queries

# name -> parameters exactly as written in PLAN.md section 3 (name, default or None)
CONTRACT = {
    "create_session": [("user_name", None), ("interview_type", None), ("role_title", None)],
    "get_active_session": [],
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
        rows = await queries.insert_vitals(calm + [VitalsReading(ts=t0 + timedelta(seconds=40), confidence=0.0)])
        assert len(rows) == 41
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

    asyncio.run(run())
