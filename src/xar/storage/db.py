"""Postgres access layer (psycopg3 + pgvector). One database for vectors,
relational data, and the bitemporal knowledge graph."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..config import get_settings
from ..logging import get_logger

log = get_logger("xar.db")
_POOL: ConnectionPool | None = None
_LOCK = threading.Lock()

_SCHEMA = Path(__file__).with_name("schema.sql")


def _configure(conn: psycopg.Connection) -> None:
    try:
        register_vector(conn)
    except Exception:
        # vector extension not yet created on a fresh DB; init_schema handles it
        pass


def pool() -> ConnectionPool:
    global _POOL
    if _POOL is None:
        with _LOCK:
            if _POOL is None:
                s = get_settings()
                _POOL = ConnectionPool(
                    s.database_url, min_size=2, max_size=16, configure=_configure, open=True
                )
    return _POOL


def init_schema() -> None:
    """Idempotently create the schema with the configured embedding dimension."""
    s = get_settings()
    ddl = _SCHEMA.read_text().replace("{EMBED_DIM}", str(s.embed_dim))
    # Use a raw connection so CREATE EXTENSION runs before register_vector.
    with psycopg.connect(s.database_url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(conn)
        conn.execute(ddl)
    log.info("schema initialized (embed_dim=%s)", s.embed_dim)


def ensure_vector_index() -> None:
    """Create the ANN index once enough rows exist (IVFFlat needs data)."""
    with conn() as c:
        n = c.execute("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()
        if n and n[0] >= 64:
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_vec ON chunks "
                "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
            )
            c.commit()


class conn:
    """Context manager yielding a configured connection from the pool."""

    def __enter__(self) -> psycopg.Connection:
        self._cm = pool().connection()
        self._conn = self._cm.__enter__()
        try:
            register_vector(self._conn)
        except Exception:
            pass
        return self._conn

    def __exit__(self, *exc: Any) -> None:
        self._cm.__exit__(*exc)


# --- small helpers ---------------------------------------------------------
def query(sql: str, params: Sequence[Any] | None = None) -> list[dict]:
    with conn() as c:
        cur = c.cursor(row_factory=dict_row)
        cur.execute(sql, params or ())
        return cur.fetchall()


def execute(sql: str, params: Sequence[Any] | None = None) -> None:
    with conn() as c:
        c.execute(sql, params or ())
        c.commit()


def executemany(sql: str, rows: Iterable[Sequence[Any]]) -> None:
    with conn() as c:
        c.cursor().executemany(sql, list(rows))
        c.commit()


class tx:
    """Transaction context: yields a connection whose statements commit atomically
    on clean exit and roll back on any exception. Use for multi-step writes that
    must not leave half-applied state (e.g. delete-then-reinsert)."""

    def __enter__(self) -> psycopg.Connection:
        self._cm = pool().connection()
        self._conn = self._cm.__enter__()
        try:
            register_vector(self._conn)
        except Exception:
            pass
        return self._conn

    def __exit__(self, exc_type: Any, *exc: Any) -> None:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._cm.__exit__(exc_type, *exc)
