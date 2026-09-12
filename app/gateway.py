"""The gateway v2: OpenAI + Anthropic + media endpoints that fan out to real providers.

Failover chain per request:
    for each candidate provider that serves the model (healthiest first):
        for each key of that provider (round-robin, cooldown-aware):
            try -> on retryable error, move to next key / next provider

Endpoints:
  /v1/chat/completions        (openai shape, works on every provider kind)
  /v1/completions             (legacy text completions)
  /v1/embeddings              (vector embeddings)
  /v1/responses               (Responses API, translated to chat upstream)
  /v1/messages                (Anthropic Messages API, native + translated)
  /v1/images/generations      (image generation, b64/url results)
  /v1/images/edits            (image editing with an input image)
  /v1/videos                  (video generation via chat-style upstreams)
  /v1/videos/{id}             (poll video job status)
  /v1/audio/speech            (TTS, binary audio response)
  /v1/audio/transcriptions    (STT, multipart -> upstream JSON)
  /v1/audio/translations      (STT translate)
  /v1/moderations             (text moderation)
  /v1/models                  (catalogue with capabilities)
"""
import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import adapters, config, db, extensions, modality, store

router = APIRouter()

# status codes that justify retrying with another key / provider
RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504, 522, 524, 402, 401, 403}

_client: Optional[httpx.AsyncClient] = None

# video job registry (in-memory; serverless restarts lose it - acceptable)
_video_jobs: Dict[str, Dict[str, Any]] = {}


def http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.REQUEST_TIMEOUT, connect=config.CONNECT_TIMEOUT),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            follow_redirects=True,
        )
    return _client


async def close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None


# ------------------------------------------------------------------ auth
def bearer(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("x-api-key") or request.query_params.get("api_key") or "").strip()


def require_client(request: Request) -> Dict[str, Any]:
    token = bearer(request)
    client = store.auth_client(token)
    if not client:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "message": "Invalid or disabled gateway API key",
                    "type": "invalid_api_key",
                }
            },
        )
    reason = store.client_rate_limited(client)
    if reason:
        raise HTTPException(
            status_code=429,
            detail={"error": {"message": reason, "type": "rate_limit_exceeded"}},
        )
    return client


# ------------------------------------------------------------------ helpers
def _err(status: int, message: str, etype: str = "upstream_error") -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"message": message, "type": etype}})


def _anthropic_err(status: int, etype: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"type": "error", "error": {"type": etype, "message": message}})


async def _parse_json(request: Request) -> Tuple[Optional[Dict[str, Any]], Optional[JSONResponse]]:
    try:
        payload = await request.json()
    except Exception:
        return None, _err(400, "Request body must be valid JSON", "invalid_request_error")
    if not isinstance(payload, dict):
        return None, _err(400, "Request body must be a JSON object", "invalid_request_error")
    return payload, None


def _resolve_candidates(client: Dict[str, Any], requested: str) -> Tuple[
        Optional[List[Tuple[Dict[str, Any], str]]], Optional[JSONResponse], str]:
    """Auth-free part of dispatch: permission + resolution. Returns (candidates, error, lookup_id)."""
    if not requested:
        return None, _err(400, "Field 'model' is required", "invalid_request_error"), requested
    if not store.client_allows(client, requested):
        return None, _err(403, f"Key not allowed to use model '{requested}'", "permission_error"), requested
    # rate limit is per the *requested* id (suffixes included)
    base, suffix = adapters.split_model_suffix(requested)
    lookup = base if suffix else requested
    candidates = store.resolve_model(lookup)
    if not candidates:
        return None, _err(
            404,
            f"Model '{requested}' not found. Sync/refresh models in the dashboard, "
            f"or check GET /v1/models for available ids.",
            "model_not_found",
        ), requested
    return candidates, None, requested


def _fallback_stages(
    client: Dict[str, Any],
    requested: str,
    direct: List[Any],
    used_fallback_ids: Optional[Set[str]] = None,
) -> List[List[Tuple[Dict[str, Any], str]]]:
    """Build the ordered list of attempt stages after the direct one.

    Stage = list of (provider, upstream_model) pairs. The requested id is
    what gets reported back to the client no matter which stage serves,
    so stages don't need to carry it. Explicit route chain first, then
    (if allowed) auto-picked stand-ins. `used_fallback_ids` marks chain
    ids an earlier stage already covered.
    """
    if not store.client_allows(client, requested):
        return []
    stages: List[List[Tuple[Dict[str, Any], str]]] = []
    seen_providers: Set[Tuple[int, str]] = {
        (p["id"], m) for p, m in direct
    }
    used_fallback_ids = used_fallback_ids or set()
    chain, allow_auto = store.fallback_chain(requested)

    def stage_for(fid: str) -> List[Tuple[Dict[str, Any], str]]:
        out: List[Tuple[Dict[str, Any], str]] = []
        for provider, upstream_model in store.resolve_model(fid):
            if (provider["id"], upstream_model) in seen_providers:
                continue
            seen_providers.add((provider["id"], upstream_model))
            out.append((provider, upstream_model))
        return out

    for fid in chain:
        if fid == requested or fid in used_fallback_ids:
            continue
        stage = stage_for(fid)
        if stage:
            stages.append(stage)
    if allow_auto and config.AUTO_FALLBACK:
        for fid in store.auto_fallback_targets(requested, limit=config.FALLBACK_MAX):
            if fid == requested or fid in chain or fid in used_fallback_ids:
                continue
            stage = stage_for(fid)
            if stage:
                stages.append(stage)
    return stages


def _spoof_enabled(payload: Dict[str, Any]) -> bool:
    """Global flag on, no per-request opt-out."""
    if not config.SPOOF_MODEL:
        return False
    nova = payload.get("nova")
    if isinstance(nova, dict) and nova.get("spoof_model") is False:
        return False
    if payload.get("spoof_model") is False:
        return False
    return True


async def _attempt(
    provider: Dict[str, Any],
    key: Dict[str, Any],
    endpoint: str,
    payload: Dict[str, Any],
) -> Tuple[int, Any, str]:
    """Returns (http_status, parsed_body_or_text, error_message)."""
    try:
        url, headers, body = adapters.build_request(provider, key["api_key"], endpoint, payload)
    except ValueError as e:
        return 400, None, str(e)
    try:
        resp = await http_client().post(url, headers=headers, json=body)
    except httpx.TimeoutException:
        return 0, None, f"timeout contacting {provider['name']}"
    except Exception as e:  # noqa: BLE001
        return 0, None, f"{type(e).__name__}: {e}"[:200]
    if resp.status_code != 200:
        return resp.status_code, resp.text, adapters.extract_error(resp.status_code, resp.text)
    try:
        data = resp.json()
    except ValueError:
        return 502, resp.text, "upstream returned non-JSON body with HTTP 200"
    inline = adapters.response_has_error(data)
    if inline:
        return 502, data, inline
    if provider.get("kind") == "anthropic" and endpoint == "chat":
        data = adapters.anthropic_to_openai(data, payload.get("model", ""))
    return 200, data, ""


# ------------------------------------------------------------------ media routing
def _media_stage(
    client: Dict[str, Any],
    requested: str,
    payload: Dict[str, Any],
    direct: List[Tuple[Dict[str, Any], str]],
    endpoint: str = "chat",
) -> Tuple[Optional[List[Tuple[Dict[str, Any], str]]], bool]:
    """Detect media the selected model can't read and pick capable stand-ins.

    Returns (media_stage, rerouted) where media_stage is the list of
    (provider, upstream_model) pairs that CAN read the payload's media —
    inserted ahead of the normal stages so the request succeeds on the
    first try. rerouted is True when the requested model itself was
    displaced (its own candidates are unusable for this payload).

    Only chat-shaped endpoints reroute: embeddings/moderations never carry
    media parts, and rerouting them to a chat model would be wrong.
    """
    if not config.MEDIA_ROUTING or endpoint not in ("chat",):
        return None, False
    modality.prepare(payload)  # inline text docs so every model can read them
    needs = modality.detect(payload)
    if not needs:
        return None, False
    caps = store.model_row_capabilities(requested)
    if modality.covers(caps, needs):
        return None, False  # selected model reads this media just fine
    # selected model can't read the media: find stand-ins that can, and
    # require the client be allowed to use them
    out: List[Tuple[Dict[str, Any], str]] = []
    for fid in store.auto_fallback_targets(requested, limit=config.FALLBACK_MAX, need_caps={k: True for k in needs}):
        if not store.client_allows(client, fid):
            continue
        for provider, upstream_model in store.resolve_model(fid):
            if (provider["id"], upstream_model) not in {(p["id"], m) for p, m in direct}:
                out.append((provider, upstream_model))
    if not out:
        return None, False  # no capable stand-in: let the normal flow try anyway
    return out, True


# ------------------------------------------------------------------ dispatch core
async def dispatch(
    request: Optional[Request],
    endpoint: str,
    *,
    payload_override: Optional[Dict[str, Any]] = None,
    endpoint_label: str = "",
    client_override: Optional[Dict[str, Any]] = None,
    allow_suffix_fallback: bool = False,
) -> Any:
    """Core fan-out. `endpoint` selects the upstream path + translation.

    Requests run in stages: the requested model's own providers first, then
    (only if those are all unusable) the fallback chain, then auto-picked
    healthy stand-ins. The response always reports the requested model id,
    so clients never see the fallback that actually served them.
    """
    client = client_override or require_client(request)
    if payload_override is not None:
        payload = payload_override
    else:
        payload, err = await _parse_json(request)
        if err:
            return err
    requested = str(payload.get("model") or "")
    candidates, err, _ = _resolve_candidates(client, requested)
    direct = candidates or []

    # permission check happens even when the model is unknown (403 beats 404)
    if not requested:
        return _err(400, "Field 'model' is required", "invalid_request_error")
    if not store.client_allows(client, requested):
        return _err(403, f"Key not allowed to use model '{requested}'", "permission_error")

    stages: List[List[Tuple[Dict[str, Any], str]]] = []
    if direct:
        stages.append(list(direct))
        extra_stages = _fallback_stages(client, requested, direct)
    else:
        # unknown model: a route's chain or auto-pick may still save it
        chain, _ = store.fallback_chain(requested)
        used: Set[str] = set()
        for fid in chain:
            cands = store.resolve_model(fid)
            if cands:
                stages.append(list(cands))
                used.add(fid)
                break
        extra_stages = _fallback_stages(client, requested, [], used)
    if err is not None and not stages and not extra_stages:
        return err  # unknown everywhere, no fallback applies
    # media routing: when the payload carries media the requested model can't
    # read, capable stand-ins go FIRST — the request succeeds on attempt one
    media_stage, _media_rerouted = _media_stage(client, requested, payload, direct, endpoint)
    if media_stage:
        stages = [media_stage] + stages
    stages.extend(extra_stages)

    stream = bool(payload.get("stream"))
    tried: List[str] = []
    last_status, last_error = 502, "no upstream attempt succeeded"
    for stage in stages:
        for provider, upstream_model in stage:
            keys = store.pick_keys(provider["id"])
            if not keys:
                tried.append(f"{provider['name']}: no keys")
                continue
            upstream_payload = dict(payload)
            upstream_payload["model"] = upstream_model
            for key in keys:
                started = time.perf_counter()
                if stream and endpoint == "chat":
                    result = await _stream_attempt(
                        provider, key, upstream_payload, client, requested
                    )
                    if result is not None:
                        return result
                    last_status, last_error = 502, f"{provider['name']} stream failed"
                    tried.append(f"{provider['name']}#{key['id']}: stream fail")
                    continue
                status, body, error = await _attempt(provider, key, endpoint, upstream_payload)
                latency = int((time.perf_counter() - started) * 1000)
                tokens_in, tokens_out = adapters.extract_usage(body) if status == 200 else (0, 0)
                db.log_request(
                    client_key_id=client["id"],
                    provider_id=provider["id"],
                    upstream_key_id=key["id"],
                    model=requested,
                    endpoint=endpoint_label or endpoint,
                    status=status,
                    latency_ms=latency,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    error=error,
                    via=upstream_model if upstream_model != requested else "",
                )
                if status == 200:
                    store.reward_key(key["id"])
                    db.bump_client_usage(client["id"], tokens_in, tokens_out)
                    if isinstance(body, str):
                        return JSONResponse(status_code=200, content={"raw": body})
                    if isinstance(body, dict):
                        if _spoof_enabled(payload):
                            body["model"] = requested
                        else:
                            body.setdefault("model", requested)
                        body["_nova"] = {
                            "provider": provider["name"],
                            "upstream_model": upstream_model,
                            "latency_ms": latency,
                            "routed_from": requested if upstream_model != requested else "",
                        }
                    return JSONResponse(status_code=200, content=body)
                store.penalize_key(key["id"], status, error)
                last_status, last_error = status, error
                tried.append(f"{provider['name']}#{key['id']} -> {status}")
                # non-retryable errors end the request immediately — unless
                # fallbacks remain, since the model itself may be the problem
                if status not in RETRYABLE and not extra_stages:
                    return _err(status, error or f"upstream returned {status}", "invalid_request_error")
    return _err(
        last_status if last_status >= 400 else 502,
        f"All upstreams failed for '{requested}'. Last error: {last_error}. Tried: {', '.join(tried[:8])}",
    )


# ------------------------------------------------------------------ chat streaming
async def _stream_attempt(
    provider: Dict[str, Any],
    key: Dict[str, Any],
    payload: Dict[str, Any],
    client_row: Dict[str, Any],
    requested_model: str,
) -> Optional[StreamingResponse]:
    """Open an upstream SSE stream. Returns None if the upstream refused.

    Every chunk is rewritten to carry `requested_model` — the id the client
    asked for, not whatever fallback ended up serving.
    """
    try:
        url, headers, body = adapters.build_request(provider, key["api_key"], "chat", payload)
    except ValueError:
        return None
    is_anthropic = provider.get("kind") == "anthropic"
    translator = adapters.AnthropicSSETranslator(requested_model) if is_anthropic else None
    started = time.perf_counter()
    req = http_client().build_request("POST", url, headers=headers, json=body)
    try:
        resp = await http_client().send(req, stream=True)
    except Exception as e:  # noqa: BLE001
        store.penalize_key(key["id"], 0, f"{type(e).__name__}: {e}")
        return None

    if resp.status_code != 200:
        text = (await resp.aread()).decode(errors="replace")
        await resp.aclose()
        error = adapters.extract_error(resp.status_code, text)
        store.penalize_key(key["id"], resp.status_code, error)
        db.log_request(
            client_key_id=client_row["id"],
            provider_id=provider["id"],
            upstream_key_id=key["id"],
            model=requested_model,
            status=resp.status_code,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=error,
        )
        return None
    store.reward_key(key["id"])

    async def gen():
        tokens_out = 0
        try:
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if translator:
                    converted = translator.convert(line)
                    if converted:
                        yield converted + "\n\n"
                else:
                    out_line = adapters.spoof_sse_line(line, requested_model)
                    yield (out_line + "\n" if line.startswith(":") else out_line + "\n\n")
        finally:
            await resp.aclose()
            if translator:
                tokens_in, tokens_out = translator.usage_in, translator.usage_out
            else:
                tokens_in = 0
            db.log_request(
                client_key_id=client_row["id"],
                provider_id=provider["id"],
                upstream_key_id=key["id"],
                model=requested_model,
                endpoint="chat",
                status=200,
                latency_ms=int((time.perf_counter() - started) * 1000),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                via=payload.get("model", "") if payload.get("model") != requested_model else "",
            )
            db.bump_client_usage(client_row["id"], tokens_in, tokens_out)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Nova-Provider": provider["name"],
            "X-Accel-Buffering": "no",
        },
    )

# ------------------------------------------------------------------ /v1/tools
@router.get("/tools")
async def tools_catalogue(request: Request):
    """Discovery endpoint: which auto-active tools this gateway injects."""
    client = require_client(request)
    tools = extensions.catalogue()
    return {
        "object": "list",
        "auto_active": bool(config.TOOL_AUTO),
        "tools": tools,
        "usage": (
            "NovaRouter auto-injects these tools into POST /v1/chat/completions "
            "and auto-executes the model's tool calls server-side. No client-side "
            "tool wiring needed - just send a normal chat request. Opt out with "
            "{\"nova\":{\"auto_tools\":false}} in the request body."
        ),
        "_nova": {"client": client["name"]},
    }


@router.post("/tools/{tool_name}")
async def call_tool_direct(tool_name: str, request: Request):
    """Direct tool call (for testing / non-LLM automation)."""
    client = require_client(request)
    payload, err = await _parse_json(request)
    if err:
        return err
    ok, content = await extensions.call_tool(tool_name, payload, client["id"])
    return JSONResponse(
        status_code=200 if ok else 400,
        content={"tool": tool_name, "ok": ok, "output": content},
    )


# ------------------------------------------------------------------ /v1/models
@router.get("/models")
async def list_models(request: Request):
    client = require_client(request)
    rows = store.list_models(only_enabled=True)
    data = []
    seen = set()
    for r in rows:
        mid = r["exposed_id"]
        if mid in seen or not store.client_allows(client, mid):
            continue
        seen.add(mid)
        caps = store.model_capabilities(r)
        data.append(
            {
                "id": mid,
                "object": "model",
                "created": int(r["checked_at"] or time.time()),
                "owned_by": r["provider_name"],
                "context_length": r.get("context_length") or 0,
                "max_output_tokens": r.get("max_output") or 0,
                "capabilities": caps,
                "nova": {
                    "provider": r["provider_name"],
                    "upstream_id": r["model_id"],
                    "status": r["status"],
                    "latency_ms": r["latency_ms"],
                    "free": bool(r["is_free"]),
                    "thinking": caps.get("reasoning", False),
                    "vision": caps.get("vision", False),
                    "tools": caps.get("tools", False),
                    "image_gen": caps.get("image_gen", False),
                    "video": caps.get("video", False),
                    "audio": caps.get("tts", False),
                },
            }
        )
    # fallback route ids the client may ask for even when no provider serves
    # them directly — agents see these in their model pickers
    for route in store.list_routes():
        rid = route["public_id"]
        if rid == "*" or rid in seen or not store.client_allows(client, rid):
            continue
        seen.add(rid)
        route_caps = adapters.infer_capabilities(rid)
        data.append(
            {
                "id": rid,
                "object": "model",
                "created": int(route.get("created_at") or time.time()),
                "owned_by": "novarouter-route",
                "context_length": 0,
                "max_output_tokens": 0,
                "capabilities": route_caps,
                "nova": {
                    "provider": "fallback route",
                    "upstream_id": ",".join(route["fallback_list"][:3]) + ("…" if len(route["fallback_list"]) > 3 else ""),
                    "status": "OK",
                    "latency_ms": 0,
                    "free": False,
                    "thinking": route_caps.get("reasoning", False),
                    "vision": route_caps.get("vision", False),
                    "tools": route_caps.get("tools", False),
                    "image_gen": route_caps.get("image_gen", False),
                    "video": route_caps.get("video", False),
                    "audio": route_caps.get("tts", False),
                },
            }
        )
    return {"object": "list", "data": data}


# ------------------------------------------------------------------ auto tool loop
def _tool_calls_of(body: Any) -> List[Dict[str, Any]]:
    """Extract openai-shape tool_calls from a chat completion body."""
    try:
        msg = body["choices"][0]["message"]
        return list(msg.get("tool_calls") or [])
    except (KeyError, TypeError, IndexError):
        return []


def _nova_auto_enabled(payload: Dict[str, Any]) -> bool:
    """Client can opt out with nova.auto_tools=false; global switch must be on."""
    if not config.TOOL_AUTO:
        return False
    flag = payload.get("nova", {})
    if isinstance(flag, dict) and flag.get("auto_tools") is False:
        return False
    if payload.get("auto_tools") is False:
        return False
    return True


async def _tool_round(
    client: Dict[str, Any],
    messages: List[Dict[str, Any]],
    base: Dict[str, Any],
    requested: str,
) -> Optional[Dict[str, Any]]:
    """One buffered (non-streaming) chat round through the normal fan-out.
    Returns the openai-shaped body, or None when upstream failed."""
    payload = {**base, "messages": messages, "model": requested, "stream": False}
    result = await dispatch(
        None, "chat",
        payload_override=payload,
        client_override=client,
        endpoint_label="chat",
    )
    if isinstance(result, JSONResponse) and result.status_code == 200:
        try:
            return json.loads(result.body)
        except (ValueError, TypeError):
            return None
    return None


def _final_response(body: Dict[str, Any], stream_requested: bool) -> JSONResponse:
    """Return the finished body; synthesize an SSE stream if the client asked for one."""
    if not stream_requested:
        return JSONResponse(status_code=200, content=body)
    try:
        msg = body["choices"][0]["message"]
        finish = body["choices"][0].get("finish_reason") or "stop"
    except (KeyError, IndexError, TypeError):
        return JSONResponse(status_code=200, content=body)
    chunk_id = body.get("id") or f"chatcmpl-{uuid.uuid4().hex[:20]}"
    created = int(time.time())
    usage = body.get("usage") or {}

    def sse(delta: Dict[str, Any], finish: Optional[str] = None, with_usage: bool = False) -> str:
        obj: Dict[str, Any] = {
            "id": chunk_id, "object": "chat.completion.chunk", "created": created,
            "model": body.get("model", ""),
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if with_usage and usage:
            obj["usage"] = usage
        return "data: " + json.dumps(obj, ensure_ascii=False)

    text = msg.get("content") or ""

    async def gen():
        yield sse({"role": "assistant", "content": ""})
        for i in range(0, max(len(text), 1), 400):
            piece = text[i: i + 400]
            if piece:
                yield sse({"content": piece})
        if msg.get("tool_calls"):
            yield sse({"tool_calls": msg["tool_calls"]})
        yield sse({}, finish=finish, with_usage=True)
        yield "data: [DONE]"

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _auto_tool_chat(
    payload: Dict[str, Any], client: Dict[str, Any]
) -> Optional[JSONResponse]:
    """Agentic loop: inject Nova extension tools, auto-execute their calls.

    Returns None when it doesn't apply (opt-out / no tools), so the caller
    falls back to plain dispatch. Only Nova-added tools are auto-executed;
    any client-declared tool call is passed through untouched.
    """
    if not _nova_auto_enabled(payload):
        return None
    working = {k: v for k, v in payload.items() if k not in ("nova", "auto_tools")}
    working, added = extensions.inject_tools(working)
    if not added:
        return None
    stream_requested = bool(payload.get("stream"))
    requested = str(payload.get("model") or "")
    messages = list(working.get("messages") or [])
    base = {k: v for k, v in working.items() if k not in ("messages", "stream", "model")}

    for _ in range(max(1, config.TOOL_MAX_HOPS)):
        body = await _tool_round(client, messages, base, requested)
        if body is None:
            return None  # dispatch already logged + the error surfaced upstream
        calls = _tool_calls_of(body)
        if not calls:
            return _final_response(body, stream_requested)
        names = {(c.get("function") or {}).get("name") for c in calls}
        if not names or not names.issubset(added):
            # client-declared tools present -> not our business, pass through
            return _final_response(body, stream_requested)
        messages = messages + [body["choices"][0]["message"]]
        for c in calls:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (ValueError, TypeError):
                args = {}
            ok, content = await extensions.call_tool(fn.get("name") or "", args, client["id"])
            messages.append({
                "role": "tool",
                "tool_call_id": c.get("id") or "",
                "content": content,
            })
    # hop budget exhausted: ask once more without tools to force a final answer
    base.pop("tools", None)
    base.pop("tool_choice", None)
    body = await _tool_round(client, messages, base, requested)
    if body is None:
        return _err(502, "tool loop exhausted and the final round failed")
    return _final_response(body, stream_requested)


# ------------------------------------------------------------------ chat endpoints
@router.post("/chat/completions")
async def chat_completions(request: Request):
    if config.TOOL_AUTO and extensions.has_enabled_tools():
        client = require_client(request)
        payload, err = await _parse_json(request)
        if err:
            return err
        auto = await _auto_tool_chat(payload, client)
        if auto is not None:
            return auto
        return await dispatch(request, "chat", payload_override=payload, client_override=client)
    return await dispatch(request, "chat")


@router.post("/completions")
async def completions(request: Request):
    return await dispatch(request, "completions")


@router.post("/embeddings")
async def embeddings(request: Request):
    return await dispatch(request, "embeddings")


# ------------------------------------------------------------------ Responses API
@router.post("/responses")
async def responses_endpoint(request: Request):
    client = require_client(request)
    payload, err = await _parse_json(request)
    if err:
        return err
    requested = str(payload.get("model") or "")
    candidates, err, _ = _resolve_candidates(client, requested)
    # NB: `err` is only acted on after fallback staging below — an unknown
    # model may still be served through its route chain
    stream = bool(payload.get("stream"))
    chat_payload = adapters.responses_to_chat(payload)
    # carry the spoof opt-out through the translation (responses_to_chat
    # rebuilds the body and would otherwise drop the nova flag)
    nova = payload.get("nova")
    if isinstance(nova, dict) and nova.get("spoof_model") is False:
        chat_payload["nova"] = {"spoof_model": False}
    if payload.get("spoof_model") is False:
        chat_payload["spoof_model"] = False
    if not stream:
        # reuse the dispatch machinery on the translated payload, then
        # re-wrap the chat response back into Responses API shape.
        result = await dispatch(
            request, "chat",
            payload_override=chat_payload,
            client_override=client,
            endpoint_label="responses",
        )
        if isinstance(result, JSONResponse) and result.status_code == 200:
            try:
                chat_body = json.loads(result.body)
            except (ValueError, TypeError):
                return result
            if isinstance(chat_body, dict) and chat_body.get("object") == "chat.completion":
                meta = chat_body.pop("_nova", None)
                resp_body = adapters.chat_to_responses(chat_body, requested)
                if meta:
                    resp_body["_nova"] = meta
                return JSONResponse(status_code=200, content=resp_body)
        return result

    # streaming: fan out with translation, emitting Responses SSE events
    stages: List[List[Tuple[Dict[str, Any], str]]] = []
    used: Set[str] = set()
    if candidates:
        stages.append(list(candidates))
    else:
        chain, _ = store.fallback_chain(requested)
        for fid in chain:
            cands = store.resolve_model(fid)
            if cands:
                stages.append(list(cands))
                used.add(fid)
                break
    extra_stages = _fallback_stages(client, requested, candidates or [], used)
    if err is not None and not stages and not extra_stages:
        return err
    # media routing: capable stand-ins first when the payload carries media
    # the requested model can't read (translated payload, same scan)
    media_stage, _media_rerouted = _media_stage(client, requested, chat_payload, candidates or [])
    if media_stage:
        stages = [media_stage] + stages
    stages.extend(extra_stages)

    tried: List[str] = []
    for stage in stages:
        for provider, upstream_model in stage:
            keys = store.pick_keys(provider["id"])
            for key in keys:
                chat_payload_m = dict(chat_payload)
                chat_payload_m["model"] = upstream_model
                result = await _stream_responses_attempt(provider, key, chat_payload_m, client, requested)
                if result is not None:
                    return result
                tried.append(f"{provider['name']}#{key['id']}")
    return _err(502, f"All upstreams failed for '{requested}'. Tried: {', '.join(tried[:8])}")


async def _stream_responses_attempt(
    provider, key, chat_payload, client_row, requested_model
) -> Optional[StreamingResponse]:
    try:
        url, headers, body = adapters.build_request(provider, key["api_key"], "chat", chat_payload)
    except ValueError:
        return None
    is_anthropic = provider.get("kind") == "anthropic"
    a_translator = adapters.AnthropicSSETranslator(requested_model) if is_anthropic else None
    r_translator = adapters.ChatToResponsesSSE(requested_model)
    started = time.perf_counter()
    req = http_client().build_request("POST", url, headers=headers, json=body)
    try:
        resp = await http_client().send(req, stream=True)
    except Exception as e:  # noqa: BLE001
        store.penalize_key(key["id"], 0, f"{type(e).__name__}: {e}")
        return None
    if resp.status_code != 200:
        text = (await resp.aread()).decode(errors="replace")
        await resp.aclose()
        error = adapters.extract_error(resp.status_code, text)
        store.penalize_key(key["id"], resp.status_code, error)
        return None
    store.reward_key(key["id"])

    async def gen():
        try:
            async for line in resp.aiter_lines():
                if not line:
                    continue
                chunk = None
                if a_translator:
                    converted = a_translator.convert(line)
                    if converted and converted.startswith("data: ") and converted != "data: [DONE]":
                        try:
                            chunk = json.loads(converted[6:])
                        except ValueError:
                            chunk = None
                else:
                    if line.startswith("data:") and line.strip() != "data: [DONE]":
                        try:
                            chunk = json.loads(line[5:].strip())
                        except ValueError:
                            chunk = None
                if isinstance(chunk, dict) and chunk.get("error") is None:
                    for evt in r_translator.feed(chunk):
                        yield "event: " + evt["type"] + "\ndata: " + json.dumps(evt) + "\n\n"
        finally:
            await resp.aclose()
            for evt in r_translator.finish():
                yield "event: " + evt["type"] + "\ndata: " + json.dumps(evt) + "\n\n"
            db.log_request(
                client_key_id=client_row["id"],
                provider_id=provider["id"],
                upstream_key_id=key["id"],
                model=requested_model,
                endpoint="responses",
                status=200,
                latency_ms=int((time.perf_counter() - started) * 1000),
                tokens_in=r_translator.usage.get("prompt_tokens", 0),
                tokens_out=r_translator.usage.get("completion_tokens", 0),
            )
            db.bump_client_usage(
                client_row["id"],
                r_translator.usage.get("prompt_tokens", 0),
                r_translator.usage.get("completion_tokens", 0),
            )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------------ /v1/messages (Anthropic native)
@router.post("/messages")
async def messages_endpoint(request: Request):
    """Anthropic Messages API on ANY provider (anthropic native or translated).

    Same staged fallback as the OpenAI endpoints: requested model first, then
    the fallback chain / auto-picked stand-ins — always reporting the
    requested model id back to the client.
    """
    client = require_client(request)
    payload, err = await _parse_json(request)
    if err:
        return err
    requested = str(payload.get("model") or "")
    candidates, err, _ = _resolve_candidates(client, requested)
    direct = candidates or []

    if not requested:
        return _anthropic_err(400, "invalid_request_error", "Field 'model' is required")
    if not store.client_allows(client, requested):
        return _anthropic_err(403, "permission_error", f"Key not allowed to use model '{requested}'")

    stages: List[List[Tuple[Dict[str, Any], str]]] = []
    if direct:
        stages.append(list(direct))
        extra_stages = _fallback_stages(client, requested, direct)
    else:
        # unknown model: try the route's chain first before erroring
        chain, _ = store.fallback_chain(requested)
        used: Set[str] = set()
        for fid in chain:
            cands = store.resolve_model(fid)
            if cands:
                stages.append(list(cands))
                used.add(fid)
                break
        extra_stages = _fallback_stages(client, requested, [], used)
    if err is not None and not stages and not extra_stages:
        return err
    # media routing: capable stand-ins first when the payload carries media
    # the requested model can't read (anthropic-shape blocks are scanned too)
    media_stage, _media_rerouted = _media_stage(client, requested, payload, direct)
    if media_stage:
        stages = [media_stage] + stages
    stages.extend(extra_stages)

    stream = bool(payload.get("stream"))

    tried: List[str] = []
    last_status, last_error = 502, "no upstream attempt succeeded"
    for stage in stages:
        for provider, upstream_model in stage:
            keys = store.pick_keys(provider["id"])
            if not keys:
                tried.append(f"{provider['name']}: no keys")
                continue
            is_anthropic = provider.get("kind") == "anthropic"            # anthropic-kind upstream: forward as-is (native protocol)
            # openai-kind upstream: translate request
            upstream_payload = dict(payload) if is_anthropic else adapters.anthropic_request_to_openai(payload)
            upstream_payload["model"] = upstream_model
            for key in keys:
                started = time.perf_counter()
                if stream:
                    result = await _stream_messages_attempt(
                        provider, key, upstream_payload, client, requested, is_anthropic
                    )
                    if result is not None:
                        return result
                    tried.append(f"{provider['name']}#{key['id']}: stream fail")
                    continue
                # non-streaming
                try:
                    if is_anthropic:
                        url = f"{(provider.get('base_url') or '').rstrip('/')}/messages"
                        headers = adapters.auth_headers(provider, key["api_key"])
                        body = upstream_payload
                    else:
                        url, headers, body = adapters.build_request(
                            provider, key["api_key"], "chat", upstream_payload
                        )
                except ValueError as e:
                    return _anthropic_err(400, "invalid_request_error", str(e))
                try:
                    resp = await http_client().post(url, headers=headers, json=body)
                except httpx.TimeoutException:
                    last_status, last_error = 504, f"timeout contacting {provider['name']}"
                    tried.append(f"{provider['name']}#{key['id']}: timeout")
                    continue
                except Exception as e:  # noqa: BLE001
                    last_status, last_error = 502, f"{type(e).__name__}: {e}"[:200]
                    tried.append(f"{provider['name']}#{key['id']}: {type(e).__name__}")
                    continue
                latency = int((time.perf_counter() - started) * 1000)
                if resp.status_code != 200:
                    error = adapters.extract_error(resp.status_code, resp.text)
                    store.penalize_key(key["id"], resp.status_code, error)
                    db.log_request(
                        client_key_id=client["id"], provider_id=provider["id"],
                        upstream_key_id=key["id"], model=requested, endpoint="messages",
                        status=resp.status_code, latency_ms=latency, error=error,
                        via=upstream_model if upstream_model != requested else "",
                    )
                    last_status, last_error = resp.status_code, error
                    tried.append(f"{provider['name']}#{key['id']} -> {resp.status_code}")
                    if resp.status_code not in RETRYABLE and not extra_stages:
                        return _anthropic_err(resp.status_code, "api_error", error or f"HTTP {resp.status_code}")
                    continue
                try:
                    data = resp.json()
                except ValueError:
                    return _anthropic_err(502, "api_error", "upstream returned non-JSON body")
                inline = adapters.response_has_error(data)
                if inline:
                    return _anthropic_err(502, "api_error", inline)
                store.reward_key(key["id"])
                tokens_in, tokens_out = adapters.extract_usage(data)
                db.log_request(
                    client_key_id=client["id"], provider_id=provider["id"],
                    upstream_key_id=key["id"], model=requested, endpoint="messages",
                    status=200, latency_ms=latency, tokens_in=tokens_in, tokens_out=tokens_out,
                    via=upstream_model if upstream_model != requested else "",
                )
                db.bump_client_usage(client["id"], tokens_in, tokens_out)
                # openai-kind upstream -> convert response back to anthropic shape
                if not is_anthropic:
                    data = adapters.openai_chat_to_anthropic(data, requested)
                if _spoof_enabled(payload):
                    data["model"] = requested
                else:
                    data.setdefault("model", requested)
                return JSONResponse(status_code=200, content=data)
    return _anthropic_err(
        last_status if last_status >= 400 else 502,
        "api_error",
        f"All upstreams failed for '{requested}'. Last error: {last_error}. Tried: {', '.join(tried[:8])}",
    )


async def _stream_messages_attempt(
    provider, key, upstream_payload, client_row, requested_model, is_anthropic
) -> Optional[StreamingResponse]:
    try:
        if is_anthropic:
            url = f"{(provider.get('base_url') or '').rstrip('/')}/messages"
            headers = adapters.auth_headers(provider, key["api_key"])
            body = upstream_payload
        else:
            url, headers, body = adapters.build_request(provider, key["api_key"], "chat", upstream_payload)
    except ValueError:
        return None
    a_pass = is_anthropic  # anthropic upstream: pass lines through untouched
    a_translator = None if a_pass else None
    o_translator = None if a_pass else adapters.ChatToAnthropicSSE(requested_model)
    started = time.perf_counter()
    req = http_client().build_request("POST", url, headers=headers, json=body)
    try:
        resp = await http_client().send(req, stream=True)
    except Exception as e:  # noqa: BLE001
        store.penalize_key(key["id"], 0, f"{type(e).__name__}: {e}")
        return None
    if resp.status_code != 200:
        text = (await resp.aread()).decode(errors="replace")
        await resp.aclose()
        error = adapters.extract_error(resp.status_code, text)
        store.penalize_key(key["id"], resp.status_code, error)
        return None
    store.reward_key(key["id"])

    async def gen():
        try:
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if a_pass:
                    # native anthropic stream: rewrite the model id so the
                    # client keeps seeing the model it asked for
                    out_line = adapters.spoof_sse_line(line, requested_model)
                    yield out_line + "\n\n" if not out_line.startswith("event:") else out_line + "\n"
                else:
                    # openai upstream: parse chat chunk -> anthropic events
                    if line.startswith("data:") and line.strip() != "data: [DONE]":
                        try:
                            chunk = json.loads(line[5:].strip())
                        except ValueError:
                            chunk = None
                        if isinstance(chunk, dict):
                            for evt_line in o_translator.feed(chunk):
                                yield evt_line
            if not a_pass:
                for evt_line in o_translator.finish():
                    yield evt_line
        finally:
            await resp.aclose()
            db.log_request(
                client_key_id=client_row["id"],
                provider_id=provider["id"],
                upstream_key_id=key["id"],
                model=requested_model,
                endpoint="messages",
                status=200,
                latency_ms=int((time.perf_counter() - started) * 1000),
                tokens_out=o_translator.usage_out if o_translator else 0,
                via=upstream_payload.get("model", "") if upstream_payload.get("model") != requested_model else "",
            )
            db.bump_client_usage(client_row["id"], 0, o_translator.usage_out if o_translator else 0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------------ images
def _img_out(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = body.get("data") or []
    out = []
    for d in data:
        if isinstance(d, dict):
            out.append({
                "url": d.get("url") or "",
                "b64_json": d.get("b64_json") or "",
                "revised_prompt": d.get("revised_prompt") or "",
            })
    return out


async def _image_dispatch(request: Request, path: str, payload: Dict[str, Any]) -> Any:
    """images/generations | images/edits with provider fan-out + model fallback."""
    client = require_client(request)
    requested = str(payload.get("model") or "")
    candidates, err, _ = _resolve_candidates(client, requested)
    direct = candidates or []

    stages: List[List[Tuple[Dict[str, Any], str]]] = []
    used: Set[str] = set()
    if direct:
        stages.append(list(direct))
    else:
        chain, _ = store.fallback_chain(requested)
        for fid in chain:
            cands = store.resolve_model(fid)
            if cands:
                stages.append(list(cands))
                used.add(fid)
                break
    extra_stages = _fallback_stages(client, requested, direct, used)
    if err is not None and not stages and not extra_stages:
        return err
    stages.extend(extra_stages)

    tried: List[str] = []
    last_status, last_error = 502, "no upstream attempt succeeded"
    n_stages = len(stages)
    for stage_no, stage in enumerate(stages):
        for provider, upstream_model in stage:
            keys = store.pick_keys(provider["id"])
            if not keys:
                tried.append(f"{provider['name']}: no keys")
                continue
            body = dict(payload)
            body["model"] = upstream_model
            url = f"{(provider.get('base_url') or '').rstrip('/')}{path}"
            for key in keys:
                started = time.perf_counter()
                headers = adapters.auth_headers(provider, key["api_key"])
                try:
                    resp = await http_client().post(url, headers=headers, json=body)
                except httpx.TimeoutException:
                    last_status, last_error = 504, f"timeout contacting {provider['name']}"
                    tried.append(f"{provider['name']}#{key['id']}: timeout")
                    continue
                except Exception as e:  # noqa: BLE001
                    last_status, last_error = 502, f"{type(e).__name__}: {e}"[:200]
                    tried.append(f"{provider['name']}#{key['id']}: {type(e).__name__}")
                    continue
                latency = int((time.perf_counter() - started) * 1000)
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except ValueError:
                        return _err(502, "upstream returned non-JSON body")
                    store.reward_key(key["id"])
                    db.log_request(
                        client_key_id=client["id"], provider_id=provider["id"],
                        upstream_key_id=key["id"], model=requested, endpoint="images",
                        status=200, latency_ms=latency,
                        via=upstream_model if upstream_model != requested else "",
                    )
                    db.bump_client_usage(client["id"], 0, 0)
                    return JSONResponse(status_code=200, content=data)
                error = adapters.extract_error(resp.status_code, resp.text)
                store.penalize_key(key["id"], resp.status_code, error)
                db.log_request(
                    client_key_id=client["id"], provider_id=provider["id"],
                    upstream_key_id=key["id"], model=requested, endpoint="images",
                    status=resp.status_code, latency_ms=latency, error=error,
                    via=upstream_model if upstream_model != requested else "",
                )
                last_status, last_error = resp.status_code, error
                tried.append(f"{provider['name']}#{key['id']} -> {resp.status_code}")
                if resp.status_code not in RETRYABLE:
                    # non-retryable: only keep going when another fallback
                    # stage remains (a different model may still work)
                    if stage_no + 1 >= n_stages:
                        return _err(resp.status_code, error or f"upstream returned {resp.status_code}")
    return _err(
        last_status if last_status >= 400 else 502,
        f"All upstreams failed for '{requested}'. Last error: {last_error}. Tried: {', '.join(tried[:8])}",
    )


@router.post("/images/generations")
async def images_generations(request: Request):
    payload, err = await _parse_json(request)
    if err:
        return err
    return await _image_dispatch(request, "/images/generations", payload)


@router.post("/images/edits")
async def images_edits(request: Request):
    payload, err = await _parse_json(request)
    if err:
        return err
    return await _image_dispatch(request, "/images/edits", payload)


# ------------------------------------------------------------------ videos
@router.post("/videos")
async def create_video(request: Request):
    """Video generation. Supports chat-style video upstreams (sora-2 style).

    Body: {"model": "...", "prompt": "...", "seconds": "8", "size": "1280x720"}
    Returns OpenAI /v1/videos style job envelope. Poll GET /v1/videos/{id}.
    """
    client = require_client(request)
    payload, err = await _parse_json(request)
    if err:
        return err
    requested = str(payload.get("model") or "")
    candidates, err, _ = _resolve_candidates(client, requested)
    if err:
        return err

    prompt = str(payload.get("prompt") or "")
    seconds = str(payload.get("seconds") or payload.get("duration") or "8")
    size = str(payload.get("size") or "1280x720")

    chat_body = {
        "model": requested,
        "messages": [{"role": "user", "content": f"{prompt}\n\nVideo duration: {seconds}s. Resolution: {size}."}],
        "modalities": ["image", "video"],
        "stream": False,
    }

    job_id = "video-" + uuid.uuid4().hex[:24]
    _video_jobs[job_id] = {
        "id": job_id, "status": "queued", "model": requested,
        "created_at": int(time.time()), "progress": 0, "output": [],
        "error": "", "prompt": prompt, "seconds": seconds, "size": size,
    }

    tried: List[str] = []
    for provider, upstream_model in candidates:
        keys = store.pick_keys(provider["id"])
        for key in keys:
            body = dict(chat_body)
            body["model"] = upstream_model
            try:
                url, headers, ubody = adapters.build_request(provider, key["api_key"], "chat", body)
            except ValueError:
                continue
            started = time.perf_counter()
            try:
                resp = await http_client().post(url, headers=headers, json=ubody)
            except Exception as e:  # noqa: BLE001
                tried.append(f"{provider['name']}#{key['id']}: {type(e).__name__}")
                continue
            latency = int((time.perf_counter() - started) * 1000)
            if resp.status_code != 200:
                error = adapters.extract_error(resp.status_code, resp.text)
                store.penalize_key(key["id"], resp.status_code, error)
                tried.append(f"{provider['name']}#{key['id']} -> {resp.status_code}")
                continue
            try:
                data = resp.json()
            except ValueError:
                tried.append(f"{provider['name']}#{key['id']}: non-JSON")
                continue
            store.reward_key(key["id"])
            db.log_request(
                client_key_id=client["id"], provider_id=provider["id"],
                upstream_key_id=key["id"], model=requested, endpoint="videos",
                status=200, latency_ms=latency,
            )
            # extract video/image URLs from the response
            urls: List[str] = []
            text = ""
            choices = data.get("choices") or []
            if choices:
                msg = (choices[0].get("message") or {})
                text = msg.get("content") or ""
                if isinstance(text, list):
                    text = "".join(
                        (p.get("text") or "") if isinstance(p, dict) else str(p)
                        for p in text
                    )
                # annotations
                for ann in msg.get("annotations") or []:
                    u = ((ann.get("url_image") or {}) if isinstance(ann, dict) else {}).get("url") or \
                        ((ann.get("url_video") or {}) if isinstance(ann, dict) else {}).get("url") or ""
                    if u:
                        urls.append(u)
                url_video = msg.get("url_video")
                if isinstance(url_video, dict):
                    urls.append(url_video.get("url") or "")
                # markdown-extract video URLs
                import re as _re
                for m in _re.finditer(r"https?://\S+", text or ""):
                    u = m.group(0).rstrip(").,]")
                    if u not in urls:
                        urls.append(u)
            job = _video_jobs[job_id]
            job.update({
                "status": "completed",
                "output": [{"type": "video", "url": u} for u in urls if u],
                "text": text[:500],
            })
            return JSONResponse(status_code=200, content={
                "id": job_id,
                "object": "video-generation",
                "status": "completed",
                "model": requested,
                "created_at": job["created_at"],
                "output": job["output"],
            })
    job = _video_jobs.get(job_id)
    if job:
        job["status"] = "failed"
        job["error"] = f"All upstreams failed for '{requested}'. Tried: {', '.join(tried[:8])}"
    return _err(502, f"All upstreams failed for '{requested}'. Tried: {', '.join(tried[:8])}")


@router.get("/videos/{job_id}")
async def get_video(request: Request, job_id: str):
    require_client(request)
    job = _video_jobs.get(job_id)
    if not job:
        return _err(404, f"video job '{job_id}' not found", "not_found")
    return {
        "id": job["id"],
        "object": "video-generation",
        "status": job["status"],
        "model": job["model"],
        "created_at": job["created_at"],
        "progress": job.get("progress", 0),
        "output": job.get("output", []),
        "error": job.get("error", ""),
    }


# ------------------------------------------------------------------ audio
@router.post("/audio/speech")
async def audio_speech(request: Request):
    """TTS. Binary audio passthrough (mp3 default)."""
    client = require_client(request)
    payload, err = await _parse_json(request)
    if err:
        return err
    requested = str(payload.get("model") or "")
    candidates, err, _ = _resolve_candidates(client, requested)
    if err:
        return err
    tried: List[str] = []
    for provider, upstream_model in candidates:
        keys = store.pick_keys(provider["id"])
        body = dict(payload)
        body["model"] = upstream_model
        url = f"{(provider.get('base_url') or '').rstrip('/')}/audio/speech"
        for key in keys:
            headers = adapters.auth_headers(provider, key["api_key"])
            started = time.perf_counter()
            try:
                resp = await http_client().post(url, headers=headers, json=body)
            except Exception as e:  # noqa: BLE001
                tried.append(f"{provider['name']}#{key['id']}: {type(e).__name__}")
                continue
            latency = int((time.perf_counter() - started) * 1000)
            if resp.status_code == 200:
                store.reward_key(key["id"])
                db.log_request(
                    client_key_id=client["id"], provider_id=provider["id"],
                    upstream_key_id=key["id"], model=requested, endpoint="audio/speech",
                    status=200, latency_ms=latency,
                )
                db.bump_client_usage(client["id"], 0, 0)
                media = resp.headers.get("content-type", "audio/mpeg")
                return Response(
                    content=resp.content,
                    media_type=media,
                    headers={"X-Nova-Provider": provider["name"]},
                )
            error = adapters.extract_error(resp.status_code, resp.text)
            store.penalize_key(key["id"], resp.status_code, error)
            db.log_request(
                client_key_id=client["id"], provider_id=provider["id"],
                upstream_key_id=key["id"], model=requested, endpoint="audio/speech",
                status=resp.status_code, latency_ms=latency, error=error,
            )
            tried.append(f"{provider['name']}#{key['id']} -> {resp.status_code}")
            if resp.status_code not in RETRYABLE:
                return _err(resp.status_code, error or f"upstream returned {resp.status_code}")
    return _err(502, f"TTS failed for '{requested}'. Tried: {', '.join(tried[:8])}")


async def _audio_upload_dispatch(request: Request, path: str) -> Any:
    """STT endpoints: multipart upload -> upstream multipart -> JSON back."""
    client = require_client(request)
    form = await request.form()
    requested = str(form.get("model") or "")
    candidates, err, _ = _resolve_candidates(client, requested)
    if err:
        return err
    tried: List[str] = []
    for provider, upstream_model in candidates:
        keys = store.pick_keys(provider["id"])
        url = f"{(provider.get('base_url') or '').rstrip('/')}{path}"
        for key in keys:
            headers = adapters.auth_headers(provider, key["api_key"])
            headers.pop("Content-Type", None)  # httpx sets multipart boundary itself
            files: Dict[str, Any] = {}
            data: Dict[str, Any] = {"model": upstream_model}
            for name in ("file",):
                f = form.get(name)
                if f is not None:
                    files[name] = (getattr(f, "filename", "audio"), await f.read(), None)
            for extra in ("language", "prompt", "response_format", "temperature", "timestamp_granularities"):
                v = form.get(extra)
                if v is not None:
                    data[extra] = v
            started = time.perf_counter()
            try:
                resp = await http_client().post(url, headers=headers, data=data, files=files)
            except Exception as e:  # noqa: BLE001
                tried.append(f"{provider['name']}#{key['id']}: {type(e).__name__}")
                continue
            latency = int((time.perf_counter() - started) * 1000)
            if resp.status_code == 200:
                try:
                    out = resp.json()
                except ValueError:
                    out = {"text": resp.text}
                store.reward_key(key["id"])
                db.log_request(
                    client_key_id=client["id"], provider_id=provider["id"],
                    upstream_key_id=key["id"], model=requested, endpoint=path.strip("/"),
                    status=200, latency_ms=latency,
                )
                return JSONResponse(status_code=200, content=out)
            error = adapters.extract_error(resp.status_code, resp.text)
            store.penalize_key(key["id"], resp.status_code, error)
            tried.append(f"{provider['name']}#{key['id']} -> {resp.status_code}")
            if resp.status_code not in RETRYABLE:
                return _err(resp.status_code, error or f"upstream returned {resp.status_code}")
    return _err(502, f"Audio transcription failed for '{requested}'. Tried: {', '.join(tried[:8])}")


@router.post("/audio/transcriptions")
async def audio_transcriptions(request: Request):
    return await _audio_upload_dispatch(request, "/audio/transcriptions")


@router.post("/audio/translations")
async def audio_translations(request: Request):
    return await _audio_upload_dispatch(request, "/audio/translations")


# ------------------------------------------------------------------ moderations
@router.post("/moderations")
async def moderations(request: Request):
    return await dispatch(request, "moderations", endpoint_label="moderations")


# ------------------------------------------------------------------ legacy aliases
@router.post("/engines/{engine}/chat/completions")
@router.post("/chat/completions/{engine}")
async def legacy_chat(request: Request, engine: str):
    """Legacy /v1/engines/{engine}/... style call (kept for very old clients)."""
    payload, err = await _parse_json(request)
    if err:
        return err
    payload.setdefault("model", engine)
    return await dispatch(request, "chat", payload_override=payload)