"""Wire-format helpers for upstream providers (not the public gateway format)."""
from __future__ import annotations

import json
import uuid


ANTHROPIC_VERSION = "2023-06-01"


def anthropic_url(base: str, path: str = "messages") -> str:
    """Accept both an API origin and a base ending in /v1."""
    root = base.rstrip("/")
    return f"{root}/{path}" if root.endswith("/v1") else f"{root}/v1/{path}"


def anthropic_headers(api_key: str) -> dict[str, str]:
    return {"Content-Type": "application/json", "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION}


def _tool_choice(choice):
    if choice == "required":
        return {"type": "any"}
    if choice == "auto":
        return {"type": "auto"}
    if isinstance(choice, dict) and choice.get("type") == "function":
        name = (choice.get("function") or {}).get("name")
        if name:
            return {"type": "tool", "name": name}
    return None


def openai_to_anthropic(model: str, messages: list[dict], options: dict | None = None,
                        stream: bool = False) -> dict:
    """Translate chat messages including tool calls/results to native Messages."""
    options = options or {}
    body: dict = {"model": model, "max_tokens": options.get("max_completion_tokens") or options.get("max_tokens") or 4096,
                  "messages": []}
    if stream:
        body["stream"] = True
    system: list[str] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            system.append(str(m.get("content") or ""))
            continue
        if role == "tool":
            body["messages"].append({"role": "user", "content": [{"type": "tool_result",
                "tool_use_id": m.get("tool_call_id") or "", "content": str(m.get("content") or "")}]})
            continue
        blocks: list[dict] = []
        content = m.get("content")
        if isinstance(content, str) and content:
            blocks.append({"type": "text", "text": content})
        elif isinstance(content, list):
            blocks.extend(b for b in content if isinstance(b, dict) and b.get("type") == "text")
        if role == "assistant":
            for call in m.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") or {}
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                blocks.append({"type": "tool_use", "id": call.get("id") or f"toolu_{uuid.uuid4().hex[:16]}",
                               "name": fn.get("name") or "", "input": args if isinstance(args, dict) else {}})
        body["messages"].append({"role": role if role in ("assistant", "user") else "user",
                                 "content": blocks or [{"type": "text", "text": ""}]})
    if system:
        body["system"] = "\n".join(system)
    for field in ("temperature", "top_p", "top_k"):
        if field in options and options[field] is not None:
            body[field] = options[field]
    if options.get("stop"):
        body["stop_sequences"] = options["stop"] if isinstance(options["stop"], list) else [options["stop"]]
    tools = []
    for tool in options.get("tools") or []:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        fn = tool.get("function") or {}
        if fn.get("name"):
            tools.append({"name": fn["name"], "description": fn.get("description") or "",
                          "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}})
    if tools:
        body["tools"] = tools
        choice = _tool_choice(options.get("tool_choice"))
        if choice:
            body["tool_choice"] = choice
    return body


def native_anthropic_body(payload: dict, model: str, stream: bool = False) -> dict:
    """Preserve native tool_result/tool_use content for /v1/messages clients."""
    body = {"model": model, "max_tokens": payload.get("max_tokens") or 4096, "messages": []}
    if stream:
        body["stream"] = True
    if payload.get("system"):
        body["system"] = payload["system"]
    for message in payload.get("messages") or []:
        if not isinstance(message, dict):
            continue
        copy = {"role": message.get("role"), "content": message.get("content")}
        if isinstance(copy["content"], list):
            copy["content"] = [b for b in copy["content"] if not isinstance(b, dict)
                               or b.get("type") not in ("thinking", "redacted_thinking")]
        body["messages"].append(copy)
    for field in ("temperature", "top_p", "top_k", "stop_sequences", "tools", "tool_choice"):
        if field in payload and payload[field] is not None:
            body[field] = payload[field]
    return body


def anthropic_to_openai(data: dict) -> dict | None:
    """Normalize a native response for the existing gateway fallback pipeline."""
    if not isinstance(data, dict) or not isinstance(data.get("content"), list):
        return None
    content = "".join(str(b.get("text") or "") for b in data["content"]
                      if isinstance(b, dict) and b.get("type") == "text")
    tools = [{"id": b.get("id"), "type": "function", "function": {
        "name": b.get("name"), "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}}
             for b in data["content"] if isinstance(b, dict) and b.get("type") == "tool_use"]
    if not content and not tools:
        return None
    reason = data.get("stop_reason")
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {"content": content or None, "tool_calls": tools or None,
            "upstream_message": {"role": "assistant", "content": content or None, **({"tool_calls": tools} if tools else {})},
            "finish_reason": {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length"}.get(reason, reason or "stop"),
            "usage": {"prompt_tokens": usage.get("input_tokens"), "completion_tokens": usage.get("output_tokens")}}
