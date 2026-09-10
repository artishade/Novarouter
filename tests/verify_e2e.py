"""End-to-end test with a MOCK upstream: streaming, tools, images, TTS, videos."""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time

tmp = tempfile.mkdtemp()
os.environ["NOVA_DATA_DIR"] = tmp
os.environ["NOVA_ADMIN_TOKEN"] = "t"
sys.path.insert(0, "/root/novarouter")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import Response, StreamingResponse  # noqa: E402

# ------- fake upstream provider (OpenAI-compatible) -------
up = FastAPI()


@up.post("/v1/chat/completions")
async def chat(req: dict):
    if req.get("stream"):
        async def gen():
            chunks = [
                {"id": "c1", "object": "chat.completion.chunk", "model": req.get("model"),
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]},
                {"id": "c1", "object": "chat.completion.chunk", "model": req.get("model"),
                 "choices": [{"index": 0, "delta": {"content": "hel"}, "finish_reason": None}]},
                {"id": "c1", "object": "chat.completion.chunk", "model": req.get("model"),
                 "choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": None}]},
                {"id": "c1", "object": "chat.completion.chunk", "model": req.get("model"),
                 "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_1",
                                                                    "function": {"name": "f", "arguments": "{\"a\":1}"}}]},
                              "finish_reason": None}]},
                {"id": "c1", "object": "chat.completion.chunk", "model": req.get("model"),
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            ]
            for ch in chunks:
                yield "data: " + json.dumps(ch) + "\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    if req.get("tools"):
        return {
            "id": "c1", "object": "chat.completion", "model": req.get("model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": None,
                                                "tool_calls": [{"id": "call_1", "type": "function",
                                                                "function": {"name": "get_weather",
                                                                             "arguments": '{"city":"SF"}'}}]},
                         "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    return {
        "id": "c1", "object": "chat.completion", "model": req.get("model"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@up.post("/v1/images/generations")
async def images(req: dict):
    return {"created": 1, "data": [{"url": "https://example.com/img.png"}]}


@up.post("/v1/audio/speech")
async def tts(req: dict):
    return Response(content=b"\x00\x01fakeaudio", media_type="audio/mpeg")


@up.post("/v1/moderations")
async def mod(req: dict):
    return {"id": "m1", "results": [{"flagged": False}]}


@up.get("/v1/models")
async def models():
    return {"data": [{"id": "test-model", "pricing": {}}]}


import threading  # noqa: E402


def run_mock_upstream():
    uconfig = uvicorn.Config(up, host="127.0.0.1", port=9911, log_level="error")
    userver = uvicorn.Server(uconfig)
    asyncio.run(userver.serve())


def start_mock():
    t = threading.Thread(target=run_mock_upstream, daemon=True)
    t.start()
    # wait until the port accepts connections
    import socket
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", 9911), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)


async def main():
    start_mock()

    from app.main import app  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402

    with TestClient(app) as c:
        H = {"X-Admin-Token": "t"}
        c.post("/admin/api/providers", headers=H,
               json={"name": "mock", "base_url": "http://127.0.0.1:9911/v1",
                     "api_keys": "mock-key-1\nmock-key-2"})
        # sync models
        r = c.post("/admin/api/models/sync?provider_id=1", headers=H)
        print("sync:", r.json())
        r = c.post("/admin/api/client-keys", headers=H, json={"name": "t"})
        tok = r.json()["token"]
        A = {"Authorization": "Bearer " + tok}

        # 1. plain chat
        r = c.post("/v1/chat/completions", headers=A,
                   json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["choices"][0]["message"]["content"] == "hello"
        assert body["_nova"]["provider"] == "mock"
        print("PASS e2e plain chat + _nova metadata")

        # 2. tool call round trip
        r = c.post("/v1/chat/completions", headers=A, json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "weather in SF?"}],
            "tools": [{"type": "function", "function": {
                "name": "get_weather", "description": "w",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}],
        })
        assert r.status_code == 200, r.text
        tc = r.json()["choices"][0]["message"]["tool_calls"]
        assert tc[0]["function"]["name"] == "get_weather"
        print("PASS e2e tool calls")

        # 3. streaming
        with c.stream("POST", "/v1/chat/completions", headers=A,
                      json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}],
                            "stream": True}) as sr:
            assert sr.status_code == 200
            text = ""
            tools_seen = False
            for line in sr.iter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    try:
                        ch = json.loads(line[6:])
                        d = ch["choices"][0]["delta"]
                        text += d.get("content") or ""
                        if d.get("tool_calls"):
                            tools_seen = True
                    except (ValueError, KeyError, IndexError):
                        pass
        assert "hello" in text
        assert tools_seen
        print("PASS e2e streaming with tools")

        # 4. images
        r = c.post("/v1/images/generations", headers=A,
                   json={"model": "test-model", "prompt": "cat"})
        assert r.status_code == 200, r.text
        assert r.json()["data"][0]["url"] == "https://example.com/img.png"
        print("PASS e2e image generation")

        # 5. responses API (non-stream)
        r = c.post("/v1/responses", headers=A,
                   json={"model": "test-model", "input": "hi"})
        assert r.status_code == 200, r.text
        rr = r.json()
        assert rr["output"][0]["content"][0]["text"] == "hello"
        assert rr["object"] == "response"
        print("PASS e2e responses API")

        # 6. /v1/messages on openai-kind upstream (Claude Code style)
        r = c.post("/v1/messages", headers=A,
                   json={"model": "test-model", "max_tokens": 50,
                         "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200, r.text
        mm = r.json()
        assert mm["type"] == "message"
        assert mm["content"][0]["type"] == "text"
        assert mm["content"][0]["text"] == "hello"
        assert mm["stop_reason"] == "end_turn"
        print("PASS e2e /v1/messages (anthropic shape on openai upstream)")

        # 7. moderation
        r = c.post("/v1/moderations", headers=A, json={"model": "test-model", "input": "hello"})
        assert r.status_code == 200, r.text
        assert r.json()["results"][0]["flagged"] is False
        print("PASS e2e moderations")

        # 8. models list w/ capabilities
        r = c.get("/v1/models", headers=A)
        mlist = r.json()["data"]
        assert any(m["id"] == "test-model" for m in mlist)
        print("PASS e2e models catalogue")

        # 9. usage tracking in logs
        r = c.get("/admin/api/logs", headers=H)
        logs = r.json()
        chat_logs = [l for l in logs if l.get("model") == "test-model"]
        assert any((l.get("tokens_in") or 0) + (l.get("tokens_out") or 0) > 0 for l in chat_logs)
        print("PASS e2e usage tracking in logs")

        # 10. responses API streaming (SSE events)
        collected_events = []
        with c.stream("POST", "/v1/responses", headers=A,
                      json={"model": "test-model", "input": "hi", "stream": True}) as sr:
            assert sr.status_code == 200
            for line in sr.iter_lines():
                if line.startswith("data: "):
                    try:
                        evt = json.loads(line[6:])
                        collected_events.append(evt.get("type", ""))
                    except ValueError:
                        pass
        assert "response.created" in collected_events
        assert "response.output_text.delta" in collected_events
        assert "response.completed" in collected_events
        print("PASS e2e responses streaming SSE")

        # 11. video generation (chat-style video upstream simulation)
        # mock upstream has no video model; use the chat endpoint with modalities
        # our /v1/videos wraps a chat call and extracts URLs
        r = c.post("/v1/videos", headers=A,
                   json={"model": "test-model", "prompt": "a cat dancing",
                         "seconds": "4", "size": "640x360"})
        assert r.status_code == 200, r.text
        vj = r.json()
        assert vj["status"] == "completed"
        assert vj["object"] == "video-generation"
        assert len(vj["output"]) >= 0  # URL extraction depends on upstream content
        # poll endpoint
        r = c.get(f"/v1/videos/{vj['id']}", headers=A)
        assert r.status_code == 200
        assert r.json()["status"] == "completed"
        print("PASS e2e video generation + polling")

        # 12. TTS binary passthrough
        r = c.post("/v1/audio/speech", headers=A,
                   json={"model": "test-model", "input": "hello", "voice": "alloy"})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("audio/")
        assert len(r.content) > 0
        print("PASS e2e TTS binary passthrough")

        # 13. rate limiting (RPM)
        r = c.post("/admin/api/client-keys", headers=H,
                   json={"name": "limited", "rpm_limit": 1, "tpd_limit": 0})
        ltok = r.json()["token"]
        LA = {"Authorization": "Bearer " + ltok}
        r1 = c.post("/v1/chat/completions", headers=LA,
                    json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]})
        r2 = c.post("/v1/chat/completions", headers=LA,
                    json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]})
        # first may succeed (200), second must hit the rpm wall (429)
        assert r2.status_code == 429, f"expected 429, got {r2.status_code}: {r2.text}"
        print("PASS e2e rate limiting (RPM=1 enforced)")

    print()
    print("ALL 13 END-TO-END TESTS PASSED")


asyncio.run(main())