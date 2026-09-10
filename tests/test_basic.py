"""Smoke tests for NovaRouter - run with: python -m pytest tests/ -x -q

Uses FastAPI's TestClient with a temporary SQLite data dir, so it never
touches real provider keys or network endpoints.
"""
import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

ADMIN_TOKEN = "test-admin-token"


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="nova-test-")
    monkeypatch.setenv("NOVA_DATA_DIR", tmp)
    monkeypatch.setenv("NOVA_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # fresh import so config picks up the env
    for mod in list(sys.modules):
        if mod.startswith("app"):
            del sys.modules[mod]
    from app.main import app  # noqa: PLC0415

    with TestClient(app) as c:
        yield c


def auth(headers=None):
    return {"X-Admin-Token": ADMIN_TOKEN, **(headers or {})}


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_dashboard_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "NOVAROUTER" in r.text


def test_admin_requires_token(client):
    r = client.get("/admin/api/stats")
    assert r.status_code == 401


def test_admin_meta_base_url_derived(client):
    r = client.get("/admin/api/meta", headers=auth())
    assert r.status_code == 200
    assert r.json()["base_url"].endswith("/v1")


def test_provider_crud_and_keys(client):
    r = client.post(
        "/admin/api/providers",
        headers=auth(),
        json={
            "name": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_keys": "sk-or-test-1\nsk-or-test-2",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["keys_added"] == 2

    r = client.get("/admin/api/providers", headers=auth())
    assert len(r.json()) == 1
    assert r.json()[0]["key_count"] == 2
    # keys must never be revealed in list views
    assert "api_key" not in r.json()[0]["keys"][0]

    r = client.delete("/admin/api/providers/1", headers=auth())
    assert r.json()["ok"] is True


def test_gateway_key_flow(client):
    # provider without keys first
    client.post(
        "/admin/api/providers",
        headers=auth(),
        json={"name": "p", "base_url": "https://example.com/v1"},
    )
    r = client.post(
        "/admin/api/client-keys", headers=auth(), json={"name": "cli"}
    )
    token = r.json()["token"]
    assert token.startswith("nova-")

    # unauthorized gateway call
    r = client.get("/v1/models")
    assert r.status_code == 401

    # authorized but empty model list
    r = client.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["data"] == []

    # unknown model -> clean 404, not 500
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "nope", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 404
    assert "not found" in r.json()["error"]["message"].lower()


def test_anthropic_embeddings_returns_400_not_500(client):
    """Regression: anthropic provider + /v1/embeddings used to crash with 500."""
    client.post(
        "/admin/api/providers",
        headers=auth(),
        json={
            "name": "anthropic",
            "base_url": "https://api.anthropic.com/v1",
            "kind": "anthropic",
            "api_keys": "sk-ant-test",
        },
    )
    # register a model row so resolve_model finds it
    from app import db, store

    db.execute(
        "INSERT INTO models (provider_id, model_id, exposed_id) VALUES (?,?,?)",
        (1, "claude-3-5-haiku-latest", "anthropic/claude-3-5-haiku-latest"),
    )
    r = client.post(
        "/v1/embeddings",
        headers={"Authorization": "Bearer " + _make_gw_token(client)},
        json={"model": "anthropic/claude-3-5-haiku-latest", "input": "hi"},
    )
    assert r.status_code == 400
    assert "anthropic" in r.json()["error"]["message"].lower()


def _make_gw_token(client):
    r = client.post("/admin/api/client-keys", headers=auth(), json={"name": "t"})
    return r.json()["token"]


def test_prune_dead_count(client):
    """Regression: prune-dead used to return lastrowid instead of rowcount."""
    client.post(
        "/admin/api/providers",
        headers=auth(),
        json={"name": "p", "base_url": "https://example.com/v1"},
    )
    from app import db

    for mid, status in (("m1", "NO_ACCESS"), ("m2", "OK"), ("m3", "UNKNOWN")):
        db.execute(
            "INSERT INTO models (provider_id, model_id, exposed_id, status) VALUES (?,?,?,?)",
            (1, mid, mid, status),
        )
    r = client.post("/admin/api/models/prune-dead", headers=auth())
    assert r.json()["affected"] == 1  # only NO_ACCESS row


def test_allowed_models_wildcard(client):
    from app import store

    c = store.create_client_key("restricted", "groq/*")
    assert store.client_allows(c, "groq/llama-3") is True
    assert store.client_allows(c, "openai/gpt-4") is False
    d = store.create_client_key("open", "")
    assert store.client_allows(d, "anything") is True
