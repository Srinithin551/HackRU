# Tiger Data in Osiris

*Draft for the Devpost "How we built it" section. Numbers are measured on our Tiger Data service (PostgreSQL 18.6, TimescaleDB 2.30.2).*

Osiris reads your heart rate and breathing from your webcam once per second while an AI interviewer (Judge) questions you, then shows exactly which question made your body react. That makes it a time-series problem from start to finish. Every reading, question and replay chart lives in **Tiger Data**, and four TimescaleDB features carry it.

### 1. A hypertable for the 1 Hz vitals stream

Every reading lands in `vitals`, a hypertable partitioned on time and indexed on `(session_id, time DESC)`:

```sql
SELECT create_hypertable('vitals', 'time', if_not_exists => TRUE);
CREATE INDEX vitals_session_time ON vitals (session_id, time DESC);
```

Each reading is scored on the way in: a 0 to 100 stress number relative to *your own* calm baseline from a short warm-up chat. While you answer, the interviewer asks "how are they right now?" every 3 seconds:

```sql
SELECT count(*), avg(heart_rate), avg(breathing_rate), avg(stress),
       mode() WITHIN GROUP (ORDER BY expression)
FROM vitals
WHERE session_id = $1 AND time > now() - make_interval(secs => $2) AND confidence >= 0.4;
```

TimescaleDB skips every chunk outside the window, and the index finds the session's last few seconds: **0.07 ms** of execution on Tiger. When stress crosses 70, the interviewer eases off. When you're calm, it pushes harder.

### 2. A time-range join: "how did they react to *that* question?"

Questions are events with a start and an end. Vitals are a stream. Joining the two by time range is the core of the product:

```sql
SELECT avg(v.stress), max(v.stress), avg(v.heart_rate), max(v.heart_rate),
       (array_agg(v.time ORDER BY v.stress DESC NULLS LAST))[1] AS peak_time
FROM questions q
JOIN vitals v ON v.session_id = q.session_id
 AND v.time BETWEEN COALESCE(q.answer_started_at, q.created_at) AND COALESCE(q.answered_at, now())
WHERE q.id = $1 AND v.confidence >= 0.7;
```

This runs after every answer (**0.17 ms**) and feeds Gemini's next move ("heart rate up 22% eight seconds into the answer"). Across all sessions, the same join powers our "which question type spikes stress most" insight in **0.5 ms**.

### 3. A continuous aggregate for the replay chart

The replay timeline reads 5-second rollups, not raw rows:

```sql
CREATE MATERIALIZED VIEW vitals_5s WITH (timescaledb.continuous) AS
SELECT time_bucket('5 seconds', time) AS bucket, session_id,
       avg(heart_rate) AS hr, avg(breathing_rate) AS br,
       avg(stress) AS stress, max(stress) AS stress_peak
FROM vitals GROUP BY bucket, session_id WITH NO DATA;
ALTER MATERIALIZED VIEW vitals_5s SET (timescaledb.materialized_only = false);   -- real-time
SELECT add_continuous_aggregate_policy('vitals_5s', start_offset => INTERVAL '1 hour',
       end_offset => INTERVAL '5 seconds', schedule_interval => INTERVAL '30 seconds');
```

It's real-time, so the last few seconds are included before the next refresh. When a session ends we call `refresh_continuous_aggregate` for that session's window, so the replay is exact the moment the interview finishes. A 5-minute interview becomes 60 points (**0.08 ms**), and we verified the aggregate matches a rollup of the raw rows exactly.

### 4. Columnstore for finished sessions

Finished sessions move to the columnstore, one segment per session:

```sql
ALTER TABLE vitals SET (timescaledb.enable_columnstore,
  timescaledb.segmentby = 'session_id', timescaledb.orderby = 'time DESC');
CALL add_columnstore_policy('vitals', after => INTERVAL '1 day');
```

At 35 ten-minute sessions (18,955 readings) the columnstore is **9.2x smaller** than the rowstore (4,208 KiB → 456 KiB), and our per-session rollups return identical results. On just our first 5 sessions (1,105 readings) it's 2.8x, because fixed per-chunk overhead dominates at that size. Replaying one session decompresses only that session's segment.

### What it bought us

- **One database, no glue.** Sessions, questions, transcripts, reports and the vitals stream live together, so "stress during question 3" is one SQL join, not a pipeline.
- **Fast enough to steer a live conversation.** Every query executes in under a millisecond, so the round trip is just the network (about 53 ms from our laptop) and the interviewer reacts within one 3-second tick.
- **The replay chart is one query** against a continuous aggregate, not a scan of every raw reading.
