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
-- Replay, reports and per-question stats read a session's questions in order.
CREATE INDEX IF NOT EXISTS questions_session_idx ON questions (session_id, idx, created_at);

CREATE TABLE IF NOT EXISTS vitals (
  time            TIMESTAMPTZ NOT NULL,
  session_id      UUID NOT NULL,
  heart_rate      REAL,
  breathing_rate  REAL,
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
       avg(stress)         AS stress,
       max(stress)         AS stress_peak
FROM vitals
GROUP BY bucket, session_id
WITH NO DATA;

ALTER MATERIALIZED VIEW vitals_5s SET (timescaledb.materialized_only = false);  -- real-time: includes fresh rows
SELECT add_continuous_aggregate_policy('vitals_5s',
  start_offset => INTERVAL '1 hour', end_offset => INTERVAL '5 seconds',
  schedule_interval => INTERVAL '30 seconds');
