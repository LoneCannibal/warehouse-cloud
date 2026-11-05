"""PostgreSQL connection pool and schema setup.

The pool is deliberately small. An HPA can multiply pods, and each pod holds a
full pool, so the RDS connection limit has to survive the maximum replica count.
"""
from __future__ import annotations

from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from warehouse_common.settings import Settings

SCHEMA_LOCK_ID = 727


def make_pool(settings: Settings) -> ConnectionPool:
    pool = ConnectionPool(
        settings.db_dsn,
        min_size=1,
        max_size=settings.db_pool_size,
        kwargs={"row_factory": dict_row},
        open=True,
    )
    pool.wait(timeout=60)
    return pool


def apply_schema(pool: ConnectionPool, path: Path) -> None:
    """Runs an idempotent schema file. The advisory lock stops pods racing at startup."""
    with pool.connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_ID,))
        conn.execute(path.read_text())
