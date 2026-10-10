#!/usr/bin/env python
"""Columnstore demo: how much smaller vitals gets when TimescaleDB stores it column by column.

    python scripts/viz/compression_demo.py [--synthetic 30] [--keep]

The live vitals chunk is not converted during the hackathon (columnstore.sql converts chunks
once they're a day old), so this measures on a copy: vitals_columnstore_demo, same columns,
indexes and columnstore settings (segmentby session_id, orderby time DESC).

  1. copy every real session from vitals, measure the rowstore size
  2. convert the chunks to the columnstore, measure again
  3. check a per-session 5 s rollup returns identical results before and after
  4. with --synthetic N, repeat with N extra 10-minute sessions from the mock_vitals
     simulator, to show the ratio at a realistic size (labeled synthetic everywhere)

Writes docs/data/samples/compression.csv and docs/data/compression.md, then drops the demo
table unless --keep.
"""
import argparse
import asyncio
import csv
import importlib.util
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

import style

sys.path.insert(0, str(style.ROOT))
from backend.contracts import Baseline, VitalsReading  # noqa: E402
from backend.data import db, metrics  # noqa: E402

DEMO = "vitals_columnstore_demo"
_spec = importlib.util.spec_from_file_location("mock_vitals", style.ROOT / "scripts" / "mock_vitals.py")
mock_vitals = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mock_vitals)

BASE = Baseline(heart_rate=72.0, breathing_rate=14.0, focus=0.8, n_samples=30)
COLS = ["time", "session_id", "heart_rate", "breathing_rate", "focus", "expression", "confidence", "stress"]
ROLLUP = f"""SELECT session_id, time_bucket('5 seconds', time) AS b, count(*) AS n, round(avg(heart_rate)::numeric, 3) AS hr,
                  round(avg(stress)::numeric, 3) AS stress, max(stress) AS peak
           FROM {DEMO} GROUP BY 1, 2 ORDER BY 1, 2"""


async def create_demo(pool) -> None:
    for stmt in (f"DROP TABLE IF EXISTS {DEMO} CASCADE",
                 f"CREATE TABLE {DEMO} (LIKE vitals INCLUDING DEFAULTS)",
                 f"SELECT create_hypertable('{DEMO}', 'time')",
                 f"CREATE INDEX {DEMO}_session_time ON {DEMO} (session_id, time DESC)",
                 f"ALTER TABLE {DEMO} SET (timescaledb.enable_columnstore, timescaledb.segmentby = 'session_id', "
                 "timescaledb.orderby = 'time DESC')"):
        await pool.execute(stmt)


def synthetic_rows(n_sessions: int) -> list[tuple]:
    """n ten-minute sessions from the mock_vitals simulator, a few days back (spikes, distraction, face lost)."""
    rows, rng = [], random.Random(42)
    start = datetime.now(timezone.utc) - timedelta(days=3)
    for k in range(n_sessions):
        sid, t0 = uuid.uuid4(), start + timedelta(minutes=15 * k)
        sim = mock_vitals.VitalsSimulator(random.Random(rng.random()))
        events = {rng.randint(60, 540): e for e in ("s", "s", "s", "d", "f")}
        for i in range(600):
            if i in events:
                sim.trigger(events[i], i)
            r = sim.reading(i, t0 + timedelta(seconds=i))
            s = metrics.stress_score(VitalsReading(**r), BASE)
            stress = None if s is None else round(s, 1)
            rows.append((t0 + timedelta(seconds=i), sid, r["heart_rate"], r["breathing_rate"], r["focus"],
                         r["expression"], r["confidence"], stress))
    return rows


async def measure(pool, label: str, synthetic: list[tuple] | None) -> dict:
    await create_demo(pool)
    await pool.execute(f"INSERT INTO {DEMO} SELECT * FROM vitals")
    if synthetic:
        await pool.copy_records_to_table(DEMO, records=synthetic, columns=COLS)
    await pool.execute(f"ANALYZE {DEMO}")
    n_rows = await pool.fetchval(f"SELECT count(*) FROM {DEMO}")
    n_sessions = await pool.fetchval(f"SELECT count(DISTINCT session_id) FROM {DEMO}")
    before = await pool.fetchrow(f"SELECT * FROM hypertable_detailed_size('{DEMO}')")
    rollup_before = await pool.fetch(ROLLUP)
    chunks = [r[0] for r in await pool.fetch(f"SELECT show_chunks('{DEMO}')")]
    for c in chunks:
        await pool.execute("CALL convert_to_columnstore($1::regclass)", c)
    stats = await pool.fetchrow(
        "SELECT sum(before_compression_total_bytes) AS before, sum(after_compression_total_bytes) AS after, "
        f"sum(total_chunks) AS chunks, sum(number_compressed_chunks) AS converted FROM hypertable_columnstore_stats('{DEMO}')")
    after = await pool.fetchrow(f"SELECT * FROM hypertable_detailed_size('{DEMO}')")
    rollup_after = await pool.fetch(ROLLUP)
    same = [tuple(r) for r in rollup_before] == [tuple(r) for r in rollup_after]
    row = {"dataset": label, "sessions": n_sessions, "rows": n_rows,
           "rowstore_bytes": before["total_bytes"], "columnstore_bytes": stats["after"],
           "ratio": round(before["total_bytes"] / stats["after"], 1) if stats["after"] else None,
           "stats_before_bytes": stats["before"], "chunks_converted": f"{stats['converted']}/{stats['chunks']}",
           "on_disk_after_bytes": after["total_bytes"], "rollup_identical": same}
    print(f"{label:40s} {n_sessions:3d} sessions {n_rows:6d} rows  rowstore {before['total_bytes'] / 1024:7.0f} KiB"
          f" -> columnstore {stats['after'] / 1024:6.0f} KiB  ({row['ratio']}x)  rollup identical: {same}")
    return row


async def main(n_synth: int, keep: bool) -> None:
    pool = await db.init_pool()
    try:
        rows = [await measure(pool, "real mock sessions (from vitals)", None)]
        if n_synth:
            rows.append(await measure(pool, f"+ {n_synth} synthetic 10-min sessions", synthetic_rows(n_synth)))
    finally:
        if not keep:
            await pool.execute(f"DROP TABLE IF EXISTS {DEMO} CASCADE")
        await db.close_pool()
    with (style.SAMPLES / "compression.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    kib = lambda b: f"{b / 1024:,.0f} KiB"
    lines = ["# Columnstore compression (Phase 7)", "",
             "`vitals` has the columnstore enabled (`segmentby = session_id`, `orderby = time DESC`, see "
             "[`backend/data/columnstore.sql`](../../backend/data/columnstore.sql)) with a policy that converts chunks "
             "once they're a day old, so the chunk taking live inserts is never converted mid-hackathon. To measure the "
             f"ratio now, `scripts/viz/compression_demo.py` copies the data into `{DEMO}` (same columns, indexes and "
             "settings), converts it, and compares. Chart: [`charts/chart6_compression.png`](charts/chart6_compression.png).", "",
             "| Data | Sessions | Rows | Rowstore (table + indexes) | Columnstore | Ratio | Same query results after? |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['dataset']} | {r['sessions']} | {r['rows']:,} | {kib(r['rowstore_bytes'])} | "
                     f"{kib(r['columnstore_bytes'])} | **{r['ratio']}x** | {'yes' if r['rollup_identical'] else '**NO**'} |")
    lines += ["", "- **Rowstore** is `hypertable_detailed_size()` (heap + indexes + TOAST) before conversion; **columnstore** is "
              "`after_compression_total_bytes` from `hypertable_columnstore_stats()`.",
              "- *Same query results* = a per-session `time_bucket('5 seconds')` rollup (what `vitals_5s` computes) returns "
              "identical rows before and after conversion.",
              "- One segment per session means a replay or question-stats read decompresses only that session's batch.",
              "- Synthetic sessions come from the `mock_vitals.py` simulator (same value ranges and events) and exist only "
              "in the demo table; they show the ratio at a size where fixed per-chunk overhead no longer dominates."]
    (style.ROOT / "docs" / "data" / "compression.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote docs/data/compression.md and docs/data/samples/compression.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", type=int, default=30, help="extra simulated 10-min sessions (0 = real only)")
    ap.add_argument("--keep", action="store_true", help=f"keep {DEMO} for inspection")
    a = ap.parse_args()
    asyncio.run(main(a.synthetic, a.keep))
