#!/usr/bin/env python
"""Apply backend/data/schema.sql to Tiger Data, then verify it.

    python scripts/init_db.py            # create anything missing (safe to re-run)
    python scripts/init_db.py --reset    # DROP sessions, questions, vitals, vitals_5s and recreate
    python scripts/init_db.py --fresh --columnstore   # the same with no prompt: scripts/run_demo.py runs this
                                                      # on every launch, so each run starts with empty tables
    python scripts/init_db.py --check    # only verify: hypertable, continuous aggregate, policy
    python scripts/init_db.py --columnstore   # also enable the columnstore on vitals (columnstore.sql)

Reads TIGER_DATABASE_URL from the environment or backend/.env. Statements run one
at a time outside any transaction block, because continuous-aggregate DDL
(CREATE MATERIALIZED VIEW ... WITH (timescaledb.continuous)) can't run inside one.
"""
import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.data.db import database_url  # noqa: E402

SCHEMA = ROOT / "backend" / "data" / "schema.sql"
COLUMNSTORE = ROOT / "backend" / "data" / "columnstore.sql"
RESET_SQL = [
    "DROP MATERIALIZED VIEW IF EXISTS vitals_5s CASCADE",   # also removes its refresh policy
    "DROP TABLE IF EXISTS vitals CASCADE",
    "DROP TABLE IF EXISTS questions CASCADE",
    "DROP TABLE IF EXISTS sessions CASCADE",
]
CONFIRM_WORD = "RESET"


def split_sql(sql: str) -> list[str]:
    """Split on ';' outside quotes, dollar-quotes and -- comments. Comments are dropped."""
    stmts, buf, i, n = [], [], 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            i = sql.find("\n", i)
            i = n if i == -1 else i
            continue
        if c == "'":
            j = i + 1
            while j < n and not (sql[j] == "'" and not sql.startswith("''", j)):
                j += 2 if sql.startswith("''", j) else 1
            buf.append(sql[i:j + 1])
            i = j + 1
            continue
        if c == "$":
            end = sql.find("$", i + 1)
            tag = sql[i:end + 1] if end != -1 else ""
            if tag and (tag == "$$" or tag[1:-1].replace("_", "").isalnum()):
                close = sql.find(tag, end + 1)
                close = n if close == -1 else close + len(tag)
                buf.append(sql[i:close])
                i = close
                continue
        if c == ";":
            stmts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
        i += 1
    stmts.append("".join(buf).strip())
    return [s for s in stmts if s]


def short(stmt: str) -> str:
    return " ".join(stmt.split())[:90]


def redact(msg: str, url: str) -> str:
    return msg.replace(url, "<TIGER_DATABASE_URL>")


async def apply_schema(conn: asyncpg.Connection, path=SCHEMA) -> None:
    for stmt in split_sql(path.read_text(encoding="utf-8")):
        try:
            await conn.execute(stmt)
            print(f"  ok       {short(stmt)}")
        except (asyncpg.DuplicateObjectError, asyncpg.InvalidParameterValueError) as exc:
            # add_continuous_aggregate_policy has no IF NOT EXISTS in schema.sql. TimescaleDB 2.30
            # raises InvalidParameterValue "refresh policy already exists"; anything else is real.
            if "already exists" not in str(exc):
                raise
            print(f"  exists   {short(stmt)}")


CHECKS = [
    ("timescaledb extension",
     "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"),
    ("vitals is a hypertable",
     "SELECT format('%s chunk(s), dimension on time', num_chunks) FROM timescaledb_information.hypertables "
     "WHERE hypertable_name = 'vitals'"),
    ("index vitals_session_time",
     "SELECT indexdef FROM pg_indexes WHERE tablename = 'vitals' AND indexname = 'vitals_session_time'"),
    ("index questions_session_idx",
     "SELECT indexdef FROM pg_indexes WHERE tablename = 'questions' AND indexname = 'questions_session_idx'"),
    ("no focus column (removed)",
     "SELECT 'ok' WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
     "AND column_name IN ('focus', 'baseline_focus'))"),
    ("vitals_5s continuous aggregate",
     "SELECT format('materialized_only=%s', materialized_only) FROM timescaledb_information.continuous_aggregates "
     "WHERE view_name = 'vitals_5s'"),
    ("vitals_5s refresh policy",
     "SELECT format('job %s every %s, %s', j.job_id, j.schedule_interval, j.config) "
     "FROM timescaledb_information.jobs j "
     "JOIN timescaledb_information.continuous_aggregates c ON c.view_name = 'vitals_5s' "
     "WHERE j.proc_name = 'policy_refresh_continuous_aggregate' "
     # 2.30 reports the cagg's view name here; older versions the materialization hypertable
     "  AND j.hypertable_name IN (c.view_name, c.materialization_hypertable_name)"),
    ("tables sessions, questions",
     "SELECT string_agg(table_name, ', ' ORDER BY table_name) FROM information_schema.tables "
     "WHERE table_schema = 'public' AND table_name IN ('sessions', 'questions') HAVING count(*) = 2"),
]


COLUMNSTORE_CHECKS = [
    ("vitals columnstore enabled",
     "SELECT format('segmentby %s, orderby %s', segmentby, orderby) "
     "FROM timescaledb_information.hypertable_columnstore_settings WHERE hypertable = 'vitals'::regclass"),
    ("vitals columnstore policy",
     "SELECT format('job %s every %s, %s', job_id, schedule_interval, config) FROM timescaledb_information.jobs "
     "WHERE hypertable_name = 'vitals' AND proc_name IN ('policy_compression', 'policy_columnstore')"),
]


async def verify(conn: asyncpg.Connection, columnstore: bool = False) -> bool:
    ok = True
    for label, sql in CHECKS + (COLUMNSTORE_CHECKS if columnstore else []):
        value = await conn.fetchval(sql)
        ok &= value is not None
        print(f"  {'PASS' if value is not None else 'FAIL'}  {label:32s} {value or ''}")
    return ok


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--reset", action="store_true", help="drop everything and recreate (asks for confirmation)")
    g.add_argument("--fresh", action="store_true",
                   help="drop everything and recreate WITHOUT asking (the launcher's wipe on every run)")
    g.add_argument("--check", action="store_true", help="only run the verification checks")
    ap.add_argument("--columnstore", action="store_true",
                    help="also apply columnstore.sql (columnstore on vitals + conversion policy)")
    args = ap.parse_args()

    url = database_url()
    try:
        conn = await asyncpg.connect(url, timeout=15)
    except Exception as exc:
        print(f"could not connect: {exc.__class__.__name__}: {redact(str(exc), url)}", file=sys.stderr)
        return 1
    try:
        if args.reset:
            print("--reset DROPS sessions, questions, vitals and vitals_5s (all data) "
                  "in the database TIGER_DATABASE_URL points to.")
            if input(f"Type {CONFIRM_WORD} to continue: ").strip() != CONFIRM_WORD:
                print("aborted, nothing dropped")
                return 1
        if args.reset or args.fresh:
            for stmt in RESET_SQL:
                await conn.execute(stmt)
                print(f"  dropped  {short(stmt)}")
        if not args.check:
            print(f"applying {SCHEMA.relative_to(ROOT)}")
            await apply_schema(conn)
            if args.columnstore:
                print(f"applying {COLUMNSTORE.relative_to(ROOT)}")
                await apply_schema(conn, COLUMNSTORE)
        print("verifying")
        return 0 if await verify(conn, args.columnstore) else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
