"""Media routing tests — the second half of the fallback promise:

A client selects any model and sends media (image / audio / video / PDF).
When the selected model can't read that media, NovaRouter silently serves
the request with a capable stand-in model — and reports the requested id
back, so the agent never sees an error or a model switch.

Uses a scripted fake upstream so no network is touched."""
import base64
import json
import time
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

ADMIN_TOKEN = "test-admin-token"

PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _caps(**kw):
    from app.adapters import CAP_KEYS

    return json.dumps({k: bool(kw.get(k, False)) for k in CAP_KEYS})


@pytest.fixture()
def client(monkeypatch):
    import tempfile

    tmp = tempfile.mkdtemp(prefix="nova-media-")
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


def _setup(client, blind_caps=None, seeing_caps=None):
    """fake provider + key + a vision-blind model and a vision-capable one."""
    blind_caps = blind_caps if blind_caps is not None else {"tools": True}
    seeing_caps = (
        seeing_caps if seeing_caps is not None else {"tools": True, "vision": True}
    )
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
        "INSERT INTO models (provider_id, model_id, exposed_id, status, checked_at, capabilities) "
        "VALUES (?,?,?,?,?,?)",
        (1, "blind-model", "blind-model", "OK", time.time(), _caps(**blind_caps)),
    )
    db.execute(
        "INSERT INTO models (provider_id, model_id, exposed_id, status, checked_at, capabilities) "
        "VALUES (?,?,?,?,?,?)",
        (1, "seeing-model", "seeing-model", "OK", time.time(), _caps(**seeing_caps)),
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


def _chat_body(model, content="described the image"):
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


class FakeStreamResp:
    """Minimal httpx streaming response: one SSE chunk + [DONE]."""

    status_code = 200

    def __init__(self, model):
        chunk = {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {"content": "described the image"}, "finish_reason": None}],
        }
        self._lines = [
            "data: " + json.dumps(chunk),
            "data: [DONE]",
        ]

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""

    async def aclose(self):
        pass


class FakeUpstream:
    """Echoes which model served the call; records every request body."""

    is_closed = False

    def __init__(self):
        self.requests = []

    async def post(self, url, headers=None, json=None):
        self.requests.append((url, json))
        return FakeResp(_chat_body((json or {}).get("model", "?")))

    def build_request(self, method, url, headers=None, json=None):
        return {"method": method, "url": url, "headers": headers, "json": json}

    async def send(self, req, stream=False):
        self.requests.append((req["url"], req["json"]))
        return FakeStreamResp((req["json"] or {}).get("model", "?"))

    async def aclose(self):
        pass


@pytest.fixture()
def fake_upstream(client, monkeypatch):
    from app import gateway

    fake = FakeUpstream()
    monkeypatch.setattr(gateway, "_client", fake)
    yield fake


def _img_message():
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": "what is this?"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_1PX}"}},
        ],
    }


# ---------------------------------------------------------------- detection

def test_blind_model_reroutes_to_seeing_model(client, fake_upstream):
    """The core scenario: image + vision-blind model -> seeing stand-in
    serves it -> response still claims the requested id."""
    token = _setup(client)
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "blind-model", "messages": [_img_message()]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "blind-model", "spoofed id must match the request"
    assert body["_nova"]["upstream_model"] == "seeing-model"
    # the capable model actually got the call
    assert any(
        (b or {}).get("model") == "seeing-model" for _u, b in fake_upstream.requests
    )


def test_seeing_model_serves_own_request(client, fake_upstream):
    """No reroute when the selected model can read the media."""
    token = _setup(client)
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "seeing-model", "messages": [_img_message()]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "seeing-model"
    assert body["_nova"]["upstream_model"] == "seeing-model"
    assert all(
        (b or {}).get("model") == "seeing-model" for _u, b in fake_upstream.requests
    )


def test_log_records_the_truth(client, fake_upstream):
    """The operator sees the reroute in the request log (via column)."""
    token = _setup(client)
    client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "blind-model", "messages": [_img_message()]},
    )
    from app import store

    log = store.recent_logs(5)[0]
    assert log["model"] == "blind-model"
    assert log["via"] == "seeing-model"


# ---------------------------------------------------------------- media shapes

def test_audio_input_reroutes(client, fake_upstream):
    token = _setup(
        client,
        seeing_caps={"tools": True, "vision": True, "audio_in": True},
    )
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "blind-model",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "transcribe this"},
                    {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "wav"}},
                ],
            }],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["_nova"]["upstream_model"] == "seeing-model"


def test_video_input_reroutes(client, fake_upstream):
    token = _setup(client)
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "blind-model",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this video"},
                    {"type": "video_url", "video_url": {"url": "https://x.local/v.mp4"}},
                ],
            }],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["_nova"]["upstream_model"] == "seeing-model"


def test_text_file_inlined_no_reroute(client, fake_upstream):
    """A plain-text file becomes a text part: even a blind model reads it."""
    token = _setup(client)
    txt = base64.b64encode(b"just some notes").decode()
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "blind-model",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "summarize this file"},
                    {"type": "file", "file": {"filename": "notes.txt", "file_data": f"data:text/plain;base64,{txt}"}},
                ],
            }],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["_nova"]["upstream_model"] == "blind-model", "text inlined, no reroute"
    # the inlined text actually reached the upstream
    sent = [b for _u, b in fake_upstream.requests if b and b.get("model") == "blind-model"]
    assert sent and "just some notes" in json.dumps(sent[-1]["messages"])


def test_pdf_file_reroutes_to_vision_model(client, fake_upstream):
    """A pdf file part can't be inlined -> vision-capable stand-in serves."""
    token = _setup(client)
    pdf = base64.b64encode(b"%PDF-1.4 fake").decode()
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "blind-model",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "read this pdf"},
                    {"type": "file", "file": {"filename": "doc.pdf", "file_data": f"data:application/pdf;base64,{pdf}"}},
                ],
            }],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["_nova"]["upstream_model"] == "seeing-model"


def test_image_file_becomes_image_url(client, fake_upstream):
    """An image *file* part is normalized to an image_url part upstream."""
    token = _setup(client)
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "seeing-model",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {"type": "file", "file": {"filename": "pic.png", "file_data": f"data:image/png;base64,{PNG_1PX}"}},
                ],
            }],
        },
    )
    assert r.status_code == 200, r.text
    sent = [b for _u, b in fake_upstream.requests if b and b.get("model") == "seeing-model"]
    assert sent and json.dumps(sent[-1]["messages"]).count("image_url") >= 1


# ---------------------------------------------------------------- endpoints

def test_anthropic_messages_media_routing(client, fake_upstream):
    """/v1/messages gets the same reroute for anthropic-shape image blocks."""
    token = _setup(client)
    r = client.post(
        "/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "blind-model",
            "max_tokens": 100,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_1PX}},
                ],
            }],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "blind-model", "spoofed id"
    assert body["type"] == "message"
    served = {(b or {}).get("model") for _u, b in fake_upstream.requests}
    assert "seeing-model" in served


def test_responses_api_media_routing(client, fake_upstream):
    """/v1/responses gets the same reroute via its chat translation."""
    token = _setup(client)
    r = client.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "blind-model",
            "input": [{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "what is this?"},
                    {"type": "input_image", "image_url": f"data:image/png;base64,{PNG_1PX}"},
                ],
            }],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "blind-model"
    served = {(b or {}).get("model") for _u, b in fake_upstream.requests}
    assert "seeing-model" in served


# ---------------------------------------------------------------- opt-out + failure

def test_media_routing_opt_out(client, fake_upstream, monkeypatch):
    """NOVA_MEDIA_ROUTING=0 keeps the old behavior (blind model gets the call)."""
    token = _setup(client)
    from app import config

    monkeypatch.setattr(config, "MEDIA_ROUTING", False)
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "blind-model", "messages": [_img_message()]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["_nova"]["upstream_model"] == "blind-model"


def test_no_capable_stand_in_falls_through(client, fake_upstream):
    """No vision model anywhere -> normal flow serves with the requested
    model (upstream may reject; the gateway doesn't crash)."""
    token = _setup(client, seeing_caps={"tools": True})  # seeing-model is also blind
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "blind-model", "messages": [_img_message()]},
    )
    # the fake upstream serves anything; what matters: no 5xx from the gateway
    assert r.status_code == 200, r.text
    assert r.json()["_nova"]["upstream_model"] == "blind-model"


def test_streaming_request_reroutes(client, fake_upstream):
    """Streaming chat requests get the same media reroute."""
    token = _setup(client)
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "blind-model", "stream": True, "messages": [_img_message()]},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    # the capable model actually got the call
    assert any(
        (b or {}).get("model") == "seeing-model" for _u, b in fake_upstream.requests
    )
    # chunks carry the requested (spoofed) model id
    assert 'chat.completion.chunk' in r.text
    assert '"blind-model"' in r.text and '"seeing-model"' not in r.text


def test_client_key_restrictions_respected(client, fake_upstream):
    """A client key limited to the blind model never reroutes past its list."""
    token = _setup(client)
    from app import store

    # restrict the client key to blind-model only
    store.update_client_key(1, allowed_models="blind-model")
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "blind-model", "messages": [_img_message()]},
    )
    assert r.status_code == 200, r.text
    # reroute candidates were all disallowed -> the blind model itself served
    assert r.json()["_nova"]["upstream_model"] == "blind-model"
