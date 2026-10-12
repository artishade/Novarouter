"""Custom MCP servers (console plugins) — transport layer.

Run: python -m unittest discover -s tests -p test_mcp_transport.py

The parts that break silently in a plugin system are pinned here: SSE-framed
replies, the `Mcp-Session-Id` echo, JSON-RPC error envelopes, a 401 that must
not be swallowed, tool-call parsing, and the allow-list. The stdio half runs a
real child process (tests/mcp_stdio_server.py), so the process handling is
exercised for real rather than mocked.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nova import mcp_client

FIXTURE = Path(__file__).resolve().parent / "mcp_stdio_server.py"


def http_spec(**overrides) -> dict:
    spec = {
        "id": "demo",
        "name": "Demo",
        "transport": "http",
        "url": "https://mcp.example.test/mcp",
        "headers": {},
        "allow": [],
        "enabled": True,
    }
    spec.update(overrides)
    return spec


def json_transport(reply_for, status=200, session_id=None):
    """An httpx MockTransport that answers the MCP handshake and tools/list.

    A reply that is a string is served as a real SSE response (the shape a
    streamable-HTTP MCP server uses); a dict is served as JSON.
    """
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        message = request.read().decode()
        import json as _json
        try:
            parsed = _json.loads(message)
        except Exception:
            parsed = {}
        seen.append(parsed)
        headers = {"Mcp-Session-Id": session_id} if session_id else {}
        payload = reply_for(parsed)
        if isinstance(payload, str):
            headers = {**headers, "content-type": "text/event-stream"}
            return httpx.Response(status, text=payload, headers=headers)
        return httpx.Response(status, json=payload, headers=headers)

    return httpx.MockTransport(handler), seen


def sse_reply(payload: dict) -> str:
    return "event: message\ndata: " + __import__("json").dumps(payload) + "\n\n"


class HttpTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        mcp_client._http_sessions.clear()

    async def test_lists_tools_from_a_json_reply(self):
        def reply(message):
            method = message.get("method")
            if method == "initialize":
                return {"jsonrpc": "2.0", "id": message["id"],
                        "result": {"protocolVersion": mcp_client.PROTOCOL_VERSION}}
            if method == "tools/list":
                return {"jsonrpc": "2.0", "id": message["id"], "result": {"tools": [
                    {"name": "ask", "description": "ask the wiki", "inputSchema": {"type": "object"}},
                    {"name": "admin", "description": "dangerous"},
                ]}}
            return {"jsonrpc": "2.0", "id": message.get("id")}

        transport, seen = json_transport(reply)
        with self._patch(transport):
            tools = await mcp_client.list_tools(http_spec())
        self.assertEqual([t["name"] for t in tools], ["ask", "admin"])
        self.assertEqual([m["method"] for m in seen], ["initialize", "notifications/initialized", "tools/list"])

    async def test_sse_framed_reply_is_understood(self):
        def reply(message):
            method = message.get("method")
            if method == "initialize":
                return sse_reply({"jsonrpc": "2.0", "id": message["id"], "result": {}})
            if method == "tools/list":
                return sse_reply({"jsonrpc": "2.0", "id": message["id"],
                                  "result": {"tools": [{"name": "search", "description": "s"}]}})
            return sse_reply({"jsonrpc": "2.0", "id": message.get("id")})

        transport, _ = json_transport(reply)
        with self._patch(transport):
            tools = await mcp_client.list_tools(http_spec())
        self.assertEqual(tools[0]["name"], "search")

    async def test_session_id_is_echoed_on_the_next_call(self):
        def reply(message):
            return {"jsonrpc": "2.0", "id": message.get("id"), "result": {"tools": []}}

        transport, seen = json_transport(reply, session_id="sess-42")
        with self._patch(transport):
            await mcp_client.list_tools(http_spec())
        self.assertEqual(mcp_client._http_sessions["demo"], "sess-42")

    async def test_jsonrpc_error_is_reported_not_swallowed(self):
        def reply(message):
            if message.get("method") == "initialize":
                return {"jsonrpc": "2.0", "id": message.get("id"), "result": {}}
            return {"jsonrpc": "2.0", "id": message.get("id"),
                    "error": {"code": -32001, "message": "token expired"}}

        transport, _ = json_transport(reply)
        with self._patch(transport):
            with self.assertRaises(mcp_client.McpError) as err:
                await mcp_client.list_tools(http_spec())
        self.assertIn("token expired", str(err.exception))

    async def test_http_failure_names_the_status(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="unauthorized")

        with self._patch(httpx.MockTransport(handler)):
            with self.assertRaises(mcp_client.McpError) as err:
                await mcp_client.list_tools(http_spec())
        self.assertIn("401", str(err.exception))

    async def test_allow_list_hides_the_rest(self):
        def reply(message):
            if message.get("method") == "initialize":
                return {"jsonrpc": "2.0", "id": message.get("id"), "result": {}}
            return {"jsonrpc": "2.0", "id": message.get("id"), "result": {"tools": [
                {"name": "ask"}, {"name": "admin"},
            ]}}

        transport, _ = json_transport(reply)
        with self._patch(transport):
            tools = await mcp_client.list_tools(http_spec(allow=["ask"]))
        self.assertEqual([t["name"] for t in tools], ["ask"])

    async def test_a_blocked_tool_cannot_be_called(self):
        with self.assertRaises(mcp_client.McpError):
            await mcp_client.call_tool(http_spec(allow=["ask"]), "admin", {})

    def _patch(self, transport):
        import unittest.mock

        return unittest.mock.patch.object(mcp_client.httpx, "AsyncClient", self._client_for(transport))

    @staticmethod
    def _client_for(transport):
        real = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return real(*args, **kwargs)

        return factory


class StdioTransportTests(unittest.IsolatedAsyncioTestCase):
    """A real child process: argv handling, handshake, reuse, teardown."""

    async def asyncSetUp(self):
        self.spec = {
            "id": "stdio-fixture",
            "name": "Fixture",
            "transport": "stdio",
            "url": "",
            "command": f"{sys.executable} {FIXTURE}",
            "headers": {},
            "allow": [],
            "enabled": True,
        }

    async def asyncTearDown(self):
        await mcp_client.close_all()

    async def test_discovers_and_calls_tools(self):
        tools = await mcp_client.list_tools(self.spec)
        self.assertEqual([t["name"] for t in tools], ["echo", "add"])

        self.assertEqual(await mcp_client.call_tool(self.spec, "echo", {"message": "hi"}), "hi")
        self.assertEqual(await mcp_client.call_tool(self.spec, "add", {"a": 2, "b": 3}), "5")
        self.assertEqual(
            await mcp_client.call_tool(self.spec, "add", {"a": 2, "b": 3, "loud": True}), "5")

    async def test_the_process_is_reused_between_calls(self):
        await mcp_client.call_tool(self.spec, "echo", {"message": "one"})
        first = mcp_client._sessions[self.spec["id"]].process
        await mcp_client.call_tool(self.spec, "echo", {"message": "two"})
        self.assertIs(mcp_client._sessions[self.spec["id"]].process, first)

    async def test_tool_errors_come_back_as_errors(self):
        with self.assertRaises(mcp_client.McpError):
            await mcp_client.call_tool(self.spec, "nope", {})

    async def test_a_missing_binary_fails_honestly(self):
        spec = dict(self.spec, id="broken", command="definitely-not-a-real-binary-xyz")
        with self.assertRaises(mcp_client.McpError) as err:
            await mcp_client.list_tools(spec)
        self.assertIn("definitely-not-a-real-binary-xyz", str(err.exception))

    async def test_close_all_tears_the_child_down(self):
        await mcp_client.call_tool(self.spec, "echo", {"message": "bye"})
        process = mcp_client._sessions[self.spec["id"]].process
        await mcp_client.close_all()
        self.assertEqual(mcp_client._sessions, {})
        self.assertIsNotNone(process.returncode)


class ParsingTests(unittest.TestCase):
    def test_canonical_form(self):
        self.assertEqual(
            mcp_client.parse_call('wiki::ask {"repo":"fastapi"}'),
            ("wiki", "ask", {"repo": "fastapi"}),
        )

    def test_no_arguments(self):
        self.assertEqual(mcp_client.parse_call("wiki::ask"), ("wiki", "ask", {}))

    def test_json_object_form(self):
        self.assertEqual(
            mcp_client.parse_call('{"server":"wiki","tool":"ask","arguments":{"repo":"x"}}'),
            ("wiki", "ask", {"repo": "x"}),
        )

    def test_free_text_arguments_are_kept(self):
        self.assertEqual(
            mcp_client.parse_call("wiki::ask hello world"), ("wiki", "ask", {"input": "hello world"})
        )

    def test_nonsense_is_rejected(self):
        with self.assertRaises(mcp_client.McpError):
            mcp_client.parse_call("just some words")

    def test_headers_are_masked_but_the_scheme_survives(self):
        masked = mcp_client.mask_headers({
            "Authorization": "Bearer super-secret",
            "X-Trace-Id": "abc123",
        })
        self.assertEqual(masked["Authorization"], "Bearer ••••")
        self.assertNotIn("super-secret", str(masked))
        self.assertEqual(masked["X-Trace-Id"], "abc123")


if __name__ == "__main__":
    unittest.main()