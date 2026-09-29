"""Anthropic-format adapter — POST /v1/messages (+ /v1/messages/count_tokens).

Claude Code / Claude Desktop speak the Anthropic Messages API. This module
converts that dialect to the gateway's OpenAI pipeline (and back), and speaks
natively to `anthropic`-kind providers when the requested model maps to one.

It lives in its own module (imported at the END of `routers/gateway.py`) so the
gateway file stays focused; it imports the shared pipeline helpers from there.

Protocol notes that matter for Claude Code:
  • Streaming MUST open the text `content_block_start` (index 0) before any
    `content_block_delta` — otherwise the Anthropic SDK assembles an empty
    message and agent turns silently produce nothing.
  • `tool_use` / `tool_result` blocks are preserved end-to-end; without them
    the agent loop dies on its second turn.
  • `thinking` / `redacted_thinking` history blocks are dropped (their crypto
    signature breaks on re-serialization upstream).
  • To keep the module importable before `gateway` finishes initialising, all
    gateway helpers are imported lazily inside the module-level `_deps()`.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from nova.provider_transport import anthropic_headers, anthropic_url, native_anthropic_body

router = APIRouter()


def _deps():
    """Gateway helpers, resolved lazily to keep the module import order safe."""
    from . import gateway as gw

    return gw


# --------------------------------------------------------------------------- #
# Anthropic ⇄ OpenAI conversion
# --------------------------------------------------------------------------- #

def anthropic_messages_to_openai(payload: dict) -> tuple[list[dict], str | None]:
    """Full-fidelity Anthropic → OpenAI conversion. Preserves tool_use /
    tool_result blocks (Claude Code/Desktop agent loops die without these)."""
    msgs: list[dict] = []
    system_text: str | None = None
    system = payload.get("system")
    if isinstance(system, str) and system.strip():
        system_text = system
    elif isinstance(system, list):
        system_text = "\n".join(
            str(b.get("text", "")) for b in system if isinstance(b, dict) and "text" in b
        ).strip() or None
    if system_text:
        msgs.append({"role": "system", "content": system_text})

    def _block_text(block) -> str:
        if not isinstance(block, dict):
            return ""
        c = block.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "\n".join(
                str(b.get("text", "")) for b in c if isinstance(b, dict) and "text" in b
            )
        return str(c or "")

    for m in payload.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role") if m.get("role") in ("user", "assistant") else "user"
        content = m.get("content")
        if isinstance(content, str):
            if content:
                msgs.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            continue

        blocks = [b for b in content if isinstance(b, dict)]
        # Strip thinking/redacted_thinking blocks from history — their crypto
        # signature breaks on re-serialization upstream (Anthropic accepts
        # text-only history, so dropping them is the safe, documented fix).
        blocks = [b for b in blocks if b.get("type") not in ("thinking", "redacted_thinking")]
        texts = [str(b.get("text", "")) for b in blocks if b.get("type") in (None, "text") and "text" in b]
        tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
        tool_results = [b for b in blocks if b.get("type") == "tool_result"]
        joined = "\n".join(texts).strip("\n")

        if role == "assistant":
            if tool_uses:
                msg: dict = {"role": "assistant", "content": joined or None, "tool_calls": []}
                for i, t in enumerate(tool_uses):
                    raw_input = t.get("input")
                    if isinstance(raw_input, str):
                        args = raw_input or "{}"
                    else:
                        try:
                            args = json.dumps(raw_input or {}, ensure_ascii=False)
                        except Exception:
                            args = "{}"
                    msg["tool_calls"].append({
                        "id": str(t.get("id") or f"call_{i}"),
                        "type": "function",
                        "function": {"name": str(t.get("name") or ""), "arguments": args},
                    })
                msgs.append(msg)
            elif joined:
                msgs.append({"role": "assistant", "content": joined})
        else:  # user
            if joined:
                msgs.append({"role": "user", "content": joined})
            for t in tool_results:
                msgs.append({
                    "role": "tool",
                    "tool_call_id": str(t.get("tool_call_id") or ""),
                    "content": _block_text(t),
                })
    return msgs, system_text


def anthropic_tools_to_openai(tools) -> list[dict] | None:
    """Anthropic tool defs → OpenAI function defs."""
    if not isinstance(tools, list) or not tools:
        return None
    out: list[dict] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        name = t.get("name")
        if not name or ("input_schema" not in t and t.get("type") not in (None, "custom")):
            continue
        out.append({
            "type": "function",
            "function": {
                "name": str(name),
                "description": str(t.get("description") or ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        })
    return out or None


def anthropic_tool_choice_to_openai(tool_choice):
    if not isinstance(tool_choice, dict):
        return None
    tc_type = tool_choice.get("type")
    if tc_type == "auto":
        return "auto"
    if tc_type == "any":
        return "required"
    if tc_type == "tool" and tool_choice.get("name"):
        return {"type": "function", "function": {"name": str(tool_choice["name"])}}
    return None


def openai_tool_calls_to_anthropic(tool_calls) -> list[dict]:
    """OpenAI tool_calls → Anthropic tool_use content blocks."""
    blocks: list[dict] = []
    for i, tc in enumerate(tool_calls or []):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                input_obj = json.loads(args) if args.strip() else {}
            except Exception:
                input_obj = {}
        else:
            input_obj = args or {}
        blocks.append({
            "type": "tool_use",
            "id": str(tc.get("id") or f"toolu_{uuid.uuid4().hex[:16]}"),
            "name": str(fn.get("name") or ""),
            "input": input_obj if isinstance(input_obj, dict) else {},
        })
    return blocks


# --------------------------------------------------------------------------- #
# POST /v1/messages/count_tokens
# --------------------------------------------------------------------------- #

@router.post("/messages/count_tokens")
async def anthropic_count_tokens(request: Request):
    """Claude Code calls /v1/messages/count_tokens?beta=true every turn.
    Without this route the client gets a 404 and agent sessions die."""
    gw = _deps()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    text = ""
    if isinstance(payload, dict):
        parts: list[str] = []
        system = payload.get("system")
        if isinstance(system, str):
            parts.append(system)
        elif isinstance(system, list):
            parts.extend(str(b.get("text", "")) for b in system if isinstance(b, dict))
        for m in payload.get("messages") or []:
            if isinstance(m, dict):
                c = m.get("content")
                if isinstance(c, str):
                    parts.append(c)
                elif isinstance(c, list):
                    parts.extend(str(b.get("text", "")) for b in c if isinstance(b, dict) and "text" in b)
        text = "\n".join(parts)
    return JSONResponse({"input_tokens": max(1, gw.estimate_tokens(text))})


# --------------------------------------------------------------------------- #
# POST /v1/messages
# --------------------------------------------------------------------------- #

@router.post("/messages")
async def anthropic_messages(request: Request):
    gw = _deps()
    started = gw.now_ms()
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid JSON body"}}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid body"}}, status_code=400)

    requested = str(payload.get("model") or "").strip()
    if not requested:
        return JSONResponse({"type": "error", "error": {"type": "invalid_request_error", "message": "Missing 'model'"}}, status_code=400)
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        return JSONResponse({"type": "error", "error": {"type": "invalid_request_error", "message": "Missing 'messages'"}}, status_code=400)

    stream = payload.get("stream") is True
    max_tokens = payload.get("max_tokens") if isinstance(payload.get("max_tokens"), int) else 4096
    temperature = payload.get("temperature") if isinstance(payload.get("temperature"), (int, float)) else None
    openai_msgs, system_text = anthropic_messages_to_openai(payload)
    tokens_in = gw.estimate_tokens("\n".join(f"{m['role']}:{m['content']}" for m in openai_msgs))
    openai_tools = anthropic_tools_to_openai(payload.get("tools"))
    openai_tool_choice = anthropic_tool_choice_to_openai(payload.get("tool_choice"))
    pipeline_body = dict(payload)
    if openai_tools:
        pipeline_body["tools"] = openai_tools
        if openai_tool_choice is not None:
            pipeline_body["tool_choice"] = openai_tool_choice

    from nova.database import SessionLocal

    if stream:
        return StreamingResponse(
            _messages_stream(requested, payload, openai_msgs, system_text, max_tokens, temperature, tokens_in, started,
                             openai_tools, openai_tool_choice, pipeline_body),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", **gw.build_headers()},
        )

    with SessionLocal() as db:
        # Stage 0: native Anthropic passthrough when the resolved provider speaks Anthropic.
        native = await _try_native_anthropic(db, requested, payload, openai_msgs, system_text, max_tokens, temperature)
        if native is not None:
            result = native
        else:
            try:
                result, stage = await gw.pipeline_nonstream(db, requested, openai_msgs, temperature, pipeline_body)
            except gw.HTTPError as err:
                gw.write_log(db, model=requested, upstream_model="nova-engine",
                             provider_name="NovaFree Engine", endpoint="/v1/messages",
                             status=err.status, latency_ms=gw.now_ms() - started, via="nova-engine",
                             error=err.message)
                return JSONResponse({"type": "error", "error": {"type": "api_error", "message": err.message}},
                                    status_code=err.status)

        tin = result.get("usage", {}).get("input_tokens") or tokens_in
        tout = result.get("usage", {}).get("output_tokens") or gw.estimate_tokens(result.get("content") or "")
        gw.write_log(db, model=requested, upstream_model=result["upstream_model"],
                     provider_name=result["provider_name"], endpoint="/v1/messages",
                     status=200, latency_ms=gw.now_ms() - started, tokens_in=tin,
                     tokens_out=tout, via=result.get("via", "upstream"),
                     spoofed=requested != result["upstream_model"])

        content_blocks: list[dict] = []
        text_out = gw.strip_think_blocks(result.get("content")) or ""
        if text_out:
            content_blocks.append({"type": "text", "text": text_out})
        if result.get("tool_blocks"):
            # native anthropic upstream already returns proper tool_use blocks
            content_blocks.extend(result["tool_blocks"])
        else:
            tc = result.get("tool_calls")
            if tc:
                content_blocks.extend(openai_tool_calls_to_anthropic(tc))
        stop_reason = result.get("stop_reason") or ("tool_use" if result.get("tool_calls") or result.get("tool_blocks") else "end_turn")

        return JSONResponse({
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "model": requested,
            "content": content_blocks or [{"type": "text", "text": ""}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": tin, "output_tokens": tout},
            "_nova": gw.nova_meta(result["upstream_model"], result["provider_name"], result.get("stage", 1), requested, result.get("stage", 1) > 1),
        })


async def _try_native_anthropic(db: Session, requested: str, payload: dict, openai_msgs: list[dict],
                                system_text: str | None, max_tokens: int, temperature) -> dict | None:
    """Speak natively to an anthropic-kind provider when the requested model maps to one."""
    gw = _deps()
    row = gw.find_model(db, requested)
    if row is None or row.provider is None or row.provider.kind != "anthropic" or not row.provider.enabled:
        return None
    key_info = await asyncio.to_thread(gw.select_upstream_key_sync, row.provider.id)
    if key_info is None:
        return None
    base = row.provider.baseUrl.rstrip("/")
    if not base or base.startswith("internal://"):
        return None

    body = native_anthropic_body(payload, row.modelId)

    try:
        async with httpx.AsyncClient(timeout=gw.NONSTREAM_READ_TIMEOUT) as client:
            res = await client.post(
                anthropic_url(base), headers=anthropic_headers(key_info["apiKey"]),
                json=body,
            )
        if res.status_code < 200 or res.status_code >= 300:
            return None
        data = res.json()
    except httpx.HTTPError:
        return None

    content = gw.strip_think_blocks("".join(
        str(b.get("text", "")) for b in data.get("content", []) if isinstance(b, dict) and b.get("type") == "text"
    ))
    usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
    # Preserve native tool_use blocks (Claude Code agent loops need these)
    tool_blocks = [
        b for b in data.get("content", [])
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    if not content and not tool_blocks:
        return None
    stop_reason = data.get("stop_reason") or ("tool_use" if tool_blocks else "end_turn")
    return {
        "content": content,
        "tool_blocks": tool_blocks,
        "stop_reason": stop_reason,
        "upstream_model": row.modelId,
        "provider_name": row.provider.name,
        "provider_id": row.provider.id,
        "via": "upstream-anthropic",
        "usage": {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")},
        "stage": 1,
    }


# --------------------------------------------------------------------------- #
# Streaming (/v1/messages, stream: true)
# --------------------------------------------------------------------------- #

async def _messages_stream(requested: str, payload: dict, openai_msgs: list[dict], system_text: str | None,
                           max_tokens: int, temperature, tokens_in: int, started: int,
                           openai_tools=None, openai_tool_choice=None, pipeline_body: dict | None = None):
    gw = _deps()
    if pipeline_body is None:
        pipeline_body = dict(payload)
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    assembled: list[str] = []
    from nova.database import SessionLocal
    db = SessionLocal()
    committed = False
    served = {"upstream_model": "nova-engine", "provider_name": "NovaFree Engine", "via": "nova-engine", "stage": 0}

    def ev(event: str, data: dict) -> bytes:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")

    def start_events() -> list[bytes]:
        """The complete Anthropic message preamble, pre-joined.

        message_start + the index-0 text `content_block_start` + a ping. It is
        returned as ONE element so every call site emits the whole preamble with
        a single yield — the text content block start used to be missing before
        the first delta, which makes the Anthropic SDK assemble an empty message.
        """
        return [
            ev("message_start", {"type": "message_start", "message": {
                "id": msg_id, "type": "message", "role": "assistant", "model": requested,
                "content": [], "stop_reason": None, "usage": {"input_tokens": tokens_in, "output_tokens": 0}}})
            + ev("content_block_start", {"type": "content_block_start", "index": 0,
                                         "content_block": {"type": "text", "text": ""}})
            + ev("ping", {"type": "ping"})
        ]

    def delta_ev(text: str) -> bytes:
        if text_block_closed:
            # Interleaved model (text after a tool call): open a fresh text block.
            nonlocal next_block, active_text_block
            block = next_block
            next_block += 1
            active_text_block = block
            return (
                ev("content_block_start", {"type": "content_block_start", "index": block,
                                           "content_block": {"type": "text", "text": ""}})
                + ev("content_block_delta", {"type": "content_block_delta", "index": block,
                                             "delta": {"type": "text_delta", "text": text}})
            )
        return ev("content_block_delta", {"type": "content_block_delta", "index": 0,
                                          "delta": {"type": "text_delta", "text": text}})
    anthro_think = gw.ThinkStreamFilter()

    tool_used = False
    text_block_closed = False
    active_text_block = 0  # block currently carrying text deltas
    closed_blocks: set = set()
    next_block = 1  # index 0 = text block
    openai_tc_index_to_block: dict = {}

    def close_text_block() -> bytes:
        """Close the open text block once (Anthropic blocks are strictly
        sequential — a stop must precede the next block's start)."""
        nonlocal text_block_closed
        if text_block_closed:
            return b""
        text_block_closed = True
        return ev("content_block_stop", {"type": "content_block_stop", "index": active_text_block})

    def tool_block_start(block_index: int, tool_id: str, name: str) -> bytes:
        # The text block must close before a tool_use block opens (Anthropic SSE
        # blocks are strictly sequential — a late text-block stop makes the
        # Claude SDK reject the stream).
        return close_text_block() + ev("content_block_start", {"type": "content_block_start", "index": block_index,
                                          "content_block": {"type": "tool_use", "id": tool_id, "name": name, "input": {}}})

    def end_events(output_tokens: int) -> list[bytes]:
        # Anthropic SSE requires strictly sequential content blocks: the text
        # block (index 0) must close BEFORE any tool_use block opens, and every
        # opened block closes exactly once (duplicates are tracked here).
        stops: list[bytes] = [close_text_block()]
        for blk in sorted(set(openai_tc_index_to_block.values())):
            if blk != 0 and blk not in closed_blocks:
                stops.append(ev("content_block_stop", {"type": "content_block_stop", "index": blk}))
                closed_blocks.add(blk)
        stops.append(ev("message_delta", {"type": "message_delta",
                                          "delta": {"stop_reason": "tool_use" if tool_used else "end_turn"},
                                          "usage": {"output_tokens": output_tokens}}))
        stops.append(ev("message_stop", {"type": "message_stop"}))
        return stops

    def handle_openai_tool_deltas(tc_deltas) -> list[bytes]:
        """OpenAI streaming tool_calls deltas → Anthropic tool_use events."""
        nonlocal tool_used, next_block, active_text_block
        out: list[bytes] = []
        for tc in tc_deltas or []:
            if not isinstance(tc, dict):
                continue
            oi = tc.get("index", 0)
            block = openai_tc_index_to_block.get(oi)
            if block is None:
                fn = tc.get("function") or {}
                block = next_block
                next_block += 1
                openai_tc_index_to_block[oi] = block
                tool_used = True
                active_text_block = 0
                out.append(tool_block_start(
                    block,
                    str(tc.get("id") or f"toolu_{uuid.uuid4().hex[:16]}"),
                    str(fn.get("name") or ""),
                ))
            args = (tc.get("function") or {}).get("arguments")
            if args:
                out.append(ev("content_block_delta", {"type": "content_block_delta", "index": block,
                                                      "delta": {"type": "input_json_delta", "partial_json": args}}))
        return out

    def relay_native_anthropic_events(obj: dict) -> list[bytes]:
        """Relay tool_use blocks from a native anthropic upstream stream."""
        nonlocal tool_used, next_block, active_text_block
        out: list[bytes] = []
        t = obj.get("type")
        if t == "content_block_start":
            cb = obj.get("content_block") or {}
            if cb.get("type") == "tool_use":
                tool_used = True
                active_text_block = 0
                out.append(tool_block_start(next_block, str(cb.get("id") or f"toolu_{uuid.uuid4().hex[:16]}"),
                                            str(cb.get("name") or "")))
                openai_tc_index_to_block[obj.get("index", next_block)] = next_block
                next_block += 1
        elif t == "content_block_delta":
            d = obj.get("delta") or {}
            if d.get("type") == "input_json_delta":
                block = openai_tc_index_to_block.get(obj.get("index"))
                if block is not None:
                    out.append(ev("content_block_delta", {"type": "content_block_delta", "index": block,
                                                          "delta": {"type": "input_json_delta",
                                                                    "partial_json": d.get("partial_json") or ""}}))
        elif t == "content_block_stop":
            block = openai_tc_index_to_block.get(obj.get("index"))
            if block is not None and block not in closed_blocks:
                closed_blocks.add(block)
                out.append(ev("content_block_stop", {"type": "content_block_stop", "index": block}))
        return out

    try:
        # Native anthropic passthrough (streaming relay)
        row = gw.find_model(db, requested)
        if row is not None and row.provider is not None and row.provider.kind == "anthropic" and row.provider.enabled:
            key_info = await asyncio.to_thread(gw.select_upstream_key_sync, row.provider.id)
            base = row.provider.baseUrl.rstrip("/")
            if key_info and base and not base.startswith("internal://"):
                body = native_anthropic_body(payload, row.modelId, stream=True)
                client = gw.UPSTREAM_CLIENT  # shared pooled client — warm connections
                try:
                    req = client.build_request(
                        "POST", anthropic_url(base), headers=anthropic_headers(key_info["apiKey"]),
                        json=body)
                    res = await client.send(req, stream=True)
                except httpx.HTTPError:
                    res = None
                if res is not None and 200 <= res.status_code < 300:
                    committed = True
                    served = {"upstream_model": row.modelId, "provider_name": row.provider.name,
                              "via": "upstream-anthropic", "stage": 1}
                    for start_chunk in start_events():
                        yield start_chunk
                    async for line in res.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:].strip()
                        try:
                            obj = json.loads(data)
                        except Exception:
                            continue
                        t = obj.get("type")
                        if t == "content_block_delta":
                            d = obj.get("delta") or {}
                            if d.get("type") == "input_json_delta":
                                for b in relay_native_anthropic_events(obj):
                                    yield b
                                continue
                            text = d.get("text", "")
                            if text:
                                filtered = anthro_think.feed(text)
                                if filtered:
                                    assembled.append(filtered)
                                    yield delta_ev(filtered)
                        elif t in ("content_block_start", "content_block_stop"):
                            for b in relay_native_anthropic_events(obj):
                                yield b
                        elif t in ("message_stop", "message_delta"):
                            continue
                    await res.aclose()
                    yield b"".join(end_events(gw.estimate_tokens("".join(assembled))))
                elif res is not None:
                    await res.aclose()

        if not committed:
            rows = gw.resolve_pipeline(db, requested)
            stage = 0
            for r in rows:
                stage += 1
                try:
                    stream, provider_name, upstream_model = await gw.attempt_upstream_stream(r, openai_msgs, temperature, pipeline_body)
                except gw.UpstreamFailure:
                    continue
                first = None
                async for piece in stream:
                    if piece["text"] or piece["finish_reason"] or piece.get("tool_calls"):
                        first = piece
                        break
                if first is None:
                    try:
                        await stream.aclose()
                    except Exception:
                        pass
                    continue
                committed = True
                served = {"upstream_model": upstream_model, "provider_name": provider_name,
                          "via": "upstream", "stage": stage}
                for start_chunk in start_events():
                    yield start_chunk
                if first.get("tool_calls"):
                    for b in handle_openai_tool_deltas(first["tool_calls"]):
                        yield b
                if first["text"]:
                    filtered = anthro_think.feed(first["text"])
                    if filtered:
                        assembled.append(filtered)
                        yield delta_ev(filtered)
                if first.get("finish_reason"):
                    yield b"".join(end_events(gw.estimate_tokens("".join(assembled))))
                    try:
                        await stream.aclose()
                    except Exception:
                        pass
                    break
                async for piece in stream:
                    if piece["text"]:
                        filtered = anthro_think.feed(piece["text"])
                        if filtered:
                            assembled.append(filtered)
                            yield delta_ev(filtered)
                    if piece.get("tool_calls"):
                        for b in handle_openai_tool_deltas(piece["tool_calls"]):
                            yield b
                    if piece["finish_reason"]:
                        break
                yield b"".join(end_events(gw.estimate_tokens("".join(assembled))))
                break

        if not committed:
            stage = len(gw.resolve_pipeline(db, requested)) + 1
            served = {"upstream_model": "nova-engine", "provider_name": "NovaFree Engine",
                      "via": "nova-engine", "stage": stage}
            try:
                for start_chunk in start_events():
                    yield start_chunk
                iterator = await gw.nova_engine.chat_stream(
                    openai_msgs, tools=openai_tools, tool_choice=openai_tool_choice, model=requested
                )
                async for data in iterator:
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    engine_meta = obj.get("_nova")
                    if isinstance(engine_meta, dict):
                        served["upstream_model"] = engine_meta.get("upstream_model") or served["upstream_model"]
                        served["provider_name"] = engine_meta.get("provider") or served["provider_name"]
                    if obj.get("error"):
                        raise gw.nova_engine.EngineUnavailable(str(obj.get("error", "engine error")))
                    choices = obj.get("choices") or []
                    text = ""
                    if choices:
                        delta = choices[0].get("delta") or {}
                        text = delta.get("content") or ""
                        for b in handle_openai_tool_deltas(delta.get("tool_calls")):
                            yield b
                        if not text:
                            text = (choices[0].get("message") or {}).get("content") or ""
                    if text:
                        filtered = anthro_think.feed(text)
                        if filtered:
                            assembled.append(filtered)
                            yield delta_ev(filtered)
                yield b"".join(end_events(gw.estimate_tokens("".join(assembled))))
            except gw.nova_engine.EngineUnavailable as err:
                detail = ("All routing stages failed: %s. Fix checklist: (1) Dashboard → Providers → Test your provider; "
                          "(2) Dashboard → Models → Sync models; (3) the built-in NovaFree engine runs the free "
                          "ai-models catalogue with no key, so a request should never reach this branch — check the "
                          "server log for the engine sidecar." % err)
                yield ev("error", {"type": "error", "error": {"type": "api_error", "message": detail}})
    finally:
        if assembled:
            gw.write_log(db, model=requested, upstream_model=served["upstream_model"],
                         provider_name=served["provider_name"], endpoint="/v1/messages",
                         status=200, latency_ms=gw.now_ms() - started, tokens_in=tokens_in,
                         tokens_out=gw.estimate_tokens("".join(assembled)), via=served["via"],
                         spoofed=requested != served["upstream_model"])
        db.close()


__all__ = [
    "anthropic_count_tokens",
    "anthropic_messages",
    "anthropic_messages_to_openai",
    "anthropic_tool_choice_to_openai",
    "anthropic_tools_to_openai",
    "openai_tool_calls_to_anthropic",
    "router",
]
