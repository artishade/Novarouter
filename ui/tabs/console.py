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
Action endpoints:
  POST /ui/console/exec          → JSON sandbox exec (BFF → terminal exec).
"""
from __future__ import annotations

import re
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ui.api_client import ApiError, api
from ui.render import html, render

router = APIRouter(tags=["ui:console"])


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
    try:
        payload = await api.get("/api/admin/models")
    except (ApiError, Exception) as err:
        message = err.message if isinstance(err, ApiError) else str(err) or "Failed to load models"
        return _err_panel(message)

    rows: list[Any] = []
    if isinstance(payload, dict):
        rows = payload.get("rows") or []
    elif isinstance(payload, list):
        rows = payload
    # TSX: enabledModels = models.filter(m => m.enabled); picker slices to 40.
    models = [m for m in rows if isinstance(m, dict) and m.get("enabled")]

    meta = None
    stats = None
    tools: list[dict] = []
    try:
        meta = await api.get("/api/admin/meta")
    except (ApiError, Exception):
        meta = None
    try:
        stats = await api.get("/api/admin/stats")
    except (ApiError, Exception):
        stats = None
    try:
        raw_tools = await api.get("/api/agent/tools")
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
    except (ApiError, Exception):
        tools = []

    return html(
        render("tabs/console.html", models=models, meta=meta, stats=stats, tools=tools)
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
