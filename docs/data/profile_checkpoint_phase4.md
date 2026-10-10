# Profile: Phase 4 checkpoint session on Tiger Data (with stress)

Source: `docs/data/samples/checkpoint_phase4_vitals.csv`

## Shape and cadence

| Metric | Value |
|---|---|
| Rows | 128 |
| Time span | 132.0 s (18:09:03 to 18:11:15 UTC) |
| Expected rows at 1 Hz | 133 |
| Interval between readings (min / median / max) | 0.948 / 1.000 / 6.001 s |
| Gaps > 1.5 s (missing seconds) | 1 |
| Readings < 0.5 s apart | 0 |
| Two readings in the same wall-clock second | 0 |
| Duplicate timestamps | 0 |

## Columns

| Column | Nulls | Null % | Min | p5 | Mean | p95 | Max | Std |
|---|---|---|---|---|---|---|---|---|
| `heart_rate` | 0 | 0.0% | 70.00 | 70.13 | 73.86 | 85.32 | 89.80 | 4.59 |
| `breathing_rate` | 0 | 0.0% | 13.00 | 13.30 | 14.43 | 17.10 | 18.70 | 1.10 |
| `focus` | 0 | 0.0% | 0.25 | 0.34 | 0.74 | 0.83 | 0.85 | 0.13 |
| `confidence` | 0 | 0.0% | 0.95 | 0.95 | 0.95 | 0.95 | 0.95 | 0.00 |
| `stress` | 0 | 0.0% | 0.00 | 0.00 | 12.90 | 66.44 | 88.60 | 20.19 |
| `expression` | 0 | 0.0% | | | | | | |

## Confidence distribution

| Confidence | Rows | % | Used in stress math (>= 0.7) |
|---|---|---|---|
| 0.95 | 128 | 100.0% | yes |

Rows below the 0.7 confidence cutoff (never stored by the backend): **0**

## Expression distribution

| Expression | Rows |
|---|---|
| neutral | 109 |
| frown | 19 |

## Events visible in the stream

| Pattern | Runs (seconds from start) |
|---|---|
| Face lost (confidence < 0.7) | none |
| Frown (stress spike) | t=61 s for 19 rows |
| Focus < 0.4 with a face (distraction) | t=94 s for 8 rows |
| Stress > 70 | t=64 s for 6 rows |
| Stress null while confidence >= 0.7 | 0 rows |
