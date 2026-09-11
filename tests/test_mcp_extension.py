"""E2E: MCP extension path — tools/list discovery + tools/call execution,
scripted through a fake httpx client (no real network)."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["NOVA_DATA_DIR"] = tempfile.mkdtemp(prefix="nova-mcp-")
os.environ["NOVA_ADMIN_TOKEN"] = "t"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app import extensions  # noqa: E402


class FakeResp:
    def __init__(self, payload, status=200, ctype="application/json"):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)
        self.headers = {"content-type": ctype}

    def json(self):
        return self._payload

    async def aread(self):
        return self.text.encode()

    async def aclose(self):
        pass

    async def aiter_lines(self):
        for line in self.text.splitlines():
            yield line


MCP_LOG = []


class FakeMCPClient:
    """Behaves like a streamable-HTTP MCP server answering JSON-RPC over HTTP."""

    is_closed = False

    async def send(self, req, stream=False):
        body = json.loads(req.content.decode())
        method = body.get("method")
        MCP_LOG.append(method)
        if method == "initialize":
            return FakeResp({"jsonrpc": "2.0", "id": body["id"],
                             "result": {"protocolVersion": "2025-03-26",
                                        "capabilities": {"tools": {}},
                                        "serverInfo": {"name": "fake-mcp", "version": "1"}}})
        if method == "notifications/initialized":
            return FakeResp(None, ctype="application/json")
        if method == "tools/list":
            return FakeResp({"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [
                {"name": "search", "description": "Search the web",
                 "inputSchema": {"type": "object",
                                 "properties": {"query": {"type": "string"}},
                                 "required": ["query"]}},
                {"name": "fetch", "description": "Fetch a URL",
                 "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}}},
            ]}})
        if method == "tools/call":
            args = body["params"]["arguments"]
            return FakeResp({"jsonrpc": "2.0", "id": body["id"], "result": {
                "content": [{"type": "text",
                             "text": f"RESULTS for {args.get('query', args.get('url', '?'))}"}],
                "isError": False,
            }})
        return FakeResp({"jsonrpc": "2.0", "id": body.get("id"),
                         "error": {"code": -32601, "message": "unknown method"}})

    def build_request(self, method, url, headers=None, json=None):
        import json as _json

        class R:
            def __init__(self, inner):
                self.content = _json.dumps(inner).encode()

        return R(json)

    async def aclose(self):
        pass


extensions._client = FakeMCPClient()

with TestClient(app) as c:
    H = {"X-Admin-Token": "t"}

    # 1. add an MCP extension -> auto-discovery runs
    r = c.post("/admin/api/extensions", headers=H, json={
        "kind": "mcp", "name": "fake-search",
        "config": {"url": "https://fake-mcp.local/mcp"},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tools"] == 2, body
    assert body["error"] == "", body
    print("PASS mcp add: 2 tools auto-discovered |", MCP_LOG)

    # 2. tools are registered and visible in the catalogue shape
    rows = c.get("/admin/api/extensions", headers=H).json()
    names = {t["tool_name"] for t in rows[0]["tools"]}
    assert names == {"search", "fetch"}, names
    print("PASS mcp tools registered:", names)

    # 3. execute one via the direct call endpoint
    token = c.post("/admin/api/client-keys", headers=H, json={"name": "t"}).json()["token"]
    r = c.post("/v1/tools/search", headers={"Authorization": "Bearer " + token},
               json={"query": "novarouter gateway"})
    assert r.status_code == 200
    out = r.json()
    assert out["ok"] is True and "RESULTS for novarouter gateway" in out["output"], out
    print("PASS mcp tools/call:", out["output"][:50])

    # 4. inject_tools exposes both mcp tools to the model
    payload = {"model": "x", "messages": []}
    payload, added = extensions.inject_tools(payload)
    assert added == {"search", "fetch"}, added
    fnames = sorted(t["function"]["name"] for t in payload["tools"])
    assert fnames == ["fetch", "search"]
    print("PASS mcp injection:", fnames)

    # 5. masked config: auth_token never leaks through the admin list
    r = c.post("/admin/api/extensions", headers=H, json={
        "kind": "mcp", "name": "secure-mcp",
        "config": {"url": "https://s.local/mcp", "auth_token": "sk-secret-123"},
    })
    # discovery fails (fake server same url) — that's fine, still registered
    rows = {e["name"]: e for e in c.get("/admin/api/extensions", headers=H).json()}
    cfg_out = rows["secure-mcp"]["config"]
    assert cfg_out.get("auth_token") in ("••••", None) or "sk-secret" not in str(cfg_out)
    print("PASS auth_token masked in admin list:", cfg_out.get("auth_token"))

    # 6. refresh endpoint re-runs discovery
    eid = rows["fake-search"]["id"]
    MCP_LOG.clear()
    r = c.post(f"/admin/api/extensions/{eid}/refresh", headers=H)
    assert r.status_code == 200 and r.json()["tools"] == 2
    assert "tools/list" in MCP_LOG
    print("PASS refresh:", MCP_LOG)

print("\nALL MCP EXTENSION TESTS PASSED")