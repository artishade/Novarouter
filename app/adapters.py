"""Provider adapters v2 — full-feature translation layer.

Kinds:
  openai     -> any OpenAI-compatible /v1 endpoint (OpenRouter, Groq, Together,
                DeepSeek, Cerebras, Mistral, xAI, Fireworks, NIM, Ollama, ...)
  anthropic  -> native Anthropic Messages API, translated to/from OpenAI shape

v2 adds:
  * tool calls (request + response + streaming, both directions)
  * vision (image_url -> anthropic image sources) + audio input
  * extended thinking: reasoning_effort / reasoning / :thinking suffix -> thinking blocks
  * reasoning passthrough (DeepSeek reasoning_content, OpenRouter reasoning)
  * Responses API <-> Chat Completions translation (non-stream + SSE)
  * model capability inference (tools/vision/image_gen/video/tts/audio/...)
"""

import json
import re
import time
import uuid
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

# reasoning_effort (OpenAI style) -> anthropic thinking budget
REASONING_EFFORT_BUDGETS = {"low": 1024, "medium": 8192, "high": 16000}

# OpenRouter-style router suffixes we strip for routing but forward upstream
KNOWN_MODEL_SUFFIXES = (":thinking", ":free", ":nitro", ":floor", ":extended")


def split_model_suffix(requested: str) -> Tuple[str, str]:
    """'deepseek/r1:thinking' -> ('deepseek/r1', ':thinking')."""
    for s in KNOWN_MODEL_SUFFIXES:
        if requested.endswith(s):
            return requested[: -len(s)], s
    return requested, ""


# --------------------------------------------------------------------------
# status classification
# --------------------------------------------------------------------------

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
# capabilities
# --------------------------------------------------------------------------

CAP_KEYS = (
    "tools", "vision", "reasoning", "image_gen", "video",
    "tts", "audio_in", "transcription", "embeddings",
)

_CAP_RULES = [
    ("reasoning", re.compile(
        r"\bo1\b|\bo3\b|\bo4\b|gpt-5|gpt-oss|r1\b|qwq|thinking|reasoner|"
        r"gemini-2\.5|gemini-3|claude-3-7|claude-sonnet-4|claude-opus-4|claude-haiku-4|"
        r"grok-4|grok-3-mini|glm-4\.6|qwen3|kimi-?k2", re.I)),
    ("vision", re.compile(
        r"vision|-vl\b|vl-|gpt-4o|gpt-4\.1|gpt-4-turbo|gpt-5|claude-3|claude-sonnet|"
        r"claude-opus|claude-haiku|gemini|pixtral|internvl|moondream|llava|"
        r"qwen.{0,4}vl|glm-4v|grok-4|o3\b|o4", re.I)),
    ("tools", re.compile(
        r"claude|gpt-4|gpt-5|o[134]|gemini|llama-3|llama-4|grok|mistral|ministral|"
        r"qwen|deepseek|glm|kimi|ernie|command-r|firefunction", re.I)),
    ("image_gen", re.compile(
        r"dall-e|gpt-image|image-generation|flux|sdxl|stable-?diff|imagen|ideogram|"
        r"recraft|seedream|grok-2-image|firefly|midjourney|photon|banana", re.I)),
    ("video", re.compile(
        r"sora|veo|kling|runway|luma|dream-?machine|hailuo|minimax-video|\bwan\b|"
        r"seedance|\bpika\b|vidu|video", re.I)),
    ("tts", re.compile(r"tts|audio-speech|-speech", re.I)),
    ("transcription", re.compile(r"whisper|transcri|-stt\b", re.I)),
    ("audio_in", re.compile(r"whisper|transcri|gpt-4o-audio|\baudio\b", re.I)),
    ("embeddings", re.compile(r"embed", re.I)),
]


def infer_capabilities(raw_model: Any) -> Dict[str, bool]:
    """Best-effort capability map from a provider /models entry (or bare id)."""
    caps = {k: False for k in CAP_KEYS}
    mid = ""
    if isinstance(raw_model, str):
        mid = raw_model
    elif isinstance(raw_model, dict):
        mid = str(raw_model.get("id") or raw_model.get("name") or raw_model.get("model") or "")
    else:
        return caps
    for key, rx in _CAP_RULES:
        if mid and rx.search(mid):
            caps[key] = True
    if isinstance(raw_model, dict):
        # structured metadata (OpenRouter /architecture, others may follow)
        arch = raw_model.get("architecture")
        if isinstance(arch, dict):
            if arch.get("tool_use"):
                caps["tools"] = True
            inp = arch.get("input_modalities") or []
            out = arch.get("output_modalities") or []
            if isinstance(inp, list):
                if "image" in inp:
                    caps["vision"] = True
                if "audio" in inp:
                    caps["audio_in"] = True
            if isinstance(out, list):
                if "image" in out:
                    caps["image_gen"] = True
                if "video" in out:
                    caps["video"] = True
                if "audio" in out:
                    caps["tts"] = True
        params = raw_model.get("supported_parameters")
        if isinstance(params, list) and "reasoning" in params:
            caps["reasoning"] = True
    return caps


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
    """Returns [{"id", "is_free", "capabilities", "context_length", "max_output"}]."""
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
            out.append({"id": m, "is_free": False, "capabilities": infer_capabilities(m),
                        "context_length": 0, "max_output": 0})
            continue
        mid = m.get("id") or m.get("name") or m.get("model")
        if not mid:
            continue
        # Google native style: "models/gemini-x" -> "gemini-x"
        if isinstance(mid, str) and mid.startswith("models/"):
            mid = mid.split("/", 1)[1]
        tp = m.get("top_provider") if isinstance(m.get("top_provider"), dict) else {}
        try:
            ctx = int(m.get("context_length") or m.get("context_window") or 0)
        except (TypeError, ValueError):
            ctx = 0
        try:
            mx = int(tp.get("max_completion_tokens") or 0)
        except (TypeError, ValueError):
            mx = 0
        out.append({
            "id": mid,
            "is_free": _is_free(m),
            "capabilities": infer_capabilities(m),
            "context_length": ctx,
            "max_output": mx,
        })
    # dedupe, stable order
    seen, uniq = set(), []
    for m in sorted(out, key=lambda x: x["id"]):
        if m["id"] not in seen:
            seen.add(m["id"])
            uniq.append(m)
    return uniq


# --------------------------------------------------------------------------
# anthropic <-> openai: content helpers
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


def _image_part_to_anthropic(url: str) -> Dict[str, Any]:
    if url.startswith("data:"):
        header, _, b64 = url.partition(",")
        media = header[5:].split(";")[0] or "image/png"
        return {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}}
    return {"type": "image", "source": {"type": "url", "url": url}}


def _parts_to_anthropic(content: Any) -> List[Dict[str, Any]]:
    """OpenAI content (str | parts list) -> anthropic content blocks (text/image/audio)."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    parts: List[Dict[str, Any]] = []
    if isinstance(content, list):
        for p in content:
            if not isinstance(p, dict):
                continue
            t = p.get("type")
            if t == "text":
                parts.append({"type": "text", "text": p.get("text", "")})
            elif t == "image_url":
                url = (p.get("image_url") or {}).get("url", "")
                if url:
                    parts.append(_image_part_to_anthropic(url))
            elif t == "input_audio":
                ia = p.get("input_audio") or {}
                fmt = ia.get("format") or "wav"
                parts.append({
                    "type": "audio",
                    "source": {"type": "base64", "media_type": f"audio/{fmt}", "data": ia.get("data", "")},
                })
    return parts


def _tools_to_anthropic(tools: List[Any]) -> List[Dict[str, Any]]:
    out = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if t.get("name") and "input_schema" in (t or {}):
            out.append(t)  # already anthropic shape
        elif t.get("type") == "function" and isinstance(t.get("function"), dict):
            fn = t["function"]
            out.append({
                "name": fn.get("name") or "",
                "description": fn.get("description") or "",
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
    return out


def _tool_choice_to_anthropic(tc: Any) -> Optional[Dict[str, Any]]:
    if tc is None:
        return None
    if isinstance(tc, str):
        if tc == "required":
            return {"type": "any"}
        if tc == "auto":
            return {"type": "auto"}
        return None  # "none" -> tools omitted by caller
    if isinstance(tc, dict):
        if tc.get("type") == "function":
            name = (tc.get("function") or {}).get("name") or tc.get("name")
            if name:
                return {"type": "tool", "name": name}
        if tc.get("type") in ("any", "auto", "tool") and not tc.get("function"):
            return {"type": tc["type"], **({"name": tc["name"]} if tc.get("name") else {})}
    return None


def thinking_budget_from_payload(payload: Dict[str, Any]) -> int:
    """Resolve an anthropic thinking budget from any client-side convention."""
    t = payload.get("thinking")
    if isinstance(t, dict) and t.get("type") == "enabled":
        try:
            return max(1024, int(t.get("budget_tokens") or 8192))
        except (TypeError, ValueError):
            return 8192
    if isinstance(payload.get("_nova_thinking"), int) and payload["_nova_thinking"] > 0:
        return payload["_nova_thinking"]
    eff: Optional[str] = None
    if isinstance(payload.get("reasoning_effort"), str):
        eff = payload["reasoning_effort"].lower()
    r = payload.get("reasoning")
    if isinstance(r, dict) and isinstance(r.get("effort"), str):
        eff = r["effort"].lower()
    if eff == "auto":
        eff = "medium"
    if payload.get("enable_thinking"):
        eff = eff or "medium"
    if eff in REASONING_EFFORT_BUDGETS:
        return REASONING_EFFORT_BUDGETS[eff]
    if isinstance(r, dict) and r.get("budget_tokens"):
        try:
            return max(1024, int(r["budget_tokens"]))
        except (TypeError, ValueError):
            pass
    return 0


def openai_to_anthropic(payload: Dict[str, Any]) -> Dict[str, Any]:
    system_parts: List[str] = []
    messages: List[Dict[str, Any]] = []
    pending_tools: List[Dict[str, Any]] = []  # tool_result blocks awaiting a user turn

    def flush_tool_results(with_user_content: Optional[List[Dict[str, Any]]] = None) -> None:
        nonlocal pending_tools
        if not pending_tools and not with_user_content:
            return
        content = list(pending_tools) + (with_user_content or [])
        messages.append({"role": "user", "content": content or [{"type": "text", "text": ""}]})
        pending_tools = []

    for msg in payload.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role in ("system", "developer"):
            system_parts.append(_text_of(msg.get("content")))
            continue
        if role == "tool":
            pending_tools.append({
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id") or "",
                "content": [{"type": "text", "text": _text_of(msg.get("content"))}],
            })
            continue
        if role == "assistant":
            flush_tool_results()
            blocks: List[Dict[str, Any]] = _parts_to_anthropic(msg.get("content"))
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (ValueError, TypeError):
                    args = {"_raw": fn.get("arguments")}
                blocks.append({
                    "type": "tool_use", "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:16]}",
                    "name": fn.get("name") or "", "input": args,
                })
            messages.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            continue
        # user
        parts = _parts_to_anthropic(msg.get("content"))
        if pending_tools:
            flush_tool_results(parts)
        else:
            messages.append({"role": "user", "content": parts or [{"type": "text", "text": ""}]})
    flush_tool_results()

    budget = thinking_budget_from_payload(payload)
    requested_max = payload.get("max_tokens") or payload.get("max_completion_tokens")
    max_tokens = int(requested_max or (8192 if budget else 4096))
    if budget:
        # extended thinking: temperature/top_p must stay default, max_tokens > budget
        if max_tokens <= budget:
            max_tokens = budget + 2048

    body: Dict[str, Any] = {
        "model": payload.get("model"),
        "messages": messages or [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "max_tokens": max_tokens,
    }
    if system_parts:
        body["system"] = "\n\n".join(p for p in system_parts if p)
    if budget:
        body["thinking"] = {"type": "enabled", "budget_tokens": budget}
    else:
        # sampling params are only legal when thinking is off
        if payload.get("temperature") is not None:
            body["temperature"] = payload.get("temperature")
        if payload.get("top_p") is not None:
            body["top_p"] = payload.get("top_p")
        stop = payload.get("stop")
        if stop:
            body["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
    if payload.get("stream"):
        body["stream"] = True
    tools = _tools_to_anthropic(payload.get("tools"))
    tc = payload.get("tool_choice")
    if tools and tc != "none":
        body["tools"] = tools
        choice = _tool_choice_to_anthropic(tc)
        if choice:
            body["tool_choice"] = choice
    if payload.get("metadata") and isinstance(payload.get("metadata"), dict):
        body["metadata"] = payload["metadata"]
    elif payload.get("user"):
        body["metadata"] = {"user_id": str(payload["user"])[:255]}
    return body


_STOP_MAP = {
    "end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length",
    "tool_use": "tool_calls",
}


def anthropic_to_openai(body: Dict[str, Any], model: str) -> Dict[str, Any]:
    text_parts: List[str] = []
    thinking_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    for block in body.get("content") or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "thinking":
            thinking_parts.append(block.get("thinking", ""))
        elif btype == "tool_use":
            tool_calls.append({
                "id": block.get("id") or f"call_{uuid.uuid4().hex[:16]}",
                "type": "function",
                "function": {
                    "name": block.get("name") or "",
                    "arguments": json.dumps(block.get("input") or {}),
                },
            })
    text = "".join(text_parts)
    message: Dict[str, Any] = {"role": "assistant", "content": text if (text or not tool_calls) else None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if thinking_parts:
        joined = "".join(thinking_parts)
        message["reasoning"] = joined
        message["reasoning_content"] = joined
    usage = body.get("usage") or {}
    return {
        "id": body.get("id") or f"chatcmpl-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model") or model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": _STOP_MAP.get(body.get("stop_reason"), "stop"),
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
    }


class AnthropicSSETranslator:
    """Stateful line-by-line Anthropic SSE -> OpenAI chat.completion.chunk converter.

    Handles: text, thinking (-> reasoning/reasoning_content), tool_use streaming
    (content_block_start + input_json_delta), usage capture, finish mapping.
    """

    def __init__(self, model: str, chunk_id: Optional[str] = None):
        self.model = model
        self.chunk_id = chunk_id or f"chatcmpl-{uuid.uuid4().hex[:20]}"
        self.usage_in = 0
        self.usage_out = 0
        self.done_sent = False
        self._tool_by_block: Dict[int, int] = {}  # anthropic block idx -> openai tool idx

    def _chunk(self, delta: Dict[str, Any], finish: Optional[str] = None) -> str:
        obj = {
            "id": self.chunk_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if finish is not None:
            obj["usage"] = {
                "prompt_tokens": self.usage_in,
                "completion_tokens": self.usage_out,
                "total_tokens": self.usage_in + self.usage_out,
            }
        return "data: " + json.dumps(obj)

    def convert(self, raw_line: str) -> Optional[str]:
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

        if etype == "message_start":
            msg = evt.get("message") or {}
            u = msg.get("usage") or {}
            self.usage_in = int(u.get("input_tokens") or 0)
            return self._chunk({"role": "assistant", "content": ""})

        if etype == "content_block_start":
            block = evt.get("content_block") or {}
            if block.get("type") == "tool_use":
                idx = evt.get("index", 0)
                tool_idx = len(self._tool_by_block)
                self._tool_by_block[idx] = tool_idx
                return self._chunk({"tool_calls": [{
                    "index": tool_idx,
                    "id": block.get("id") or f"call_{uuid.uuid4().hex[:16]}",
                    "type": "function",
                    "function": {"name": block.get("name") or "", "arguments": ""},
                }]})
            return None

        if etype == "content_block_delta":
            d = evt.get("delta") or {}
            dtype = d.get("type")
            if dtype == "text_delta":
                if d.get("text"):
                    return self._chunk({"content": d.get("text", "")})
                return None
            if dtype == "thinking_delta":
                if d.get("thinking"):
                    return self._chunk({"reasoning": d.get("thinking", ""),
                                        "reasoning_content": d.get("thinking", "")})
                return None
            if dtype == "input_json_delta":
                idx = self._tool_by_block.get(evt.get("index", -1))
                if idx is not None and d.get("partial_json"):
                    return self._chunk({"tool_calls": [{
                        "index": idx,
                        "function": {"arguments": d.get("partial_json", "")},
                    }]})
                return None
            return None  # signature_delta etc.

        if etype == "message_delta":
            d = evt.get("delta") or {}
            u = evt.get("usage") or {}
            self.usage_out = int(u.get("output_tokens") or self.usage_out)
            reason = d.get("stop_reason")
            return self._chunk({}, finish=_STOP_MAP.get(reason, "stop"))

        if etype == "message_stop":
            self.done_sent = True
            return "data: [DONE]"

        if etype == "error":
            err = evt.get("error") or {}
            return "data: " + json.dumps({
                "error": {"message": err.get("message") or "upstream error",
                          "type": err.get("type") or "upstream_error"},
            })
        return None


def anthropic_sse_to_openai(raw_line: str, model: str, chunk_id: str) -> Optional[str]:
    """Back-compat wrapper (stateless single line)."""
    return AnthropicSSETranslator(model, chunk_id).convert(raw_line)


# --------------------------------------------------------------------------
# request building
# --------------------------------------------------------------------------

_OPENAI_DROP_KEYS = ("_nova_thinking", "_nova", "nova", "auto_tools", "spoof_model")


def spoof_sse_line(line: str, model: str) -> str:
    """Rewrite an SSE data line so the model id inside matches what the client
    asked for (openai chunk root, or anthropic message_start's nested message)."""
    if not line.startswith("data:"):
        return line
    raw = line[5:].strip()
    if not raw or raw == "[DONE]":
        return line
    try:
        obj = json.loads(raw)
    except ValueError:
        return line
    if not isinstance(obj, dict):
        return line
    if "model" in obj:
        obj["model"] = model
    elif isinstance(obj.get("message"), dict) and "model" in obj["message"]:
        obj["message"]["model"] = model
    else:
        return line
    return "data: " + json.dumps(obj, ensure_ascii=False)


def build_request(
    provider: Dict[str, Any],
    api_key: str,
    endpoint: str,
    payload: Dict[str, Any],
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """endpoint: 'chat' | 'completions' | 'embeddings'. Returns (url, headers, body)."""
    kind = provider.get("kind", "openai")
    headers = auth_headers(provider, api_key)

    if kind == "anthropic":
        if endpoint != "chat":
            raise ValueError(f"anthropic provider does not support endpoint '{endpoint}'")
        return f"{_base(provider)}/messages", headers, openai_to_anthropic(payload)

    path = {
        "chat": "/chat/completions",
        "completions": "/completions",
        "embeddings": "/embeddings",
        "moderations": "/moderations",
    }[endpoint]
    body = {k: v for k, v in payload.items() if k not in _OPENAI_DROP_KEYS}
    return f"{_base(provider)}{path}", headers, body


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


# --------------------------------------------------------------------------
# Responses API <-> Chat Completions
# --------------------------------------------------------------------------

def _resp_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") in ("input_text", "output_text", "text")
        )
    return ""


def _resp_content_to_openai(content: Any) -> Any:
    if isinstance(content, str) or content is None:
        return content or ""
    if not isinstance(content, list):
        return ""
    parts: List[Any] = []
    for p in content:
        if not isinstance(p, dict):
            continue
        t = p.get("type")
        if t in ("input_text", "output_text", "text"):
            parts.append({"type": "text", "text": p.get("text", "")})
        elif t == "input_image":
            url = (p.get("image_url") or {}).get("url") or p.get("url") or ""
            if url:
                parts.append({"type": "image_url", "image_url": {"url": url}})
        elif t == "input_audio":
            parts.append({"type": "input_audio", "input_audio": {
                "data": p.get("data") or (p.get("input_audio") or {}).get("data", ""),
                "format": p.get("format") or (p.get("input_audio") or {}).get("format", "wav"),
            }})
    return parts or ""


def responses_to_chat(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Translate an OpenAI Responses API request into a Chat Completions request."""
    messages: List[Dict[str, Any]] = []
    if payload.get("instructions"):
        messages.append({"role": "system", "content": payload["instructions"]})

    inp = payload.get("input")
    items: List[Any]
    if isinstance(inp, str):
        items = [{"type": "message", "role": "user", "content": inp}]
    elif isinstance(inp, list):
        items = inp
    else:
        items = []

    pending: Optional[Dict[str, Any]] = None  # assistant turn being assembled

    def flush_pending() -> None:
        nonlocal pending
        if pending is not None:
            if pending.get("tool_calls"):
                messages.append({"role": "assistant", "content": pending.get("content") or None,
                                "tool_calls": pending["tool_calls"]})
            elif pending.get("content"):
                messages.append({"role": "assistant", "content": pending["content"]})
            pending = None

    for it in items:
        if not isinstance(it, dict):
            continue
        t = it.get("type")
        if t == "message":
            role = it.get("role") or "user"
            if role == "developer":
                role = "system"
            if role == "assistant":
                txt = _resp_text(it.get("content"))
                if pending is not None:
                    pending["content"] = (pending.get("content") or "") + txt
                else:
                    pending = {"content": txt, "tool_calls": []}
                continue
            flush_pending()
            if role == "system":
                messages.append({"role": "system", "content": _resp_text(it.get("content"))})
            else:
                messages.append({"role": "user", "content": _resp_content_to_openai(it.get("content"))})
        elif t == "function_call":
            if pending is None:
                pending = {"content": "", "tool_calls": []}
            pending["tool_calls"].append({
                "id": it.get("call_id") or it.get("id") or f"call_{uuid.uuid4().hex[:16]}",
                "type": "function",
                "function": {"name": it.get("name") or "", "arguments": it.get("arguments") or "{}"},
            })
        elif t == "function_call_output":
            flush_pending()
            out = it.get("output")
            if not isinstance(out, str):
                out = json.dumps(out or "")
            messages.append({
                "role": "tool",
                "tool_call_id": it.get("call_id") or "",
                "content": out,
            })
        # "reasoning" items are internal -> skip
    flush_pending()

    body: Dict[str, Any] = {"model": payload.get("model", ""), "messages": messages}

    tools_out = []
    for t in payload.get("tools") or []:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "function" and isinstance(t.get("function"), dict):
            tools_out.append(t)  # already chat shape
        elif t.get("name"):
            tools_out.append({
                "type": "function",
                "function": {
                    "name": t.get("name"),
                    "description": t.get("description") or "",
                    "parameters": t.get("parameters") or {"type": "object", "properties": {}},
                },
            })
    if tools_out:
        body["tools"] = tools_out
        tc = payload.get("tool_choice")
        if isinstance(tc, str) and tc in ("auto", "none", "required"):
            body["tool_choice"] = tc
        elif isinstance(tc, dict) and tc.get("type") == "function":
            name = tc.get("name") or (tc.get("function") or {}).get("name")
            if name:
                body["tool_choice"] = {"type": "function", "function": {"name": name}}

    if payload.get("temperature") is not None:
        body["temperature"] = payload["temperature"]
    if payload.get("top_p") is not None:
        body["top_p"] = payload["top_p"]
    if payload.get("max_output_tokens"):
        body["max_tokens"] = payload["max_output_tokens"]
    if payload.get("stream"):
        body["stream"] = True
    if payload.get("parallel_tool_calls") is not None:
        body["parallel_tool_calls"] = payload["parallel_tool_calls"]
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort"):
        body["reasoning_effort"] = reasoning["effort"]
    text = payload.get("text")
    if isinstance(text, dict) and isinstance(text.get("format"), dict):
        fmt = text["format"]
        if fmt.get("type") == "json_schema":
            body["response_format"] = {"type": "json_schema", "json_schema": fmt}
    return body


def chat_to_responses(body: Dict[str, Any], model: Optional[str] = None) -> Dict[str, Any]:
    """Translate a Chat Completions response into a Responses API response."""
    rid = "resp_" + uuid.uuid4().hex[:24]
    msg = ((body.get("choices") or [{}])[0].get("message")) or {}
    text = msg.get("content") or ""
    output: List[Dict[str, Any]] = []
    if text or not msg.get("tool_calls"):
        output.append({
            "type": "message", "id": "msg_" + uuid.uuid4().hex[:16], "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text or "", "annotations": []}],
        })
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        output.append({
            "type": "function_call", "id": "fc_" + uuid.uuid4().hex[:16],
            "call_id": tc.get("id"), "name": fn.get("name") or "",
            "arguments": fn.get("arguments") or "{}", "status": "completed",
        })
    u = body.get("usage") or {}
    return {
        "id": rid, "object": "response", "created_at": int(time.time()),
        "status": "completed", "model": model or body.get("model", ""),
        "output": output,
        "usage": {
            "input_tokens": u.get("prompt_tokens", 0),
            "output_tokens": u.get("completion_tokens", 0),
            "total_tokens": u.get("total_tokens", 0),
        },
        "output_text": text,
    }


class ChatToResponsesSSE:
    """Stateful chat.completion.chunk -> Responses SSE event converter."""

    def __init__(self, model: str):
        self.model = model
        self.resp_id = "resp_" + uuid.uuid4().hex[:24]
        self.started = False
        self.usage: Dict[str, int] = {}
        self.items: List[Dict[str, Any]] = []       # output items being assembled
        self.text = ""
        self._msg_open = False
        self._call_by_index: Dict[int, Dict[str, Any]] = {}

    def _skeleton(self) -> Dict[str, Any]:
        return {
            "id": self.resp_id, "object": "response", "created_at": int(time.time()),
            "status": "in_progress", "model": self.model, "output": [],
        }

    def _final_response(self) -> Dict[str, Any]:
        return {
            "id": self.resp_id, "object": "response", "created_at": int(time.time()),
            "status": "completed", "model": self.model, "output": self.items,
            "usage": {
                "input_tokens": self.usage.get("prompt_tokens", 0),
                "output_tokens": self.usage.get("completion_tokens", 0),
                "total_tokens": self.usage.get("total_tokens", 0),
            },
            "output_text": self.text,
        }

    def feed(self, chunk: Dict[str, Any]) -> List[Dict[str, Any]]:
        evts: List[Dict[str, Any]] = []
        if not self.started:
            self.started = True
            evts.append({"type": "response.created", "response": self._skeleton()})
            evts.append({"type": "response.in_progress", "response": self._skeleton()})

        u = chunk.get("usage")
        if isinstance(u, dict) and u:
            self.usage = u

        choices = chunk.get("choices") or []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or {}

        # ---- text
        content = delta.get("content")
        if isinstance(content, str) and content:
            if not self._msg_open:
                self._msg_open = True
                item = {"type": "message", "id": "msg_" + uuid.uuid4().hex[:16],
                        "status": "in_progress", "role": "assistant", "content": []}
                self.items.append(item)
                evts.append({"type": "response.output_item.added",
                             "output_index": len(self.items) - 1, "item": item})
            self.text += content
            evts.append({"type": "response.output_text.delta",
                         "output_index": len(self.items) - 1, "content_index": 0,
                         "delta": content})

        # ---- tool calls
        for tc in delta.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            idx = int(tc.get("index", 0) or 0)
            fn = tc.get("function") or {}
            call = self._call_by_index.get(idx)
            if call is None or (tc.get("id") and call.get("call_id") != tc["id"]):
                name = (fn.get("name") or (call or {}).get("name") or "")
                call = {
                    "type": "function_call", "id": "fc_" + uuid.uuid4().hex[:16],
                    "call_id": tc.get("id") or f"call_{uuid.uuid4().hex[:16]}",
                    "name": name, "arguments": "", "status": "in_progress",
                }
                self._call_by_index[idx] = call
                self.items.append(call)
                evts.append({"type": "response.output_item.added",
                             "output_index": len(self.items) - 1, "item": dict(call, arguments="")})
            if fn.get("name"):
                call["name"] = fn["name"]
            if fn.get("arguments"):
                call["arguments"] += fn["arguments"]
                evts.append({"type": "response.function_call_arguments.delta",
                             "output_index": len(self.items) - 1, "item_id": call["id"],
                             "delta": fn["arguments"]})

        # ---- thinking (passthrough as reasoning summary text)
        reasoning = delta.get("reasoning") or delta.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            if not any(i.get("type") == "reasoning" for i in self.items):
                item = {"type": "reasoning", "id": "rs_" + uuid.uuid4().hex[:16],
                        "summary": [], "content": []}
                self.items.append(item)
                evts.append({"type": "response.output_item.added",
                             "output_index": len(self.items) - 1, "item": item})
            evts.append({"type": "response.reasoning_summary_text.delta",
                         "output_index": len(self.items) - 1, "content_index": 0,
                         "delta": reasoning})
        return evts

    def finish(self) -> List[Dict[str, Any]]:
        """Called on [DONE] / stream end. Closes items + emits response.completed."""
        evts: List[Dict[str, Any]] = []
        for i, item in enumerate(self.items):
            if item.get("type") == "message" and item.get("status") == "in_progress":
                item["status"] = "completed"
                item["content"] = [{"type": "output_text", "text": self.text, "annotations": []}]
                evts.append({"type": "response.output_text.done", "output_index": i,
                             "content_index": 0, "text": self.text})
                evts.append({"type": "response.output_item.done", "output_index": i, "item": item})
            elif item.get("type") == "function_call" and item.get("status") != "completed":
                item["status"] = "completed"
                evts.append({"type": "response.output_item.done", "output_index": i, "item": item})
        evts.append({"type": "response.completed", "response": self._final_response()})
        return evts


def extract_usage(body: Any) -> Tuple[int, int]:
    """Generic usage extraction from any provider's response body."""
    if not isinstance(body, dict):
        return 0, 0
    u = body.get("usage")
    if isinstance(u, dict):
        ti = u.get("prompt_tokens") or u.get("input_tokens") or 0
        to = u.get("completion_tokens") or u.get("output_tokens") or 0
        try:
            return int(ti), int(to)
        except (TypeError, ValueError):
            return 0, 0
    return 0, 0


# --------------------------------------------------------------------------
# Anthropic native (/v1/messages) on OpenAI-kind upstreams
# --------------------------------------------------------------------------

def anthropic_request_to_openai(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Native Anthropic Messages request -> OpenAI Chat Completions request."""
    messages: List[Dict[str, Any]] = []
    if payload.get("system"):
        sysv = payload["system"]
        if isinstance(sysv, list):
            sysv = "".join(b.get("text", "") for b in sysv if isinstance(b, dict))
        messages.append({"role": "system", "content": sysv})

    for msg in payload.get("messages") or []:
        role = msg.get("role") or "user"
        content = msg.get("content")
        if isinstance(content, str):
            messages.append({"role": "assistant" if role == "assistant" else role,
                             "content": content})
            continue
        blocks = content if isinstance(content, list) else []
        tool_calls = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
        tool_results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
        if tool_results:
            for tr in tool_results:
                inner = tr.get("content")
                if isinstance(inner, list):
                    inner = "".join(
                        b.get("text", "") for b in inner if isinstance(b, dict)
                    )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr.get("tool_use_id") or "",
                    "content": inner if isinstance(inner, str) else json.dumps(inner or ""),
                })
            text_blocks = [b.get("text", "") for b in blocks
                           if isinstance(b, dict) and b.get("type") == "text"]
            if text_blocks:
                messages.append({"role": "user", "content": "".join(text_blocks)})
            continue
        if role == "assistant":
            parts: List[Any] = [
                {"type": "text", "text": b.get("text", "")}
                for b in blocks if isinstance(b, dict) and b.get("type") == "text"
            ]
            # image blocks in assistant turns can't be expressed in openai -> skip
            tc_out = [{
                "id": b.get("id") or f"call_{uuid.uuid4().hex[:16]}",
                "type": "function",
                "function": {"name": b.get("name") or "",
                             "arguments": json.dumps(b.get("input") or {})},
            } for b in tool_calls]
            entry: Dict[str, Any] = {"role": "assistant",
                                     "content": "".join(p["text"] for p in parts) or None}
            if tc_out:
                entry["tool_calls"] = tc_out
            messages.append(entry)
            continue
        # user turn
        user_parts: List[Any] = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                user_parts.append({"type": "text", "text": b.get("text", "")})
            elif bt == "image":
                src = b.get("source") or {}
                if src.get("type") == "base64":
                    user_parts.append({"type": "image_url",
                                       "image_url": {"url": f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"}})
                elif src.get("type") == "url":
                    user_parts.append({"type": "image_url", "image_url": {"url": src.get("url", "")}})
            elif bt == "audio":
                src = b.get("source") or {}
                user_parts.append({"type": "input_audio", "input_audio": {
                    "data": src.get("data", ""),
                    "format": (src.get("media_type") or "audio/wav").split("/")[-1],
                }})
            elif bt == "tool_result":
                pass  # handled above in theory; skip stragglers
        messages.append({"role": "user",
                         "content": user_parts or ""})

    body: Dict[str, Any] = {"model": payload.get("model", ""), "messages": messages}
    if payload.get("max_tokens"):
        body["max_tokens"] = payload["max_tokens"]
    t = payload.get("thinking")
    if isinstance(t, dict) and t.get("type") == "enabled":
        body["reasoning_effort"] = "high" if (t.get("budget_tokens") or 0) > 10000 else "medium"
    if payload.get("temperature") is not None and not t:
        body["temperature"] = payload["temperature"]
    if payload.get("top_p") is not None and not t:
        body["top_p"] = payload["top_p"]
    if payload.get("stream"):
        body["stream"] = True
    tools = payload.get("tools")
    if tools:
        body["tools"] = [{
            "type": "function",
            "function": {
                "name": t_.get("name") or "",
                "description": t_.get("description") or "",
                "parameters": t_.get("input_schema") or {"type": "object", "properties": {}},
            },
        } for t_ in tools if isinstance(t_, dict)]
        tc = payload.get("tool_choice")
        if isinstance(tc, dict):
            if tc.get("type") == "any":
                body["tool_choice"] = "required"
            elif tc.get("type") == "auto":
                body["tool_choice"] = "auto"
            elif tc.get("type") == "tool" and tc.get("name"):
                body["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
    return body


_OPENAI_STOP_MAP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}


def openai_chat_to_anthropic(body: Dict[str, Any], model: str) -> Dict[str, Any]:
    """OpenAI Chat Completions response -> native Anthropic Messages response."""
    choice = (body.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content: List[Dict[str, Any]] = []
    reasoning = msg.get("reasoning") or msg.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning:
        content.append({"type": "thinking", "thinking": reasoning})
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (ValueError, TypeError):
            args = {}
        content.append({"type": "tool_use", "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:16]}",
                        "name": fn.get("name") or "", "input": args})
    if not content:
        content.append({"type": "text", "text": ""})
    u = body.get("usage") or {}
    stop = _OPENAI_STOP_MAP.get(choice.get("finish_reason"), "end_turn")
    return {
        "id": body.get("id") or f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": body.get("model") or model,
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {
            "input_tokens": u.get("prompt_tokens", 0),
            "output_tokens": u.get("completion_tokens", 0),
        },
    }


class ChatToAnthropicSSE:
    """Stateful OpenAI chat.completion.chunk -> native Anthropic SSE converter.

    Lets Claude Code stream live from any OpenAI-kind upstream.
    """

    def __init__(self, model: str):
        self.model = model
        self.msg_id = "msg_" + uuid.uuid4().hex[:24]
        self.usage_in = 0
        self.usage_out = 0
        self.started = False
        self._text_open = False
        self._tool_idx = -1
        self._tools: List[Dict[str, str]] = []   # {"id":..., "name":..., "args":...}
        self._final_stop = "end_turn"
        self._reasoning_open = False

    @staticmethod
    def _sse(evt: Dict[str, Any]) -> str:
        return "event: " + evt.get("type", "") + "\ndata: " + json.dumps(evt) + "\n\n"

    def _start_if_needed(self) -> List[str]:
        if self.started:
            return []
        self.started = True
        first = {
            "type": "message_start",
            "message": {
                "id": self.msg_id, "type": "message", "role": "assistant", "model": self.model,
                "content": [], "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        }
        return [self._sse(first)]

    def feed(self, chunk: Dict[str, Any]) -> List[str]:
        out: List[str] = list(self._start_if_needed())
        u = chunk.get("usage")
        if isinstance(u, dict):
            self.usage_in = int(u.get("prompt_tokens") or self.usage_in)
            self.usage_out = int(u.get("completion_tokens") or self.usage_out)
        choices = chunk.get("choices") or []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or {}
        finish = choice.get("finish_reason")

        content = delta.get("content")
        if isinstance(content, str) and content:
            if not self._text_open:
                self._text_open = True
                out.append(self._sse({"type": "content_block_start", "index": 0,
                                      "content_block": {"type": "text", "text": ""}}))
            out.append(self._sse({"type": "content_block_delta", "index": 0,
                                  "delta": {"type": "text_delta", "text": content}}))

        reasoning = delta.get("reasoning") or delta.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            if not self._reasoning_open:
                self._reasoning_open = True
                out.append(self._sse({"type": "content_block_start", "index": 0,
                                      "content_block": {"type": "thinking", "thinking": ""}}))
            out.append(self._sse({"type": "content_block_delta", "index": 0,
                                  "delta": {"type": "thinking_delta", "thinking": reasoning}}))

        for tc in delta.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            idx = int(tc.get("index", 0) or 0)
            fn = tc.get("function") or {}
            while idx >= len(self._tools):
                self._tools.append({"id": "", "name": "", "args": ""})
            cur = self._tools[idx]
            if tc.get("id") and not cur["id"]:
                cur["id"] = tc["id"]
                cur["name"] = fn.get("name") or cur["name"]
                block_idx = 1 + idx
                out.append(self._sse({
                    "type": "content_block_start", "index": block_idx,
                    "content_block": {"type": "tool_use", "id": cur["id"],
                                      "name": cur["name"], "input": {}},
                }))
            if fn.get("name"):
                cur["name"] = fn["name"]
            if fn.get("arguments"):
                cur["args"] += fn["arguments"]
                out.append(self._sse({
                    "type": "content_block_delta", "index": 1 + idx,
                    "delta": {"type": "input_json_delta", "partial_json": fn["arguments"]},
                }))

        if finish:
            self._final_stop = _OPENAI_STOP_MAP.get(finish, "end_turn")
        return out

    def finish(self) -> List[str]:
        out: List[str] = list(self._start_if_needed())
        if self._text_open:
            out.append(self._sse({"type": "content_block_stop", "index": 0}))
        for i, t_ in enumerate(self._tools):
            if t_["id"]:
                out.append(self._sse({"type": "content_block_stop", "index": 1 + i}))
        out.append(self._sse({
            "type": "message_delta",
            "delta": {"stop_reason": self._final_stop, "stop_sequence": None},
            "usage": {"output_tokens": self.usage_out},
        }))
        out.append(self._sse({"type": "message_stop"}))
        return out
