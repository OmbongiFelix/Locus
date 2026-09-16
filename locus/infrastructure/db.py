from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg
from asyncpg.pool import PoolConnectionProxy

from locus.config import get_settings

_pool: asyncpg.Pool | None = None


async def init_db_pool() -> None:
    """Create the process-wide asyncpg connection pool."""
    global _pool
    settings = get_settings()
    _pool = await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=settings.DB_POOL_MIN_SIZE,
        max_size=settings.DB_POOL_MAX_SIZE,
        command_timeout=settings.DB_POOL_TIMEOUT,
    )


async def close_db_pool() -> None:
    """Gracefully close the connection pool at shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_connection() -> AsyncGenerator[PoolConnectionProxy, None]:
    """Borrow a connection from the pool.

    Only infrastructure/boundary_repository.py should call this.
    If you are reaching for it from domain/ or api/, the query
    belongs in the repository instead.

    Usage::

        async with get_connection() as conn:
            row = await conn.fetchrow(...)
    """
    if _pool is None:
        raise RuntimeError(
            "Database pool is not initialised.  Call init_db_pool() first."
        )
    async with _pool.acquire() as conn:
        yield conn