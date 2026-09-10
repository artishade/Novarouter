"""Storage layer. Two interchangeable backends behind one tiny facade:

  * SQLite  - default for local/self-hosted runs (zero setup)
  * Postgres - selected automatically when DATABASE_URL is set (Vercel/Neon)

Both implement the same five primitives (init/query/one/execute/executemany),
so the rest of the app never cares which one is active. `execute()` returns
lastrowid for inserts (both backends), and Postgres UPDATE/DELETE return the
affected row count via `execute_rowcount()` where it matters.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional, Sequence

from . import config

_local = threading.local()
_write_lock = threading.Lock()

# ---------------------------------------------------------------------------
# schema (portable across sqlite + postgres)
# ---------------------------------------------------------------------------
SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS providers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,
    base_url        TEXT    NOT NULL,
    kind            TEXT    NOT NULL DEFAULT 'openai',
    prefix          TEXT    NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    extra_headers   TEXT    NOT NULL DEFAULT '{}',
    created_at      REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS upstream_keys (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id     INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    label           TEXT    NOT NULL DEFAULT '',
    api_key         TEXT    NOT NULL,
    weight          INTEGER NOT NULL DEFAULT 1,
    enabled         INTEGER NOT NULL DEFAULT 1,
    cooldown_until  REAL    NOT NULL DEFAULT 0,
    last_error      TEXT    NOT NULL DEFAULT '',
    req_count       INTEGER NOT NULL DEFAULT 0,
    err_count       INTEGER NOT NULL DEFAULT 0,
    last_used_at    REAL    NOT NULL DEFAULT 0,
    created_at      REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS client_keys (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    token           TEXT    NOT NULL UNIQUE,
    enabled         INTEGER NOT NULL DEFAULT 1,
    allowed_models  TEXT    NOT NULL DEFAULT '',
    req_count       INTEGER NOT NULL DEFAULT 0,
    last_used_at    REAL    NOT NULL DEFAULT 0,
    created_at      REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS models (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id     INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    model_id        TEXT    NOT NULL,
    exposed_id      TEXT    NOT NULL,
    is_free         INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'UNKNOWN',
    http_status     INTEGER NOT NULL DEFAULT 0,
    detail          TEXT    NOT NULL DEFAULT '',
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    checked_at      REAL    NOT NULL DEFAULT 0,
    enabled         INTEGER NOT NULL DEFAULT 1,
    UNIQUE(provider_id, model_id)
);
CREATE TABLE IF NOT EXISTS request_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              REAL    NOT NULL,
    client_key_id   INTEGER,
    provider_id     INTEGER,
    upstream_key_id INTEGER,
    model           TEXT    NOT NULL DEFAULT '',
    status          INTEGER NOT NULL DEFAULT 0,
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    error           TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_log_ts ON request_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_models_exposed ON models(exposed_id);
"""

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS providers (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT    NOT NULL UNIQUE,
    base_url        TEXT    NOT NULL,
    kind            TEXT    NOT NULL DEFAULT 'openai',
    prefix          TEXT    NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    extra_headers   TEXT    NOT NULL DEFAULT '{}',
    created_at      DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS upstream_keys (
    id              BIGSERIAL PRIMARY KEY,
    provider_id     BIGINT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    label           TEXT    NOT NULL DEFAULT '',
    api_key         TEXT    NOT NULL,
    weight          INTEGER NOT NULL DEFAULT 1,
    enabled         INTEGER NOT NULL DEFAULT 1,
    cooldown_until  DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_error      TEXT    NOT NULL DEFAULT '',
    req_count       INTEGER NOT NULL DEFAULT 0,
    err_count       INTEGER NOT NULL DEFAULT 0,
    last_used_at    DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at      DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS client_keys (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT    NOT NULL,
    token           TEXT    NOT NULL UNIQUE,
    enabled         INTEGER NOT NULL DEFAULT 1,
    allowed_models  TEXT    NOT NULL DEFAULT '',
    req_count       INTEGER NOT NULL DEFAULT 0,
    last_used_at    DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at      DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS models (
    id              BIGSERIAL PRIMARY KEY,
    provider_id     BIGINT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    model_id        TEXT    NOT NULL,
    exposed_id      TEXT    NOT NULL,
    is_free         INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'UNKNOWN',
    http_status     INTEGER NOT NULL DEFAULT 0,
    detail          TEXT    NOT NULL DEFAULT '',
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    checked_at      DOUBLE PRECISION NOT NULL DEFAULT 0,
    enabled         INTEGER NOT NULL DEFAULT 1,
    UNIQUE(provider_id, model_id)
);
CREATE TABLE IF NOT EXISTS request_log (
    id              BIGSERIAL PRIMARY KEY,
    ts              DOUBLE PRECISION NOT NULL,
    client_key_id   BIGINT,
    provider_id     BIGINT,
    upstream_key_id BIGINT,
    model           TEXT    NOT NULL DEFAULT '',
    status          INTEGER NOT NULL DEFAULT 0,
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    error           TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_log_ts ON request_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_models_exposed ON models(exposed_id);
"""

USE_POSTGRES = bool(config.DATABASE_URL)


def _to_pg(sql: str) -> str:
    """Translate sqlite-style `?` placeholders to psycopg `%s`.

    Safe for this codebase: no SQL text contains a literal '?' or bare '%'
    (LIKE patterns are always passed as parameter values, never inlined).
    """
    return sql.replace("?", "%s")


def _pg_dsn() -> str:
    url = config.DATABASE_URL
    # Accept both postgres:// and postgresql:// (Neon uses postgresql://).
    if url.startswith("postgresql+"):
        url = url.split("+", 1)[0]
    return url


# ---------------------------------------------------------------------------
# sqlite backend
# ---------------------------------------------------------------------------
def _sqlite_conn():
    import sqlite3

    if getattr(_local, "conn", None) is None:
        c = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return _local.conn


def _sqlite_init() -> None:
    with _write_lock:
        c = _sqlite_conn()
        c.executescript(SCHEMA_SQLITE)
        c.commit()


def _sqlite_query(sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in _sqlite_conn().execute(sql, params).fetchall()]


def _sqlite_one(sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    row = _sqlite_conn().execute(sql, params).fetchone()
    return dict(row) if row else None


def _sqlite_execute(sql: str, params: Sequence[Any] = ()) -> int:
    with _write_lock:
        cur = _sqlite_conn().execute(sql, params)
        _sqlite_conn().commit()
        return cur.lastrowid


def _sqlite_execute_rowcount(sql: str, params: Sequence[Any] = ()) -> int:
    with _write_lock:
        cur = _sqlite_conn().execute(sql, params)
        _sqlite_conn().commit()
        return cur.rowcount


def _sqlite_executemany(sql: str, seq: Sequence[Sequence[Any]]) -> None:
    with _write_lock:
        c = _sqlite_conn()
        c.executemany(sql, seq)
        c.commit()


# ---------------------------------------------------------------------------
# postgres backend (psycopg3)
# ---------------------------------------------------------------------------
_pg_pool: Optional[Any] = None
_pg_pool_lock = threading.Lock()
_pg_checked: Dict[int, bool] = {}


def _pg_pool_get():
    import psycopg_pool

    global _pg_pool
    with _pg_pool_lock:
        if _pg_pool is None or _pg_pool.closed:
            _pg_pool = psycopg_pool.ConnectionPool(
                _pg_dsn(),
                min_size=1,
                max_size=8,
                open=True,
                timeout=15,
                # Pooled Neon endpoints (PgBouncer, transaction pooling) do not
                # support server-side prepared statements; keep them off.
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": None,
                },
            )
    return _pg_pool


def _pg_dict_row(cur) -> List[Dict[str, Any]]:
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _pg_init() -> None:
    pool = _pg_pool_get()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_PG)


def _pg_query(sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    pool = _pg_pool_get()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_to_pg(sql), params)
            if cur.description is None:
                return []
            return _pg_dict_row(cur)


def _pg_one(sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    rows = _pg_query(sql + " LIMIT 1", params)
    return rows[0] if rows else None


def _pg_execute(sql: str, params: Sequence[Any] = ()) -> int:
    """Execute, translating placeholders. INSERTs get RETURNING id appended
    so callers get a lastrowid like SQLite gives them."""
    pool = _pg_pool_get()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            stmt = _to_pg(sql)
            up = stmt.strip().upper()
            is_insert = up.startswith("INSERT")
            if is_insert and "RETURNING" not in up:
                stmt = stmt + " RETURNING id"
            cur.execute(stmt, params)
            if is_insert:
                row = cur.fetchone()
                return int(row[0]) if row else 0
            return 0


def _pg_execute_rowcount(sql: str, params: Sequence[Any] = ()) -> int:
    pool = _pg_pool_get()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_to_pg(sql), params)
            return cur.rowcount


def _pg_executemany(sql: str, seq: Sequence[Sequence[Any]]) -> None:
    if not seq:
        return
    pool = _pg_pool_get()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(_to_pg(sql), seq)


# ---------------------------------------------------------------------------
# unified facade
# ---------------------------------------------------------------------------
def init() -> None:
    if USE_POSTGRES:
        _pg_init()
    else:
        _sqlite_init()


def query(sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    if USE_POSTGRES:
        return _pg_query(sql, params)
    return _sqlite_query(sql, params)


def one(sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    if USE_POSTGRES:
        return _pg_one(sql, params)
    return _sqlite_one(sql, params)


def execute(sql: str, params: Sequence[Any] = ()) -> int:
    if USE_POSTGRES:
        return _pg_execute(sql, params)
    return _sqlite_execute(sql, params)


def execute_rowcount(sql: str, params: Sequence[Any] = ()) -> int:
    """UPDATE/DELETE - returns number of affected rows (both backends)."""
    if USE_POSTGRES:
        return _pg_execute_rowcount(sql, params)
    return _sqlite_execute_rowcount(sql, params)


def executemany(sql: str, seq: Sequence[Sequence[Any]]) -> None:
    if USE_POSTGRES:
        _pg_executemany(sql, seq)
    else:
        _sqlite_executemany(sql, seq)


def log_request(**kw) -> None:
    execute(
        """INSERT INTO request_log
           (ts, client_key_id, provider_id, upstream_key_id, model, status, latency_ms, error)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            time.time(),
            kw.get("client_key_id"),
            kw.get("provider_id"),
            kw.get("upstream_key_id"),
            kw.get("model", ""),
            kw.get("status", 0),
            kw.get("latency_ms", 0),
            (kw.get("error") or "")[:300],
        ),
    )
    # cheap retention trim
    if config.LOG_RETENTION > 0:
        execute_rowcount(
            "DELETE FROM request_log WHERE id < (SELECT MAX(id) - ? FROM request_log)",
            (config.LOG_RETENTION,),
        )