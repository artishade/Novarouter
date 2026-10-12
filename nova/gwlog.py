"""Gateway request log persistence — mirrors gateway_request_log output into
a local sqlite table so Claude Code / agent failures can be diagnosed from
the dashboard without SSH. Writes are fire-and-forget and never raise.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time

log = logging.getLogger("nova.gwlog")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gateway_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    client_ip TEXT,
    method TEXT,
    path TEXT,
    status INTEGER,
    duration_ms INTEGER,
    summary TEXT,
    error_snip TEXT
);
CREATE INDEX IF NOT EXISTS idx_gateway_requests_ts ON gateway_requests(ts DESC);
"""


def _get_conn():
    global _conn
    if _conn is not None:
        return _conn
    try:
        from .config import PROJECT_ROOT
        db_path = PROJECT_ROOT / "db" / "gateway_requests.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(db_path), check_same_thread=False)
        # executescript — the schema is multiple statements and sqlite3.execute()
        # only accepts one, which used to disable this log entirely.
        _conn.executescript(_SCHEMA)
        _conn.commit()
        return _conn
    except Exception as err:
        log.warning("gateway request log disabled: %s", err)
        _conn = None
        return None


def record_request(client_ip, method, path, status, duration_ms, summary="", error_snip=""):
    conn = _get_conn()
    if conn is None:
        return
    try:
        with _lock:
            conn.execute(
                "INSERT INTO gateway_requests (ts, client_ip, method, path, status, duration_ms, summary, error_snip) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (time.time(), client_ip, method, path, int(status), int(duration_ms), summary[:600], error_snip[:800]),
            )
            conn.commit()
    except Exception:
        pass


def recent(limit=50, errors_only=False):
    conn = _get_conn()
    if conn is None:
        return []
    try:
        q = "SELECT ts, client_ip, method, path, status, duration_ms, summary, error_snip FROM gateway_requests"
        if errors_only:
            q += " WHERE status >= 400"
        q += " ORDER BY ts DESC LIMIT ?"
        with _lock:
            return conn.execute(q, (int(limit),)).fetchall()
    except Exception:
        return []


def clear():
    conn = _get_conn()
    if conn is not None:
        try:
            with _lock:
                conn.execute("DELETE FROM gateway_requests")
                conn.commit()
        except Exception:
            pass
