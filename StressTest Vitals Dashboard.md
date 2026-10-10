# StressTest vitals on Tiger Data

Webcam vitals stored once a second in a TimescaleDB hypertable, scored against each person's calm baseline, joined to interview questions by time range, and rolled into 5-second buckets for the replay. All numbers below are measured on our Tiger Data service. The sessions are mock sessions run through the real interview loop.

Sessions on Tiger Data

5

1,110 vitals readings at 1 Hz

get_window_stats on Tiger

0.07 ms

server time; 53 ms round trip (network floor 53 ms)

Columnstore

8.1x smaller

19,110 readings, segmentby session_id

Replay below

peak 87

7 questions, 60 points from vitals_5s

## One session, replayed from the continuous aggregate

Demo Candidate, 7 questions. Stress is 0 to 100 against the calibration baseline (under 40 calm, 40 to 70 rising, over 70 high). Shaded bands are answer windows; + marks a follow-up. Hover for values.

stress, mean per 5 s stress, peak per 5 sheart rate on its own axis below

## How they reacted to each question

QuestionStats from the time-range join between each answer window and the vitals hypertable.

| # | Kind | Question | Answer | HR mean | HR vs base | Stress mean | Stress peak | Level | Peak at |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Query latency

20 runs each on a session with 10 minutes of data. Every query executes in under a millisecond on Tiger; the round trip is the network.

p50p95

## Columnstore compression

vitals with segmentby session_id, orderby time DESC, measured on a copy so the live chunk is untouched. Query results are identical after conversion.

## Which questions spike stress most

Average answer peak across 43 answers in 4 interview types. Mock data: mock_vitals --auto spikes every other question, so this pattern is by construction until real sessions come in.

Stored in Tiger Data (PostgreSQL 18.6, TimescaleDB 2.30.2): hypertable vitals, continuous aggregate vitals_5s, columnstore. Built by scripts/viz/build_dashboard.py from frontend/src/mocks/replay_sample.json and docs/data/samples/.