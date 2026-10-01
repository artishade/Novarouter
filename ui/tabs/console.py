"""Console tab — Python UI port of src/components/nova/tabs/ConsoleTab.tsx.

The unified Nova Console: one chatbox, three modes, auto-routed.
  Chat      → POST /api/v1/chat/completions (stream: true) with the Nova Console
              system prompt; SSE deltas are appended live; the [[DELEGATE]]
              protocol hands actionable tasks to the autonomous agent.
  Terminal  → `$ <command>` and slash diagnostics run in the sandbox executor
              via POST /ui/console/exec (BFF → /api/admin/terminal/exec).
  Agent     → `! <goal>` / `/agent <goal>` launches a real agent task
              (POST /api/agent/tasks); steps stream live into the conversation
              via 2s polling — only while a task is queued/running.

Fragment endpoints:
  GET  /partials/tab/console     → the full console frame (toolbar, stream,
                                   composer, inline JS). NO data-live here: a
                                   5s clobber-refresh would kill the chat.
  GET  /ui/console/chat-meta     → JSON honest routing metadata for the last
                                   chat completion (from the gateway's own
                                   RequestLog — provider/upstream/spoofed/
                                   latency/tokens) used for the reply chips.
  GET  /ui/console/plugins       → the MCP/plugin drawer body (registered
                                   servers, their discovered tools, the form)
  GET  /ui/console/plugins/form  → add/edit form for one server
  GET  /ui/console/plugins/run   → the "run this tool" form for one tool
Action endpoints:
  POST /ui/console/exec          → JSON sandbox exec (BFF → terminal exec).
  POST /ui/console/plugins/{save,delete,toggle,test,test-all,call}
                                 → HTMX form posts, each swapping the drawer
                                   body (BFF → /api/admin/mcp/*).
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ui.api_client import ApiError, api
from ui.render import form_dict, html, render, truthy

router = APIRouter(tags=["ui:console"])

# Toolbar button label + the chip that tells the user plugins are available.
PLUGINS_URL = "/ui/console/plugins"


def _icon_kebab(name: str) -> str:
    """'TerminalSquare' → 'terminal-square' (lucide data-lucide names)."""
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name or "").lower()


def _err_panel(message: str) -> HTMLResponse:
    return html(
        render("partials/console_error.html", message=message, retry_url="/partials/tab/console"),
        status_code=200,
    )


# --------------------------------------------------------------------------- #
# GET /partials/tab/console — the flagship fragment
# --------------------------------------------------------------------------- #

@router.get("/partials/tab/console")
async def console_tab() -> HTMLResponse:
    # Models are essential (picker) → hard error panel on failure, like the
    # TSX would render a broken console without them. meta/stats/tools are
    # optional decorations (nullable props in the TSX) → degrade to None.
    payload, meta, stats, raw_tools, raw_plugins = await asyncio.gather(
        api.get("/api/admin/models"), api.get("/api/admin/meta"),
        api.get("/api/admin/stats"), api.get("/api/agent/tools"),
        api.get("/api/admin/mcp/tools"),
        return_exceptions=True,
    )
    if isinstance(payload, BaseException):
        message = payload.message if isinstance(payload, ApiError) else str(payload) or "Failed to load models"
        return _err_panel(message)

    rows: list[Any] = []
    if isinstance(payload, dict):
        rows = payload.get("rows") or []
    elif isinstance(payload, list):
        rows = payload
    # TSX: enabledModels = models.filter(m => m.enabled); picker slices to 40.
    models = [m for m in rows if isinstance(m, dict) and m.get("enabled")]

    if isinstance(meta, BaseException):
        meta = None
    if isinstance(stats, BaseException):
        stats = None
    tools: list[dict] = []
    if not isinstance(raw_tools, BaseException):
        try:
            tools = [
                {
                    "id": t.get("id"),
                    "name": t.get("name"),
                    "description": t.get("description"),
                    "icon": _icon_kebab(str(t.get("icon") or "wrench")),
                }
                for t in (raw_tools or [])
                if isinstance(t, dict)
            ]
        except Exception:
            tools = []

    # Custom MCP servers ("plugins") — a chip only; the drawer loads on demand.
    plugins: dict[str, Any] = {"servers": 0, "enabled": 0, "tools": 0, "connected": 0}
    if not isinstance(raw_plugins, BaseException) and isinstance(raw_plugins, dict):
        summary = raw_plugins.get("summary")
        if isinstance(summary, dict):
            plugins = summary

    return html(
        render("tabs/console.html", models=models, meta=meta, stats=stats, tools=tools,
               plugins=plugins, plugins_url=PLUGINS_URL)
    )


# --------------------------------------------------------------------------- #
# POST /ui/console/exec — sandbox terminal (BFF → /api/admin/terminal/exec)
# --------------------------------------------------------------------------- #

@router.post("/ui/console/exec")
async def console_exec(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    command = body.get("command") if isinstance(body, dict) else None
    if not isinstance(command, str) or not command.strip():
        return JSONResponse({"error": "command is required"}, status_code=400)

    try:
        result = await api.post("/api/admin/terminal/exec", json={"command": command.strip()})
    except ApiError as err:
        return JSONResponse(
            {"error": err.message}, status_code=err.status if err.status >= 400 else 500
        )
    return JSONResponse(result)


# --------------------------------------------------------------------------- #
# MCP plugins — the drawer's BFF. Every action swaps the same panel, so the
# list stays in sync without a page reload. Secrets only ever travel INTO the
# admin API: the drawer is rendered from the masked read path.
# --------------------------------------------------------------------------- #


async def _plugin_state(error: str = "") -> dict:
    payload: dict = {"servers": [], "summary": {"servers": 0, "enabled": 0, "tools": 0, "connected": 0}}
    try:
        data = await api.get("/api/admin/mcp/servers")
        if isinstance(data, dict):
            payload = {
                "servers": [s for s in (data.get("servers") or []) if isinstance(s, dict)],
                "summary": data.get("summary") if isinstance(data.get("summary"), dict) else payload["summary"],
                "error": error or data.get("error") or "",
            }
    except (ApiError, Exception) as err:
        payload["error"] = error or getattr(err, "message", str(err)) or "Could not reach the MCP API"
    return payload


def _panel(state: dict, toast: dict | None = None) -> HTMLResponse:
    return html(render("partials/console_plugins.html", **state), toast=toast)


@router.get(PLUGINS_URL)
async def console_plugins() -> HTMLResponse:
    return _panel(await _plugin_state())


@router.get("/ui/console/plugins/form")
async def console_plugin_form(server: str = "") -> HTMLResponse:
    state = await _plugin_state()
    row = next((s for s in state["servers"] if s.get("id") == server), None)
    if server and row is None:
        return _panel(state, {"message": f"No MCP server called {server}", "type": "error"})

    if row is None:
        form = {"id": "", "name": "", "transport": "http", "url": "", "command": "",
                "headers_text": "{}", "allow": [], "enabled": True}
    else:
        form = {
            "id": row.get("id", ""),
            "name": row.get("name", ""),
            "transport": row.get("transport", "http"),
            "url": row.get("url", ""),
            "command": row.get("command", ""),
            # Masked on the way out; the admin API keeps the real value.
            "headers_text": json.dumps(row.get("headers") or {}, separators=(",", ": ")),
            "allow": row.get("allow") or [],
            "enabled": bool(row.get("enabled", True)),
        }
    state["form"] = form
    return _panel(state)


@router.post("/ui/console/plugins/save")
async def console_plugin_save(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    payload: dict[str, Any] = {
        "id": str(form.get("id") or "").strip(),
        "name": str(form.get("name") or "").strip(),
        "transport": str(form.get("transport") or "http").strip(),
        "url": str(form.get("url") or "").strip(),
        "command": str(form.get("command") or "").strip(),
        "allow": str(form.get("allow") or "").strip(),
        "enabled": truthy(form.get("enabled")) if "enabled" in form else True,
    }
    headers = str(form.get("headers") or "").strip()
    if headers:
        payload["headers"] = headers        # a JSON string; the API parses it
    try:
        data = await api.post("/api/admin/mcp/servers", json=payload)
    except ApiError as err:
        return _panel(await _plugin_state(err.message or "Could not save the MCP server"),
                      {"message": err.message or "Could not save the MCP server", "type": "error"})
    except Exception:
        return _panel(await _plugin_state("Could not reach the MCP API"),
                      {"message": "Could not reach the MCP API", "type": "error"})

    saved = data.get("server") if isinstance(data, dict) else None
    if saved and not str(saved.get("status")) == "connected":
        # First contact: connect right away so the tool list is not empty.
        try:
            await api.post("/api/admin/mcp/servers/test", json={"id": saved.get("id")})
        except Exception:
            pass
    name = saved.get("name") if isinstance(saved, dict) else payload["name"]
    return _panel(await _plugin_state(),
                  {"message": f"{name or 'MCP server'} saved — testing the connection", "type": "success"})


@router.post("/ui/console/plugins/delete")
async def console_plugin_delete(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    server_id = str(form.get("id") or "").strip()
    try:
        await api.post("/api/admin/mcp/servers/delete", json={"id": server_id})
        message, kind = f"Removed {server_id}", "success"
    except ApiError as err:
        message, kind = err.message or "Could not remove it", "error"
    return _panel(await _plugin_state(), {"message": message, "type": kind})


@router.post("/ui/console/plugins/toggle")
async def console_plugin_toggle(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    server_id = str(form.get("id") or "").strip()
    enabled = truthy(form.get("enabled"))
    try:
        await api.post("/api/admin/mcp/servers/toggle", json={"id": server_id, "enabled": enabled})
        message = f"{server_id} {'enabled' if enabled else 'disabled'}"
        kind = "success"
    except ApiError as err:
        message, kind = err.message or "Could not change it", "error"
    return _panel(await _plugin_state(), {"message": message, "type": kind})


@router.post("/ui/console/plugins/test")
async def console_plugin_test(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    server_id = str(form.get("id") or "").strip()
    try:
        data = await api.post("/api/admin/mcp/servers/test", json={"id": server_id})
        count = int((data or {}).get("count") or 0) if isinstance(data, dict) else 0
        if data.get("ok"):
            message, kind = f"{server_id} connected — {count} tools available", "success"
        else:
            message, kind = f"{server_id}: {data.get('error') or 'could not connect'}", "error"
    except ApiError as err:
        message, kind = err.message or "Could not test the server", "error"
    return _panel(await _plugin_state(), {"message": message, "type": kind})


@router.post("/ui/console/plugins/test-all")
async def console_plugin_test_all() -> HTMLResponse:
    try:
        data = await api.post("/api/admin/mcp/servers/refresh", json={})
        probed = int((data or {}).get("probed") or 0) if isinstance(data, dict) else 0
        message, kind = f"Reconnected {probed} plugin(s)", "success"
    except ApiError as err:
        message, kind = err.message or "Could not refresh the plugins", "error"
    return _panel(await _plugin_state(), {"message": message, "type": kind})


def _schema_fields(schema: dict) -> list[dict]:
    """One form field per declared property, with the type it asks for."""
    properties = schema.get("properties") if isinstance(schema, dict) else None
    required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
    fields: list[dict] = []
    for name, spec in (properties or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        kind = str(spec.get("type") or "string").split("|")[0]
        if kind not in ("string", "number", "integer", "boolean"):
            kind = "string"
        default = spec.get("default", "")
        fields.append({
            "name": str(name),
            "type": kind,
            "required": name in required,
            "default": "" if default is None else str(default),
            "description": str(spec.get("description") or "")[:120],
        })
    return fields[:20]


@router.get("/ui/console/plugins/run")
async def console_plugin_run(server: str = "", tool: str = "") -> HTMLResponse:
    """The "run this tool" form, generated from the tool's cached schema."""
    server_id, tool_name = server.strip(), tool.strip()
    if not server_id or not tool_name:
        return html("")
    description, schema = "", {}
    try:
        data = await api.get("/api/admin/mcp/servers")
        for row in (data or {}).get("servers") or []:
            if row.get("id") != server_id:
                continue
            for entry in row.get("tools") or []:
                if entry.get("name") == tool_name:
                    description = str(entry.get("description") or "")
                    schema = entry.get("input_schema") or {}
    except (ApiError, Exception):
        pass
    return html(render("partials/console_plugin_run.html", server=server_id, tool=tool_name,
                       description=description, fields=_schema_fields(schema)))


@router.post("/ui/console/plugins/call")
async def console_plugin_call(request: Request) -> HTMLResponse:
    """Run one tool from the drawer and show what it actually returned."""
    form = await form_dict(request)
    server_id = str(form.get("server") or "").strip()
    tool_name = str(form.get("tool") or "").strip()
    arguments: dict[str, Any] = {}
    for key, value in form.items():
        if not str(key).startswith("arg__"):
            continue
        name = str(key)[5:]
        if isinstance(value, str):
            text = value.strip()
            if text == "":
                continue                  # an empty optional field is simply not passed
            try:
                arguments[name] = json.loads(text) if text[:1] in "[{" else text
            except Exception:
                arguments[name] = value
        else:
            arguments[name] = value
    payload: dict[str, Any] = {"server": server_id, "tool": tool_name}
    if arguments:
        payload["arguments"] = arguments

    output, ok, kind = "", True, "success"
    try:
        data = await api.post("/api/admin/mcp/servers/call", json=payload)
        ok = bool(data.get("ok")) if isinstance(data, dict) else False
        output = str((data or {}).get("output") or (data or {}).get("error") or "")
        kind = "success" if ok else "error"
    except ApiError as err:
        ok, kind = False, "error"
        output = err.message or "The tool call failed"
    except Exception as err:
        ok, kind = False, "error"
        output = f"{err.__class__.__name__}: {err}"
    return html(render("partials/console_plugin_run.html", server=server_id, tool=tool_name,
                       output=output[:8000], ok=ok),
               toast={"message": f"{server_id}::{tool_name} returned", "type": kind})


def _schema_names(form: dict[str, Any]) -> set[str]:
    """Argument names present in a submitted run form (for tests + callers)."""
    return {str(k)[5:] for k in form if str(k).startswith("arg__")}


# --------------------------------------------------------------------------- #
# GET /ui/console/chat-meta — honest routing metadata for the last chat reply.
#
# The gateway's SSE stream does not carry the `_nova` block (only non-stream
# responses do), but every completion is logged to RequestLog. We look up the
# newest /v1/chat/completions entry for the requested model at/after the
# client's send timestamp and expose the same honest fields the TSX chips show.
# --------------------------------------------------------------------------- #

@router.get("/ui/console/chat-meta")
async def console_chat_meta(ts: str = "", model: str = "") -> JSONResponse:
    try:
        ts_val = int(float(ts))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False})

    try:
        logs = await api.get("/api/admin/logs", params={"limit": 30})
    except (ApiError, Exception):
        return JSONResponse({"ok": False})

    now_ms = time.time() * 1000
    for entry in logs or []:
        if not isinstance(entry, dict):
            continue
        if "chat/completions" not in str(entry.get("endpoint") or ""):
            continue
        if model and entry.get("model") != model:
            continue
        log_ts = entry.get("ts") or 0
        # tolerate small client/server clock skew, reject entries from long ago
        if log_ts < ts_val - 1500 or log_ts > now_ms + 60_000:
            continue

        requested = entry.get("model") or ""
        upstream = entry.get("upstream_model") or requested
        return JSONResponse(
            {
                "ok": True,
                "meta": {
                    "provider": entry.get("provider_name") or "gateway",
                    "upstream_model": upstream,
                    "fallback": bool(requested and upstream and upstream != requested),
                    "stage": None,  # the log does not carry the pipeline stage
                    "cached": False,
                    "spoofed": bool(entry.get("spoofed")),
                    "latency_ms": entry.get("latency_ms") or 0,
                    "tokens_in": entry.get("tokens_in") or 0,
                    "tokens_out": entry.get("tokens_out") or 0,
                },
            }
        )

    return JSONResponse({"ok": False})
