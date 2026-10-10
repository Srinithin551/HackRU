"""The real Tiger Data queries, end to end. Skipped when no TIGER_DATABASE_URL is configured.

Each test makes its own session (user_name 'pytest'), backdates it, and deletes every row it
wrote afterwards, so the shared database is left as it was.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from backend.contracts import Baseline, QuestionStats, VitalsReading, WindowStats
from backend.data import db, queries
from test_queries_contract import assert_replay_shape

pytestmark = pytest.mark.skipif(
    not (db.config.TIGER_DATABASE_URL), reason="no TIGER_DATABASE_URL: real-DB tests skipped")


@pytest.fixture(autouse=True)
def real_db(monkeypatch):
    monkeypatch.setattr(queries, "FAKE", False)
    queries._reset_caches()
    yield
    queries._reset_caches()


def run(coro_fn):
    async def wrapper():
        await db.init_pool()
        pool = await db.get_pool()
        sid = await queries.create_session("pytest", "test", "Data integration test")
        try:
            db_now = await pool.fetchval("SELECT now()")
            await pool.execute("UPDATE sessions SET started_at = $2 WHERE id = $1::uuid",
                               sid, db_now - timedelta(seconds=120))
            await coro_fn(sid, db_now, pool)
        finally:
            await pool.execute("DELETE FROM vitals WHERE session_id = $1::uuid", sid)
            await pool.execute("DELETE FROM questions WHERE session_id = $1::uuid", sid)
            await pool.execute("DELETE FROM sessions WHERE id = $1::uuid", sid)
            await db.close_pool()
    asyncio.run(wrapper())


def calm(t, conf=0.95):
    return VitalsReading(ts=t, heart_rate=72, breathing_rate=14, focus=0.8, expression="neutral", confidence=conf)


def spiked(t):
    return VitalsReading(ts=t, heart_rate=90, breathing_rate=18.2, focus=0.5, expression="frown", confidence=0.95)


def test_full_session_on_tiger_data():
    async def body(sid, now, pool):
        t0 = now - timedelta(seconds=120)
        assert await queries.get_active_session() == sid

        # calibration: 40 calm readings + 2 face-lost, no baseline yet -> stress NULL
        cal = [calm(t0 + timedelta(seconds=i)) for i in range(40)]
        cal += [calm(t0 + timedelta(seconds=40 + i), conf=0.0) for i in range(2)]
        rows = await queries.insert_vitals(cal)
        assert len(rows) == 42 and all(r["stress"] is None and r["session_id"] == sid for r in rows)

        b = await queries.compute_baseline(sid)
        assert isinstance(b, Baseline) and b.n_samples == 30        # first 10 s skipped
        assert b.heart_rate == pytest.approx(72) and b.focus == pytest.approx(0.8)
        assert await queries.get_baseline(sid) == b
        stored = await pool.fetchrow("SELECT baseline_hr, baseline_n FROM sessions WHERE id = $1::uuid", sid)
        assert stored["baseline_n"] == 30
        # backfill: every good calibration row now has stress ~0; face-lost rows stay NULL
        backfilled = await pool.fetch("SELECT confidence, stress FROM vitals WHERE session_id = $1::uuid", sid)
        assert all(r["stress"] == 0 for r in backfilled if r["confidence"] >= 0.5)
        assert all(r["stress"] is None for r in backfilled if r["confidence"] < 0.5)

        # question 1: a stressed answer over the last 20 s
        qid = await queries.insert_question(sid, 1, "main", "Design a rate limiter.")
        start = now - timedelta(seconds=20)
        await queries.mark_answer_started(qid, start)
        rows = await queries.insert_vitals([spiked(start + timedelta(seconds=i)) for i in range(20)])
        assert rows[0]["stress"] == pytest.approx(84.5, abs=0.1)
        await queries.mark_answered(qid, now - timedelta(seconds=1), "Token bucket per user...")

        w = await queries.get_window_stats(sid, 15)
        assert isinstance(w, WindowStats) and w.seconds == 15 and 12 <= w.n_samples <= 16
        assert w.stress == pytest.approx(84.5, abs=0.1) and w.hr_delta_pct == pytest.approx(25, abs=0.1)
        assert w.dominant_expression == "frown"

        qs = await queries.get_question_stats(qid)
        assert isinstance(qs, QuestionStats) and qs.question_id == qid
        assert qs.n_samples == 20 and qs.duration_s == pytest.approx(19, abs=0.1)
        assert qs.hr_peak == pytest.approx(90) and qs.stress_peak == pytest.approx(84.5, abs=0.1)
        assert 0 <= qs.peak_offset_s <= 19

        empty = await queries.get_question_stats("00000000-0000-0000-0000-000000000000")
        assert empty.n_samples == 0 and empty.stress_peak is None

        hist = await queries.get_session_history(sid)
        assert [h["question_id"] for h in hist] == [qid] and hist[0]["stats"] == qs

        await queries.save_report(sid, {"composure_score": 61, "top_tips": ["breathe"]})
        assert await queries.get_report(sid) == {"composure_score": 61, "top_tips": ["breathe"]}

        await queries.end_session(sid)
        assert await queries.get_active_session() != sid

        # replay: end_session materialized the whole session, so the backfilled calibration
        # stress is in vitals_5s right away (not after the next 30 s policy refresh)
        replay = await queries.get_replay(sid)
        assert_replay_shape(replay)
        assert replay["session"]["baseline"] == {"heart_rate": 72.0, "breathing_rate": 14.0, "focus": 0.8}
        cal_buckets = [p for p in replay["series"] if p["t"] < (now - timedelta(seconds=85)).isoformat()]
        assert cal_buckets and all(p["stress"] == 0 for p in cal_buckets if p["hr"] is not None)
        assert max(p["stress_peak"] or 0 for p in replay["series"]) == pytest.approx(84.5, abs=0.1)
        assert [q["id"] for q in replay["questions"]] == [qid] and replay["questions"][0]["stats"] == qs.model_dump()
    run(body)


def test_baseline_falls_back_to_population_defaults():
    async def body(sid, now, pool):
        await queries.insert_vitals([calm(now - timedelta(seconds=100 - i)) for i in range(5)])
        b = await queries.compute_baseline(sid)
        assert (b.heart_rate, b.breathing_rate, b.focus, b.n_samples) == (75.0, 15.0, 0.75, 0)
    run(body)


def test_no_active_session_drops_readings():
    async def body(sid, now, pool):
        await queries.end_session(sid)
        before = await pool.fetchval("SELECT count(*) FROM vitals WHERE session_id = $1::uuid", sid)
        active = await queries.get_active_session()
        rows = await queries.insert_vitals([calm(now)])
        if active is None:      # someone else may have a live session on the shared DB
            assert rows == []
        assert await pool.fetchval("SELECT count(*) FROM vitals WHERE session_id = $1::uuid", sid) == before
    run(body)
