"""Feature tests for the v2.2 provider expansion — run with:
python -m pytest tests/test_features.py -x -q

Covers (all mocked, no network):
  1. provider priority      — routing order follows explicit priority
  2. response caching       — 2nd identical request served via cache
  3. files API              — upload / list / content / delete / isolation
  4. batches API            — JSONL batch runs end-to-end, OpenAI envelope
  5. gemini native kind     — build_request URL + translation round trip
  6. analytics              — timeseries buckets from seeded logs
  7. hedging                — slow primary loses the race to the hedge
"""
import asyncio
import json
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
    tmp = tempfile.mkdtemp(prefix="nova-feat-")
    monkeypatch.setenv("NOVA_DATA_DIR", tmp)
    monkeypatch.setenv("NOVA_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("NOVA_CACHE_TTL", "60")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for mod in list(sys.modules):
        if mod.startswith("app"):
            del sys.modules[mod]
    from app.main import app  # noqa: PLC0415

    with TestClient(app) as c:
        yield c


def auth(headers=None):
    return {"X-Admin-Token": ADMIN_TOKEN, **(headers or {})}


def _add_provider(client, name, url, priority=100):
    r = client.post(
        "/admin/api/providers",
        headers=auth(),
        json={"name": name, "base_url": url, "kind": "openai",
              "api_keys": f"sk-{name}", "priority": priority},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _add_model(client, provider_id, model_id, status="OK", exposed=None):
    from app import db

    db.execute(
        "INSERT INTO models (provider_id, model_id, exposed_id, status, checked_at, latency_ms) "
        "VALUES (?,?,?,?,?,?)",
        (provider_id, model_id, exposed or model_id, status, time.time(), 10),
    )


def _client_token(client, name="t"):
    return client.post(
        "/admin/api/client-keys", headers=auth(), json={"name": name}
    ).json()["token"]


class FakeResp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _chat_body(model, content="hi"):
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                      "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
    }


class FakeUpstream:
    """openai-kind fake: records requests; per-provider behaviours set later."""

    is_closed = False

    def __init__(self):
        self.requests = []

    async def post(self, url, headers=None, json=None):
        self.requests.append((url, json, headers))

        async def gen():
            yield b""

        return FakeResp(_chat_body((json or {}).get("model", "?")))

    async def aclose(self):
        pass


@pytest.fixture()
def fake_upstream(client, monkeypatch):
    from app import gateway

    fake = FakeUpstream()
    monkeypatch.setattr(gateway, "_client", fake)
    yield fake


# ------------------------------------------------------------------ 1. priority
def test_provider_priority_orders_routing(client, fake_upstream):
    """Priority 5 provider is tried before priority 100 for the same model."""
    _add_provider(client, "cheap", "http://cheap.local/v1", priority=100)
    _add_provider(client, "premium", "http://premium.local/v1", priority=1)
    from app import db

    # same model on both providers
    for pid in (1, 2):
        db.execute(
            "INSERT INTO models (provider_id, model_id, exposed_id, status, checked_at, latency_ms) "
            "VALUES (?,?,?,?,?,?)",
            (pid, "m", "m", "OK", time.time(), 10),
        )
    token = _client_token(client)

    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200, r.text
    # premium (priority 1) must have been contacted first
    first_url = fake_upstream.requests[0][0]
    assert "premium" in first_url, f"expected premium first, got {first_url}"


def test_provider_patch_priority(client):
    pid = _add_provider(client, "p1", "http://p1.local/v1")
    r = client.patch(
        f"/admin/api/providers/{pid}", headers=auth(), json={"priority": 7}
    )
    assert r.status_code == 200, r.text
    rows = client.get("/admin/api/providers", headers=auth()).json()
    assert rows[0]["priority"] == 7


# ------------------------------------------------------------------ 2. cache
def test_response_cache_serves_second_request(client, fake_upstream):
    _add_provider(client, "fake", "http://fake.local/v1")
    _add_model(client, 1, "m")
    token = _client_token(client)
    hdr = {"Authorization": f"Bearer {token}"}
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}

    r1 = client.post("/v1/chat/completions", headers=hdr, json=body)
    assert r1.status_code == 200, r1.text
    upstream_calls = len(fake_upstream.requests)

    r2 = client.post("/v1/chat/completions", headers=hdr, json=body)
    assert r2.status_code == 200, r2.text
    assert r2.json()["_nova"]["cached"] is True
    assert len(fake_upstream.requests) == upstream_calls, "cache hit must not reach upstream"

    # different content -> different key -> upstream again
    r3 = client.post(
        "/v1/chat/completions", headers=hdr,
        json={"model": "m", "messages": [{"role": "user", "content": "different"}]},
    )
    assert r3.status_code == 200
    assert len(fake_upstream.requests) == upstream_calls + 1

    # admin stats + clear
    stats = client.get("/admin/api/cache/stats", headers=auth()).json()
    assert stats["hits"] == 1 and stats["entries"] == 2
    assert client.post("/admin/api/cache/clear", headers=auth()).json()["ok"] is True
    assert client.get("/admin/api/cache/stats", headers=auth()).json()["entries"] == 0


def test_cache_opt_out_via_nova_flag(client, fake_upstream):
    _add_provider(client, "fake", "http://fake.local/v1")
    _add_model(client, 1, "m")
    token = _client_token(client)
    hdr = {"Authorization": f"Bearer {token}"}
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}],
            "nova": {"cache": False}}

    client.post("/v1/chat/completions", headers=hdr, json=body)
    calls = len(fake_upstream.requests)
    client.post("/v1/chat/completions", headers=hdr, json=body)
    assert len(fake_upstream.requests) == calls + 1, "opt-out must not cache"


# ------------------------------------------------------------------ 3. files
def test_files_api_roundtrip(client):
    _add_provider(client, "fake", "http://fake.local/v1")
    token = _client_token(client)
    hdr = {"Authorization": f"Bearer {token}"}

    up = client.post(
        "/v1/files", headers=hdr,
        files={"file": ("data.jsonl", b'{"a": 1}\n{"a": 2}\n', "application/jsonl")},
        data={"purpose": "batch"},
    )
    assert up.status_code == 200, up.text
    fid = up.json()["id"]
    assert up.json()["object"] == "file"
    assert up.json()["bytes"] == len(b'{"a": 1}\n{"a": 2}\n')

    meta = client.get(f"/v1/files/{fid}", headers=hdr).json()
    assert meta["purpose"] == "batch"

    lst = client.get("/v1/files", headers=hdr).json()
    assert any(f["id"] == fid for f in lst["data"])

    content = client.get(f"/v1/files/{fid}/content", headers=hdr)
    assert content.content == b'{"a": 1}\n{"a": 2}\n'

    # a second client key must not see this file
    other = _client_token(client, "other")
    assert client.get(f"/v1/files/{fid}", headers={"Authorization": f"Bearer {other}"}).status_code == 404
    assert client.get(
        f"/v1/files/{fid}/content", headers={"Authorization": f"Bearer {other}"}
    ).status_code == 404

    assert client.delete(f"/v1/files/{fid}", headers=hdr).json()["deleted"] is True
    assert client.get(f"/v1/files/{fid}", headers=hdr).status_code == 404


# ------------------------------------------------------------------ 4. batches
def test_batch_end_to_end(client, fake_upstream):
    _add_provider(client, "fake", "http://fake.local/v1")
    _add_model(client, 1, "m")
    token = _client_token(client)
    hdr = {"Authorization": f"Bearer {token}"}

    jsonl = "\n".join([
        json.dumps({"custom_id": "r1", "body": {"model": "m", "messages": [{"role": "user", "content": "one"}]}}),
        json.dumps({"custom_id": "r2", "body": {"model": "m", "messages": [{"role": "user", "content": "two"}]}}),
    ]).encode()
    up = client.post(
        "/v1/files", headers=hdr,
        files={"file": ("in.jsonl", jsonl, "application/jsonl")},
        data={"purpose": "batch"},
    )
    fid = up.json()["id"]

    # a bad batch (missing model) is rejected at creation
    bad = client.post(
        "/v1/files", headers=hdr,
        files={"file": ("bad.jsonl", b'{"custom_id": "x", "body": {"messages": []}}\n', "application/jsonl")},
        data={"purpose": "batch"},
    )
    bad_batch = client.post(
        "/v1/batches", headers=hdr, json={"input_file_id": bad.json()["id"]}
    )
    assert bad_batch.status_code == 400, bad_batch.text

    created = client.post("/v1/batches", headers=hdr, json={"input_file_id": fid})
    assert created.status_code == 200, created.text
    bid = created.json()["id"]
    assert created.json()["object"] == "batch"
    assert created.json()["request_counts"]["total"] == 2

    # wait for the async worker (TestClient runs the loop between calls)
    for _ in range(50):
        row = client.get(f"/v1/batches/{bid}", headers=hdr).json()
        if row.get("status") in ("completed", "failed", "expired", "cancelled"):
            break
        time.sleep(0.1)
    assert row["status"] == "completed", row

    out_id = row["output_file_id"]
    content = client.get(f"/v1/files/{out_id}/content", headers=hdr).text
    lines = [json.loads(x) for x in content.splitlines() if x.strip()]
    assert len(lines) == 2
    assert {ln["custom_id"] for ln in lines} == {"r1", "r2"}
    assert all(ln["response"]["status_code"] == 200 for ln in lines)


# ------------------------------------------------------------------ 5. gemini
def test_gemini_build_request_and_translation():
    from app import adapters

    provider = {"kind": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta"}
    url, headers, body = adapters.build_request(
        provider, "AIzaX", "chat",
        {"model": "gemini-2.5-pro", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert url.endswith("/models/gemini-2.5-pro:generateContent")
    assert headers["x-goog-api-key"] == "AIzaX"
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]

    # streaming picks the SSE method
    url2, _, _ = adapters.build_request(
        provider, "AIzaX", "chat",
        {"model": "gemini-2.5-pro", "stream": True,
         "messages": [{"role": "user", "content": "hi"}]},
    )
    assert ":streamGenerateContent?alt=sse" in url2


def test_gemini_translation_round_trip():
    from app import adapters

    req = adapters.openai_to_gemini({
        "model": "gemini-2.5-pro",
        "messages": [
            {"role": "system", "content": "be nice"},
            {"role": "user", "content": [
                {"type": "text", "text": "what is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
            ]},
        ],
        "tools": [{"type": "function", "function": {
            "name": "look", "description": "look at things",
            "parameters": {"type": "object", "properties": {}}}}],
        "max_tokens": 100,
    })
    assert req["systemInstruction"]["parts"][0]["text"] == "be nice"
    img_part = req["contents"][0]["parts"][1]
    assert img_part["inlineData"]["mimeType"] == "image/png"
    assert img_part["inlineData"]["data"] == "QUJD"
    assert req["tools"][0]["functionDeclaration"]["name"] == "look"
    assert req["generationConfig"]["maxOutputTokens"] == 100

    # response with functionCall -> openai tool_calls
    resp = adapters.gemini_to_openai({
        "candidates": [{"content": {"parts": [
            {"text": "let me look"},
            {"functionCall": {"name": "look", "args": {"q": "that"}}},
        ]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3, "totalTokenCount": 8},
    }, "gemini-2.5-pro")
    assert resp["choices"][0]["message"]["content"] == "let me look"
    tc = resp["choices"][0]["message"]["tool_calls"][0]
    assert tc["function"]["name"] == "look"
    assert json.loads(tc["function"]["arguments"]) == {"q": "that"}
    assert resp["usage"]["prompt_tokens"] == 5

    # thought parts -> reasoning passthrough
    resp2 = adapters.gemini_to_openai({
        "candidates": [{"content": {"parts": [
            {"text": "thinking...", "thought": True},
            {"text": "answer"},
        ]}, "finishReason": "STOP"}],
    }, "m")
    assert resp2["choices"][0]["message"]["reasoning"] == "thinking..."
    assert resp2["choices"][0]["message"]["content"] == "answer"


def test_gemini_sse_translator():
    from app import adapters

    t = adapters.GeminiSSETranslator("m")
    chunks = []
    for line in [
        'data: {"candidates":[{"content":{"parts":[{"text":"hel"}]}}]}',
        'data: {"candidates":[{"content":{"parts":[{"text":"lo"}]}}]}',
        'data: {"candidates":[{"content":{"parts":[{"text":""}]},"finishReason":"STOP"}],'
        '"usageMetadata":{"promptTokenCount":2,"candidatesTokenCount":2}}',
    ]:
        out = t.convert(line)
        if out:
            chunks.extend(out.split("\n\n"))
    texts = "".join(
        json.loads(c[6:])["choices"][0]["delta"].get("content", "")
        for c in chunks if c.startswith("data: ")
    )
    assert texts == "hello"
    assert t.usage_in == 2 and t.usage_out == 2
    finishes = [json.loads(c[6:])["choices"][0]["finish_reason"]
                for c in chunks if c.startswith("data: ")]
    assert "stop" in finishes


# ------------------------------------------------------------------ 6. analytics
def test_analytics_timeseries(client, fake_upstream):
    _add_provider(client, "fake", "http://fake.local/v1")
    _add_model(client, 1, "m")
    token = _client_token(client)
    hdr = {"Authorization": f"Bearer {token}"}
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}

    # 2 successes + 1 cache hit -> analytics rows exist
    client.post("/v1/chat/completions", headers=hdr, json=body)
    client.post("/v1/chat/completions", headers=hdr, json=body)  # cache
    client.post(
        "/v1/chat/completions", headers=hdr,
        json={"model": "m", "messages": [{"role": "user", "content": "other"}]},
    )

    r = client.get("/admin/api/analytics?hours=24", headers=auth())
    assert r.status_code == 200, r.text
    data = r.json()
    total_reqs = sum(b["requests"] for b in data["timeseries"])
    total_cache = sum(b["cache_hits"] for b in data["timeseries"])
    assert total_reqs == 3, data
    assert total_cache == 1
    top = data["top_models"][0]
    assert top["name"] == "m" and top["requests"] == 3

    # stats endpoint exposes the new counters
    stats = client.get("/admin/api/stats", headers=auth()).json()
    assert stats["cache_hits_24h"] == 1
    assert "batches_active" in stats


# ------------------------------------------------------------------ 7. hedging
class SlowUpstream:
    """Primary provider hangs; hedge answers fast."""

    is_closed = False

    def __init__(self):
        self.requests = []

    async def post(self, url, headers=None, json=None):
        self.requests.append((url, json))

        async def delayed():
            await asyncio.sleep(10)  # way past HEDGE_DELAY
            return FakeResp(_chat_body((json or {}).get("model", "?")))

        return await delayed()

    async def aclose(self):
        pass


def test_hedging_slow_primary_loses_race(client, monkeypatch):
    monkeypatch.setenv("NOVA_HEDGE_DELAY", "0.3")
    # re-import config so HEDGE_DELAY is picked up
    from app import config

    config.HEDGE_DELAY = 0.3

    _add_provider(client, "slow", "http://slow.local/v1", priority=1)
    _add_provider(client, "fast", "http://fast.local/v1", priority=2)
    from app import db

    for pid, model in ((1, "m"), (2, "m")):
        db.execute(
            "INSERT INTO models (provider_id, model_id, exposed_id, status, checked_at, latency_ms) "
            "VALUES (?,?,?,?,?,?)",
            (pid, model, "m", "OK", time.time(), 10),
        )
    token = _client_token(client)

    from app import gateway

    class Split:
        """slow provider hangs; fast provider answers."""

        is_closed = False

        def __init__(self):
            self.requests = []

        async def post(self, url, headers=None, json=None):
            self.requests.append((url, json))
            if "slow" in url:
                await asyncio.sleep(10)
                return FakeResp(_chat_body("slow-m"))
            return FakeResp(_chat_body("fast-m", content="from hedge"))

        async def aclose(self):
            pass

    fake = Split()
    monkeypatch.setattr(gateway, "_client", fake)

    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "from hedge"
    assert r.json()["model"] == "m", "spoofed id preserved"
    assert r.json()["_nova"]["hedged"] is True
