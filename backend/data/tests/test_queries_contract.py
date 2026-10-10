"""Every function in PLAN.md section 3 exists with the agreed signature and return type."""
import asyncio
import inspect
from datetime import datetime, timezone

from backend.data import queries
from backend.data._contracts import Baseline, QuestionStats, VitalsReading, WindowStats

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


def test_signatures_match_plan():
    for name, expected in CONTRACT.items():
        fn = getattr(queries, name)
        assert inspect.iscoroutinefunction(fn), f"{name} must be async"
        params = [(p.name, None if p.default is inspect.Parameter.empty else p.default)
                  for p in inspect.signature(fn).parameters.values()]
        assert params == expected, f"{name}{params} != PLAN.md {expected}"


def test_return_types():
    async def run():
        now = datetime.now(timezone.utc)
        sid = await queries.create_session("Likhil", "demo", "SWE Intern")
        assert isinstance(sid, str)
        assert await queries.get_active_session() == sid

        rows = await queries.insert_vitals([
            VitalsReading(ts=now, heart_rate=72, breathing_rate=14, focus=0.8,
                          expression="neutral", confidence=0.95),
            VitalsReading(ts=now, confidence=0.0),                       # face lost
        ])
        assert isinstance(rows, list) and len(rows) == 2
        for r in rows:
            assert set(r) == {"time", "session_id", "heart_rate", "breathing_rate", "focus",
                              "expression", "confidence", "stress"}
            assert r["session_id"] == sid and isinstance(r["time"], datetime)
        assert rows[1]["stress"] is None

        b = await queries.compute_baseline(sid)
        assert isinstance(b, Baseline)
        assert isinstance(await queries.get_baseline(sid), Baseline)

        w = await queries.get_window_stats(sid)
        assert isinstance(w, WindowStats) and w.seconds == 15
        assert 20 <= w.stress <= 60
        assert (await queries.get_window_stats(sid, 10)).seconds == 10

        qid = await queries.insert_question(sid, 1, "main", "Tell me about a bug you fixed.")
        assert isinstance(qid, str)
        assert await queries.mark_answer_started(qid, now) is None
        assert await queries.mark_answered(qid, now, "I once...") is None

        qs = await queries.get_question_stats(qid)
        assert isinstance(qs, QuestionStats) and qs.question_id == qid

        hist = await queries.get_session_history(sid)
        assert isinstance(hist, list) and len(hist) == 1
        assert set(hist[0]) == {"idx", "kind", "text", "transcript", "stats"}
        assert isinstance(hist[0]["stats"], QuestionStats)

        assert await queries.get_report(sid) is None
        assert await queries.save_report(sid, {"composure_score": 80}) is None
        assert await queries.get_report(sid) == {"composure_score": 80}

        assert await queries.end_session(sid) is None
        assert await queries.get_active_session() is None
        assert await queries.insert_vitals([VitalsReading(ts=now, heart_rate=70)]) == []

    asyncio.run(run())
