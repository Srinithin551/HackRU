#!/usr/bin/env python
"""Phase 6: time every Data query on a session with 10 minutes of data.

    python scripts/viz/bench_queries.py [--runs 20] [--keep]

Seeds a fresh session (user_name 'bench') with 600 readings at 1 Hz from the mock_vitals
simulator (spikes, a distraction, a face-lost gap), a calibration baseline and 8 answered
questions. Then for each function: 2 warm-up calls, `--runs` timed calls (round trip from
this machine, p50/p95), plus the server-side `EXPLAIN ANALYZE` execution time of its SQL.
Also measures the network floor (`SELECT 1`). Writes docs/data/samples/bench_latency.csv and
docs/data/latency.md, then deletes everything it created unless --keep.
"""
import argparse
import asyncio
import csv
import importlib.util
import random
import re
import statistics
import sys
import time
from datetime import timedelta

import style

sys.path.insert(0, str(style.ROOT))
from backend.contracts import VitalsReading  # noqa: E402
from backend.data import db, queries  # noqa: E402

TARGET_MS = 50.0
_spec = importlib.util.spec_from_file_location("mock_vitals", style.ROOT / "scripts" / "mock_vitals.py")
mock_vitals = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mock_vitals)


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, round(p / 100 * len(xs)) - 1))]


async def seed(pool, minutes: int = 10):
    sid = await queries.create_session("bench", "swe", "Phase 6 benchmark")
    now = await pool.fetchval("SELECT now()")
    t0 = now - timedelta(seconds=minutes * 60)
    await pool.execute("UPDATE sessions SET started_at = $2 WHERE id = $1::uuid", sid, t0)
    sim = mock_vitals.VitalsSimulator(random.Random(6))
    events = {120: "s", 200: "d", 300: "s", 400: "f", 480: "s"}
    readings = []
    for i in range(minutes * 60):
        if i in events:
            sim.trigger(events[i], i)
        r = sim.reading(i, t0 + timedelta(seconds=i))
        readings.append(VitalsReading(session_id=sid, **r))
    await queries.insert_vitals(readings[:45])                 # calibration, no baseline yet
    cal = await queries.insert_question(sid, 0, "calibration", "How's HackRU going?")
    await queries.mark_answer_started(cal, t0 + timedelta(seconds=2))
    await queries.mark_answered(cal, t0 + timedelta(seconds=44), "Great so far.")
    await queries.compute_baseline(sid)
    await queries.insert_vitals(readings[45:])
    qids = []
    for k in range(8):                                       # 8 answers of ~60 s from t=50 s
        start = t0 + timedelta(seconds=50 + k * 68)
        qid = await queries.insert_question(sid, k + 1, "follow_up" if k % 3 == 2 else "main", f"Question {k + 1}")
        await pool.execute("UPDATE questions SET created_at = $2 WHERE id = $1::uuid", qid, start - timedelta(seconds=3))
        await queries.mark_answer_started(qid, start)
        await queries.mark_answered(qid, start + timedelta(seconds=60), f"Answer {k + 1}")
        qids.append(qid)
    n = await pool.fetchval("SELECT count(*) FROM vitals WHERE session_id = $1::uuid", sid)
    return sid, qids, n


async def time_calls(fn, runs: int, before=None) -> list[float]:
    for _ in range(2):
        if before:
            before()
        await fn()
    out = []
    for _ in range(runs):
        if before:
            before()
        t = time.perf_counter()
        await fn()
        out.append((time.perf_counter() - t) * 1000)
    return out


async def explain_ms(pool, sql: str, *args, runs: int = 10) -> float:
    """Median server execution time. Always inside a rolled-back transaction, so EXPLAIN ANALYZE of a write is safe."""
    vals = []
    for _ in range(runs):
        async with pool.acquire() as conn:
            tr = conn.transaction()
            await tr.start()
            try:
                plan = await conn.fetch("EXPLAIN (ANALYZE, SUMMARY ON) " + sql, *args)
            finally:
                await tr.rollback()
        text = "\n".join(r[0] for r in plan)
        vals.append(float(re.search(r"Execution Time: ([\d.]+) ms", text).group(1)))
    return statistics.median(vals)


async def main(runs: int, keep: bool) -> None:
    queries.FAKE = False
    pool = await db.init_pool()
    sid, qids, n_rows = await seed(pool)
    mid_q = qids[3]
    created: list[str] = []
    rows: list[dict] = []
    try:
        uuid_sid = queries._uuid(sid)
        floor = await time_calls(lambda: pool.fetchval("SELECT 1"), runs)
        now = lambda: queries.datetime.now(queries.timezone.utc)
        reading = lambda: [VitalsReading(session_id=sid, ts=now(), heart_rate=73, breathing_rate=14,
                                         focus=0.8, expression="neutral", confidence=0.95)]
        q_extra = []

        async def new_question():
            q_extra.append(await queries.insert_question(sid, 99, "main", "bench question"))

        async def new_session():
            created.append(await queries.create_session("bench", "swe", "throwaway"))

        async def end_one():
            await queries.end_session(created.pop(0))

        async def ins_vitals():
            await queries.insert_vitals(reading())

        cases = [
            # (name, who calls it / how often, fn, before, explain (sql, args))
            ("get_window_stats(15 s)", "Brain watcher, every 3 s", lambda: queries.get_window_stats(sid, 15), None,
             (queries.WINDOW_SQL, [uuid_sid, 15.0])),
            ("insert_vitals (1 reading)", "Sensing, every 1 s", ins_vitals, None,
             (queries.INSERT_VITALS, [now(), uuid_sid, 73.0, 14.0, 0.8, "neutral", 0.95, 2.0])),
            ("get_question_stats", "Brain, after every answer", lambda: queries.get_question_stats(mid_q), None,
             (queries.QUESTION_STATS_SQL + "WHERE q.id = $1", [queries._uuid(mid_q)])),
            ("get_active_session (uncached)", "Sensing client, every 2 s",
             queries.get_active_session, queries._reset_caches,
             ("SELECT id FROM sessions WHERE ended_at IS NULL AND started_at > now() - interval '30 minutes' "
              "ORDER BY started_at DESC LIMIT 1", [])),
            ("get_baseline (uncached)", "Brain, Data internals", lambda: queries.get_baseline(sid),
             queries._reset_caches,
             ("SELECT baseline_hr, baseline_br, baseline_focus, baseline_n FROM sessions WHERE id = $1", [uuid_sid])),
            ("insert_question", "Brain, per question", new_question, None, None),
            ("mark_answer_started", "Brain, per question", lambda: queries.mark_answer_started(qids[0], now()), None, None),
            ("mark_answered", "Brain, per question", lambda: queries.mark_answered(qids[0], now(), "x"), None, None),
            ("get_session_history (9 questions)", "Brain, once at the end",
             lambda: queries.get_session_history(sid), None,
             (queries.QUESTION_STATS_SQL + "WHERE q.session_id = $1 ORDER BY q.idx, q.created_at", [uuid_sid])),
            ("get_replay (3 queries in parallel)", "Frontend replay page, once", lambda: queries.get_replay(sid), None,
             (queries.REPLAY_SERIES_SQL, [uuid_sid])),
            ("compute_baseline", "Brain, once after calibration", lambda: queries.compute_baseline(sid), None,
             (queries.BASELINE_SQL, [uuid_sid, 10.0])),
            ("save_report", "Brain, once", lambda: queries.save_report(sid, {"composure_score": 70}), None, None),
            ("get_report", "Frontend, once", lambda: queries.get_report(sid), None, None),
            ("create_session", "Brain, once", new_session, None, None),
            ("end_session (+ vitals_5s refresh)", "Brain, once", end_one, None, None),
        ]
        for name, caller, fn, before, ex in cases:
            if name.startswith("end_session"):
                while len(created) < runs + 2:
                    await new_session()
            ms = await time_calls(fn, runs, before)
            server = await explain_ms(pool, ex[0], *ex[1]) if ex else None
            rows.append({"query": name, "caller": caller, "runs": runs, "p50_ms": round(statistics.median(ms), 1),
                         "p95_ms": round(pct(ms, 95), 1), "min_ms": round(min(ms), 1), "max_ms": round(max(ms), 1),
                         "server_exec_ms": None if server is None else round(server, 3)})
            print(f"{name:38s} p50 {rows[-1]['p50_ms']:6.1f}  p95 {rows[-1]['p95_ms']:6.1f}  "
                  f"server {'' if server is None else f'{server:.3f}'} ms")
        floor_row = {"query": "network floor: SELECT 1", "caller": "-", "runs": runs,
                     "p50_ms": round(statistics.median(floor), 1), "p95_ms": round(pct(floor, 95), 1),
                     "min_ms": round(min(floor), 1), "max_ms": round(max(floor), 1), "server_exec_ms": None}
        rows.append(floor_row)
        print(f"{'network floor: SELECT 1':38s} p50 {floor_row['p50_ms']:6.1f}  p95 {floor_row['p95_ms']:6.1f}")

        style.SAMPLES.mkdir(parents=True, exist_ok=True)
        with (style.SAMPLES / "bench_latency.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        write_md(rows, n_rows, runs)
    finally:
        if not keep:
            ids = [sid] + created
            await pool.execute("DELETE FROM vitals WHERE session_id = ANY($1::uuid[])", ids)
            await pool.execute("DELETE FROM questions WHERE session_id = ANY($1::uuid[])", ids)
            await pool.execute("DELETE FROM sessions WHERE user_name = 'bench'")
        await db.close_pool()


def write_md(rows: list[dict], n_rows: int, runs: int) -> None:
    floor = next(r for r in rows if r["query"].startswith("network floor"))
    lines = ["# Query latency (Phase 6)", "",
             f"Session seeded with **{n_rows} readings (10 min at 1 Hz)** and 9 questions on Tiger Data. "
             f"Each function: 2 warm-up calls, then {runs} timed calls from the dev laptop. "
             "*Round trip* = what the caller waits; *server* = `EXPLAIN ANALYZE` execution time on Tiger "
             "(median of 10). Generated by `scripts/viz/bench_queries.py`; chart: "
             "[`charts/chart4_query_latency.png`](charts/chart4_query_latency.png).", "",
             f"**Network floor** (`SELECT 1`, no table): p50 {floor['p50_ms']} ms, p95 {floor['p95_ms']} ms.", "",
             "| Function | Called by | Round trip p50 | p95 | Server execution | Under 50 ms? |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        if r is floor:
            continue
        server = "n/a (one row by primary key)" if r["server_exec_ms"] is None else f"{r['server_exec_ms']:.2f} ms"
        ok = "yes" if r["p50_ms"] < TARGET_MS else "server yes; round trip no (network floor)"
        lines.append(f"| `{r['query']}` | {r['caller']} | {r['p50_ms']} ms | {r['p95_ms']} ms | {server} | {ok} |")
    (style.ROOT / "docs" / "data" / "latency.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote docs/data/latency.md and docs/data/samples/bench_latency.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--keep", action="store_true", help="keep the seeded session (for compression tests)")
    a = ap.parse_args()
    asyncio.run(main(a.runs, a.keep))
