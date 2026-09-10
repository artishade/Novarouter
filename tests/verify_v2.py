"""Deep verification of the v2 upgrade: routes + every translation path."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp(prefix="nova-ver-")
os.environ["NOVA_DATA_DIR"] = tmp
os.environ["NOVA_ADMIN_TOKEN"] = "t"
sys.path.insert(0, "/root/novarouter")

from app.main import app  # noqa: E402

routes = sorted({r.path for r in app.routes if getattr(r, "path", "").startswith("/v1")})
print("== GATEWAY ROUTES ==")
for p in routes:
    print("  ", p)

from app import adapters  # noqa: E402

# 1. anthropic tools translation (request)
payload = {
    "model": "claude-x",
    "messages": [
        {"role": "system", "content": "be nice"},
        {"role": "user", "content": "what is the weather?"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "get_weather", "arguments": '{"city": "SF"}'}}
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "sunny 72f"},
    ],
    "tools": [{"type": "function", "function": {
        "name": "get_weather", "description": "get weather",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}],
    "tool_choice": "auto",
}
body = adapters.openai_to_anthropic(payload)
assert body["system"] == "be nice", body
assert any(b.get("type") == "tool_use" for m in body["messages"] for b in m["content"])
assert any(b.get("type") == "tool_result" for m in body["messages"] for b in m["content"])
assert body["tools"][0]["name"] == "get_weather"
print("PASS 1  anthropic tools request translation")

# 2. anthropic response with tool_use + thinking
resp = adapters.anthropic_to_openai({
    "id": "msg_1", "model": "claude-x", "stop_reason": "tool_use",
    "content": [
        {"type": "thinking", "thinking": "hmm"},
        {"type": "text", "text": "let me check"},
        {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "SF"}},
    ],
    "usage": {"input_tokens": 10, "output_tokens": 5},
}, "claude-x")
msg = resp["choices"][0]["message"]
assert resp["choices"][0]["finish_reason"] == "tool_calls"
assert msg["reasoning"] == "hmm"
assert msg["tool_calls"][0]["function"]["name"] == "get_weather"
print("PASS 2  anthropic tools response translation")

# 3. thinking budget mapping
p2 = {"model": "x", "messages": [], "reasoning_effort": "high"}
b2 = adapters.openai_to_anthropic(p2)
assert b2["thinking"] == {"type": "enabled", "budget_tokens": 16000}, b2
assert "temperature" not in b2
assert b2["max_tokens"] > 16000
p3 = {"model": "x", "messages": [], "thinking": {"type": "enabled", "budget_tokens": 2000}}
b3 = adapters.openai_to_anthropic(p3)
assert b3["thinking"]["budget_tokens"] == 2000
print("PASS 3  thinking budget translation")

# 4. anthropic SSE tool streaming
tr = adapters.AnthropicSSETranslator("claude-x")
evts = []
for line in [
    'data: {"type":"message_start","message":{"usage":{"input_tokens":5}}}',
    'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"t1","name":"get"}}',
    'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"a\\"}"}}',
    'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":3}}',
    'data: {"type":"message_stop"}',
]:
    out = tr.convert(line)
    if out:
        evts.append(out)
assert any("tool_calls" in e for e in evts)
assert evts[-1] == "data: [DONE]"
print("PASS 4  anthropic SSE tool streaming")

# 5. responses -> chat translation
rp = {
    "model": "gpt", "instructions": "be terse",
    "input": [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        {"type": "function_call", "call_id": "fc1", "name": "f", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "fc1", "output": "42"},
    ],
    "tools": [{"type": "function", "name": "f", "description": "d",
               "parameters": {"type": "object", "properties": {}}}],
    "reasoning": {"effort": "high"},
    "max_output_tokens": 100,
}
cp = adapters.responses_to_chat(rp)
assert cp["messages"][0] == {"role": "system", "content": "be terse"}
assert any(m["role"] == "tool" for m in cp["messages"])
assert any(m.get("tool_calls") for m in cp["messages"])
assert cp["reasoning_effort"] == "high"
assert cp["max_tokens"] == 100
print("PASS 5  responses->chat translation")

# 6. chat -> responses
cr = adapters.chat_to_responses({
    "id": "c1", "model": "gpt",
    "choices": [{"message": {"role": "assistant", "content": "hello", "tool_calls": None},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
}, "gpt")
assert cr["output"][0]["content"][0]["text"] == "hello"
assert cr["usage"]["total_tokens"] == 5
print("PASS 6  chat->responses translation")

# 7. ChatToResponsesSSE streaming
s = adapters.ChatToResponsesSSE("gpt")
chunks = [
    {"choices": [{"delta": {"role": "assistant", "content": "he"}}]},
    {"choices": [{"delta": {"content": "llo"}}]},
    {"choices": [{"delta": {}, "finish_reason": "stop"}],
     "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}},
]
ev = []
for c in chunks:
    ev.extend(s.feed(c))
ev.extend(s.finish())
types = [e["type"] for e in ev]
assert "response.created" in types
assert "response.output_text.delta" in types
assert "response.completed" in types
print("PASS 7  ChatToResponsesSSE streaming")

# 8. anthropic native request -> openai (/v1/messages on openai upstream)
ap = {
    "model": "gpt", "system": "sys", "max_tokens": 100,
    "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "hi"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}},
        ]},
    ],
    "tools": [{"name": "t", "description": "d", "input_schema": {"type": "object"}}],
}
oc = adapters.anthropic_request_to_openai(ap)
assert oc["messages"][0]["role"] == "system"
assert any(p["type"] == "image_url" for p in oc["messages"][1]["content"])
assert oc["tools"][0]["function"]["name"] == "t"
print("PASS 8  anthropic-native request on openai upstream")

# 9. openai chat -> anthropic response (back-translation)
oa = adapters.openai_chat_to_anthropic({
    "id": "c2", "model": "gpt",
    "choices": [{"message": {"role": "assistant", "content": "hi",
                            "tool_calls": [{"id": "call_1", "function": {"name": "f", "arguments": '{"a":1}'}}]},
                 "finish_reason": "tool_calls"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}, "gpt")
assert oa["stop_reason"] == "tool_use"
assert oa["content"][1]["type"] == "tool_use"
print("PASS 9  openai->anthropic response back-translation")

# 10. ChatToAnthropicSSE streaming (Claude Code on openai upstream)
s2 = adapters.ChatToAnthropicSSE("gpt")
lines = []
for c in [
    {"choices": [{"delta": {"role": "assistant", "content": "yo"}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1",
                                            "function": {"name": "f", "arguments": "{}"}}]}}]},
    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
]:
    lines.extend(s2.feed(c))
lines.extend(s2.finish())
joined = "".join(lines)
assert "message_start" in joined
assert "content_block_start" in joined
assert "tool_use" in joined
assert "input_json_delta" in joined
assert "message_stop" in joined
print("PASS 10 ChatToAnthropicSSE (Claude Code streaming on openai upstream)")

# 11. capabilities
caps = adapters.infer_capabilities("google/gemini-2.5-flash")
assert caps["vision"] and caps["reasoning"] and caps["tools"]
caps2 = adapters.infer_capabilities("openai/dall-e-3")
assert caps2["image_gen"]
caps3 = adapters.infer_capabilities({"id": "openai/sora-2",
                                     "architecture": {"output_modalities": ["video"]}})
assert caps3["video"]
print("PASS 11 capability inference")

# 12. model suffix routing
base, suf = adapters.split_model_suffix("deepseek/deepseek-r1:thinking")
assert base == "deepseek/deepseek-r1" and suf == ":thinking"
print("PASS 12 model suffix split")

print()
print("ALL 12 VERIFICATION TESTS PASSED")