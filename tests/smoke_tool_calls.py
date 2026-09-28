"""Tool-use round-trip tests for the Claude Code (/v1/messages) path.

Boots the app with a STUB NovaFree engine that answers with an OpenAI-style
tool_call, then asserts the gateway converts it into a valid Anthropic stream /
JSON — the exact contract Claude Code depends on for agent turns.

    python3 tests/smoke_tool_calls.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        failures.append(f"{label}{(' — ' + detail) if detail else ''}")
        print(f"  FAIL {label}{(' — ' + detail) if detail else ''}")


ENGINE_PORT = 3098  # different from the real sidecar port (3099)
TOOL_CALL = {
    "id": "call_stub_1",
    "type": "function",
    "function": {"name": "get_weather", "arguments": '{"city": "Dhaka"}'},
}


class StubEngine(BaseHTTPRequestHandler):
    """OpenAI-shaped engine that always answers with one tool_call."""

    def log_message(self, *args):  # silence
        return

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}
        stream = payload.get("stream") is True
        wants_tools = bool(payload.get("tools"))
        tool_args = payload.get("tool_choice")
        model = payload.get("model")

        def send(obj, event=None):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if event else "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if event:
                self.wfile.write(f"event: {event}\ndata: ".encode() + data + b"\n\n")
            else:
                self.wfile.write(data)

        # The engine must receive the converted OpenAI tools + tool_choice.
        captured = {
            "tools": wants_tools,
            "tool_choice": tool_args,
            "model": model,
        }
        StubEngine.captured = captured  # type: ignore[attr-defined]

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [
                {"id": "chatcmpl-stub", "object": "chat.completion.chunk", "created": 0, "model": model or "stub",
                 "_nova": {"upstream_model": "stub/free-model", "provider": "Stub", "stage": 9},
                 "choices": [{"index": 0, "delta": {}, "finish_reason": None}]},
                {"id": "chatcmpl-stub", "object": "chat.completion.chunk", "created": 0, "model": model or "stub",
                 "_nova": {"upstream_model": "stub/free-model", "provider": "Stub", "stage": 9},
                 "choices": [{"index": 0, "delta": {"content": "Checking the weather."}, "finish_reason": None}]},
                {"id": "chatcmpl-stub", "object": "chat.completion.chunk", "created": 0, "model": model or "stub",
                 "_nova": {"upstream_model": "stub/free-model", "provider": "Stub", "stage": 9},
                 "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, **TOOL_CALL}]}, "finish_reason": None}]},
                {"id": "chatcmpl-stub", "object": "chat.completion.chunk", "created": 0, "model": model or "stub",
                 "_nova": {"upstream_model": "stub/free-model", "provider": "Stub", "stage": 9},
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            ]
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            body = {
                "id": "chatcmpl-stub", "object": "chat.completion", "created": 0, "model": model or "stub",
                "_nova": {"upstream_model": "stub/free-model", "provider": "Stub", "stage": 9},
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "Checking the weather.", "tool_calls": [TOOL_CALL]},
                    "finish_reason": "tool_calls",
                }],
            }
            send(body)


def start_stub() -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", ENGINE_PORT), StubEngine)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def main() -> int:
    srv = start_stub()
    import os

    os.environ["NOVA_ENGINE_PORT"] = str(ENGINE_PORT)
    # Re-import with the stub port (config reads the env at import time).
    import nova.config as _cfg
    _cfg.ENGINE_SIDECAR_PORT = ENGINE_PORT
    _cfg.ENGINE_SIDECAR_URL = f"http://127.0.0.1:{ENGINE_PORT}"

    import nova.engine as ne
    ne.ENGINE_SIDECAR_PORT = ENGINE_PORT
    ne.ENGINE_SIDECAR_URL = f"http://127.0.0.1:{ENGINE_PORT}"

    import main as app_module
    app = app_module.app

    print("· booting app against the stub engine")
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://nova.test", timeout=60.0) as client:
            # Make sure the pipeline reaches the engine: no upstream providers
            # exist in a fresh DB, so stage-1 upstreams are skipped and the
            # engine is the final stage.
            tools = [{
                "name": "get_weather",
                "description": "Get current weather for a city",
                "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
            }]
            base = {
                "model": "nova/air",
                "max_tokens": 256,
                "tools": tools,
                "messages": [{"role": "user", "content": "What's the weather in Dhaka? Use the tool."}],
            }

            # ---- streaming tool call ------------------------------------
            print("· POST /v1/messages stream with tools → tool_use block")
            events: list[tuple[str, dict]] = []
            async with client.stream("POST", "/v1/messages", json={**base, "stream": True},
                                     headers={"anthropic-version": "2023-06-01"}) as res:
                check(res.status_code == 200, "stream status 200", str(res.status_code))
                current = None
                async for line in res.aiter_lines():
                    if line.startswith("event: "):
                        current = line[7:].strip()
                    elif line.startswith("data: ") and current:
                        events.append((current, json.loads(line[6:])))
                        if current == "message_stop":
                            break
            kinds = [k for k, _ in events]
            check("error" not in kinds, "no error event", json.dumps(events[:2])[:200])

            # block sequencing: text block closes before tool block starts
            starts = [d for k, d in events if k == "content_block_start"]
            stops = [d for k, d in events if k == "content_block_stop"]
            text_start_idx = next((i for i, (k, d) in enumerate(events) if k == "content_block_start"
                                   and (d.get("content_block") or {}).get("type") == "text"), None)
            tool_start_idx = next((i for i, (k, d) in enumerate(events) if k == "content_block_start"
                                   and (d.get("content_block") or {}).get("type") == "tool_use"), None)
            check(tool_start_idx is not None, "tool_use content_block_start present")
            text_stop_idx = next((i for i, (k, d) in enumerate(events) if k == "content_block_stop"), None)
            if text_start_idx is not None and tool_start_idx is not None and text_stop_idx is not None:
                check(text_start_idx < text_stop_idx < tool_start_idx,
                      "strict block order: text start → text stop → tool start",
                      f"start@{text_start_idx} stop@{text_stop_idx} tool@{tool_start_idx}")
            check(len(stops) == len(starts), f"every opened block closes exactly once ({len(starts)} starts / {len(stops)} stops)",
                  json.dumps([k for k, _ in events]))

            tool_block = starts[1]["content_block"] if len(starts) > 1 else {}
            check(tool_block.get("type") == "tool_use" and tool_block.get("name") == "get_weather",
                  "tool_use block carries the tool name", json.dumps(tool_block))
            json_deltas = "".join(
                (d.get("delta") or {}).get("partial_json") or ""
                for k, d in events if k == "content_block_delta" and (d.get("delta") or {}).get("type") == "input_json_delta"
            )
            check(json.loads(json_deltas).get("city") == "Dhaka" if json_deltas else False,
                  "input_json_delta reassembles the tool arguments", json_deltas[:120])
            stop_delta = next((d for k, d in events if k == "message_delta"), {})
            check((stop_delta.get("delta") or {}).get("stop_reason") == "tool_use",
                  "stop_reason=tool_use", json.dumps(stop_delta)[:120])

            # engine received converted tools?
            cap = getattr(StubEngine, "captured", {})
            check(cap.get("tools") is True, "engine received OpenAI-format tools", json.dumps(cap)[:160])
            check(cap.get("tool_choice") in (None, "auto", {}), "tool_choice forwarded sanely", json.dumps(cap.get("tool_choice")))

            # ---- non-streaming tool call ---------------------------------
            print("· POST /v1/messages non-stream with tools → tool_use block")
            res = await client.post("/v1/messages", json=base, headers={"anthropic-version": "2023-06-01"})
            msg = res.json()
            check(res.status_code == 200, "non-stream status 200", str(res.status_code))
            blocks = msg.get("content") or []
            tool_blocks = [b for b in blocks if b.get("type") == "tool_use"]
            check(any(b.get("name") == "get_weather" for b in tool_blocks),
                  "tool_use block present in JSON message", json.dumps(blocks)[:200])
            check(all(b.get("input", {}).get("city") == "Dhaka" for b in tool_blocks),
                  "tool_use.input parses the arguments", json.dumps(tool_blocks)[:160])
            check(msg.get("stop_reason") == "tool_use", "stop_reason=tool_use (non-stream)", str(msg.get("stop_reason")))
            nova_meta = msg.get("_nova") or {}
            check(nova_meta.get("upstream_model") == "stub/free-model",
                  "attribution uses the engine _nova meta", json.dumps(nova_meta))

            # ---- OpenAI shape too ----------------------------------------
            print("· POST /v1/chat/completions with tools → tool_calls preserved")
            res = await client.post("/v1/chat/completions", json={**base, "messages": [{"role": "user", "content": "weather in Dhaka?"}]})
            payload = res.json()
            tcs = ((payload.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
            check(any(tc.get("function", {}).get("name") == "get_weather" for tc in tcs),
                  "OpenAI tool_calls preserved", json.dumps(tcs)[:160])
            check(((payload.get("choices") or [{}])[0].get("finish_reason")) == "tool_calls",
                  "OpenAI finish_reason=tool_calls", str((payload.get('choices') or [{}])[0].get('finish_reason')))

    srv.shutdown()
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all tool-call checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
