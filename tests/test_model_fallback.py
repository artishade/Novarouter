"""Model fallback + identity spoofing tests — the core promise of the router:

A client asks for model X. X is dead (or doesn't exist). NovaRouter silently
retries on a fallback model and returns a response whose `model` field still
says X. The agent never sees the switch.

Uses a scripted fake upstream so no network is touched."""
import importlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

ADMIN_TOKEN = "test-admin-token"


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="nova-fb-")
    monkeypatch.setenv("NOVA_DATA_DIR", tmp)
    monkeypatch.setenv("NOVA_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for mod in list(sys.modules):
        if mod.startswith("app"):
            del sys.modules[mod]
    from app.main import app  # noqa: PLC0415

    with TestClient(app) as c:
        yield c


def auth(headers=None):
    return {"X-Admin-Token": ADMIN_TOKEN, **(headers or {})}


def _setup(client):
    """fake provider + key + two models: dead-model and stand-in."""
    r = client.post(
        "/admin/api/providers",
        headers=auth(),
        json={
            "name": "fake",
            "base_url": "http://fake.local/v1",
            "kind": "openai",
            "api_keys": "sk-fake",
        },
    )
    assert r.status_code == 200, r.text
    from app import db

    db.execute(
        "INSERT INTO models (provider_id, model_id, exposed_id, status, checked_at) VALUES (?,?,?,?,?)",
        (1, "dead-model", "dead-model", "OK", time.time()),
    )
    db.execute(
        "INSERT INTO models (provider_id, model_id, exposed_id, status, checked_at) VALUES (?,?,?,?,?)",
        (1, "stand-in", "stand-in", "OK", time.time()),
    )
    token = client.post(
        "/admin/api/client-keys", headers=auth(), json={"name": "t"}
    ).json()["token"]
    return token


class FakeResp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _chat_body(model, content="hi from upstream"):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content},
             "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
    }


class FakeUpstream:
    """Fails every request for dead-model (500), succeeds otherwise."""

    is_closed = False

    def __init__(self):
        self.requests = []  # (url, body)

    async def post(self, url, headers=None, json=None):
        self.requests.append((url, json))
        if getattr(self, "fail_all", False) or (json and json.get("model") == "dead-model"):
            return FakeResp({"error": {"message": "model is dead"}}, 500)
        return FakeResp(_chat_body(json.get("model", "?")))

    async def aclose(self):
        pass


@pytest.fixture()
def fake_upstream(client, monkeypatch):
    """Fails dead-model; a test can fail every model by setting fake.fail_all."""
    from app import gateway

    fake = FakeUpstream()
    fake.fail_all = False
    monkeypatch.setattr(gateway, "_client", fake)
    yield fake


def test_route_crud(client):
    r = client.post(
        "/admin/api/routes",
        headers=auth(),
        json={"public_id": "claude-opus-4-6",
              "fallbacks": "stand-in, other"},
    )
    assert r.status_code == 200, r.text
    rid = r.json()["id"]

    rows = client.get("/admin/api/routes", headers=auth()).json()
    assert rows[0]["public_id"] == "claude-opus-4-6"
    assert rows[0]["fallback_list"] == ["stand-in", "other"]

    r = client.patch(
        f"/admin/api/routes/{rid}", headers=auth(),
        json={"fallbacks": "stand-in"},
    )
    assert r.json()["ok"] is True
    assert client.get("/admin/api/routes", headers=auth()).json()[0]["fallback_list"] == ["stand-in"]

    # duplicate rejected
    r = client.post(
        "/admin/api/routes", headers=auth(),
        json={"public_id": "claude-opus-4-6", "fallbacks": "x"},
    )
    assert r.status_code == 400

    r = client.delete(f"/admin/api/routes/{rid}", headers=auth())
    assert r.json()["ok"] is True
    assert client.get("/admin/api/routes", headers=auth()).json() == []


def test_dead_model_falls_back_and_spoofs_id(client, fake_upstream):
    """The core scenario: dead requested model -> stand-in serves it ->
    response still claims the requested id."""
    token = _setup(client)
    client.post(
        "/admin/api/routes", headers=auth(),
        json={"public_id": "dead-model", "fallbacks": "stand-in", "auto": False},
    )

    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "dead-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "dead-model", "spoofed id must match the request"
    assert body["_nova"]["upstream_model"] == "stand-in"
    assert body["_nova"]["routed_from"] == "dead-model"
    # the fallback actually got the call
    assert any(
        (b or {}).get("model") == "stand-in" for _u, b in fake_upstream.requests
    )

    # the log records the truth for the operator
    from app import store

    log = store.recent_logs(5)[0]
    assert log["model"] == "dead-model"
    assert log["via"] == "stand-in"


def test_unknown_model_uses_route_chain(client, fake_upstream):
    """Model not registered anywhere -> route chain still serves it."""
    token = _setup(client)
    client.post(
        "/admin/api/routes", headers=auth(),
        json={"public_id": "claude-opus-4-6", "fallbacks": "stand-in", "auto": False},
    )

    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "claude-opus-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "claude-opus-4-6"
    assert body["_nova"]["upstream_model"] == "stand-in"


def test_auto_fallback_picks_stand_in(client, fake_upstream):
    """No route at all -> auto-pick serves the dead model."""
    token = _setup(client)  # dead-model + stand-in both registered

    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "dead-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "dead-model"
    assert body["_nova"]["upstream_model"] == "stand-in"


def test_spoof_opt_out_reports_true_model(client, fake_upstream):
    token = _setup(client)
    client.post(
        "/admin/api/routes", headers=auth(),
        json={"public_id": "dead-model", "fallbacks": "stand-in", "auto": False},
    )

    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "dead-model",
            "nova": {"spoof_model": False},
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "stand-in"


def test_models_endpoint_lists_route_ids(client):
    """Agents' model pickers must show the routed public ids."""
    token = _setup(client)
    client.post(
        "/admin/api/routes", headers=auth(),
        json={"public_id": "claude-opus-4-6", "fallbacks": "stand-in", "auto": False},
    )
    r = client.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
    ids = [m["id"] for m in r.json()["data"]]
    assert "claude-opus-4-6" in ids
    assert "stand-in" in ids


def test_route_preview(client):
    _setup(client)
    client.post(
        "/admin/api/routes", headers=auth(),
        json={"public_id": "dead-model", "fallbacks": "stand-in", "auto": False},
    )
    r = client.get("/admin/api/routes/preview", headers=auth(), params={"model": "dead-model"})
    assert r.status_code == 200
    body = r.json()
    assert body["direct"] is True
    assert body["explicit_chain"] == ["stand-in"]
    assert body["spoof_model"] is True


def test_all_dead_returns_error_not_hang(client, fake_upstream, monkeypatch):
    """Everything fails -> clean error, listing what was tried.

    With the fix, the dashboard-added custom fallback target `also-dead` is
    auto-registered as a passthrough row, so the router actually *tries* it
    before giving up: primary dead-model fails (500) and the fallback target
    also fails upstream -> a clean 502 that lists every attempt, no hang,
    never a silent success.
    """
    from app import config
    token = _setup(client)
    client.post(
        "/admin/api/routes", headers=auth(),
        json={"public_id": "dead-model", "fallbacks": "also-dead", "auto": False},
    )
    # Disable auto-pick so only the explicit chain target is a stand-in.
    monkeypatch.setattr(config, "AUTO_FALLBACK", False)
    # Force the fake to fail on `also-dead` too so nothing can serve.
    fake_upstream.fail_all = True
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "dead-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code >= 500
    body = r.json()
    assert "dead-model" in body["error"]["message"]
    assert "Tried" in body["error"]["message"]
    # the fallback target was actually tried upstream as part of the fan-out
    assert any((b or {}).get("model") == "also-dead" for _u, b in fake_upstream.requests)


def test_unlisted_unknown_model_still_hard_fails(client, fake_upstream, monkeypatch):
    """A model id that is NOT a route fallback target must still 404 —
    the fix must not make every random unknown id resolve.
    (Auto-pick disabled so only the explicit route chain can serve.)"""
    from app import config
    monkeypatch.setattr(config, "AUTO_FALLBACK", False)
    token = _setup(client)
    fake_upstream.fail_all = True
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "never-heard-of-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    # unknown id + auto-pick off + no route -> clean 404 model_not_found
    assert r.status_code == 404, r.text
    assert "never-heard-of-model" in r.json()["error"]["message"]
    # and it was NOT auto-registered as a route fallback target
    from app import store, db
    assert not any(
        (m.get("model_id") == "never-heard-of-model") for m in
        db.query("SELECT * FROM models WHERE 1=1")
    ), "unknown id must not be auto-registered"


def test_anthropic_messages_endpoint_fallback(client, fake_upstream):
    """/v1/messages (Claude Code / Claude Desktop) gets the same behavior."""
    token = _setup(client)
    client.post(
        "/admin/api/routes", headers=auth(),
        json={"public_id": "dead-model", "fallbacks": "stand-in", "auto": False},
    )

    r = client.post(
        "/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "dead-model",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "dead-model"
    assert body["type"] == "message"
    assert body["content"][0]["text"] == "hi from upstream"


def test_dashboard_custom_unregistered_fallback_serves(client, fake_upstream):
    """Operator adds a custom fallback target that is NOT in the synced models
    catalogue (the normal dashboard flow). The primary model fails -> the
    unregistered fallback target must still serve and report the requested id."""
    token = _setup(client)
    r = client.post(
        "/admin/api/routes", headers=auth(),
        json={"public_id": "dead-model", "fallbacks": "operator-custom-model", "auto": False},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "dead-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "dead-model"
    assert body["_nova"]["upstream_model"] == "operator-custom-model"
    assert any(
        (b or {}).get("model") == "operator-custom-model" for _u, b in fake_upstream.requests
    ), "fallback target actually got the upstream call"


def test_edited_route_chain_custom_target_serves(client, fake_upstream):
    """PATCH the chain to a new custom target -> that target resolves too."""
    token = _setup(client)
    rid = client.post(
        "/admin/api/routes", headers=auth(),
        json={"public_id": "dead-model", "fallbacks": "stand-in", "auto": False},
    ).json()["id"]
    r = client.patch(
        f"/admin/api/routes/{rid}", headers=auth(),
        json={"fallbacks": "another-custom-model"},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "dead-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["_nova"]["upstream_model"] == "another-custom-model"
