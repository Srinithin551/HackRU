# Profile: mock_vitals.py stream, 180 s real time

Source: `docs/data/samples/mock_stream_180s.csv`

## Shape and cadence

| Metric | Value |
|---|---|
| Rows | 180 |
| Time span | 179.0 s (17:38:38 to 17:41:37 UTC) |
| Expected rows at 1 Hz | 180 |
| Interval between readings (min / median / max) | 0.891 / 1.000 / 1.109 s |
| Gaps > 1.5 s (missing seconds) | 0 |
| Readings < 0.5 s apart | 0 |
| Two readings in the same wall-clock second | 0 |
| Duplicate timestamps | 0 |

## Columns

| Column | Nulls | Null % | Min | p5 | Mean | p95 | Max | Std |
|---|---|---|---|---|---|---|---|---|
| `heart_rate` | 5 | 2.8% | 70.00 | 70.74 | 74.66 | 86.44 | 90.70 | 5.09 |
| `breathing_rate` | 5 | 2.8% | 13.00 | 13.20 | 14.60 | 17.60 | 18.80 | 1.28 |
| `focus` | 5 | 2.8% | 0.29 | 0.49 | 0.74 | 0.84 | 0.85 | 0.13 |
| `confidence` | 0 | 0.0% | 0.00 | 0.95 | 0.92 | 0.95 | 0.95 | 0.16 |
| `expression` | 5 | 2.8% | | | | | | |

## Confidence distribution

| Confidence | Rows | % | Used in stress math (>= 0.7) |
|---|---|---|---|
| 0.00 | 5 | 2.8% | no |
| 0.95 | 175 | 97.2% | yes |

Rows below the 0.7 confidence cutoff (never stored by the backend): **5**

## Expression distribution

| Expression | Rows |
|---|---|
| neutral | 137 |
| frown | 38 |
| (null) | 5 |

## Events visible in the stream

| Pattern | Runs (seconds from start) |
|---|---|
| Face lost (confidence < 0.7) | t=100 s for 5 rows |
| Frown (stress spike) | t=22 s for 19 rows, t=142 s for 19 rows |
| Focus < 0.4 with a face (distraction) | t=60 s for 8 rows |
