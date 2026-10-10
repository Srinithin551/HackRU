"""asyncpg connection pool for Tiger Data (Postgres + TimescaleDB).

Created once on FastAPI startup (min_size=2, max_size=10). Every query function
takes connections from it. Scripts can call init_pool() / close_pool() directly.

The connection string comes from TIGER_DATABASE_URL (env, or backend/.env).
Never print or log it.
"""
import asyncio
import json
import logging
import os
from pathlib import Path

import asyncpg

log = logging.getLogger(__name__)

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"     # backend/.env
_pool: asyncpg.Pool | None = None
_lock = asyncio.Lock()


def database_url() -> str:
    if not os.environ.get("TIGER_DATABASE_URL") and ENV_FILE.exists():
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE)
    url = os.environ.get("TIGER_DATABASE_URL")
    if not url:
        raise RuntimeError(f"TIGER_DATABASE_URL is not set (put it in {ENV_FILE})")
    return url


async def _init_connection(conn: asyncpg.Connection) -> None:
    # jsonb <-> dict, so sessions.report round-trips as a Python dict
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def init_pool() -> asyncpg.Pool:
    """Create the pool once. Safe to call repeatedly; call it from FastAPI startup."""
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
