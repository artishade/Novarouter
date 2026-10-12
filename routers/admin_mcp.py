"""Custom MCP server (plugin) admin routes, mounted under /api/admin/mcp.

  GET  /servers              → every registered plugin + its cached tools
  POST /servers              → create/update one plugin (headers are secrets)
  POST /servers/delete       → {id}
  POST /servers/toggle       → {id, enabled}
  POST /servers/test         → {id} — connect now and refresh the tool cache
  POST /servers/refresh      → re-probe every enabled plugin
  POST /servers/call         → {server, tool, arguments} — run one tool by hand
  GET  /tools                → the flattened catalogue the agent sees

Credentials only travel one way: they are stored, never returned. Every read
path goes through `mcp_registry.serialize`, which masks header values.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from nova import mcp_registry
from nova.database import get_db
from nova.mcp_client import McpError

router = APIRouter()


async def _body(request: Request) -> dict:
    try:
        parsed = await request.json()
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _list_payload(db: Session) -> dict:
    return {
        "servers": [mcp_registry.serialize(r) for r in mcp_registry.rows(db)],
        "summary": mcp_registry.catalogue_summary(db),
    }


# --------------------------------------------------------------------------- #
# GET /servers
# --------------------------------------------------------------------------- #


@router.get("/servers")
def servers(db: Session = Depends(get_db)):
    return _list_payload(db)


# --------------------------------------------------------------------------- #
# POST /servers
# --------------------------------------------------------------------------- #


@router.post("/servers")
async def save_server(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        body = {}
    try:
        row = mcp_registry.upsert(db, body)
    except ValueError as err:
        return JSONResponse({"error": str(err)}, status_code=400)
    return JSONResponse({"ok": True, "server": mcp_registry.serialize(row), **_list_payload(db)})


# --------------------------------------------------------------------------- #
# POST /servers/delete · /servers/toggle
# --------------------------------------------------------------------------- #


@router.post("/servers/delete")
async def delete_server(request: Request, db: Session = Depends(get_db)):
    body = await _body(request)
    server_id = str(body.get("id") or "").strip()
    if not mcp_registry.delete(db, server_id):
        return JSONResponse({"error": "That MCP server is not registered"}, status_code=404)
    return JSONResponse({"ok": True, **_list_payload(db)})


@router.post("/servers/toggle")
async def toggle_server(request: Request, db: Session = Depends(get_db)):
    body = await _body(request)
    server_id = str(body.get("id") or "").strip()
    enabled = bool(body.get("enabled", True))
    row = mcp_registry.set_enabled(db, server_id, enabled)
    if row is None:
        return JSONResponse({"error": "That MCP server is not registered"}, status_code=404)
    return JSONResponse({"ok": True, **_list_payload(db)})


# --------------------------------------------------------------------------- #
# POST /servers/test · /servers/refresh
# --------------------------------------------------------------------------- #


@router.post("/servers/test")
async def test_server(request: Request, db: Session = Depends(get_db)):
    body = await _body(request)
    server_id = str(body.get("id") or "").strip()
    row = mcp_registry.get_row(db, server_id)
    if row is None:
        return JSONResponse({"error": "That MCP server is not registered"}, status_code=404)
    timeout = float(body.get("timeout") or 25)
    result = await mcp_registry.refresh(db, row, timeout=max(2.0, min(timeout, 90.0)))
    return JSONResponse({
        "ok": result["ok"],
        "error": result["error"],
        "count": result["count"],
        "latency_ms": result["latency_ms"],
        "server": mcp_registry.serialize(row),
        **_list_payload(db),
    })


@router.post("/servers/refresh")
async def refresh_servers(request: Request, db: Session = Depends(get_db)):
    timeout = 25.0
    body = await _body(request)
    if isinstance(body.get("timeout"), (int, float)):
        timeout = float(body["timeout"])
    count = await mcp_registry.refresh_enabled(db, max(2.0, min(timeout, 90.0)))
    return JSONResponse({"ok": True, "probed": count, **_list_payload(db)})


# --------------------------------------------------------------------------- #
# POST /servers/call — the Playgrounds "try it" button
# --------------------------------------------------------------------------- #


@router.post("/servers/call")
async def call_server_tool(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        body = {}
    server_id = str(body.get("server") or "").strip()
    tool = str(body.get("tool") or "").strip()
    arguments = body.get("arguments")
    if not tool:
        return JSONResponse({"error": "tool is required"}, status_code=400)
    if not isinstance(arguments, dict):
        arguments = {}
    try:
        output = await mcp_registry.invoke(db, server_id, tool, arguments)
    except McpError as err:
        return JSONResponse({"ok": False, "error": str(err)}, status_code=200)
    return JSONResponse({"ok": True, "output": output[:8000]})


# --------------------------------------------------------------------------- #
# GET /tools — what the agent is allowed to call
# --------------------------------------------------------------------------- #


@router.get("/tools")
def tools(db: Session = Depends(get_db)):
    return {
        "tools": mcp_registry.tool_catalogue(db),
        "summary": mcp_registry.catalogue_summary(db),
    }