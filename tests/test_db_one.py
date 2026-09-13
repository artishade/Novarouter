"""Regression: db.one() must not rewrite the SQL it runs.

The Postgres backend used to append " LIMIT 1" to every db.one() query;
call sites that already carry their own LIMIT 1 (store._requested_profile,
store.model_row_capabilities, extensions.has_enabled_tools) then produced
"LIMIT 1 LIMIT 1" — a syntax error that surfaced as HTTP 500 on every
request hitting those paths (Render / Neon / Vercel Postgres deployments).
"""
import sys
import tempfile
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

ADMIN_TOKEN = "test-admin-token"


@pytest.fixture()
def db_mod(monkeypatch):
    """Fresh app.db against a throwaway data dir (repo isolation pattern)."""
    monkeypatch.setenv("NOVA_DATA_DIR", tempfile.mkdtemp(prefix="nova-dbone-"))
    monkeypatch.setenv("NOVA_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for mod in list(sys.modules):
        if mod.startswith("app"):
            del sys.modules[mod]
    from app import db  # noqa: PLC0415

    return db


class _Col:
    def __init__(self, name):
        self.name = name


class _Cur:
    def __init__(self):
        self.executed = ""
        self.description = [_Col("x")]

    def execute(self, sql, params=()):
        self.executed = sql

    def fetchone(self):
        return (7,)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self):
        self.cur = _Cur()

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Pool:
    def __init__(self):
        self.conn = _Conn()

    def connection(self):
        return self.conn


def test_one_leaves_limit_1_sql_untouched(db_mod, monkeypatch):
    pool = _Pool()
    monkeypatch.setattr(db_mod, "_pg_pool_get", lambda: pool)
    monkeypatch.setattr(db_mod, "USE_POSTGRES", True)

    sql = "SELECT 1 AS x FROM models WHERE exposed_id=? LIMIT 1"
    row = db_mod.one(sql, ("m",))

    assert row == {"x": 7}
    # byte-identical after placeholder translation: no appended LIMIT
    assert pool.conn.cur.executed == db_mod._to_pg(sql)
    assert "LIMIT 1 LIMIT 1" not in pool.conn.cur.executed


def test_one_returns_none_when_no_rows(db_mod, monkeypatch):
    pool = _Pool()
    pool.conn.cur.fetchone = lambda: None
    monkeypatch.setattr(db_mod, "_pg_pool_get", lambda: pool)
    monkeypatch.setattr(db_mod, "USE_POSTGRES", True)

    assert db_mod.one("SELECT 1 AS x FROM t") is None


def test_one_handles_non_select(db_mod, monkeypatch):
    pool = _Pool()
    pool.conn.cur.description = None
    monkeypatch.setattr(db_mod, "_pg_pool_get", lambda: pool)
    monkeypatch.setattr(db_mod, "USE_POSTGRES", True)

    assert db_mod.one("DELETE FROM t WHERE id=?") is None
