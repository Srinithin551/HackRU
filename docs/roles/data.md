# Role: Data

> **At submission (HackRU 2026):** this is the plan written at the start, when the app was called StressTest.
> As built: there is **no focus vital** (Presage never measures it), so the stress score is 5/8 heart rate +
> 3/8 breathing and the schema has no focus columns. The launcher wipes and recreates the tables on every run
> (`init_db.py --fresh --columnstore`). See [docs/data/README.md](../data/README.md) and the [README](../../README.md).

**Owner:** Likhil
**You own:** Tiger Data, the schema, the stress score, every query Brain uses, the replay endpoint, and the mock vitals generator.
**Files:** `backend/data/*`, `scripts/mock_vitals.py`
**Sponsor prize you carry:** Tiger Data

You sit in the middle. Sensing writes into your tables, Brain reads out of them every few seconds, Frontend draws your replay data. Read [../PLAN.md](../PLAN.md) first; your function signatures there are a promise to the team.

---

## What you consume and what you provide

| You consume | From |
|---|---|
| `VitalsReading` batches via `insert_vitals()` | Sensing's `/api/vitals` route |
| Session/question lifecycle calls | Brain |

| You provide | To |
|---|---|
| All functions in `data/queries.py` (signatures in PLAN.md section 3) | Brain, Sensing |
| `metrics.py` stress score | Everyone |
| `GET /api/sessions/active`, `GET /api/sessions/{id}/replay` | Sensing client, Frontend |
| `scripts/mock_vitals.py` | Everyone |

---

## Hour 1 (people are waiting on this)

1. Create a Tiger Data cloud service (free tier, use the MLH link from the prizes page). Put the connection string in the group chat for `TIGER_DATABASE_URL`.
2. Push **stubbed** `backend/data/queries.py`: every function from PLAN.md with the exact signature, returning fake but correctly shaped values (e.g. `get_window_stats` returns a `WindowStats` with stress = random 20 to 60). Brain codes against this immediately.
3. Then build the real thing behind the same signatures.

## Hour 1 to 2: `scripts/mock_vitals.py`

Posts one reading per second to `POST /api/vitals` (no session_id, so the backend uses the active session). Realistic behavior:

- Baseline: HR 72 ± 2 noise, breathing 14 ± 1, focus 0.8 ± 0.05, expression `neutral`, confidence 0.95.
- Press `s`: stress spike. HR ramps to +25% over 6 seconds, breathing +30%, focus drops to 0.5, expression `frown`, then decays back over 20 seconds.
- Press `d`: distraction. Focus drops to 0.3 for 8 seconds, HR unchanged.
- Press `f`: face lost. 5 seconds of nulls with confidence 0.
- `--auto` flag: spikes automatically about 10 seconds into every other question (poll `/api/sessions/active`).

This is the most-used tool on the team until Presage is live. Make it solid.

---

## Schema (`backend/data/schema.sql`)

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS sessions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_name       TEXT,
  interview_type  TEXT,
  role_title      TEXT,
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at        TIMESTAMPTZ,
  baseline_hr     REAL,
  baseline_br     REAL,
  baseline_focus  REAL,
  baseline_n      INT,
  report          JSONB
);

CREATE TABLE IF NOT EXISTS questions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id       UUID NOT NULL REFERENCES sessions(id),
  idx              INT NOT NULL,
  kind             TEXT NOT NULL,          -- calibration | main | follow_up
  text             TEXT NOT NULL,
  transcript       TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  answer_started_at TIMESTAMPTZ,
  answered_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS vitals (
  time            TIMESTAMPTZ NOT NULL,
  session_id      UUID NOT NULL,
  heart_rate      REAL,
  breathing_rate  REAL,
  focus           REAL,
  expression      TEXT,
  confidence      REAL,
  stress          REAL                     -- computed on insert, null before baseline exists
);
SELECT create_hypertable('vitals', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS vitals_session_time ON vitals (session_id, time DESC);

-- 5-second rollups for the replay chart
CREATE MATERIALIZED VIEW IF NOT EXISTS vitals_5s
WITH (timescaledb.continuous) AS
SELECT time_bucket('5 seconds', time) AS bucket,
       session_id,
       avg(heart_rate)     AS hr,
       avg(breathing_rate) AS br,
       avg(focus)          AS focus,
       avg(stress)         AS stress,
       max(stress)         AS stress_peak
FROM vitals
GROUP BY bucket, session_id
WITH NO DATA;

ALTER MATERIALIZED VIEW vitals_5s SET (timescaledb.materialized_only = false);  -- real-time: includes fresh rows
SELECT add_continuous_aggregate_policy('vitals_5s',
  start_offset => INTERVAL '1 hour', end_offset => INTERVAL '5 seconds',
  schedule_interval => INTERVAL '30 seconds');
```

Stretch (good for the Tiger Data judges): enable compression / columnstore on `vitals` with `segmentby = session_id` and show the compression ratio after loading a few sessions. Syntax has changed across versions, so check the current Tiger Data docs.

Run `schema.sql` with a tiny `scripts/init_db.py` so anyone can reset the DB.

## Driver (`backend/data/db.py`)

`asyncpg` connection pool created on FastAPI startup (`min_size=2, max_size=10`). Every query function takes from the pool. Target **under 50 ms per query** since Brain calls you every 3 seconds plus after every answer.

## Stress score (`backend/data/metrics.py`)

Formula is in PLAN.md section 3. Implement it as a pure function:

```python
def stress_score(r: VitalsReading, b: Baseline | None) -> float | None
```

Return `None` if there's no baseline yet or `confidence < 0.5`. Write 5 quick asserts (calm reading ≈ 0, +25% HR ≈ 50, all maxed = 100, missing focus renormalizes, low confidence = None).

Keep the latest baseline per session in an in-memory dict so `insert_vitals` doesn't hit the DB for it every second.

---

## Query functions (`backend/data/queries.py`)

Notes on the ones that matter most:

**`insert_vitals(readings)`**: resolve `session_id` (use active session if missing, drop readings if no active session), compute `stress` per row, bulk insert with `executemany` or `copy_records_to_table`, return the rows as dicts so the ingest route can broadcast them.

**`compute_baseline(session_id)`**: average of readings with `confidence >= 0.5` from session start until now (the calibration answer). Skip the first 10 seconds (Presage warm-up). Require at least 10 samples; if fewer, fall back to population defaults (HR 75, BR 15, focus 0.75) and log a warning. Store on `sessions`, update the in-memory cache. Then **backfill** `stress` for the calibration rows so the chart isn't empty at the start.

**`get_window_stats(session_id, seconds)`**:

```sql
SELECT count(*), avg(heart_rate), avg(breathing_rate), avg(focus), avg(stress),
       mode() WITHIN GROUP (ORDER BY expression)
FROM vitals
WHERE session_id = $1 AND time > now() - make_interval(secs => $2) AND confidence >= 0.5;
```

Compute `hr_delta_pct` and `br_delta_pct` against the cached baseline in Python.

**`get_question_stats(question_id)`**: window is `answer_started_at` to `answered_at` (fall back to `created_at` if not started):

```sql
SELECT count(*), avg(v.heart_rate), max(v.heart_rate), avg(v.breathing_rate), avg(v.focus),
       avg(v.stress), max(v.stress),
       EXTRACT(EPOCH FROM ((array_agg(v.time ORDER BY v.stress DESC NULLS LAST))[1] - q.answer_started_at))
FROM questions q
JOIN vitals v ON v.session_id = q.session_id
 AND v.time BETWEEN COALESCE(q.answer_started_at, q.created_at) AND COALESCE(q.answered_at, now())
WHERE q.id = $1 AND v.confidence >= 0.5
GROUP BY q.answer_started_at;
```

This time-range join between events and a hypertable is exactly the Tiger Data story. Show it to the judges.

**`get_session_history(session_id)`**: list of `{idx, kind, text, transcript, stats: QuestionStats}` in order. Brain feeds this to Gemini for the report.

**`get_active_session()`**: most recent session with `ended_at IS NULL` started in the last 30 minutes. Cache for 2 seconds.

---

## Replay endpoint (`backend/data/routes.py`)

`GET /api/sessions/{id}/replay` returns:

```json
{
  "session": { "id": "...", "user_name": "...", "started_at": "...", "ended_at": "...",
               "baseline": { "heart_rate": 72.1, "breathing_rate": 14.2, "focus": 0.81 } },
  "series": [
    { "t": "2026-10-10T22:01:05Z", "hr": 74.2, "br": 14.0, "focus": 0.79, "stress": 12.5, "stress_peak": 18.0 }
  ],
  "questions": [
    { "id": "...", "idx": 1, "kind": "main", "text": "...", "transcript": "...",
      "answer_started_at": "...", "answered_at": "...",
      "stats": { "...QuestionStats fields..." } }
  ]
}
```

`series` comes from `vitals_5s`, ordered by bucket. Agree on this exact shape with Frontend by 3 PM and put a sample in `frontend/src/mocks/replay_sample.json`.

---

## Your checklist

**Sat 1 to 2 PM**
- [ ] Tiger Data service created, URL shared
- [ ] Stubbed `queries.py` pushed

**2 to 6 PM**
- [ ] `mock_vitals.py` with `s`, `d`, `f` keys and `--auto`
- [ ] `schema.sql` + `init_db.py`
- [ ] Real `insert_vitals`, `compute_baseline`, `get_window_stats`, `metrics.py` with asserts
- [ ] `get_active_session` route

**6 PM: Integration 1** (your queries must be real by now)

**6 PM to 12 AM**
- [ ] `get_question_stats`, `get_session_history`, report save/load
- [ ] Replay endpoint + sample JSON for Frontend
- [ ] Tune stress constants with Sensing using real Presage data
- [ ] Check every query is under 50 ms on a session with 10 minutes of data

**12 AM: Integration 2**

**After**
- [ ] Compression / columnstore stretch
- [ ] "Across all sessions, which question type spikes stress most" query for a demo slide
- [ ] Write the Tiger Data section of the Devpost (hypertable, continuous aggregate, time-range join)

## Done means

Brain gets a correct `WindowStats` in under 50 ms every 3 seconds, the stress number visibly jumps when someone is actually stressed, and the replay chart loads the full session from the continuous aggregate.
