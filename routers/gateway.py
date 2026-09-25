"""OpenAI-compatible gateway — /v1/* (also mounted under /api/v1/*).

Endpoints (requirement #3):
  GET  /v1/models               list models (DB) — ?discover=1 live discovery
  POST /v1/chat/completions     streaming (SSE) + non-streaming, full fallback pipeline
  POST /v1/completions          legacy completions (prompt → chat pipeline)
  POST /v1/messages             Anthropic-format adapter (native passthrough for
                                anthropic-kind providers, pipeline otherwise)
  POST /v1/embeddings           embeddings via upstream providers

Routing pipeline (parity with the previous TypeScript implementation):
  Stage 1     — direct upstream (requested model, enabled key)
  Stage 2..N  — explicit fallback chain (ModelRoute publicId match, else '*')
  Final stage — NovaFree engine (z-ai sidecar, when available)

The response `model` field is ALWAYS the requested id (identity spoofing);
`_nova` metadata exposes what actually served the request. Every request is
logged to RequestLog.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from nova import engine as nova_engine
from nova.discovery import aggregate_discovery, discover_provider_models
from nova.models import ClientKey, Model as ModelRow, ModelRoute, Provider, RequestLog

log = logging.getLogger("nova.gateway")

router = APIRouter()

UPSTREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=20.0, pool=10.0)
NONSTREAM_READ_TIMEOUT = 30.0


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def now_ms() -> int:
    return int(time.time() * 1000)


def normalize_messages(raw: list[dict]) -> list[dict]:
    out = []
    for m in raw:
        role = m.get("role")
        role = role if role in ("system", "assistant", "user") else "user"
        content = m.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "\n".join(
                str(p.get("text", "")) for p in content
                if isinstance(p, dict) and "text" in p
            ).strip("\n")
        else:
            text = str(content or "")
        out.append({"role": role, "content": text})
    return out


def extract_upstream_content(payload: Any) -> str | None:
    try:
        choices = payload["choices"]
        if not isinstance(choices, list) or not choices:
            return None
        content = choices[0]["message"]["content"]
    except (KeyError, TypeError, IndexError):
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        joined = "\n".join(
            str(p.get("text", "")) for p in content if isinstance(p, dict) and "text" in p
        ).strip("\n")
        return joined or None
    return None


def find_model(db: Session, id_or_exposed: str) -> ModelRow | None:
    return db.scalars(
        select(ModelRow)
        .where((ModelRow.exposedId == id_or_exposed) | (ModelRow.modelId == id_or_exposed))
        .options(joinedload(ModelRow.provider))
        .order_by(ModelRow.id)
    ).first()


def resolve_chain(db: Session, requested: str) -> list[str]:
    route = db.scalars(
        select(ModelRoute).where(ModelRoute.publicId == requested, ModelRoute.enabled.is_(True))
    ).first()
    if route is None:
        route = db.scalars(
            select(ModelRoute).where(ModelRoute.publicId == "*", ModelRoute.enabled.is_(True))
        ).first()
    if route is None:
        return []
    try:
        parsed = json.loads(route.fallbacks)
        return [f for f in parsed if isinstance(f, str)] if isinstance(parsed, list) else []
    except Exception:
        return []


def write_log(db: Session, **p) -> None:
    try:
        db.add(RequestLog(
            model=p.get("model", ""), upstreamModel=p.get("upstream_model", ""),
            providerName=p.get("provider_name", ""), endpoint=p.get("endpoint", "/v1/chat/completions"),
            status=p.get("status", 200), latencyMs=p.get("latency_ms", 0),
            tokensIn=p.get("tokens_in", 0), tokensOut=p.get("tokens_out", 0),
            via=p.get("via", ""), spoofed=p.get("spoofed", False), error=p.get("error", ""),
        ))
        db.commit()
    except Exception:  # logging must never break the response
        db.rollback()


def nova_meta(upstream_model: str, provider_name: str, stage: int, requested: str, fallback: bool) -> dict:
    return {
        "upstream_model": upstream_model,
        "provider": provider_name,
        "fallback": fallback,
        "hedged": False,
        "cached": False,
        "spoofed": requested != upstream_model,
        "stage": stage,
    }


def build_headers() -> dict:
    return {"x-content-type-options": "nosniff"}


class UpstreamFailure(Exception):
    def __init__(self, detail: str, status: int = 0):
        super().__init__(detail)
        self.detail = detail
        self.status = status


# --------------------------------------------------------------------------- #
# Non-streaming upstream attempt (parity with attemptUpstream)
# --------------------------------------------------------------------------- #

async def attempt_upstream(row: ModelRow, messages: list[dict], temperature: float | None) -> dict | None:
    provider = row.provider
    if not provider or not provider.enabled or provider.kind in ("builtin", "anthropic"):
        return None
    base = provider.baseUrl.rstrip("/")
    if not base or base.startswith("internal://"):
        return None

    body: dict = {"model": row.modelId, "messages": messages}
    if temperature is not None:
        body["temperature"] = temperature

    import asyncio

    from nova.database import SessionLocal
    from nova.models import ProviderKey

    def _load_key():
        with SessionLocal() as s:
            from sqlalchemy import select as sel

            k = s.scalars(
                sel(ProviderKey)
                .where(ProviderKey.providerId == provider.id, ProviderKey.enabled.is_(True))
                .order_by(ProviderKey.weight.desc(), ProviderKey.id)
            ).first()
            if k is None:
                return None
            now = datetime.utcnow()
            if k.cooldownUntil and k.cooldownUntil > now:
                return {"apiKey": k.apiKey}  # last resort — same as TS parity
            return {"apiKey": k.apiKey}

    key_info = await asyncio.to_thread(_load_key)
    if not key_info:
        return None

    try:
        async with httpx.AsyncClient(timeout=NONSTREAM_READ_TIMEOUT) as client:
            res = await client.post(
                f"{base}/chat/completions",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {key_info['apiKey']}"},
                json=body,
            )
        if res.status_code < 200 or res.status_code >= 300:
            return None
        payload = res.json()
        content = extract_upstream_content(payload)
        if content is None:
            return None
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return {
            "content": content,
            "upstream_model": row.modelId,
            "upstream_exposed_id": row.exposedId,
            "provider_name": provider.name,
            "provider_id": provider.id,
            "via": "upstream",
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
            },
        }
    except httpx.HTTPError:
        return None


def select_upstream_key_sync(provider_id: int):
    """Direct key selection used by the streaming path."""
    from nova.database import SessionLocal
    from nova.models import ProviderKey

    with SessionLocal() as s:
        rows = s.scalars(
            select(ProviderKey)
            .where(ProviderKey.providerId == provider_id, ProviderKey.enabled.is_(True))
            .order_by(ProviderKey.weight.desc(), ProviderKey.id)
        ).all()
        now = datetime.utcnow()
        for k in rows:
            if not k.cooldownUntil or k.cooldownUntil <= now:
                return {"apiKey": k.apiKey}
        return {"apiKey": rows[0].apiKey} if rows else None


# --------------------------------------------------------------------------- #
# Streaming upstream attempt (SSE relay)
# --------------------------------------------------------------------------- #

async def attempt_upstream_stream(row: ModelRow, messages: list[dict], temperature: float | None):
    """Open an SSE stream to the upstream provider.

    Returns (generator, provider_name, upstream_model) or raises UpstreamFailure
    before anything is committed (connect errors / HTTP error status).
    """
    provider = row.provider
    if not provider or not provider.enabled or provider.kind in ("builtin", "anthropic"):
        raise UpstreamFailure("provider not streamable")
    key_info = select_upstream_key_sync(provider.id)
    if key_info is None:
        raise UpstreamFailure("no enabled key")
    base = provider.baseUrl.rstrip("/")
    if not base or base.startswith("internal://"):
        raise UpstreamFailure("no base url")

    body: dict = {"model": row.modelId, "messages": messages, "stream": True}
    if temperature is not None:
        body["temperature"] = temperature

    client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT)
    try:
        req = client.build_request(
            "POST", f"{base}/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key_info['apiKey']}"},
            json=body,
        )
        res = await client.send(req, stream=True)
    except httpx.HTTPError as err:
        await client.aclose()
        raise UpstreamFailure(f"connect failed: {err}") from err

    if res.status_code < 200 or res.status_code >= 300:
        await res.aclose()
        await client.aclose()
        raise UpstreamFailure(f"upstream returned HTTP {res.status_code}", status=res.status_code)

    async def relay() -> AsyncIterator[dict]:
        try:
            async for line in res.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except Exception:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                finish = choices[0].get("finish_reason")
                if text or finish:
                    yield {"text": text or "", "finish_reason": finish}
        finally:
            await res.aclose()
            await client.aclose()

    return relay, provider.name, row.modelId


# --------------------------------------------------------------------------- #
# Chat completions core
# --------------------------------------------------------------------------- #

def resolve_pipeline(db: Session, requested: str) -> list[ModelRow]:
    """Stage 1 row (direct) + stage 2..N rows (fallback chain)."""
    chain_ids = [f for f in resolve_chain(db, requested) if f != requested]
    rows: list[ModelRow] = []
    direct = find_model(db, requested)
    if direct is not None:
        rows.append(direct)
    for cid in chain_ids:
        row = find_model(db, cid)
        if row is not None:
            rows.append(row)
    return rows


async def pipeline_nonstream(db: Session, requested: str, messages: list[dict], temperature: float | None):
    """Returns (result_dict, stage) or (None, final_stage)."""
    started = now_ms()
    rows = resolve_pipeline(db, requested)
    stage = 0
    for row in rows:
        stage += 1
        result = await attempt_upstream(row, messages, temperature)
        if result is not None:
            result["stage"] = stage
            return result, stage

    # Final stage: NovaFree engine
    stage = len(rows) + 1
    try:
        completion = await nova_engine.chat(messages)
        content = ""
        try:
            content = completion["choices"][0]["message"]["content"]
        except (KeyError, TypeError, IndexError):
            content = str(completion)
        return {
            "content": content,
            "upstream_model": "nova-engine",
            "upstream_exposed_id": "nova-engine",
            "provider_name": "NovaFree Engine",
            "provider_id": None,
            "via": "nova-engine",
            "usage": {"prompt_tokens": None, "completion_tokens": None},
            "stage": stage,
        }, stage
    except nova_engine.EngineUnavailable as err:
        raise HTTPError(502, f"All routing stages failed: {err}") from err


class HTTPError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------- #
# GET /v1/models
# --------------------------------------------------------------------------- #

@router.get("/models")
async def list_models(request: Request, discover: str = "", provider: str = "", free: str = ""):
    from nova.database import SessionLocal

    with SessionLocal() as db:
        if discover in ("1", "true"):
            results = await discover_provider_models(db, provider.strip() or None)
            summary = aggregate_discovery(results)
            if free in ("1", "true"):
                summary["data"] = [m for m in summary["data"] if m.get("is_free")]
                summary["total_models"] = len(summary["data"])
            return JSONResponse(summary)

        rows = db.execute(
            select(ModelRow)
            .where(ModelRow.enabled.is_(True))
            .options(joinedload(ModelRow.provider))
            .order_by(ModelRow.providerId)
        ).scalars().unique().all()

        data = []
        for m in rows:
            if not m.provider.enabled:
                continue
            try:
                capabilities = json.loads(m.capabilities)
                if not isinstance(capabilities, dict):
                    capabilities = {}
            except Exception:
                capabilities = {}
            data.append({
                "id": m.exposedId,
                "object": "model",
                "owned_by": m.provider.key,
                "context_length": m.contextLength,
                "capabilities": capabilities,
                "is_free": m.isFree,
                "status": m.status,
            })
        return JSONResponse({"object": "list", "data": data})


# --------------------------------------------------------------------------- #
# POST /v1/chat/completions
# --------------------------------------------------------------------------- #

@router.post("/chat/completions")
async def chat_completions(request: Request):
    started = now_ms()
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    requested = str(body.get("model") or "").strip()
    raw_messages = body.get("messages")
    if not requested:
        return JSONResponse({"error": "Missing required parameter: 'model'"}, status_code=400)
    if not isinstance(raw_messages, list) or len(raw_messages) == 0:
        return JSONResponse({"error": "Missing required parameter: 'messages' (non-empty array)"}, status_code=400)

    messages = normalize_messages(raw_messages)
    temperature = body.get("temperature") if isinstance(body.get("temperature"), (int, float)) else None
    stream = body.get("stream") is True
    tokens_in = estimate_tokens("\n".join(f"{m['role']}:{m['content']}" for m in messages))

    from nova.database import SessionLocal

    if stream:
        return StreamingResponse(
            _chat_stream_response(requested, messages, temperature, tokens_in, started),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", **build_headers()},
        )

    with SessionLocal() as db:
        try:
            result, stage = await pipeline_nonstream(db, requested, messages, temperature)
        except HTTPError as err:
            write_log(db, model=requested, upstream_model="nova-engine",
                      provider_name="NovaFree Engine", endpoint="/v1/chat/completions",
                      status=err.status, latency_ms=now_ms() - started, via="nova-engine",
                      error=err.message)
            return JSONResponse({"error": err.message}, status_code=err.status)

        prompt_tokens = result["usage"]["prompt_tokens"] or tokens_in
        completion_tokens = result["usage"]["completion_tokens"] or estimate_tokens(result["content"])
        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                 "total_tokens": prompt_tokens + completion_tokens}
        meta = nova_meta(result["upstream_model"], result["provider_name"], result["stage"], requested,
                         result["stage"] > 1)
        write_log(db, model=requested, upstream_model=result["upstream_model"],
                  provider_name=result["provider_name"], status=200,
                  latency_ms=now_ms() - started, tokens_in=prompt_tokens,
                  tokens_out=completion_tokens, via=result["via"], spoofed=meta["spoofed"])

        return JSONResponse({
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": requested,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": result["content"]},
                         "finish_reason": "stop"}],
            "usage": usage,
            "_nova": meta,
        })


def sse_chunk(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


async def _chat_stream_response(requested: str, messages: list[dict], temperature, tokens_in: int, started: int):
    """Full pipeline with SSE output. Commits to a stage only after the first
    real content arrives; otherwise falls through to the next stage."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    def base_chunk() -> dict:
        return {"id": completion_id, "object": "chat.completion.chunk", "created": created,
                "model": requested, "choices": []}

    from nova.database import SessionLocal

    db = SessionLocal()
    committed = False
    assembled: list[str] = []
    served = {"upstream_model": "nova-engine", "provider_name": "NovaFree Engine", "via": "nova-engine", "stage": 0}
    try:
        rows = resolve_pipeline(db, requested)
        stage = 0
        for row in rows:
            stage += 1
            try:
                relay, provider_name, upstream_model = await attempt_upstream_stream(row, messages, temperature)
            except UpstreamFailure as err:
                log.info("stream stage %s failed: %s", stage, err)
                continue

            # Probe the first delta before committing.
            first = None
            async for piece in relay():
                if piece["text"] or piece["finish_reason"]:
                    first = piece
                    break

            if first is None or (not first["text"] and not first["finish_reason"]):
                continue

            committed = True
            served = {"upstream_model": upstream_model, "provider_name": provider_name,
                      "via": "upstream", "stage": stage}
            yield sse_chunk({**base_chunk(), "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]})
            if first["text"]:
                assembled.append(first["text"])
                yield sse_chunk({**base_chunk(), "choices": [{"index": 0, "delta": {"content": first["text"]}, "finish_reason": None}]})
            async for piece in relay():
                if piece["text"]:
                    assembled.append(piece["text"])
                    yield sse_chunk({**base_chunk(), "choices": [{"index": 0, "delta": {"content": piece["text"]}, "finish_reason": None}]})
                if piece["finish_reason"]:
                    break
            yield sse_chunk({**base_chunk(), "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            yield b"data: [DONE]\n\n"
            break

        if not committed:
            # Final stage: NovaFree engine (streamed).
            stage = len(rows) + 1
            served = {"upstream_model": "nova-engine", "provider_name": "NovaFree Engine",
                      "via": "nova-engine", "stage": stage}
            try:
                iterator = await nova_engine.chat_stream(messages)
                yield sse_chunk({**base_chunk(), "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]})
                async for data in iterator:
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except Exception:
                        continue
                    if not isinstance(chunk, dict) or chunk.get("error"):
                        raise nova_engine.EngineUnavailable(str(chunk.get("error", "engine error")))
                    choices = chunk.get("choices") or []
                    text = ""
                    if choices:
                        delta = choices[0].get("delta") or {}
                        text = delta.get("content") or ""
                        if not text:
                            msg = (choices[0].get("message") or {})
                            text = msg.get("content") or ""
                    if text:
                        assembled.append(text)
                        yield sse_chunk({**base_chunk(), "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]})
                yield sse_chunk({**base_chunk(), "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                yield b"data: [DONE]\n\n"
            except nova_engine.EngineUnavailable as err:
                msg = f"All routing stages failed: {err}. Fix checklist: (1) Dashboard → Providers → Test your provider; (2) Dashboard → Models → Sync models; (3) request a model id that exists there (e.g. \"nova/air\")."
                yield sse_chunk({"error": {"message": msg, "type": "gateway_error", "code": 502}})
                yield b"data: [DONE]\n\n"
    finally:
        content = "".join(assembled)
        if committed or assembled:
            write_log(db, model=requested, upstream_model=served["upstream_model"],
                      provider_name=served["provider_name"], status=200,
                      latency_ms=now_ms() - started,
                      tokens_in=tokens_in, tokens_out=estimate_tokens(content) or 1,
                      via=served["via"], spoofed=requested != served["upstream_model"])
        db.close()


# --------------------------------------------------------------------------- #
# POST /v1/completions — legacy
# --------------------------------------------------------------------------- #

@router.post("/completions")
async def legacy_completions(request: Request):
    started = now_ms()
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    requested = str(body.get("model") or "").strip()
    prompt = body.get("prompt")
    if not requested:
        return JSONResponse({"error": "Missing required parameter: 'model'"}, status_code=400)
    if prompt is None:
        return JSONResponse({"error": "Missing required parameter: 'prompt'"}, status_code=400)

    prompts = prompt if isinstance(prompt, list) else [prompt]
    if not prompts or not all(isinstance(p, str) for p in prompts):
        return JSONResponse({"error": "'prompt' must be a string or array of strings"}, status_code=400)
    if body.get("stream") is True and len(prompts) > 1:
        return JSONResponse({"error": "Streaming is supported for a single prompt only"}, status_code=400)

    temperature = body.get("temperature") if isinstance(body.get("temperature"), (int, float)) else None

    if body.get("stream") is True:
        return StreamingResponse(
            _legacy_stream(requested, prompts[0], temperature, started),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", **build_headers()},
        )

    from nova.database import SessionLocal

    choices = []
    total_in = total_out = 0
    with SessionLocal() as db:
        for i, p in enumerate(prompts):
            messages = normalize_messages([{"role": "user", "content": p}])
            try:
                result, stage = await pipeline_nonstream(db, requested, messages, temperature)
            except HTTPError as err:
                write_log(db, model=requested, upstream_model="nova-engine",
                          provider_name="NovaFree Engine", endpoint="/v1/completions",
                          status=err.status, latency_ms=now_ms() - started, via="nova-engine",
                          error=err.message)
                return JSONResponse({"error": err.message}, status_code=err.status)
            tin = result["usage"]["prompt_tokens"] or estimate_tokens(p)
            tout = result["usage"]["completion_tokens"] or estimate_tokens(result["content"])
            total_in += tin
            total_out += tout
            choices.append({"text": result["content"], "index": i, "finish_reason": "stop", "logprobs": None})
            if i == 0:
                served = result
        write_log(db, model=requested, upstream_model=served["upstream_model"],
                  provider_name=served["provider_name"], endpoint="/v1/completions",
                  status=200, latency_ms=now_ms() - started, tokens_in=total_in,
                  tokens_out=total_out, via=served["via"],
                  spoofed=requested != served["upstream_model"])

    return JSONResponse({
        "id": f"cmpl-{uuid.uuid4().hex[:24]}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": requested,
        "choices": choices,
        "usage": {"prompt_tokens": total_in, "completion_tokens": total_out,
                  "total_tokens": total_in + total_out},
        "_nova": nova_meta(served["upstream_model"], served["provider_name"], served["stage"], requested, served["stage"] > 1),
    })


async def _legacy_stream(requested: str, prompt: str, temperature, started: int):
    completion_id = f"cmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    messages = normalize_messages([{"role": "user", "content": prompt}])
    assembled: list[str] = []
    from nova.database import SessionLocal
    db = SessionLocal()
    committed = False
    served = {"upstream_model": "nova-engine", "provider_name": "NovaFree Engine", "via": "nova-engine", "stage": 0}
    try:
        rows = resolve_pipeline(db, requested)
        stage = 0
        for row in rows:
            stage += 1
            try:
                relay, provider_name, upstream_model = await attempt_upstream_stream(row, messages, temperature)
            except UpstreamFailure:
                continue
            first = None
            async for piece in relay():
                if piece["text"] or piece["finish_reason"]:
                    first = piece
                    break
            if first is None:
                continue
            committed = True
            served = {"upstream_model": upstream_model, "provider_name": provider_name, "via": "upstream", "stage": stage}

            def text_chunk(t: str, finish=None) -> bytes:
                return sse_chunk({"id": completion_id, "object": "text_completion.chunk",
                                  "created": created, "model": requested,
                                  "choices": [{"text": t, "index": 0, "finish_reason": finish, "logprobs": None}]})

            if first["text"]:
                assembled.append(first["text"])
                yield text_chunk(first["text"])
            async for piece in relay():
                if piece["text"]:
                    assembled.append(piece["text"])
                    yield text_chunk(piece["text"])
                if piece["finish_reason"]:
                    break
            yield text_chunk("", "stop")
            yield b"data: [DONE]\n\n"
            break

        if not committed:
            stage = len(rows) + 1
            served = {"upstream_model": "nova-engine", "provider_name": "NovaFree Engine", "via": "nova-engine", "stage": stage}
            try:
                result_content = ""
                completion = await nova_engine.chat(messages)
                try:
                    result_content = completion["choices"][0]["message"]["content"]
                except (KeyError, TypeError, IndexError):
                    result_content = str(completion)
                committed = True
                assembled.append(result_content)

                def text_chunk(t: str, finish=None) -> bytes:
                    return sse_chunk({"id": completion_id, "object": "text_completion.chunk",
                                      "created": created, "model": requested,
                                      "choices": [{"text": t, "index": 0, "finish_reason": finish, "logprobs": None}]})

                # chunk the completed text for a streaming experience
                step = 24
                for i in range(0, len(result_content), step):
                    yield text_chunk(result_content[i:i + step])
                yield text_chunk("", "stop")
                yield b"data: [DONE]\n\n"
            except nova_engine.EngineUnavailable as err:
                yield sse_chunk({"error": {"message": f"All routing stages failed: {err}", "type": "gateway_error", "code": 502}})
                yield b"data: [DONE]\n\n"
    finally:
        if assembled:
            write_log(db, model=requested, upstream_model=served["upstream_model"],
                      provider_name=served["provider_name"], endpoint="/v1/completions",
                      status=200, latency_ms=now_ms() - started,
                      tokens_in=estimate_tokens(prompt), tokens_out=estimate_tokens("".join(assembled)),
                      via=served["via"], spoofed=requested != served["upstream_model"])
        db.close()


# --------------------------------------------------------------------------- #
# POST /v1/messages — Anthropic-format adapter
# --------------------------------------------------------------------------- #

def anthropic_messages_to_openai(payload: dict) -> tuple[list[dict], str | None]:
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
    for m in payload.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role") if m.get("role") in ("user", "assistant") else "user"
        content = m.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "\n".join(
                str(b.get("text", "")) for b in content if isinstance(b, dict) and "text" in b
            ).strip("\n")
        else:
            text = str(content or "")
        msgs.append({"role": role, "content": text})
    return msgs, system_text


@router.post("/messages")
async def anthropic_messages(request: Request):
    started = now_ms()
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
    tokens_in = estimate_tokens("\n".join(f"{m['role']}:{m['content']}" for m in openai_msgs))

    from nova.database import SessionLocal

    if stream:
        return StreamingResponse(
            _messages_stream(requested, payload, openai_msgs, system_text, max_tokens, temperature, tokens_in, started),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", **build_headers()},
        )

    with SessionLocal() as db:
        # Stage 0: native Anthropic passthrough when the resolved provider speaks Anthropic.
        native = await _try_native_anthropic(db, requested, payload, openai_msgs, system_text, max_tokens, temperature)
        if native is not None:
            result = native
        else:
            try:
                result, stage = await pipeline_nonstream(db, requested, openai_msgs, temperature)
            except HTTPError as err:
                write_log(db, model=requested, upstream_model="nova-engine",
                          provider_name="NovaFree Engine", endpoint="/v1/messages",
                          status=err.status, latency_ms=now_ms() - started, via="nova-engine",
                          error=err.message)
                return JSONResponse({"type": "error", "error": {"type": "api_error", "message": err.message}},
                                    status_code=err.status)

        tin = result.get("usage", {}).get("input_tokens") or tokens_in
        tout = result.get("usage", {}).get("output_tokens") or estimate_tokens(result["content"])
        write_log(db, model=requested, upstream_model=result["upstream_model"],
                  provider_name=result["provider_name"], endpoint="/v1/messages",
                  status=200, latency_ms=now_ms() - started, tokens_in=tin,
                  tokens_out=tout, via=result.get("via", "upstream"),
                  spoofed=requested != result["upstream_model"])

        return JSONResponse({
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "model": requested,
            "content": [{"type": "text", "text": result["content"]}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": tin, "output_tokens": tout},
            "_nova": nova_meta(result["upstream_model"], result["provider_name"], result.get("stage", 1), requested, result.get("stage", 1) > 1),
        })


async def _try_native_anthropic(db: Session, requested: str, payload: dict, openai_msgs: list[dict],
                                system_text: str | None, max_tokens: int, temperature) -> dict | None:
    """Speak natively to an anthropic-kind provider when the requested model maps to one."""
    row = find_model(db, requested)
    if row is None or row.provider is None or row.provider.kind != "anthropic" or not row.provider.enabled:
        return None
    key_info = select_upstream_key_sync(row.provider.id)
    if key_info is None:
        return None
    base = row.provider.baseUrl.rstrip("/")
    if not base or base.startswith("internal://"):
        return None

    body: dict = {"model": row.modelId, "max_tokens": max_tokens, "messages": []}
    for m in openai_msgs:
        if m["role"] == "system":
            body["system"] = m["content"]
        else:
            body["messages"].append({"role": m["role"], "content": m["content"]})
    if system_text and "system" not in body:
        body["system"] = system_text
    if temperature is not None:
        body["temperature"] = temperature

    try:
        async with httpx.AsyncClient(timeout=NONSTREAM_READ_TIMEOUT) as client:
            res = await client.post(
                f"{base}/v1/messages",
                headers={"x-api-key": key_info["apiKey"], "anthropic-version": "2023-06-01",
                         "Content-Type": "application/json"},
                json=body,
            )
        if res.status_code < 200 or res.status_code >= 300:
            return None
        data = res.json()
    except httpx.HTTPError:
        return None

    content = "".join(
        str(b.get("text", "")) for b in data.get("content", []) if isinstance(b, dict) and b.get("type") == "text"
    )
    if not content:
        return None
    usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
    return {
        "content": content,
        "upstream_model": row.modelId,
        "provider_name": row.provider.name,
        "provider_id": row.provider.id,
        "via": "upstream-anthropic",
        "usage": {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")},
        "stage": 1,
    }


async def _messages_stream(requested: str, payload: dict, openai_msgs: list[dict], system_text: str | None,
                           max_tokens: int, temperature, tokens_in: int, started: int):
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    assembled: list[str] = []
    from nova.database import SessionLocal
    db = SessionLocal()
    committed = False
    served = {"upstream_model": "nova-engine", "provider_name": "NovaFree Engine", "via": "nova-engine", "stage": 0}

    def ev(event: str, data: dict) -> bytes:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")

    def start_events() -> list[bytes]:
        return [
            ev("message_start", {"type": "message_start", "message": {
                "id": msg_id, "type": "message", "role": "assistant", "model": requested,
                "content": [], "stop_reason": None, "usage": {"input_tokens": tokens_in, "output_tokens": 0}}}),
            ev("content_block_start", {"type": "content_block_start", "index": 0,
                                       "content_block": {"type": "text", "text": ""}}),
            ev("ping", {"type": "ping"}),
        ]

    def delta_ev(text: str) -> bytes:
        return ev("content_block_delta", {"type": "content_block_delta", "index": 0,
                                          "delta": {"type": "text_delta", "text": text}})

    def end_events(output_tokens: int) -> list[bytes]:
        return [
            ev("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                                 "usage": {"output_tokens": output_tokens}}),
            ev("message_stop", {"type": "message_stop"}),
        ]

    try:
        # Native anthropic passthrough (streaming relay)
        row = find_model(db, requested)
        if row is not None and row.provider is not None and row.provider.kind == "anthropic" and row.provider.enabled:
            key_info = select_upstream_key_sync(row.provider.id)
            base = row.provider.baseUrl.rstrip("/")
            if key_info and base and not base.startswith("internal://"):
                body: dict = {"model": row.modelId, "max_tokens": max_tokens, "stream": True, "messages": []}
                for m in openai_msgs:
                    if m["role"] == "system":
                        body["system"] = m["content"]
                    else:
                        body["messages"].append({"role": m["role"], "content": m["content"]})
                if system_text and "system" not in body:
                    body["system"] = system_text
                if temperature is not None:
                    body["temperature"] = temperature
                client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT)
                try:
                    req = client.build_request(
                        "POST", f"{base}/v1/messages",
                        headers={"x-api-key": key_info["apiKey"], "anthropic-version": "2023-06-01",
                                 "Content-Type": "application/json"},
                        json=body)
                    res = await client.send(req, stream=True)
                except httpx.HTTPError:
                    res = None
                if res is not None and 200 <= res.status_code < 300:
                    committed = True
                    served = {"upstream_model": row.modelId, "provider_name": row.provider.name,
                              "via": "upstream-anthropic", "stage": 1}
                    yield start_events()[0]
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
                            text = (obj.get("delta") or {}).get("text", "")
                            if text:
                                assembled.append(text)
                                yield delta_ev(text)
                        elif t in ("message_stop", "message_delta"):
                            continue
                    await res.aclose()
                    await client.aclose()
                    yield b"".join(end_events(estimate_tokens("".join(assembled))))
                elif res is not None:
                    await res.aclose()
                    await client.aclose()

        if not committed:
            rows = resolve_pipeline(db, requested)
            stage = 0
            for r in rows:
                stage += 1
                try:
                    relay, provider_name, upstream_model = await attempt_upstream_stream(r, openai_msgs, temperature)
                except UpstreamFailure:
                    continue
                first = None
                async for piece in relay():
                    if piece["text"] or piece["finish_reason"]:
                        first = piece
                        break
                if first is None:
                    continue
                committed = True
                served = {"upstream_model": upstream_model, "provider_name": provider_name,
                          "via": "upstream", "stage": stage}
                yield start_events()[0]
                if first["text"]:
                    assembled.append(first["text"])
                    yield delta_ev(first["text"])
                async for piece in relay():
                    if piece["text"]:
                        assembled.append(piece["text"])
                        yield delta_ev(piece["text"])
                    if piece["finish_reason"]:
                        break
                yield b"".join(end_events(estimate_tokens("".join(assembled))))
                break

        if not committed:
            stage = len(resolve_pipeline(db, requested)) + 1
            served = {"upstream_model": "nova-engine", "provider_name": "NovaFree Engine",
                      "via": "nova-engine", "stage": stage}
            try:
                for chunk in start_events():
                    yield chunk
                iterator = await nova_engine.chat_stream(openai_msgs)
                async for data in iterator:
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except Exception:
                        continue
                    if not isinstance(obj, dict) or obj.get("error"):
                        raise nova_engine.EngineUnavailable(str(obj.get("error", "engine error")))
                    choices = obj.get("choices") or []
                    text = ""
                    if choices:
                        delta = choices[0].get("delta") or {}
                        text = delta.get("content") or ""
                        if not text:
                            text = (choices[0].get("message") or {}).get("content") or ""
                    if text:
                        assembled.append(text)
                        yield delta_ev(text)
                yield b"".join(end_events(estimate_tokens("".join(assembled))))
            except nova_engine.EngineUnavailable as err:
                yield ev("error", {"type": "error", "error": {"type": "api_error",
                                                             "message": f"All routing stages failed: {err}"}})
    finally:
        if assembled:
            write_log(db, model=requested, upstream_model=served["upstream_model"],
                      provider_name=served["provider_name"], endpoint="/v1/messages",
                      status=200, latency_ms=now_ms() - started, tokens_in=tokens_in,
                      tokens_out=estimate_tokens("".join(assembled)), via=served["via"],
                      spoofed=requested != served["upstream_model"])
        db.close()


# --------------------------------------------------------------------------- #
# POST /v1/embeddings
# --------------------------------------------------------------------------- #

@router.post("/embeddings")
async def embeddings(request: Request):
    started = now_ms()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    requested = str(body.get("model") or "").strip()
    raw_input = body.get("input")
    if not requested:
        return JSONResponse({"error": "Missing required parameter: 'model'"}, status_code=400)
    if raw_input is None:
        return JSONResponse({"error": "Missing required parameter: 'input'"}, status_code=400)

    inputs = raw_input if isinstance(raw_input, list) else [raw_input]
    inputs = [i if isinstance(i, str) else json.dumps(i) for i in inputs]

    from nova.database import SessionLocal

    with SessionLocal() as db:
        row = find_model(db, requested)
        if row is None or row.provider is None or not row.provider.enabled:
            return JSONResponse({
                "error": f"Model '{requested}' is not configured. Sync models in the dashboard or pick one from GET /v1/models.",
            }, status_code=404)
        provider = row.provider
        key_info = select_upstream_key_sync(provider.id)
        base = provider.baseUrl.rstrip("/")

        if provider.kind == "openai" and key_info and base and not base.startswith("internal://"):
            try:
                async with httpx.AsyncClient(timeout=NONSTREAM_READ_TIMEOUT) as client:
                    res = await client.post(
                        f"{base}/embeddings",
                        headers={"Authorization": f"Bearer {key_info['apiKey']}", "Content-Type": "application/json"},
                        json={"model": row.modelId, "input": inputs},
                    )
            except httpx.HTTPError as err:
                write_log(db, model=requested, provider_name=provider.name, endpoint="/v1/embeddings",
                          status=502, latency_ms=now_ms() - started, error=str(err))
                return JSONResponse({"error": f"Upstream embeddings call failed: {err}"}, status_code=502)
            if res.status_code < 200 or res.status_code >= 300:
                write_log(db, model=requested, provider_name=provider.name, endpoint="/v1/embeddings",
                          status=res.status_code, latency_ms=now_ms() - started,
                          error=f"upstream HTTP {res.status_code}")
                return JSONResponse({"error": f"Upstream returned HTTP {res.status_code} for embeddings"},
                                    status_code=502)
            data = res.json()
            write_log(db, model=requested, upstream_model=row.modelId, provider_name=provider.name,
                      endpoint="/v1/embeddings", status=200, latency_ms=now_ms() - started,
                      tokens_in=estimate_tokens(" ".join(inputs)), tokens_out=0, via="upstream",
                      spoofed=requested != row.modelId)
            return JSONResponse(data)

        if provider.kind == "gemini" and key_info and base and not base.startswith("internal://"):
            vectors: list[dict] = []
            try:
                async with httpx.AsyncClient(timeout=NONSTREAM_READ_TIMEOUT) as client:
                    for i, text in enumerate(inputs):
                        res = await client.post(
                            f"{base}/models/{row.modelId}:embedContent?key={key_info['apiKey']}",
                            json={"content": {"parts": [{"text": text}]}},
                        )
                        if res.status_code < 200 or res.status_code >= 300:
                            return JSONResponse(
                                {"error": f"Upstream returned HTTP {res.status_code} for embeddings"},
                                status_code=502)
                        values = res.json().get("embedding", {}).get("values", [])
                        vectors.append({"object": "embedding", "index": i, "embedding": values})
            except httpx.HTTPError as err:
                return JSONResponse({"error": f"Upstream embeddings call failed: {err}"}, status_code=502)
            write_log(db, model=requested, upstream_model=row.modelId, provider_name=provider.name,
                      endpoint="/v1/embeddings", status=200, latency_ms=now_ms() - started,
                      tokens_in=estimate_tokens(" ".join(inputs)), tokens_out=0, via="upstream",
                      spoofed=requested != row.modelId)
            return JSONResponse({
                "object": "list", "data": vectors, "model": requested,
                "usage": {"prompt_tokens": estimate_tokens(" ".join(inputs)), "total_tokens": estimate_tokens(" ".join(inputs))},
            })

        return JSONResponse({
            "error": ("Embeddings require an OpenAI-compatible or Gemini provider with a working key. "
                      f"'{requested}' resolved to provider '{provider.key}' (kind={provider.kind}) which cannot serve embeddings."),
        }, status_code=502)
