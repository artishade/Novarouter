"""Registry of the user's custom MCP servers (console plugins).

Storage + lifecycle on top of `nova.mcp_client`, which stays transport-only and
database-free. Everything here is additive: rows are created by the user from
the console, never seeded, never wiped — a deploy keeps the plugins, exactly
like providers and storage targets.

Tool discovery is cached on the row (`tools` column) and refreshed on demand
("Test" / "Refresh"), so building an agent prompt never blocks on a third-party
plugin that happens to be slow or down. The cached snapshot is what the agent
sees; a plugin that fails at call time reports its error honestly in the step
log instead of silently disappearing.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import slugify
from .mcp_client import (
    CALL_TIMEOUT,
    McpError,
    call_tool,
    mask_headers,
    probe,
    spec_from_row,
    tool_ref,
)
from .models import McpServer, utcnow

log = logging.getLogger("nova.mcp.registry")

MAX_SERVERS = 24


def _dumps(value: Any, fallback: str = "[]") -> str:
    try:
        return json.dumps(value if value is not None else json.loads(fallback))
    except Exception:
        return fallback


def _loads(raw: str, fallback) -> Any:
    try:
        value = json.loads(raw or "")
    except Exception:
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #

def get_row(db: Session, server_id: str) -> McpServer | None:
    return db.get(McpServer, (server_id or "").strip()) if server_id else None


def rows(db: Session, enabled_only: bool = False) -> list[McpServer]:
    query = select(McpServer).order_by(McpServer.createdAt, McpServer.id)
    if enabled_only:
        query = query.where(McpServer.enabled.is_(True))
    return list(db.scalars(query).all())


def serialize(row: McpServer) -> dict:
    """API shape — credentials masked, never echoed back."""
    tools = _loads(row.tools, [])
    return {
        "id": row.id,
        "name": row.name,
        "transport": row.transport,
        "url": row.url or "",
        "command": row.command or "",
        "headers": mask_headers(_loads(row.headers, {})),
        "has_credentials": bool(_loads(row.headers, {})),
        "allow": _loads(row.allow, []),
        "enabled": bool(row.enabled),
        "status": row.status or "unknown",
        "last_error": row.lastError or None,
        "tool_count": len(tools),
        "tools": [
            {
                "name": t.get("name"),
                "description": t.get("description", ""),
                "input_schema": t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {},
            }
            for t in tools if isinstance(t, dict)
        ],
        "last_checked_at": int(row.lastCheckedAt.replace(tzinfo=None).timestamp() * 1000)
        if row.lastCheckedAt else None,
        "updated_at": int(row.updatedAt.replace(tzinfo=None).timestamp() * 1000)
        if row.updatedAt else None,
    }


def specs(db: Session, enabled_only: bool = True) -> list[dict]:
    return [spec_from_row(r) for r in rows(db, enabled_only=enabled_only) if r.enabled or not enabled_only]


def tool_catalogue(db: Session) -> list[dict]:
    """Every tool the enabled plugins expose, from the cached snapshot."""
    out: list[dict] = []
    for row in rows(db, enabled_only=True):
        spec = spec_from_row(row)
        for tool in _loads(row.tools, []):
            if isinstance(tool, dict) and tool.get("name"):
                out.append(tool_ref(spec, tool))
    return out


def catalogue_summary(db: Session) -> dict:
    servers = rows(db)
    enabled = [r for r in servers if r.enabled]
    return {
        "servers": len(servers),
        "enabled": len(enabled),
        "tools": len(tool_catalogue(db)),
        "connected": sum(1 for r in enabled if (r.status or "") == "connected"),
    }


# --------------------------------------------------------------------------- #
# Write
# --------------------------------------------------------------------------- #

def _validate(payload: dict) -> dict:
    transport = str(payload.get("transport") or "http").strip().lower()
    if transport not in ("http", "stdio"):
        raise ValueError("transport must be http or stdio")
    name = " ".join(str(payload.get("name") or "").split())[:60]
    server_id = slugify(str(payload.get("id") or name or "mcp-server"))
    url = str(payload.get("url") or "").strip()
    command = str(payload.get("command") or "").strip()[:400]
    if transport == "http" and not url:
        raise ValueError("an MCP URL is required for the http transport")
    if transport == "http" and not url.lower().startswith(("http://", "https://")):
        raise ValueError("the MCP URL must start with http:// or https://")
    if transport == "stdio" and not command:
        raise ValueError("a command is required for the stdio transport")

    headers = payload.get("headers")
    if isinstance(headers, str):
        headers = _loads(headers, {})
    if not isinstance(headers, dict):
        headers = {}
    headers = {str(k): str(v) for k, v in headers.items() if str(k).strip()}

    allow = payload.get("allow")
    if isinstance(allow, str):
        allow = [part.strip() for part in allow.split(",")]
    if not isinstance(allow, list):
        allow = []
    allow = sorted({str(a).strip() for a in allow if str(a).strip()})

    enabled = payload.get("enabled")
    return {
        "id": server_id,
        "name": name or server_id,
        "transport": transport,
        "url": url[:500],
        "command": command,
        "headers": _dumps(headers, "{}"),
        "allow": _dumps(allow),
        "enabled": bool(payload.get("enabled", True)) if enabled is not None else True,
    }


def upsert(db: Session, payload: dict) -> McpServer:
    """Create or update one plugin. Existing secrets survive a masked re-save."""
    clean = _validate(payload)
    row = db.get(McpServer, clean["id"])
    if row is None and len(rows(db)) >= MAX_SERVERS:
        raise ValueError(f"the limit of {MAX_SERVERS} MCP servers is reached")

    incoming = _loads(clean["headers"], {})
    if row is not None:
        # The UI never receives the real secret values, so an incoming header
        # that is still masked keeps whatever is already stored. A header the
        # user typed out fully replaces it, and a header dropped from the form
        # is dropped here too.
        stored = _loads(row.headers, {})
        incoming = {
            key: (stored.get(key, "") if str(value).strip().endswith("••••") else value)
            for key, value in incoming.items()
        }
        row.name = clean["name"]
        row.transport = clean["transport"]
        row.url = clean["url"]
        row.command = clean["command"]
        row.headers = _dumps(incoming, "{}")
        row.allow = clean["allow"]
        row.enabled = clean["enabled"]
        row.updatedAt = utcnow()
        # Connection facts belong to the old endpoint — re-probe on demand.
        if (row.url, row.command, row.transport) != (clean["url"], clean["command"], clean["transport"]):
            row.status = "unknown"
    else:
        row = McpServer(
            id=clean["id"],
            name=clean["name"],
            transport=clean["transport"],
            url=clean["url"],
            command=clean["command"],
            headers=_dumps(incoming, "{}"),
            allow=clean["allow"],
            enabled=clean["enabled"],
            updatedAt=utcnow(),
        )
        db.add(row)
    db.commit()
    log.info("MCP server %s saved (%s)", row.id, row.transport)
    return row


def delete(db: Session, server_id: str) -> bool:
    row = get_row(db, server_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    log.info("MCP server %s removed", server_id)
    return True


def set_enabled(db: Session, server_id: str, enabled: bool) -> McpServer | None:
    row = get_row(db, server_id)
    if row is None:
        return None
    row.enabled = bool(enabled)
    row.updatedAt = utcnow()
    db.commit()
    return row


# --------------------------------------------------------------------------- #
# Discovery + invocation
# --------------------------------------------------------------------------- #

async def refresh(db: Session, row: McpServer, timeout: float = 25.0) -> dict:
    """Probe one plugin and cache what it offers (used by Test / Refresh)."""
    result = await probe(spec_from_row(row), timeout)
    # `probe` hands back the server's own tool dicts (inputSchema), which is
    # also the shape `tool_ref` reads back out of this snapshot.
    row.tools = _dumps([
        {
            "name": t.get("name"),
            "description": str(t.get("description") or "")[:400],
            "inputSchema": t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {},
        }
        for t in result["tools"]
    ])
    row.status = "connected" if result["ok"] else "error"
    row.lastError = None if result["ok"] else (result["error"] or "")[:400]
    row.lastCheckedAt = utcnow()
    db.commit()
    return result


async def refresh_enabled(db: Session, timeout: float = 25.0) -> int:
    count = 0
    for row in rows(db, enabled_only=True):
        await refresh(db, row, timeout)
        count += 1
    return count


def find_call_target(db: Session, qualified: str, tool: str) -> tuple[McpServer | None, str]:
    """`server::tool` or a bare tool name that exactly one enabled plugin has."""
    server_id, _, tail = (qualified or "").partition("::")
    row = get_row(db, server_id) if server_id else None
    if row is not None:
        return (row if row.enabled else None), (tail or tool)
    matches = [
        r for r in rows(db, enabled_only=True)
        if any(t.get("name") == tool for t in _loads(r.tools, []) if isinstance(t, dict))
    ]
    if len(matches) == 1:
        return matches[0], tool
    if len(matches) > 1:
        names = ", ".join(r.id for r in matches)
        raise McpError(f"`{tool}` exists on several plugins — qualify it: " +
                       ", ".join(f"{r.id}::{tool}" for r in matches))
    return None, tool


async def invoke(db: Session, server_id: str, tool: str,
                 arguments: dict | None = None, timeout: float = CALL_TIMEOUT) -> str:
    row, name = find_call_target(db, server_id, tool)
    if row is None:
        known = ", ".join(r.id for r in rows(db, enabled_only=True)) or "(none registered)"
        raise McpError(f"no enabled MCP server `{server_id}` — registered: {known}")
    return await call_tool(spec_from_row(row), name, arguments, timeout)