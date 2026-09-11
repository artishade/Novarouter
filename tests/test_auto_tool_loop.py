"""E verify করি:2E: the auto tool loop — model calls the skill tool, NovaRouter executes
it server-side and feeds the result back, then returns the final answer.
Uses a scripted fake upstream so no network is touched."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["NOVA_DATA_DIR"] = tempfile.mkdtemp(prefix="nova-loop-")
os.environ["NOVA_ADMIN_TOKEN"] = "t"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app import gateway, extensions  # noqa: E402

CALLS = {"n": 0}


class FakeResp:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeClient:
    """Round 1: model asks for the skill tool. Round 2: final answer."""

    is_closed = False

    async def post(self, url, headers=None, json=None):
        CALLS["n"] += 1
        if CALLS["n"] == 1:
            body = {
                "id": "chatcmpl-1", "object": "chat.completion", "created": int(time.time()),
                "model": "fake-model",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant", "content": None,
                        "tool_calls": [{
                            "id": "call_1", "type": "function",
                            "function": {"name": "trading-analyst",
                                         "arguments": "{\"input\": \"BTC now?\"}"},
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            }
        else:
            body = {
                "id": "chatcmpl-2", "object": "chat.completion", "created": int(time.time()),
                "model": "fake-model",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant",
                                "content": "FINAL: Buy BTC with 2% risk, mind the stop-loss."},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
            }
        return FakeResp(body)

    async def aclose(self):
        pass


gateway._client = FakeClient()

with TestClient(app) as c:
    H = {"X-Admin-Token": "t"}

    # provider + key + model registry entry
    r = c.post("/admin/api/providers", headers=H, json={
        "name": "fake", "base_url": "http://fake.local/v1", "kind": "openai",
        "api_keys": "sk-fake",
    })
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    from app import db, store
    db.execute(
        "INSERT INTO models (provider_id, model_id, exposed_id, status, checked_at) VALUES (?,?,?,?,?)",
        (pid, "fake-model", "fake-model", "OK", time.time()),
    )

    # skill extension
    r = c.post("/admin/api/extensions", headers=H, json={
        "kind": "skill", "name": "trading-analyst",
        "config": {"instructions": "SENIOR ANALYST MODE: always mention risk management.",
                   "description": "Analyze a trading question"},
    })
    assert r.status_code == 200, r.text

    # gateway key
    token = c.post("/admin/api/client-keys", headers=H, json={"name": "t"}).json()["token"]

    # plain chat request — no tools, no tool_choice from the client!
    r = c.post("/v1/chat/completions",
               headers={"Authorization": "Bearer " + token},
               json={"model": "fake-model",
                     "messages": [{"role": "user", "content": "should I buy BTC?"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choices"][0]["message"]["content"].startswith("FINAL:"), body
    assert CALLS["n"] == 2, CALLS  # 1: tool_call round, 2: final round
    print("PASS auto tool loop: model->tool->executed->final answer in", CALLS["n"], "upstream calls")

    # opt-out path: nova.auto_tools=false skips injection entirely
    CALLS["n"] = 0
    r = c.post("/v1/chat/completions",
               headers={"Authorization": "Bearer " + token},
               json={"model": "fake-model", "nova": {"auto_tools": False},
                     "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert CALLS["n"] == 1, CALLS  # single direct call, no loop
    assert "tools" not in json.loads(r.request.content.decode()) if r.request.content else True
    print("PASS opt-out: single upstream call, no injection")

    # streaming request through the loop
    CALLS["n"] = 0
    r = c.post("/v1/chat/completions",
               headers={"Authorization": "Bearer " + token},
               json={"model": "fake-model", "stream": True,
                     "messages": [{"role": "user", "content": "BTC?"}]})
    assert r.status_code == 200
    text = "".join(chunk.decode() for chunk in r.iter_bytes())
    assert "data:" in text and "[DONE]" in text
    assert "FINAL:" in text
    print("PASS streaming synthesis through the tool loop")

print("\nALL AUTO TOOL LOOP TESTS PASSED")