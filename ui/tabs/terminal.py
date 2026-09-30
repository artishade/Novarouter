"""Nova Console + Terminal workspace — one unified cloud shell page.

The sidebar "Nova Console" entry opens this as a FULL standalone page in a
new browser tab (`/terminal`), laid out like the reference screenshot:

    ┌─────────────────────────┬──────────────────────────────┐
    │ Nova Console (chat)     │ Root@Build cloud terminal    │
    │ chat / $cmd / !goal /   │ xterm.js PTY · permanent     │
    │ /shortcut · agent steps │ root shell on Debian         │
    └─────────────────────────┴──────────────────────────────┘

Routes:
  GET  /terminal                 → the standalone page (own HTML shell,
                                   no dashboard chrome — meant for a tab)
  GET  /partials/tab/terminal    → the embedded fragment (kept for compat;
                                   the dashboard no longer links to it)
  POST /ui/terminal/exec         → JSON sandbox exec (console BFF)
  POST /ui/terminal/clear        → wipes terminal history (BFF → /terminal/clear)
  POST /ui/terminal/settings     → HTMX form: save a cloud build target
  GET  /ui/terminal/provider-form?provider=<id> → add/edit form fragment
  GET  /ui/terminal/providers    → the live providers panel fragment
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ui.api_client import ApiError, api
from ui.render import form_dict, html, render, truthy

router = APIRouter(tags=["ui:terminal"])

PANEL_URL = "/ui/terminal/providers"


def _err_panel(message: str) -> HTMLResponse:
    return html(
        render("partials/console_error.html", message=message, retry_url="/partials/tab/terminal"),
        status_code=200,
    )


async def _build_info() -> dict:
    try:
        payload = await api.get("/api/build/info")
        return payload if isinstance(payload, dict) else {}
    except (ApiError, Exception):
        return {}


async def _session_snapshot() -> dict:
    try:
        payload = await api.get("/api/admin/terminal/history")
        if isinstance(payload, dict):
            sessions = payload.get("sessions")
            if isinstance(sessions, dict):
                return sessions
    except (ApiError, Exception):
        pass
    return {"sessions": [], "active": None, "max_sessions": 8}


# --------------------------------------------------------------------------- #
# GET /terminal — the standalone cloud workspace page
# --------------------------------------------------------------------------- #

@router.get("/terminal")
async def terminal_page() -> HTMLResponse:
    sessions = await _session_snapshot()
    build = await _build_info()
    return html(
        render(
            "terminal_page.html",
            sessions=sessions,
            sessions_list=sessions.get("sessions") or [],
            active_session=sessions.get("active"),
            history=[],
            system={},
            memory={},
            tools=build.get("tools") or [],
            providers=build.get("providers") or [],
            identity=build.get("identity") or {},
            engine=build.get("engine") or {},
        )
    )


@router.get("/partials/tab/terminal")
async def terminal_tab() -> HTMLResponse:
    """Embedded fragment — same workspace, sized for the dashboard frame."""
    sessions = await _session_snapshot()
    build = await _build_info()
    return html(
        render(
            "tabs/terminal.html",
            history=[],
            system={},
            memory={},
            sessions=sessions,
            sessions_list=sessions.get("sessions") or [],
            active_session=sessions.get("active"),
            tools=build.get("tools") or [],
            providers=build.get("providers") or [],
            identity=build.get("identity") or {},
            engine=build.get("engine") or {},
        )
    )


# --------------------------------------------------------------------------- #
# One-shot exec (shared with the console BFF)
# --------------------------------------------------------------------------- #

@router.post("/ui/terminal/exec")
async def terminal_exec(request: Request) -> JSONResponse:
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


# --------------------------------------------------------------------------- #
# Cloud build-target settings — add / edit / delete from the web UI
# --------------------------------------------------------------------------- #

@router.get("/ui/terminal/provider-form")
async def provider_form(provider: str = "") -> HTMLResponse:
    build = await _build_info()
    presets = {p.get("id"): p for p in build.get("providers") or []}
    preset = presets.get(provider) or {}
    return html(render("partials/terminal_provider_form.html", preset=preset))


@router.get(PANEL_URL)
async def providers_panel() -> HTMLResponse:
    build = await _build_info()
    return html(
        render(
            "partials/terminal_providers.html",
            providers=build.get("providers") or [],
            engine=build.get("engine") or {},
            tools=build.get("tools") or [],
        )
    )


@router.post("/ui/terminal/settings")
async def save_settings(request: Request) -> HTMLResponse:
    """HTMX form submit — upserts one cloud build target.

    Fields: provider (preset id), [name, type] for custom rows, then any
    credential keys the preset defines (endpoint, bucket_name, access_key,
    secret_key, token, region…). Checkbox fields arrive as "on".
    """
    form = await form_dict(request)
    provider = str(form.get("provider") or "").strip()
    if not provider:
        return html(
            render("partials/terminal_providers.html", providers=[], engine={}, tools=[],
                   error="Missing build target id"),
            status_code=400,
        )

    config: dict[str, str] = {}
    for key, value in form.items():
        if key in ("provider", "name", "type"):
            continue
        text = value if isinstance(value, str) else ""
        if truthy(text) and text == "on":  # checkbox without a value
            continue
        if text.strip():
            config[key] = text.strip()

    payload: dict[str, Any] = {"id": provider, "config": config}
    if provider.startswith("custom:") or provider == "__new__":
        payload = {
            "name": str(form.get("name") or "Custom target"),
            "type": str(form.get("type") or "s3_compatible"),
            "config": config,
        }
    try:
        await api.post("/api/build/providers", json=payload)
        message = "Build target saved — background tasks can use it"
        toast_type = "success"
    except ApiError as err:
        message = err.message or "Could not save the build target"
        toast_type = "error"
    except Exception:
        message = "Could not reach the build API"
        toast_type = "error"

    build = await _build_info()
    return html(
        render(
            "partials/terminal_providers.html",
            providers=build.get("providers") or [],
            engine=build.get("engine") or {},
            tools=build.get("tools") or [],
        ),
        toast={"message": message, "type": toast_type},
    )


@router.post("/ui/terminal/tools-register")
async def tools_register() -> HTMLResponse:
    """Re-run the permanent agent-tool registration (button in the drawer)."""
    message, toast_type = "Agent toolbox re-registered — permanently active", "success"
    try:
        await api.post("/api/build/tools/register")
    except (ApiError, Exception) as err:
        message = str(getattr(err, "message", err)) or "Could not re-register the toolbox"
        toast_type = "error"
    build = await _build_info()
    return html(
        render(
            "partials/terminal_providers.html",
            providers=build.get("providers") or [],
            engine=build.get("engine") or {},
            tools=build.get("tools") or [],
        ),
        toast={"message": message, "type": toast_type},
    )


@router.post("/ui/terminal/provider-delete")
async def delete_custom_provider(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    key = str(form.get("provider") or "").strip()
    message, toast_type = "Custom build target removed", "success"
    if key:
        try:
            # Custom rows live in the storage table; remove via the admin API.
            rows = await api.get("/api/admin/storage/info")
            ids = {p.get("id") for p in (rows.get("providers") or [])} if isinstance(rows, dict) else set()
            if key in ids:
                await api.post("/api/admin/storage/disconnect", json={"provider_key": key})
            message = "Build target disconnected"
        except (ApiError, Exception) as err:
            message = str(getattr(err, "message", err)) or "Could not remove the build target"
            toast_type = "error"
    build = await _build_info()
    return html(
        render(
            "partials/terminal_providers.html",
            providers=build.get("providers") or [],
            engine=build.get("engine") or {},
            tools=build.get("tools") or [],
        ),
        toast={"message": message, "type": toast_type},
    )
