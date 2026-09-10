"""Extensions engine: MCP servers, custom skills, HTTP plugins.

One registry, three kinds:
  mcp    - remote MCP server (JSON-RPC over plain HTTP / streamable HTTP SSE)
  skill  - prompt-style skill: exposed as a tool that returns expert
           instructions for the model to apply
  plugin - generic HTTP tool endpoint: you define the JSON schema,
           NovaRouter executes the call server-side

The gateway auto-injects every enabled extension tool into chat requests
and auto-executes matching tool calls (agentic loop in gateway.dispatch),
so ANY client with a gateway API key gets MCP / skill / plugin abilities
without changing a single line of their own code.
"""
import itertools
import json
import re
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

from . import config, db

VALID_KINDS = ("mcp", "skill", "plugin")

SKILL_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "input": {
            "type": "string",
            "description": "The task or question to apply this skill to.",
        }
    },
    "required": ["input"],
}

_client: Optional[httpx.AsyncClient] = None
_id_lock = threading.Lock()
_ids = itertools.count(1)


def _next_rpc_id() -> int:
    with _id_lock:
        return next(_ids)


def http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.TOOL_TIMEOUT, connect=config.CONNECT_TIMEOUT),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
            follow_redirects=True,
        )
    return _client


async def close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None


def now() -> float:
    return time.time()


def _load_json(raw: Any, default: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else default
    except (ValueError, TypeError):
        return default


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9_.-]+", "_", (name or "").strip().lower()).strip("_")
    return s or "tool"


# --------------------------------------------------------------------------
# registry CRUD
# --------------------------------------------------------------------------

def get_extension(eid: int) -> Optional[Dict[str, Any]]:
    row = db.one("SELECT * FROM extensions WHERE id=?", (eid,))
    if row:
        row["config"] = _load_json(row.get("config"), {})
    return row


def list_extensions(kind: Optional[str] = None, reveal: bool = False) -> List[Dict[str, Any]]:
    rows = db.query("SELECT * FROM extensions ORDER BY kind, name")
    if kind:
        rows = [r for r in rows if r["kind"] == kind]
    for r in rows:
        r["config"] = _load_json(r.get("config"), {})
        if not reveal:
            r["config"] = _mask_config_for_out(r["config"])
        r["tools"] = list_tools(r["id"])
    return rows


def add_extension(
    kind: str, name: str, description: str = "", config: Optional[Dict[str, Any]] = None,
    enabled: bool = True,
) -> int:
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}")
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    cfg = dict(config or {})
    if kind == "skill" and not (cfg.get("instructions") or "").strip():
        raise ValueError("skill needs config.instructions")
    if kind == "mcp" and not (cfg.get("url") or "").strip():
        raise ValueError("mcp needs config.url")
    eid = db.execute(
        """INSERT INTO extensions (kind, name, description, config, enabled, status, created_at)
           VALUES (?,?,?,?,?,'UNKNOWN',?)""",
        (kind, name, (description or "").strip(), json.dumps(cfg), 1 if enabled else 0, now()),
    )
    if kind == "skill":
        sync_skill_tool(eid)
    return eid


def update_extension(eid: int, **fields) -> None:
    ext = get_extension(eid)
    if not ext:
        raise ValueError("extension not found")
    allowed = {"name", "description", "config", "enabled"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "enabled":
            v = 1 if v else 0
        if k == "config" and isinstance(v, dict):
            v = json.dumps(v)
        sets.append(f"{k}=?")
        params.append(v)
    if not sets:
        return
    params.append(eid)
    db.execute(f"UPDATE extensions SET {', '.join(sets)} WHERE id=?", tuple(params))
    # keep the auto-generated skill tool in sync
    updated = get_extension(eid)
    if updated and updated["kind"] == "skill" and (
        "name" in fields or "description" in fields or "config" in fields
    ):
        sync_skill_tool(eid)


def delete_extension(eid: int) -> None:
    db.execute("DELETE FROM extension_tools WHERE extension_id=?", (eid,))
    db.execute("DELETE FROM extensions WHERE id=?", (eid,))


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------

def list_tools(eid: int) -> List[Dict[str, Any]]:
    rows = db.query("SELECT * FROM extension_tools WHERE extension_id=? ORDER BY id", (eid,))
    for r in rows:
        r["parameters"] = _load_json(r.get("parameters"), {})
    return rows


def register_tool(eid: int, name: str, description: str = "", parameters: Any = None) -> int:
    if not get_extension(eid):
        raise ValueError("extension not found")
    tool_name = _slug(name)
    if not tool_name:
        raise ValueError("tool name is required")
    schema = parameters if isinstance(parameters, dict) else _load_json(parameters, {"type": "object", "properties": {}})
    return db.execute(
        """INSERT INTO extension_tools (extension_id, tool_name, description, parameters, enabled, created_at)
           VALUES (?,?,?,?,1,?)
           ON CONFLICT(extension_id, tool_name)
           DO UPDATE SET description=excluded.description, parameters=excluded.parameters""",
        (eid, tool_name, (description or "")[:2000], json.dumps(schema), now()),
    )


def sync_skill_tool(eid: int) -> int:
    """A skill is exposed as one tool whose call returns the skill instructions."""
    ext = get_extension(eid)
    if not ext or ext["kind"] != "skill":
        return 0
    cfg = _load_json(ext.get("config"), {})
    desc = (cfg.get("description") or ext.get("description") or f"Apply the '{ext['name']}' skill").strip()
    kw = (cfg.get("keywords") or "").strip()
    if kw:
        desc += f" (trigger keywords: {kw})"
    register_tool(eid, ext["name"], desc[:2000], SKILL_SCHEMA)
    return 1


def set_tool_enabled(tid: int, enabled: bool) -> None:
    db.execute("UPDATE extension_tools SET enabled=? WHERE id=?", (1 if enabled else 0, tid))


def delete_tool(tid: int) -> None:
    db.execute("DELETE FROM extension_tools WHERE id=?", (tid,))


def _store_tools(eid: int, tools: List[Dict[str, Any]]) -> int:
    db.execute("DELETE FROM extension_tools WHERE extension_id=?", (eid,))
    n = 0
    for t in tools:
        if not isinstance(t, dict):
            continue
        name = (t.get("name") or "").strip()
        if not name:
            continue
        register_tool(eid, name, t.get("description") or "", t.get("inputSchema"))
        n += 1
    return n


# --------------------------------------------------------------------------
# what the model sees
# --------------------------------------------------------------------------

def has_enabled_tools() -> bool:
    return bool(
        db.one(
            """SELECT 1 AS x FROM extension_tools t
               JOIN extensions e ON e.id = t.extension_id
               WHERE t.enabled=1 AND e.enabled=1 LIMIT 1"""
        )
    )


def catalogue() -> List[Dict[str, Any]]:
    """Client-facing tool list for GET /v1/tools."""
    rows = db.query(
        """SELECT t.tool_name, t.description, t.parameters, e.kind AS ext_kind, e.name AS ext_name
           FROM extension_tools t JOIN extensions e ON e.id = t.extension_id
           WHERE t.enabled=1 AND e.enabled=1
           ORDER BY e.kind, t.tool_name"""
    )
    return [
        {
            "name": r["tool_name"],
            "description": r["description"],
            "parameters": _load_json(r.get("parameters"), {}),
            "nova": {"kind": r["ext_kind"], "extension": r["ext_name"]},
        }
        for r in rows
    ]


def _mask_config_for_out(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(cfg or {})
    tok = (out.get("auth_token") or "").strip()
    if tok:
        out["auth_token"] = "••••"
        out["auth_token_set"] = True
    return out


def preserve_secret(new_cfg: Dict[str, Any], old_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """If the dashboard sends back a masked/blank auth_token, keep the stored one."""
    out = dict(new_cfg or {})
    tok = (out.get("auth_token") or "").strip()
    if tok in ("", "••••", "****"):
        old_tok = (old_cfg or {}).get("auth_token") or ""
        if old_tok:
            out["auth_token"] = old_tok
    out.pop("auth_token_set", None)
    return out


def openai_tools() -> List[Dict[str, Any]]:
    rows = db.query(
        """SELECT t.*, e.name AS ext_name FROM extension_tools t
           JOIN extensions e ON e.id = t.extension_id
           WHERE t.enabled=1 AND e.enabled=1
           ORDER BY e.kind, t.tool_name"""
    )
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "type": "function",
            "function": {
                "name": r["tool_name"],
                "description": (r.get("description") or r.get("ext_name") or "")[:1200],
                "parameters": _load_json(r.get("parameters"), {"type": "object", "properties": {}}),
            },
        })
    return out


def inject_tools(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Set[str]]:
    """Merge enabled extension tools into a chat payload.

    Returns (payload, names_added_by_nova). Client tools always win on name
    collisions; only the names NovaRouter itself added are auto-executable.
    """
    tools = openai_tools()
    if not tools:
        return payload, set()
    current = payload.get("tools")
    merged = list(current) if isinstance(current, list) else []
    have = {(t.get("function") or {}).get("name") for t in merged if isinstance(t, dict)}
    added: Set[str] = set()
    for t in tools:
        n = t["function"]["name"]
        if n in have:
            continue
        merged.append(t)
        have.add(n)
        added.add(n)
    if added:
        payload["tools"] = merged
        payload.setdefault("tool_choice", "auto")
    return payload, added


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------

async def call_tool(
    name: str, arguments: Optional[Dict[str, Any]], client_key_id: Optional[int] = None
) -> Tuple[bool, str]:
    """Execute a registered extension tool. Returns (ok, content_for_model)."""
    row = db.one(
        """SELECT t.*, e.kind AS ext_kind, e.name AS ext_name, e.config AS ext_config
           FROM extension_tools t JOIN extensions e ON e.id = t.extension_id
           WHERE t.tool_name=? AND t.enabled=1 AND e.enabled=1""",
        (name,),
    )
    if not row:
        return False, json.dumps({"error": f"tool '{name}' is not available"})
    cfg = _load_json(row.get("ext_config"), {})
    started = time.perf_counter()
    ok, content = False, "unknown extension kind"
    try:
        if row["ext_kind"] == "skill":
            ok, content = _call_skill(cfg, arguments)
        elif row["ext_kind"] == "mcp":
            ok, content = await _call_mcp(cfg, row["tool_name"], arguments)
        elif row["ext_kind"] == "plugin":
            ok, content = await _call_plugin(cfg, row["tool_name"], arguments)
    except Exception as e:  # noqa: BLE001 - tool errors go back to the model
        ok, content = False, f"tool execution failed: {type(e).__name__}: {e}"
    latency = int((time.perf_counter() - started) * 1000)
    db.log_request(
        client_key_id=client_key_id,
        model=name,
        endpoint=f"tool:{row['ext_kind']}:{row['ext_name']}",
        status=200 if ok else 500,
        latency_ms=latency,
        error="" if ok else (content or "")[:250],
    )
    return ok, str(content)[:60000]


def _call_skill(cfg: Dict[str, Any], arguments: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    instr = (cfg.get("instructions") or "").strip()
    if not instr:
        return False, "skill has no instructions configured"
    task = arguments.get("input") if isinstance(arguments, dict) else ""
    out = instr if not task else f"{instr}\n\n[Task to apply this skill to]\n{task}"
    return True, out


# ----------------------------- MCP (JSON-RPC over HTTP / streamable SSE)

def _auth_headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    headers.update(cfg.get("headers") or {})
    tok = (cfg.get("auth_token") or "").strip()
    if tok:
        hdr = cfg.get("auth_header") or "Authorization"
        val = tok if re.match(r"^[A-Za-z0-9-]+ ", tok) else "Bearer " + tok
        headers[hdr] = val
    return headers


async def _rpc(
    cfg: Dict[str, Any], method: str, params: Any = None, notification: bool = False
) -> Any:
    url = (cfg.get("url") or "").strip()
    if not url:
        raise ValueError("extension config missing url")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    headers.update(_auth_headers(cfg))
    if cfg.get("protocol_version"):
        headers["MCP-Protocol-Version"] = str(cfg["protocol_version"])
    body: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if not notification:
        body["id"] = _next_rpc_id()
    if params is not None:
        body["params"] = params

    client = http()
    req = client.build_request("POST", url, headers=headers, json=body)
    resp = await client.send(req, stream=True)
    try:
        if resp.status_code >= 400:
            text = (await resp.aread()).decode(errors="replace")
            raise RuntimeError(f"HTTP {resp.status_code}: {text[:200]}")
        if notification:
            return None
        ctype = resp.headers.get("content-type", "")
        if "event-stream" in ctype:
            want_id = body.get("id")
            found: Optional[Dict[str, Any]] = None
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                    if want_id is None or obj.get("id") == want_id or "id" not in obj:
                        found = obj
                        break
            if found is None:
                raise RuntimeError("no JSON-RPC response found in the MCP event stream")
            data: Any = found
        else:
            raw = await resp.aread()
            try:
                data = json.loads(raw)
            except ValueError:
                raise RuntimeError("MCP server returned non-JSON body") from None
    finally:
        await resp.aclose()

    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(str(data["error"])[:300])
    return data.get("result") if isinstance(data, dict) else data


def _mcp_text(content: Any) -> str:
    if not isinstance(content, list):
        return content if isinstance(content, str) else ""
    parts: List[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
        else:
            parts.append(json.dumps(item, ensure_ascii=False))
    return "\n".join(parts)


async def _call_mcp(cfg: Dict[str, Any], tool_name: str, arguments: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    res = await _rpc(cfg, "tools/call", {"name": tool_name, "arguments": arguments or {}})
    if not isinstance(res, dict):
        return True, str(res)
    if res.get("isError"):
        return False, _mcp_text(res.get("content")) or "tool reported an error"
    if res.get("structuredContent") is not None:
        return True, json.dumps(res["structuredContent"], ensure_ascii=False)[:60000]
    text = _mcp_text(res.get("content"))
    if text:
        return True, text
    return True, json.dumps(res, ensure_ascii=False)[:60000]


async def refresh_mcp_tools(eid: int) -> Dict[str, Any]:
    """initialize (best effort) -> tools/list -> replace stored tools."""
    ext = get_extension(eid)
    if not ext:
        raise ValueError("extension not found")
    if ext["kind"] != "mcp":
        raise ValueError("not an mcp extension")
    cfg = _load_json(ext.get("config"), {})
    err = ""
    tools: List[Dict[str, Any]] = []
    try:
        try:
            init = await _rpc(cfg, "initialize", {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "novarouter", "version": "2.1.0"},
            })
            if isinstance(init, dict) and init.get("protocolVersion"):
                cfg["protocol_version"] = init["protocolVersion"]
                db.execute("UPDATE extensions SET config=? WHERE id=?", (json.dumps(cfg), eid))
        except Exception:  # noqa: BLE001 - some servers skip the handshake
            pass
        try:
            await _rpc(cfg, "notifications/initialized", notification=True)
        except Exception:  # noqa: BLE001
            pass
        res = await _rpc(cfg, "tools/list", {})
        tools = (res or {}).get("tools") or []
        if not isinstance(tools, list):
            tools = []
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"[:250]
    n = _store_tools(eid, tools) if not err else 0
    status = "OK" if not err else "ERROR"
    db.execute("UPDATE extensions SET status=?, last_error=? WHERE id=?", (status, err, eid))
    return {"tools": n, "error": err}


# ----------------------------- plugin (generic HTTP endpoint)

async def _call_plugin(cfg: Dict[str, Any], tool_name: str, arguments: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    url = (cfg.get("url") or "").strip()
    if not url:
        return False, "plugin config missing url"
    headers = {"Content-Type": "application/json"}
    headers.update(_auth_headers(cfg))
    method = (cfg.get("method") or "POST").upper()
    args = arguments if isinstance(arguments, dict) else {}
    client = http()
    if method == "GET":
        resp = await client.request("GET", url, params=args, headers=headers)
    else:
        style = cfg.get("params_style") or "direct"
        body: Any = args if style != "wrapped" else {"tool": tool_name, "arguments": args}
        resp = await client.request(method, url, json=body, headers=headers)
    text = resp.text
    if resp.status_code >= 400:
        return False, f"HTTP {resp.status_code}: {text[:500]}"
    return True, text[:60000]
