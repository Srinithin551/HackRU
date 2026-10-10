-- Columnstore (hypercore) for vitals: closed chunks are stored column-by-column, compressed,
-- one segment per session, newest first. Syntax for TimescaleDB 2.18+ (verified on 2.30.2).
-- Applied by: python scripts/init_db.py --columnstore   (kept out of schema.sql, which mirrors data.md)

ALTER TABLE vitals SET (
  timescaledb.enable_columnstore,
  timescaledb.segmentby = 'session_id',
  timescaledb.orderby = 'time DESC');

-- Convert a chunk once its whole time range is more than a day old. The chunk taking live
-- inserts (and compute_baseline's stress backfill) is never converted while it's in use.
CALL add_columnstore_policy('vitals', after => INTERVAL '1 day', if_not_exists => true);
