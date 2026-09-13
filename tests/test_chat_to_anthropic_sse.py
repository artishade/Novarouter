"""Regression: ChatToAnthropicSSE must emit a well-formed Anthropic stream.

The old translator reused content-block index 0 for both thinking and text
blocks and never emitted content_block_stop — reasoning models (GLM,
DeepSeek-R1, ...) that stream reasoning_content before content produced a
malformed stream: duplicate content_block_start on the same index, deltas
for never-opened blocks. Clients (Claude Code etc.) parse nothing: the
request shows 200 success in the dashboard but the agent gets an empty
response.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters import ChatToAnthropicSSE  # noqa: E402


def _events(translator, chunks):
    evts = []
    for c in chunks:
        for line in translator.feed(c):
            evts.append(json.loads(line.split("data: ", 1)[1]))
    for line in translator.finish():
        evts.append(json.loads(line.split("data: ", 1)[1]))
    return evts


def _assert_valid(evts):
    """Anthropic protocol: one open block per index; start before delta; stop before message_stop."""
    open_idx = set()
    for evt in evts:
        et = evt.get("type")
        if et == "content_block_start":
            assert evt["index"] not in open_idx, f"double start on index {evt['index']}"
            open_idx.add(evt["index"])
        elif et == "content_block_stop":
            assert evt["index"] in open_idx, f"stop without start on index {evt['index']}"
            open_idx.discard(evt["index"])
        elif et == "content_block_delta":
            assert evt["index"] in open_idx, f"delta for unopened block {evt['index']}"
    assert not open_idx, f"blocks never stopped: {open_idx}"
    types = [e.get("type") for e in evts]
    assert types[0] == "message_start"
    assert types[-1] == "message_stop"
    assert "message_delta" in types


def test_reasoning_then_text_stream():
    """GLM/R1-style: reasoning_content first, then content — both must arrive."""
    t = ChatToAnthropicSSE("glm-5.3")
    evts = _events(t, [
        {"choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": "thinking hard"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"reasoning_content": " about the task"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "final answer here"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ])
    _assert_valid(evts)
    # both kinds of content survive the translation
    blocks = [e["content_block"]["type"] for e in evts if e.get("type") == "content_block_start"]
    assert blocks == ["thinking", "text"], blocks
    text = "".join(
        e["delta"]["text"] for e in evts
        if e.get("type") == "content_block_delta" and e["delta"].get("type") == "text_delta"
    )
    assert text == "final answer here", text
    thinking = "".join(
        e["delta"]["thinking"] for e in evts
        if e.get("type") == "content_block_delta" and e["delta"].get("type") == "thinking_delta"
    )
    assert thinking == "thinking hard about the task", thinking


def test_text_only_stream():
    """Plain text stream: single block, starts and stops cleanly."""
    t = ChatToAnthropicSSE("gpt-x")
    evts = _events(t, [
        {"choices": [{"index": 0, "delta": {"content": "hello "}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "world"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ])
    _assert_valid(evts)
    blocks = [e["content_block"]["type"] for e in evts if e.get("type") == "content_block_start"]
    assert blocks == ["text"], blocks


def test_tool_call_stream():
    """Tool call stream (agent loop): text + tool_use blocks, args reassembled."""
    t = ChatToAnthropicSSE("glm-5.3")
    evts = _events(t, [
        {"choices": [{"index": 0, "delta": {"content": "reading the file"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "read_file", "arguments": ""}}]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '{"path"'}}]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": ': "main.py"}'}}]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ])
    _assert_valid(evts)
    blocks = [e["content_block"]["type"] for e in evts if e.get("type") == "content_block_start"]
    assert blocks == ["text", "tool_use"], blocks
    args = "".join(
        e["delta"]["partial_json"] for e in evts
        if e.get("type") == "content_block_delta" and e["delta"].get("type") == "input_json_delta"
    )
    assert json.loads(args) == {"path": "main.py"}, args
    # finish_reason tool_calls -> stop_reason tool_use
    deltas = [e for e in evts if e.get("type") == "message_delta"]
    assert deltas[0]["delta"]["stop_reason"] == "tool_use"


def test_tool_then_text_interleaved():
    """Some models emit text after tool args: blocks must interleave cleanly."""
    t = ChatToAnthropicSSE("glm-5.3")
    evts = _events(t, [
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_9", "function": {"name": "f", "arguments": "{}"}}]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "after the tool"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ])
    _assert_valid(evts)
    blocks = [e["content_block"]["type"] for e in evts if e.get("type") == "content_block_start"]
    assert blocks == ["tool_use", "text"], blocks


def test_midstream_error_surfaces():
    """Upstream error mid-stream must become an anthropic error event, not silence."""
    t = ChatToAnthropicSSE("glm-5.3")
    evts = _events(t, [
        {"choices": [{"index": 0, "delta": {"content": "partial"}, "finish_reason": None}]},
        {"error": {"message": "upstream exploded", "type": "server_error"}},
    ])
    errs = [e for e in evts if e.get("type") == "error"]
    assert errs, "error event missing"
    assert errs[0]["error"]["message"] == "upstream exploded"


def test_empty_stream_still_wellformed():
    """Nothing streamed: message_start + message_delta + message_stop, nothing else."""
    t = ChatToAnthropicSSE("gpt-x")
    evts = _events(t, [])
    _assert_valid(evts)
    assert [e.get("type") for e in evts] == ["message_start", "message_delta", "message_stop"]
