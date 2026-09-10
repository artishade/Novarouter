"""Provider adapters.

Two "kinds" are supported, which together cover almost every vendor:

  openai     -> any OpenAI-compatible /v1 endpoint
                (OpenRouter, Groq, Together, DeepSeek, Cerebras, Mistral, xAI,
                 Fireworks, Nvidia NIM, Ollama, LM Studio, vLLM, and Google's
                 OpenAI-compat layer at .../v1beta/openai)
  anthropic  -> native Anthropic Messages API (translated to/from OpenAI shape)

Everything is plain httpx, no vendor SDKs.
"""

import json
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

ANTHROPIC_VERSION = "2023-06-01"

# result buckets (shared with the checker + dashboard)
OK = "OK"
RATE_LIMITED = "RATE_LIMITED"
NEEDS_CREDIT = "NEEDS_CREDIT"
NO_ACCESS = "NO_ACCESS"
BAD_REQUEST = "BAD_REQUEST"
SERVER_ERROR = "SERVER_ERROR"
NETWORK_ERROR = "NETWORK_ERROR"

KNOWN_KINDS = ("openai", "anthropic")


def classify(status: int) -> str:
    if status == 200:
        return OK
    if status == 429:
        return RATE_LIMITED
    if status == 402:
        return NEEDS_CREDIT
    if status in (401, 403, 404):
        return NO_ACCESS
    if status == 400:
        return BAD_REQUEST
    if status and status >= 500:
        return SERVER_ERROR
    return NETWORK_ERROR


def _base(provider: Dict[str, Any]) -> str:
    return (provider.get("base_url") or "").rstrip("/")


def extra_headers(provider: Dict[str, Any]) -> Dict[str, str]:
    raw = provider.get("extra_headers") or "{}"
    try:
        data = json.loads(raw)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def auth_headers(provider: Dict[str, Any], api_key: str) -> Dict[str, str]:
    kind = provider.get("kind", "openai")
    headers = {"Content-Type": "application/json"}
    if kind == "anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = ANTHROPIC_VERSION
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    headers.update(extra_headers(provider))
    return headers


# --------------------------------------------------------------------------
# model listing
# --------------------------------------------------------------------------

def _is_free(model: Dict[str, Any]) -> bool:
    pricing = model.get("pricing") or {}
    if not isinstance(pricing, dict):
        return False
    try:
        return (
            float(pricing.get("prompt") or 0) == 0
            and float(pricing.get("completion") or 0) == 0
        )
    except (TypeError, ValueError):
        return False


async def list_models(
    client: httpx.AsyncClient, provider: Dict[str, Any], api_key: str
) -> List[Dict[str, Any]]:
    """Returns [{"id": ..., "is_free": bool}] from the provider's model endpoint."""
    url = f"{_base(provider)}/models"
    resp = await client.get(url, headers=auth_headers(provider, api_key))
    resp.raise_for_status()
    body = resp.json()

    raw = body.get("data") if isinstance(body, dict) else body
    if raw is None and isinstance(body, dict):
        raw = body.get("models") or []
    out = []
    for m in raw or []:
        if isinstance(m, str):
            out.append({"id": m, "is_free": False})
            continue
        mid = m.get("id") or m.get("name") or m.get("model")
        if not mid:
            continue
        # Google native style: "models/gemini-x" -> "gemini-x"
        if isinstance(mid, str) and mid.startswith("models/"):
            mid = mid.split("/", 1)[1]
        out.append({"id": mid, "is_free": _is_free(m)})
    # dedupe, stable order
    seen, uniq = set(), []
    for m in sorted(out, key=lambda x: x["id"]):
        if m["id"] not in seen:
            seen.add(m["id"])
            uniq.append(m)
    return uniq


# --------------------------------------------------------------------------
# anthropic <-> openai translation
# --------------------------------------------------------------------------

def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for chunk in content:
            if isinstance(chunk, dict) and chunk.get("type") == "text":
                parts.append(chunk.get("text", ""))
            elif isinstance(chunk, str):
                parts.append(chunk)
        return "".join(parts)
    return ""


def openai_to_anthropic(payload: Dict[str, Any]) -> Dict[str, Any]:
    system_parts, messages = [], []
    for msg in payload.get("messages") or []:
        role = msg.get("role")
        if role == "system":
            system_parts.append(_text_of(msg.get("content")))
            continue
        messages.append(
            {
                "role": "assistant" if role == "assistant" else "user",
                "content": msg.get("content") if isinstance(msg.get("content"), list)
                else [{"type": "text", "text": _text_of(msg.get("content"))}],
            }
        )
    body: Dict[str, Any] = {
        "model": payload.get("model"),
        "messages": messages or [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "max_tokens": int(payload.get("max_tokens") or payload.get("max_completion_tokens") or 1024),
    }
    if system_parts:
        body["system"] = "\n\n".join(p for p in system_parts if p)
    for src, dst in (("temperature", "temperature"), ("top_p", "top_p"), ("stop", "stop_sequences")):
        if payload.get(src) is not None:
            val = payload[src]
            if dst == "stop_sequences" and isinstance(val, str):
                val = [val]
            body[dst] = val
    if payload.get("stream"):
        body["stream"] = True
    return body


_STOP_MAP = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "tool_use": "tool_calls"}


def anthropic_to_openai(body: Dict[str, Any], model: str) -> Dict[str, Any]:
    text = _text_of(body.get("content"))
    usage = body.get("usage") or {}
    return {
        "id": body.get("id") or f"chatcmpl-{int(time.time()*1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model") or model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": _STOP_MAP.get(body.get("stop_reason"), "stop"),
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
    }


def anthropic_sse_to_openai(raw_line: str, model: str, chunk_id: str) -> Optional[str]:
    """Convert one Anthropic SSE `data:` line into an OpenAI chunk line (or None)."""
    if not raw_line.startswith("data:"):
        return None
    data = raw_line[5:].strip()
    if not data:
        return None
    try:
        evt = json.loads(data)
    except ValueError:
        return None

    etype = evt.get("type")
    base = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }
    if etype == "content_block_delta":
        delta = evt.get("delta") or {}
        if delta.get("type") == "text_delta":
            base["choices"] = [{"index": 0, "delta": {"content": delta.get("text", "")}, "finish_reason": None}]
            return "data: " + json.dumps(base)
        return None
    if etype == "message_start":
        base["choices"] = [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]
        return "data: " + json.dumps(base)
    if etype == "message_delta":
        reason = (evt.get("delta") or {}).get("stop_reason")
        base["choices"] = [{"index": 0, "delta": {}, "finish_reason": _STOP_MAP.get(reason, "stop")}]
        return "data: " + json.dumps(base)
    if etype == "message_stop":
        return "data: [DONE]"
    return None


# --------------------------------------------------------------------------
# request building
# --------------------------------------------------------------------------

def build_request(
    provider: Dict[str, Any],
    api_key: str,
    endpoint: str,
    payload: Dict[str, Any],
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """endpoint: 'chat', 'completions', 'embeddings'. Returns (url, headers, body)."""
    kind = provider.get("kind", "openai")
    headers = auth_headers(provider, api_key)

    if kind == "anthropic":
        if endpoint != "chat":
            raise ValueError(f"anthropic provider does not support endpoint '{endpoint}'")
        return f"{_base(provider)}/messages", headers, openai_to_anthropic(payload)

    path = {"chat": "/chat/completions", "completions": "/completions", "embeddings": "/embeddings"}[endpoint]
    return f"{_base(provider)}{path}", headers, payload


def ping_payload(provider: Dict[str, Any], model_id: str) -> Dict[str, Any]:
    return {
        "model": model_id,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
    }


def extract_error(status: int, text: str) -> str:
    try:
        body = json.loads(text)
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)[:200]
        if isinstance(err, str):
            return err[:200]
        if body.get("message"):
            return str(body["message"])[:200]
    except (ValueError, AttributeError):
        pass
    return (text or f"HTTP {status}")[:200]


def response_has_error(body: Any) -> Optional[str]:
    """Some gateways return HTTP 200 with an error body."""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)[:200]
        if isinstance(err, str) and err:
            return err[:200]
    return None