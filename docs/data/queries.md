# Query review: `backend/data/queries.py`

Every query Brain, Sensing and the replay page depend on, reviewed for PostgreSQL + TimescaleDB:
what it does, which index the plan uses, and why it's shaped the way it is. Plans are from
`EXPLAIN (ANALYZE)` on Tiger Data (PostgreSQL 18.6, TimescaleDB 2.30.2). Latency is in
[Phase 6](#latency) below.

The one index that matters is **`vitals_session_time (session_id, time DESC)`**. Every vitals read
filters on `session_id =` plus a time range, so it's an index range scan inside the one or two
7-day chunks that overlap the range. TimescaleDB excludes all other chunks.

## Hot path (Brain's watcher every 3 s, and after every answer)

### `get_window_stats(session_id, seconds=15)`: "how is the person right now"

```sql
SELECT count(*) AS n, avg(heart_rate) AS hr, avg(breathing_rate) AS br, avg(focus) AS focus,
       avg(stress) AS stress, mode() WITHIN GROUP (ORDER BY expression) AS expression
FROM vitals
WHERE session_id = $1 AND time > now() - make_interval(secs => $2) AND confidence >= 0.7
```

- **Plan:** `ChunkAppend` on the newest chunk, `vitals_session_time` index scan with `session_id` and `time` in the index condition. `confidence >= 0.7` is a cheap filter (rows below 0.7 aren't stored anyway; it keeps the rule explicit). (On a near-empty table the planner picks a seq scan of the 2-page chunk instead, which is also correct.)
- `hr_delta_pct` / `br_delta_pct` are computed in Python against the **in-memory baseline cache**, so this stays one round trip.
- `mode()` ignores NULL expressions; a window with no face returns `n = 0` and all-null stats.

### `get_question_stats(question_id)`: the time-range join (Tiger Data story)

```sql
SELECT q.*, COALESCE(q.answer_started_at, q.created_at) AS t_start,
       COALESCE(q.answered_at, now()) AS t_end, st.*
FROM questions q
LEFT JOIN LATERAL (
    SELECT count(*) AS n, avg(v.heart_rate) AS hr_mean, max(v.heart_rate) AS hr_peak,
           avg(v.breathing_rate) AS br_mean, avg(v.focus) AS focus_mean,
           avg(v.stress) AS stress_mean, max(v.stress) AS stress_peak,
           (array_agg(v.time ORDER BY v.stress DESC NULLS LAST))[1] AS peak_time
    FROM vitals v
    WHERE v.session_id = q.session_id
      AND v.time BETWEEN COALESCE(q.answer_started_at, q.created_at) AND COALESCE(q.answered_at, now())
      AND v.confidence >= 0.7
) st ON true
WHERE q.id = $1
```

- **Plan:** `questions_pkey` lookup, then a **nested loop into `vitals_session_time`** with `session_id = q.session_id AND time >= … AND time <= …` all in the index condition. Chunks are excluded at runtime from the question's own timestamps.
- **Change from data.md's version:** same join, written as `LEFT JOIN LATERAL` instead of `JOIN … GROUP BY`, so a question with no usable vitals (face lost the whole answer) still returns one row with `n = 0` instead of nothing. `peak_offset_s` is measured from the same `t_start` the window uses, so it's defined even if `answer_started_at` never arrived.
- `duration_s`, `hr_delta_pct`, `peak_offset_s` are computed in Python. An unknown id returns empty stats (never raises), so Brain's loop can't crash on it.

### `get_session_history(session_id)`

Same SQL with `WHERE q.session_id = $1 ORDER BY q.idx, q.created_at`: **one query for all questions** (not one per question). Items are `{question_id, idx, kind, text, transcript, stats}`.

## Write path (Sensing, 1 reading per second)

### `insert_vitals(readings)`

```sql
INSERT INTO vitals (time, session_id, heart_rate, breathing_rate, focus, expression, confidence, stress)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)          -- executemany: one prepared statement, one round trip
```

- Readings with `confidence < 0.7` are skipped (never stored) but still returned so the ingest route can broadcast "no face".
- Stress is computed in Python with the cached baseline before the insert. No DB read per reading (the baseline cache misses at most once per 10 s until calibration ends).
- `executemany` sends the whole batch (1 row normally, up to 30 after a sensing-client outage) in one round trip.
- Readings without a `session_id` go to the active session (cached 2 s). With no active session they're dropped.

### `compute_baseline(session_id)` (once per session, after calibration)

```sql
SELECT count(*) AS n, avg(heart_rate), avg(breathing_rate), avg(focus)
FROM vitals
WHERE session_id = $1 AND confidence >= 0.7 AND time <= now()
  AND time >= (SELECT greatest(s.started_at, (SELECT min(time) FROM vitals WHERE session_id = $1))
               FROM sessions s WHERE s.id = $1) + make_interval(secs => $2)   -- skip 10 s warm-up
```

Then `UPDATE sessions SET baseline_* …` and the **stress backfill**: the calibration rows' stress is computed in Python (one formula, `metrics.py`) and written in one statement:

```sql
UPDATE vitals v SET stress = u.stress
FROM unnest($2::timestamptz[], $3::real[]) AS u(time, stress)
WHERE v.session_id = $1 AND v.time = u.time AND v.stress IS NULL
```

The warm-up skip starts from whichever is later, session creation or the first reading, so a slow sensing start doesn't eat the calibration.

## Session lifecycle

| Function | SQL | Notes |
|---|---|---|
| `create_session` | `INSERT INTO sessions (…) RETURNING id` | Primes the 2 s active-session cache |
| `get_active_session` | `SELECT id FROM sessions WHERE ended_at IS NULL AND started_at > now() - interval '30 minutes' ORDER BY started_at DESC LIMIT 1` | Cached 2 s. `sessions` is tiny (one row per interview), so no extra index |
| `end_session` | `UPDATE sessions SET ended_at = now() … RETURNING started_at, ended_at`, then `CALL refresh_continuous_aggregate('vitals_5s', start, end)` | The refresh materializes the whole session so the replay is exact immediately (~200 ms, once per session) |
| `insert_question` / `mark_answer_started` / `mark_answered` | single-row `INSERT` / `UPDATE … WHERE id = $1` | Primary-key lookups |
| `save_report` / `get_report` | `UPDATE sessions SET report = $2` / `SELECT report` | JSONB ↔ dict codec on the pool |

## Replay (`GET /api/sessions/{id}/replay`)

```sql
SELECT bucket, hr, br, focus, stress, stress_peak FROM vitals_5s WHERE session_id = $1 ORDER BY bucket
```

- **Plan:** real-time continuous aggregate: the materialized part (TimescaleDB's automatic `(session_id, bucket DESC)` index on the materialization hypertable) `UNION ALL` a live `time_bucket` aggregation of raw rows newer than the watermark.
- The session row, the series and the question stats run **in parallel** (`asyncio.gather` on the pool): three queries, one round trip of wall time.

## Insight: which question type spikes stress most (`get_stress_by_question_type`)

The same time-range join, over every answered question, grouped by question kind and interview type
(`GROUPING SETS ((kind), (interview_type), (interview_type, kind))`). It counts answers whose peak went over 70.
**Plan:** one `vitals_session_time` index scan per question (nested loop). **0.5 ms** for 43 answers across 5 sessions.
Not a Brain contract function; it feeds the demo-slide chart and the dashboard.

## Latency

Measured on a session with 10 minutes of data, 20 runs each (details and every function: [latency.md](latency.md), chart: [chart4](charts/chart4_query_latency.png)):

| Query | Server execution | Round trip p50 / p95 from the dev laptop |
|---|---|---|
| `get_window_stats` | 0.07 ms | 52.6 / 54.8 ms |
| `get_question_stats` | 0.17 ms | 52.4 / 54.2 ms |
| `insert_vitals` | 0.14 ms | 53.8 / 79.1 ms |
| replay series (`vitals_5s`) | 0.08 ms | 54.2 / 79.8 ms (whole endpoint) |
| network floor `SELECT 1` | | 53.3 / 66.1 ms |

At 10 minutes of data the `get_window_stats` plan is an index scan on `vitals_session_time` with both conditions in the index. For `get_question_stats` the planner sometimes prefers TimescaleDB's default `vitals_time_idx (time DESC)` plus a session filter: with one interview at a time that reads the same ~60 rows, both in under 0.2 ms.
