"""Routes tab — fallback route manager + identity-spoofing preview.

Python/HTMX port of src/components/nova/tabs/RoutesTab.tsx. Every mutation
forwards to the same JSON API the gateway exposes (/api/admin/routes*) so
behaviour, error messages and wire shapes are exact; this module only turns
those responses into fragments + HX toasts.

Endpoints:
  GET    /partials/tab/routes          main fragment (list + alert + preview entry)
  GET    /ui/routes/form?id=N          create/edit <dialog> partial
  GET    /ui/routes/preview?model=…    3-stage resolution preview <dialog> partial
  POST   /ui/routes/create             → POST    /api/admin/routes
  POST   /ui/routes/{id}/update        → PATCH   /api/admin/routes/{id}
  POST   /ui/routes/{id}/toggle        → PATCH   /api/admin/routes/{id} (enabled)
  DELETE /ui/routes/{id}               → DELETE  /api/admin/routes/{id}
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ui.api_client import ApiError, api
from ui.render import form_dict, html, render, truthy

router = APIRouter(tags=["ui:routes"])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

async def _safe_get(path: str, **kw: Any) -> Any:
    """GET that degrades to None — optional payloads (meta/stats) only."""
    try:
        return await api.get(path, **kw)
    except Exception:  # noqa: BLE001 — any failure just hides the widget
        return None


def _err_message(err: BaseException) -> str:
    if isinstance(err, ApiError):
        return err.message
    return f"API request failed: {err.__class__.__name__}"


async def _model_options() -> list[dict[str, Any]]:
    """Exposed ids for the fallback picker (Model[] in the TSX)."""
    res = await _safe_get("/api/admin/models")
    rows = (res or {}).get("rows") or []
    return [{"id": m.get("id"), "exposed_id": m.get("exposed_id") or ""} for m in rows]


def _toast(message: str, kind: str) -> dict[str, str]:
    return {"message": message, "type": kind}


# --------------------------------------------------------------------------- #
# GET /partials/tab/routes — main fragment
# --------------------------------------------------------------------------- #

@router.get("/partials/tab/routes")
async def routes_tab() -> HTMLResponse:
    routes, meta, stats = await asyncio.gather(
        api.get("/api/admin/routes"),
        _safe_get("/api/admin/meta"),
        _safe_get("/api/admin/stats"),
        return_exceptions=True,
    )
    if isinstance(routes, BaseException) or routes is None:
        error = _err_message(routes) if isinstance(routes, BaseException) else "no response"
        return html(
            render(
                "tabs/routes.html",
                error=error,
                routes=None,
                meta=None,
                stats=None,
            )
        )
    return html(
        render(
            "tabs/routes.html",
            error=None,
            routes=routes if isinstance(routes, list) else [],
            meta=meta if isinstance(meta, dict) else None,
            stats=stats if isinstance(stats, dict) else None,
        )
    )


# --------------------------------------------------------------------------- #
# GET /ui/routes/form — create / edit dialog
# --------------------------------------------------------------------------- #

@router.get("/ui/routes/form")
async def routes_form(request: Request) -> HTMLResponse:
    route_id = request.query_params.get("id")
    initial: dict[str, Any] = {"public_id": "", "fallbacks": [], "auto": True, "note": ""}
    editing = False

    if route_id:
        routes = await _safe_get("/api/admin/routes") or []
        row = next((r for r in routes if str(r.get("id")) == str(route_id)), None)
        if row is not None:
            editing = True
            initial = {
                "public_id": row.get("public_id") or "",
                "fallbacks": list(row.get("fallbacks") or []),
                "auto": bool(row.get("auto")),
                "note": row.get("note") or "",
            }

    return html(
        render(
            "partials/routes_form.html",
            editing=editing,
            route_id=route_id,
            initial=initial,
            models=await _model_options(),
            error=None,
        )
    )


# --------------------------------------------------------------------------- #
# POST /ui/routes/create  ·  POST /ui/routes/{id}/update
# --------------------------------------------------------------------------- #

def _route_validation_toast(public_id: str, fallbacks: list[str], auto: bool) -> str | None:
    """Exact client-side messages from RouteFormDialog.submit()."""
    if not public_id:
        return "Public model id is required (use * for the default chain)"
    if len(fallbacks) == 0 and not auto:
        return "Pick at least one fallback model, or enable auto stand-ins"
    return None


async def _save_route(request: Request, route_id: str | None) -> HTMLResponse:
    form = await request.form()
    public_id = str(form.get("public_id") or "").strip()
    note = str(form.get("note") or "").strip()
    auto = truthy(form.get("auto"))
    # Chain order = order of the hidden fallbacks inputs (Alpine-managed).
    fallbacks = [str(v).strip() for k, v in form.multi_items() if k == "fallbacks" and str(v).strip()]

    async def rerender(toast: dict[str, str] | None) -> HTMLResponse:
        frag = render(
            "partials/routes_form.html",
            editing=route_id is not None,
            route_id=route_id,
            initial={"public_id": public_id, "fallbacks": fallbacks, "auto": auto, "note": note},
            models=await _model_options(),
            error=None,
        )
        return html(frag, toast=toast)

    problem = _route_validation_toast(public_id, fallbacks, auto)
    if problem is not None:
        return await rerender(_toast(problem, "error"))

    payload = {"public_id": public_id, "fallbacks": fallbacks, "auto": auto, "note": note}
    try:
        if route_id is None:
            await api.post("/api/admin/routes", json=payload)
        else:
            await api.patch(f"/api/admin/routes/{route_id}", json=payload)
    except ApiError as err:
        # Duplicate public_id / rename clash surface the exact API message.
        return await rerender(_toast(err.message, "error"))

    verb = "updated" if route_id is not None else "created"
    return html("", toast=_toast(f"Route {public_id} {verb}", "success"), refresh=True)


@router.post("/ui/routes/create")
async def routes_create(request: Request) -> HTMLResponse:
    return await _save_route(request, route_id=None)


@router.post("/ui/routes/{route_id}/update")
async def routes_update(request: Request, route_id: str) -> HTMLResponse:
    return await _save_route(request, route_id=route_id)


# --------------------------------------------------------------------------- #
# POST /ui/routes/{id}/toggle — enabled switch
# --------------------------------------------------------------------------- #

@router.post("/ui/routes/{route_id}/toggle")
async def routes_toggle(request: Request, route_id: str) -> HTMLResponse:
    form = await form_dict(request)
    enabled = truthy(form.get("enabled"))

    routes = await _safe_get("/api/admin/routes") or []
    row = next((r for r in routes if str(r.get("id")) == str(route_id)), None)
    public_id = (row or {}).get("public_id") or f"#{route_id}"

    try:
        await api.patch(f"/api/admin/routes/{route_id}", json={"enabled": enabled})
    except ApiError as err:
        return html("", toast=_toast(err.message, "error"), refresh=True)
    state = "enabled" if enabled else "disabled"
    return html("", toast=_toast(f"Route {public_id} {state}", "success"), refresh=True)


# --------------------------------------------------------------------------- #
# DELETE /ui/routes/{id}
# --------------------------------------------------------------------------- #

@router.delete("/ui/routes/{route_id}")
async def routes_delete(route_id: str) -> HTMLResponse:
    routes = await _safe_get("/api/admin/routes") or []
    row = next((r for r in routes if str(r.get("id")) == str(route_id)), None)
    public_id = (row or {}).get("public_id") or f"#{route_id}"

    try:
        await api.delete(f"/api/admin/routes/{route_id}")
    except ApiError as err:
        return html("", toast=_toast(err.message, "error"), refresh=True)
    return html("", toast=_toast(f"Route {public_id} deleted", "success"), refresh=True)


# --------------------------------------------------------------------------- #
# GET /ui/routes/preview — 3-stage resolution preview dialog
# --------------------------------------------------------------------------- #

@router.get("/ui/routes/preview")
async def routes_preview(request: Request) -> HTMLResponse:
    model = (request.query_params.get("model") or "").strip()
    if not model:
        return html("", toast=_toast("Enter a model id to preview its fallback resolution", "error"))
    try:
        preview = await api.get("/api/admin/routes/preview", params={"model": model})
    except ApiError as err:
        return html("", toast=_toast(err.message or "Preview failed", "error"))
    return html(render("partials/routes_preview.html", preview=preview))
