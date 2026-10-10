"""asyncpg connection pool for Tiger Data (Postgres + TimescaleDB).

main.py's lifespan calls startup() / shutdown(), so the pool (min_size=2,
max_size=10) is created once when FastAPI starts. Every query function takes
connections from it. Scripts can call init_pool() / close_pool() directly.

TIGER_DATABASE_URL comes from backend/config.py (.env at the repo root or in
backend/). Never print or log it.
"""
import asyncio
import json
import logging
import os
import sys

import asyncpg

from backend import config

log = logging.getLogger("data.db")

_pool: asyncpg.Pool | None = None
_lock = asyncio.Lock()


def database_url() -> str:
    url = config.TIGER_DATABASE_URL or os.getenv("TIGER_DATABASE_URL", "")
    if not url:
        raise RuntimeError("TIGER_DATABASE_URL is not set (put it in .env at the repo root or backend/.env)")
    return url


def use_fake() -> bool:
    """True when Data should run in memory (fake_queries.py) instead of Tiger Data.

    DATA_FAKE=1 forces it and DATA_FAKE=0 forbids it. Otherwise it's on under pytest, so
    tests never write into the shared database, and when no TIGER_DATABASE_URL is set.
    """
    flag = os.getenv("DATA_FAKE", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return "pytest" in sys.modules or not (config.TIGER_DATABASE_URL or os.getenv("TIGER_DATABASE_URL"))


async def _init_connection(conn: asyncpg.Connection) -> None:
    # jsonb <-> dict, so sessions.report round-trips as a Python dict
    await conn.set_type_codec("jsonb", schema="pg_catalog", decoder=json.loads,
                              encoder=lambda v: json.dumps(v, default=str))


async def init_pool() -> asyncpg.Pool:
    """Create the pool once. Safe to call repeatedly."""
    global _pool
    async with _lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(database_url(), min_size=2, max_size=10,
                                              command_timeout=10, init=_init_connection)
            log.info("Tiger Data pool ready (min 2, max 10)")
    return _pool


async def get_pool() -> asyncpg.Pool:
    return _pool if _pool is not None else await init_pool()


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def startup() -> None:
    """FastAPI lifespan hook (main.py)."""
    if use_fake():
        log.warning("Data is running IN MEMORY (DATA_FAKE or no TIGER_DATABASE_URL): nothing is saved")
        return
    await init_pool()


async def shutdown() -> None:
    await close_pool()
