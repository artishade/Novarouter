"""Minimal MCP server over stdio — used as a real plugin process in tests.

Speaks newline-delimited JSON-RPC 2.0 on stdin/stdout:

  initialize            → protocol handshake
  tools/list            → two tools: `echo` (text in, text out) and `add`
  tools/call            → runs one of them
  unknown method        → JSON-RPC error envelope (the client must surface it)

Run standalone:  python3 tests/mcp_stdio_server.py
"""
from __future__ import annotations

import json
import sys

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "echo",
        "description": "Echo the message back, upper-cased.",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string", "description": "what to echo"}},
            "required": ["message"],
        },
    },
    {
        "name": "add",
        "description": "Add two numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
                "loud": {"type": "boolean"},
            },
            "required": ["a", "b"],
        },
    },
]


def _result(rpc_id, result):
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _text(rpc_id, text, is_error=False):
    return _result(rpc_id, {"content": [{"type": "text", "text": text}], "isError": is_error})


def handle(message):
    method = message.get("method")
    rpc_id = message.get("id")

    if method == "initialize":
        return _result(rpc_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "nova-test-plugin", "version": "1.0"},
        })
    if method == "tools/list":
        return _result(rpc_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            return _text(rpc_id, str(args.get("message", "")))
        if name == "add":
            try:
                total = float(args.get("a", 0)) + float(args.get("b", 0))
            except (TypeError, ValueError):
                return _text(rpc_id, "add needs two numbers", is_error=True)
            rendered = str(int(total)) if total == int(total) else str(total)
            return _text(rpc_id, rendered.upper() if args.get("loud") else rendered)
        return _text(rpc_id, f"unknown tool: {name}", is_error=True)
    if method is None or method.startswith("notifications/"):
        return None
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": f"no method {method}"}}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except Exception:
            continue
        reply = handle(message)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())