"""The gateway: OpenAI-compatible endpoints that fan out to real providers.

Failover chain per request:
    for each candidate provider that serves the model (healthiest first):
        for each key of that provider (round-robin, cooldown-aware):
            try -> on retryable error, move to next key / next provider
"""
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import adapters, config, db, store

router = APIRouter()

# status codes that justify retrying with another key / provider
RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504, 522, 524, 402, 401, 403}

_client: Optional[httpx.AsyncClient] = None


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
    return client


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
        data.append(
            {
                "id": mid,
                "object": "model",
                "created": int(r["checked_at"] or time.time()),
                "owned_by": r["provider_name"],
                "nova": {
                    "provider": r["provider_name"],
                    "upstream_id": r["model_id"],
                    "status": r["status"],
                    "latency_ms": r["latency_ms"],
                    "free": bool(r["is_free"]),
                },
            }
        )
    return {"object": "list", "data": data}


# ------------------------------------------------------------------ core proxy
def _err(status: int, message: str, etype: str = "upstream_error") -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"message": message, "type": etype}})


async def _attempt(
    provider: Dict[str, Any],
    key: Dict[str, Any],
    endpoint: str,
    payload: Dict[str, Any],
) -> Tuple[int, Any, str]:
    """Returns (http_status, parsed_body_or_text, error_message)."""
    # FIX: build_request raises for unsupported combos (anthropic + embeddings);
    # catch it instead of crashing the request with a 500.
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
        # FIX: 200 with non-JSON body used to crash json.loads downstream;
        # return the raw text marked as an upstream failure.
        return 502, resp.text, "upstream returned non-JSON body with HTTP 200"
    inline = adapters.response_has_error(data)
    if inline:
        return 502, data, inline
    if provider.get("kind") == "anthropic":
        data = adapters.anthropic_to_openai(data, payload.get("model", ""))
    return 200, data, ""


async def dispatch(request: Request, endpoint: str) -> Any:
    client = require_client(request)
    try:
        payload = await request.json()
    except Exception:
        return _err(400, "Request body must be valid JSON", "invalid_request_error")
    requested = payload.get("model")
    if not requested:
        return _err(400, "Field 'model' is required", "invalid_request_error")
    if not store.client_allows(client, requested):
        return _err(403, f"Key not allowed to use model '{requested}'", "permission_error")
    candidates = store.resolve_model(requested)
    if not candidates:
        return _err(
            404,
            f"Model '{requested}' not found. Sync/refresh models in the dashboard, "
            f"or check GET /v1/models for available ids.",
            "model_not_found",
        )
    stream = bool(payload.get("stream"))
    tried: List[str] = []
    last_status, last_error = 502, "no upstream attempt succeeded"
    for provider, upstream_model in candidates:
        keys = store.pick_keys(provider["id"])
        if not keys:
            tried.append(f"{provider['name']}: no keys")
            continue
        upstream_payload = dict(payload)
        upstream_payload["model"] = upstream_model
        for key in keys:
            started = time.perf_counter()
            if stream and endpoint == "chat":
                result = await _stream_attempt(provider, key, upstream_payload, client, requested)
                if result is not None:
                    return result
                last_status, last_error = 502, f"{provider['name']} stream failed"
                tried.append(f"{provider['name']}#{key['id']}: stream fail")
                continue
            status, body, error = await _attempt(provider, key, endpoint, upstream_payload)
            latency = int((time.perf_counter() - started) * 1000)
            db.log_request(
                client_key_id=client["id"],
                provider_id=provider["id"],
                upstream_key_id=key["id"],
                model=requested,
                status=status,
                latency_ms=latency,
                error=error,
            )
            if status == 200:
                store.reward_key(key["id"])
                if isinstance(body, str):
                    # FIX: non-JSON 200 body - never crash on json.loads; pass through raw.
                    return JSONResponse(status_code=200, content={"raw": body})
                if isinstance(body, dict):
                    body.setdefault("model", requested)
                    body["_nova"] = {
                        "provider": provider["name"],
                        "upstream_model": upstream_model,
                        "latency_ms": latency,
                    }
                return JSONResponse(status_code=200, content=body)
            store.penalize_key(key["id"], status, error)
            last_status, last_error = status, error
            tried.append(f"{provider['name']}#{key['id']} -> {status}")
            if status not in RETRYABLE:
                # deterministic client error (e.g. malformed request) - stop early
                return _err(status, error or f"upstream returned {status}", "invalid_request_error")
    return _err(
        last_status if last_status >= 400 else 502,
        f"All upstreams failed for '{requested}'. Last error: {last_error}. Tried: {', '.join(tried[:8])}",
    )


async def _stream_attempt(
    provider: Dict[str, Any],
    key: Dict[str, Any],
    payload: Dict[str, Any],
    client_row: Dict[str, Any],
    requested_model: str,
) -> Optional[StreamingResponse]:
    """Open an upstream SSE stream. Returns None if the upstream refused."""
    # FIX: same ValueError guard as _attempt (anthropic does not stream /embeddings etc.)
    try:
        url, headers, body = adapters.build_request(provider, key["api_key"], "chat", payload)
    except ValueError as e:
        return None
    is_anthropic = provider.get("kind") == "anthropic"
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:20]}"
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
        try:
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if is_anthropic:
                    converted = adapters.anthropic_sse_to_openai(line, requested_model, chunk_id)
                    if converted:
                        # FIX: converter returns bare "data: ..." lines; append the
                        # SSE terminator uniformly here. This also covers the
                        # message_stop -> [DONE] event, so the old post-loop
                        # duplicate "[DONE]" is gone.
                        yield converted + "\n\n"
                else:
                    yield (line + "\n" if line.startswith(":") else line + "\n\n")
        finally:
            await resp.aclose()
            db.log_request(
                client_key_id=client_row["id"],
                provider_id=provider["id"],
                upstream_key_id=key["id"],
                model=requested_model,
                status=200,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Nova-Provider": provider["name"],
            "X-Accel-Buffering": "no",
        },
    )


# ------------------------------------------------------------------ endpoints
@router.post("/chat/completions")
async def chat_completions(request: Request):
    return await dispatch(request, "chat")


@router.post("/completions")
async def completions(request: Request):
    return await dispatch(request, "completions")


@router.post("/embeddings")
async def embeddings(request: Request):
    return await dispatch(request, "embeddings")