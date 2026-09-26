"""Providers tab — Python UI port of src/components/nova/tabs/ProvidersTab.tsx.

Fragments:
  GET /partials/tab/providers        full tab (list root + add/sign-in dialogs)
  GET /partials/providers/list       list root only (live-refresh + filter target)
  GET /partials/providers/keys       one provider's keys panel (reload / after mutation)

Actions (HTMX mutations → html(..., toast=..., refresh=True)):
  POST /ui/providers/create         preset-based create (auto-sync surfaced)
  POST /ui/providers/toggle         enabled switch (PATCH)
  POST /ui/providers/priority       priority edit (PATCH)
  POST /ui/providers/test           real connectivity probe (JSON — consumed by
                                    the inline Test / Test All JS sweep)
  POST /ui/providers/signin         session + upstream key (sign-in dialog)
  POST /ui/providers/signout        drop session
  POST /ui/providers/delete         delete provider (novafree guard surfaces API error)
  POST /ui/providers/keys/add       single or bulk key import (newline/comma split)
  POST /ui/providers/keys/delete    remove one upstream key
"""
from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ui.api_client import ApiError, api
from ui.render import form_dict, html, render, truthy

router = APIRouter(tags=["ui:providers"])

# Test results (mirrors the TSX `testResults` React state): provider id → last
# probe payload. Module-level, so badges survive live re-renders like the
# React state survived the 5s poll. Ephemeral by design (lost on restart).
_test_results: dict[int, dict] = {}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _to_int(v: Any) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _form_str(form: dict, key: str) -> str:
    return str(form.get(key) or "").strip()


def _root_url(q: str = "", expand: int | None = None) -> str:
    params: dict[str, str] = {}
    if q:
        params["q"] = q
    if expand:
        params["expand"] = str(expand)
    qs = urlencode(params)
    return f"/partials/providers/list?{qs}" if qs else "/partials/providers/list"


def _filter_providers(rows: list[dict], q: str) -> list[dict]:
    if not q:
        return rows
    needle = q.lower()
    return [
        p for p in rows
        if needle in str(p.get("name", "")).lower()
        or needle in str(p.get("key", "")).lower()
        or needle in str(p.get("prefix", "")).lower()
        or needle in str(p.get("base_url", "")).lower()
    ]


def _decorate(p: dict, q: str, expand: int | None) -> dict:
    """Add per-card computed fields (URLs, cached test result)."""
    pid = p.get("id")
    p["_test"] = _test_results.get(pid)
    if expand == pid:
        p["_toggle_url"] = _root_url(q, None)          # collapse
    else:
        p["_toggle_url"] = _root_url(q, pid)           # expand this one
    return p


async def _providers_ctx(request: Request) -> dict:
    """List context shared by the tab fragment and the live-refresh partial."""
    sp = request.query_params
    q = (sp.get("q") or "").strip()
    expand = _to_int(sp.get("expand") or "")
    if expand is not None and expand <= 0:
        expand = None

    rows: list[dict] = []
    keys: list[dict] = []
    error: str | None = None
    try:
        rows = await api.get("/api/admin/providers")
    except ApiError as e:
        error = e.message
    except Exception as e:  # noqa: BLE001 — any self-API failure becomes the error panel
        error = f"Failed to load providers ({e.__class__.__name__})"

    visible = _filter_providers(rows, q)
    visible = [_decorate(p, q, expand) for p in visible]
    # "Test All" targets — same rule as ProvidersTab.tsx (enabled + has keys or builtin).
    test_ids = [
        p["id"] for p in rows
        if p.get("enabled") and (p.get("key_count", 0) > 0 or p.get("kind") == "builtin")
    ]

    if expand and not error:
        try:
            keys = await api.get("/api/admin/keys", params={"provider_id": expand})
        except Exception:  # noqa: BLE001 — panel renders its own empty state
            keys = []

    stats = None
    try:
        stats = await api.get("/api/admin/stats")
    except Exception:  # noqa: BLE001
        stats = None
    meta = None
    try:
        meta = await api.get("/api/admin/meta")
    except Exception:  # noqa: BLE001
        meta = None

    return {
        "rows": rows,
        "visible": visible,
        "test_ids": test_ids,
        "keys": keys,
        "q": q,
        "expand": expand,
        "error": error,
        "stats": stats,
        "meta": meta,
        "now_ms": time.time() * 1000,
    }


async def _providers_tab_ctx(request: Request) -> dict:
    ctx = await _providers_ctx(request)
    presets: list[dict] = []
    try:
        presets = await api.get("/api/admin/providers/presets")
    except Exception:  # noqa: BLE001 — dialog degrades to Custom-only
        presets = []
    ctx["presets"] = presets
    return ctx


async def _provider_name(pid: int) -> str:
    """Best-effort name lookup for toast messages."""
    try:
        rows = await api.get("/api/admin/providers")
        for r in rows or []:
            if r.get("id") == pid:
                return str(r.get("name") or "Provider")
    except Exception:  # noqa: BLE001
        pass
    return "Provider"


def _toast(fragment: str = "", message: str = "", ttype: str = "success", refresh: bool = True) -> HTMLResponse:
    return html(fragment, toast={"message": message, "type": ttype}, refresh=refresh)


def _form_error(message: str, target_fragment: str | None = None) -> HTMLResponse:
    """Dialog forms: error text into the inline feedback slot + error toast."""
    from html import escape

    frag = target_fragment or (
        f'<p class="rounded-md border border-rose-900/60 bg-rose-950/40 px-2.5 py-1.5 '
        f'text-[11px] text-rose-300">{escape(str(message))}</p>'
    )
    return html(frag, toast={"message": str(message), "type": "error"}, refresh=False)


async def _fetch_provider(pid: int) -> dict | None:
    try:
        return next((p for p in await api.get("/api/admin/providers") if p.get("id") == pid), None)
    except Exception:  # noqa: BLE001
        return None


async def _fetch_keys(pid: int) -> list[dict]:
    try:
        return await api.get("/api/admin/keys", params={"provider_id": pid})
    except Exception:  # noqa: BLE001
        return []


def _keys_error_fragment(pid: int, message: str) -> str:
    """Degraded keys panel (correct swap id) when the provider row is unreachable."""
    from html import escape

    return (
        f'<div id="pkeys-{pid}" class="border-t border-slate-800 bg-black/20 p-4">'
        f'<p class="rounded-md border border-rose-900/60 bg-rose-950/40 px-2.5 py-1.5 '
        f'text-[11px] text-rose-300">{escape(str(message))}</p></div>'
    )


async def _keys_panel_response(pid: int | None, message: str, ttype: str, refresh: bool) -> HTMLResponse:
    """Toast + re-rendered keys panel (TSX parity: the open panel survives errors)."""
    if pid is None:
        return _toast(message=message, ttype=ttype, refresh=refresh)
    provider = await _fetch_provider(pid)
    if provider is None:
        return html(_keys_error_fragment(pid, message), toast={"message": message, "type": ttype}, refresh=refresh)
    keys = await _fetch_keys(pid)
    frag = render("partials/providers_keys.html", p=provider, keys=keys,
                  now_ms=time.time() * 1000, q="", expand=pid)
    return html(frag, toast={"message": message, "type": ttype}, refresh=refresh)


# --------------------------------------------------------------------------- #
# Fragments
# --------------------------------------------------------------------------- #

@router.get("/partials/tab/providers")
async def providers_tab(request: Request) -> HTMLResponse:
    ctx = await _providers_tab_ctx(request)
    return html(render("tabs/providers.html", **ctx))


@router.get("/partials/providers/list")
async def providers_list(request: Request) -> HTMLResponse:
    ctx = await _providers_ctx(request)
    # region=results renders ONLY the results region (#providers-results) so a
    # search never re-renders the focused input (mobile keyboard safety).
    if request.query_params.get("region") == "results":
        return html(render("partials/providers_results.html", **ctx))
    return html(render("partials/providers_list.html", **ctx))


@router.get("/partials/providers/keys")
async def providers_keys(request: Request) -> HTMLResponse:
    sp = request.query_params
    pid = _to_int(sp.get("provider_id") or "")
    provider: dict | None = None
    keys: list[dict] = []
    if pid is not None:
        provider = await _fetch_provider(pid)
        keys = await _fetch_keys(pid)
    return html(render("partials/providers_keys.html", p=provider or {}, keys=keys,
                       now_ms=time.time() * 1000, q="", expand=pid))


# --------------------------------------------------------------------------- #
# Mutations
# --------------------------------------------------------------------------- #

@router.post("/ui/providers/create")
async def create_provider(request: Request) -> Response:
    """Create a provider.

    ?logs=1 (used by the Add dialog's live-log JS) → JSON mode: the admin API
    runs the catalogue discovery as a background job and this returns the SSE
    stream URL so the dialog can follow the progress line by line.
    Without logs=1 → legacy HTMX behaviour (inline sync + reset script).
    """
    form = await form_dict(request)
    logs_mode = truthy(request.query_params.get("logs"))
    name = _form_str(form, "name")
    if not name:
        msg = "Give the provider a name"
        if logs_mode:
            return JSONResponse({"ok": False, "error": msg})
        return _form_error(msg)

    payload: dict[str, Any] = {"name": name, "kind": _form_str(form, "kind") or "openai"}
    for field in ("base_url", "prefix", "auth_url", "free_tier", "docs_url"):
        value = _form_str(form, field)
        if value:
            payload[field] = value
    priority = _to_int(form.get("priority"))
    if priority is not None:
        payload["priority"] = priority
    api_keys = _form_str(form, "api_keys")
    if api_keys:
        payload["api_keys"] = api_keys

    try:
        res = await api.post(
            "/api/admin/providers",
            json=payload,
            params={"logs": "1"} if logs_mode else None,
        )
    except ApiError as e:
        if logs_mode:
            return JSONResponse({"ok": False, "error": e.message})
        return _form_error(e.message)
    except Exception as e:  # noqa: BLE001
        msg = f"Failed to create provider ({e.__class__.__name__})"
        if logs_mode:
            return JSONResponse({"ok": False, "error": msg})
        return _form_error(msg)

    if logs_mode:
        return JSONResponse({
            "ok": True,
            "id": res.get("id"),
            "name": name,
            "keys_added": res.get("keys_added", 0),
            "job": res.get("job"),
            "stream": res.get("stream"),
        })

    keys_added = res.get("keys_added", 0)
    models_added = res.get("models_added") or 0
    sync_error = res.get("sync_error")
    if sync_error:
        message = (
            f"{name} added · {keys_added} key(s) imported — model discovery failed: "
            f"{sync_error}. Use \"Sync Catalogue\" on the Models tab to retry."
        )
        ttype = "info"
    elif models_added > 0:
        message = f"{name} added · {keys_added} key(s) · {models_added} live models discovered"
        ttype = "success"
    else:
        message = f"{name} added · {keys_added} key(s) imported"
        ttype = "success"

    reset_script = (
        '<script>(function(){'
        "var f=document.getElementById('np-add-form'); if(f){f.reset();}"
        "var v=document.getElementById('np-priority-val'); if(v){v.textContent='50';}"
        "var fb=document.getElementById('np-add-feedback'); if(fb){fb.innerHTML='';}"
        "var d=document.getElementById('np-add-dialog'); if(d){d.close();}"
        "})();</script>"
    )
    return html(reset_script, toast={"message": message, "type": ttype}, refresh=True)


@router.post("/ui/providers/toggle")
async def toggle_provider(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    pid = _to_int(form.get("id"))
    enabled = truthy(form.get("enabled"))
    if pid is None:
        return _toast(message="Invalid provider id", ttype="error")
    try:
        await api.patch(f"/api/admin/providers/{pid}", json={"enabled": enabled})
    except ApiError as e:
        return _toast(message=e.message, ttype="error")
    except Exception as e:  # noqa: BLE001
        return _toast(message=f"Failed to update provider ({e.__class__.__name__})", ttype="error")
    name = await _provider_name(pid)
    return _toast(message=f"{name} {'enabled' if enabled else 'disabled'}")


@router.post("/ui/providers/priority")
async def set_priority(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    pid = _to_int(form.get("id"))
    priority = _to_int(form.get("priority"))
    if pid is None:
        return _toast(message="Invalid provider id", ttype="error")
    if priority is None:
        return _toast(message="Priority must be a number", ttype="error")
    try:
        await api.patch(f"/api/admin/providers/{pid}", json={"priority": priority})
    except ApiError as e:
        return _toast(message=e.message, ttype="error")
    except Exception as e:  # noqa: BLE001
        return _toast(message=f"Failed to update provider ({e.__class__.__name__})", ttype="error")
    name = await _provider_name(pid)
    return _toast(message=f"{name} priority set to {priority}")


@router.post("/ui/providers/test")
async def test_provider(request: Request) -> JSONResponse:
    """Real probe — consumed by the inline Test / Test All JS (badge + toasts)."""
    pid = _to_int(request.query_params.get("id") or "")
    if pid is None:
        return JSONResponse({"ok": False, "error": "Invalid provider id"})
    try:
        res = await api.post(f"/api/admin/providers/{pid}/test")
    except ApiError as e:
        return JSONResponse({"ok": False, "error": e.message})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"Test failed ({e.__class__.__name__})"})
    _test_results[pid] = res
    return JSONResponse(res)


@router.post("/ui/providers/signin")
async def sign_in(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    pid = _to_int(form.get("id"))
    key = _form_str(form, "key")
    if pid is None:
        return _form_error("Invalid provider id")
    if not key:
        return _form_error("Paste the API key you copied from the provider console")
    try:
        await api.post(f"/api/admin/providers/{pid}/signin")
        await api.post("/api/admin/keys", json={
            "provider_id": pid, "api_key": key, "label": "sign-in",
        })
    except ApiError as e:
        return _form_error(e.message)
    except Exception as e:  # noqa: BLE001
        return _form_error(f"Sign-in failed ({e.__class__.__name__})")

    name = await _provider_name(pid)
    close_script = (
        '<script>(function(){'
        "var f=document.getElementById('np-signin-form'); if(f){f.reset();}"
        "var fb=document.getElementById('np-si-feedback'); if(fb){fb.innerHTML='';}"
        "var d=document.getElementById('np-signin-dialog'); if(d){d.close();}"
        "})();</script>"
    )
    return html(close_script, toast={"message": f"Signed in to {name}", "type": "success"}, refresh=True)


@router.post("/ui/providers/signout")
async def sign_out(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    pid = _to_int(form.get("id"))
    if pid is None:
        return _toast(message="Invalid provider id", ttype="error")
    name = await _provider_name(pid)
    try:
        await api.post(f"/api/admin/providers/{pid}/signout")
    except ApiError as e:
        return _toast(message=e.message, ttype="error")
    except Exception as e:  # noqa: BLE001
        return _toast(message=f"Sign-out failed ({e.__class__.__name__})", ttype="error")
    return _toast(message=f"Signed out of {name}")


@router.post("/ui/providers/delete")
async def delete_provider(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    pid = _to_int(form.get("id"))
    if pid is None:
        return _toast(message="Invalid provider id", ttype="error")
    name = await _provider_name(pid)
    try:
        await api.delete(f"/api/admin/providers/{pid}")
    except ApiError as e:
        # e.g. "The built-in NovaFree engine cannot be deleted" — surfaced verbatim.
        return _toast(message=e.message, ttype="error")
    except Exception as e:  # noqa: BLE001
        return _toast(message=f"Failed to delete provider ({e.__class__.__name__})", ttype="error")
    _test_results.pop(pid, None)
    return _toast(message=f"{name} removed")


@router.post("/ui/providers/keys/add")
async def keys_add(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    pid = _to_int(form.get("provider_id"))
    raw = _form_str(form, "api_key")
    label = _form_str(form, "label")
    if pid is None:
        return _toast(message="Invalid provider id", ttype="error")
    if not raw:
        # Keep the open keys panel intact (empty-body swaps would wipe it).
        return await _keys_panel_response(pid, "Paste at least one API key", "error", refresh=False)

    provider = await _fetch_provider(pid)
    name = str(provider.get("name")) if provider else "provider"

    try:
        if re.search(r"[\n,]", raw):
            res = await api.post("/api/admin/keys/bulk", json={
                "provider_id": pid, "keys": raw, "label_prefix": label or "imported",
            })
            message = f"{res.get('added', 0)} key(s) added to {name}"
        else:
            await api.post("/api/admin/keys", json={
                "provider_id": pid, "api_key": raw, "label": label or "manual",
            })
            message = f"Key added to {name}"
    except ApiError as e:
        return await _keys_panel_response(pid, e.message, "error", refresh=False)
    except Exception as e:  # noqa: BLE001
        return await _keys_panel_response(pid, f"Failed to add key ({e.__class__.__name__})", "error", refresh=False)

    if provider is None:
        # Provider row unreadable — let the root refresh restore the panel instead of
        # swapping in a fragment with a broken panel id.
        return _toast(message=message)
    keys = await _fetch_keys(pid)
    frag = render("partials/providers_keys.html", p=provider, keys=keys,
                  now_ms=time.time() * 1000, q="", expand=pid)
    return html(frag, toast={"message": message, "type": "success"}, refresh=True)


@router.post("/ui/providers/keys/delete")
async def keys_delete(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    kid = _to_int(form.get("id"))
    pid = _to_int(form.get("provider_id"))
    if kid is None:
        return await _keys_panel_response(pid, "Invalid key id", "error", refresh=False)
    try:
        await api.delete(f"/api/admin/keys/{kid}")
    except ApiError as e:
        return await _keys_panel_response(pid, e.message, "error", refresh=False)
    except Exception as e:  # noqa: BLE001
        return await _keys_panel_response(pid, f"Failed to delete key ({e.__class__.__name__})", "error", refresh=False)

    if pid is None:
        return _toast(message="Key deleted")
    provider = await _fetch_provider(pid)
    if provider is None:
        return _toast(message="Key deleted")
    keys = await _fetch_keys(pid)
    frag = render("partials/providers_keys.html", p=provider, keys=keys,
                  now_ms=time.time() * 1000, q="", expand=pid)
    return html(frag, toast={"message": "Key deleted", "type": "success"}, refresh=True)
