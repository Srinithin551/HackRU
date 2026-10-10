# Columnstore compression (Phase 7)

`vitals` has the columnstore enabled (`segmentby = session_id`, `orderby = time DESC`, see [`backend/data/columnstore.sql`](../../backend/data/columnstore.sql)) with a policy that converts chunks once they're a day old, so the chunk taking live inserts is never converted mid-hackathon. To measure the ratio now, `scripts/viz/compression_demo.py` copies the data into `vitals_columnstore_demo` (same columns, indexes and settings), converts it, and compares. Chart: [`charts/chart6_compression.png`](charts/chart6_compression.png).

| Data | Sessions | Rows | Rowstore (table + indexes) | Columnstore | Ratio | Same query results after? |
|---|---|---|---|---|---|---|
| real mock sessions (from vitals) | 5 | 1,105 | 296 KiB | 104 KiB | **2.8x** | yes |
| + 30 synthetic 10-min sessions | 35 | 18,955 | 4,208 KiB | 456 KiB | **9.2x** | yes |

- **Rowstore** is `hypertable_detailed_size()` (heap + indexes + TOAST) before conversion; **columnstore** is `after_compression_total_bytes` from `hypertable_columnstore_stats()`.
- *Same query results* = a per-session `time_bucket('5 seconds')` rollup (what `vitals_5s` computes) returns identical rows before and after conversion.
- One segment per session means a replay or question-stats read decompresses only that session's batch.
- Synthetic sessions come from the `mock_vitals.py` simulator (same value ranges and events) and exist only in the demo table; they show the ratio at a size where fixed per-chunk overhead no longer dominates.
