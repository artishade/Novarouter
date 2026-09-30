"""Terminal tab — multi-session terminal with a sidebar assistant.

Layout: the terminal owns the main pane (session tabs, real shells, one-shot
commands) and a collapsible right sidebar routes chat + `$ commands` +
`! agent tasks` through the same Nova Console logic.

  GET  /partials/tab/terminal   → the full fragment (history + live sessions)
  POST /ui/terminal/exec        → JSON sandbox exec (delegates to console BFF)
  POST /ui/terminal/clear       → wipes terminal history (BFF → /terminal/clear)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ui.api_client import ApiError, api
from ui.render import html, render

router = APIRouter(tags=["ui:terminal"])


def _err_panel(message: str) -> HTMLResponse:
    return html(
        render("partials/console_error.html", message=message, retry_url="/partials/tab/terminal"),
        status_code=200,
    )


@router.get("/partials/tab/terminal")
async def terminal_tab() -> HTMLResponse:
    history: list[dict] = []
    system: dict = {}
    memory: dict = {}
    sessions: dict = {"sessions": [], "active": None, "max_sessions": 8}
    try:
        payload = await api.get("/api/admin/terminal/history")
        if isinstance(payload, dict):
            history = payload.get("history") or []
            system = payload.get("system") or {}
            memory = payload.get("memory_config") or {}
            sessions = payload.get("sessions") or sessions
    except (ApiError, Exception):
        history, system, memory = [], {}, {}
    return html(
        render(
            "tabs/terminal.html",
            history=history[-40:],
            system=system,
            memory=memory,
            sessions=sessions,
            sessions_list=sessions.get("sessions") or [],
            active_session=sessions.get("active"),
        )
    )


@router.post("/ui/terminal/exec")
async def terminal_exec(request: Request) -> JSONResponse:
    """Same executor as the console (`/ui/console/exec`) — shared sandbox."""
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


@router.post("/ui/terminal/clear")
async def terminal_clear() -> HTMLResponse:
    try:
        await api.post("/api/admin/terminal/clear")
    except (ApiError, Exception):
        pass
    return html("", toast={"message": "Command history cleared", "type": "success"}, refresh=True)
