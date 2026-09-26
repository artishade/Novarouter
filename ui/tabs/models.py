"""Models tab — Python UI port of src/components/nova/tabs/ModelsTab.tsx.

Fragments:
  GET /partials/tab/models      full tab (list root + detail dialog slot)
  GET /partials/models/list     list root only (live-refresh + filter target)
  GET /partials/models/detail   one model's detail <dialog> (opened per card)

Actions (HTMX mutations → html(..., toast=..., refresh=True)):
  POST /ui/models/toggle        enabled switch (PATCH); context=dialog swaps the
                                dialog's own switch in place
  POST /ui/models/ping          real health probe (JSON — consumed by the inline
                                Ping / Health check JS sweep)
  POST /ui/models/sync          live catalogue sync (all or one provider)
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ui.api_client import ApiError, api
from ui.render import form_dict, html, render, truthy

router = APIRouter(tags=["ui:models"])

MODEL_STATUSES = ("all", "healthy", "cooling", "dead", "unknown")
CAPABILITIES = ("all", "tools", "vision", "reasoning")

STATUS_STYLES: dict[str, dict[str, str]] = {
    "healthy": {"dot": "bg-emerald-400", "chip": "border-emerald-900/70 bg-emerald-950/40 text-emerald-300"},
    "cooling": {"dot": "bg-amber-400", "chip": "border-amber-900/70 bg-amber-950/40 text-amber-300"},
    "dead": {"dot": "bg-rose-400", "chip": "border-rose-900/70 bg-rose-950/40 text-rose-300"},
    "unknown": {"dot": "bg-slate-500", "chip": "border-slate-700 bg-slate-900/60 text-slate-400"},
}

# Sequential real probes take seconds each — the Health check sweep caps at this
# many candidates (the TSX swept the whole visible list; noted as a deviation).
HEALTH_CHECK_CAP = 24

# Ping results (mirrors the TSX per-row state updates): model id → last probe.
_ping_results: dict[int, dict] = {}


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


def _toast(message: str, ttype: str = "success") -> HTMLResponse:
    return html("", toast={"message": message, "type": ttype}, refresh=True)


def _parse_filters(sp: Any) -> dict:
    q = (sp.get("q") or "").strip()
    provider_id = (sp.get("provider_id") or "all").strip()
    if provider_id != "all":
        n = _to_int(provider_id)
        provider_id = "all" if n is None or n <= 0 else str(n)
    status = (sp.get("status") or "all").strip()
    if status not in MODEL_STATUSES:
        status = "all"
    capability = (sp.get("capability") or "all").strip()
    if capability not in CAPABILITIES:
        capability = "all"
    free = truthy(sp.get("free"))
    return {"q": q, "provider_id": provider_id, "status": status, "capability": capability, "free": free}


def _api_params(f: dict) -> dict:
    params: dict[str, Any] = {}
    if f["q"]:
        params["search"] = f["q"]
    if f["provider_id"] != "all":
        params["provider_id"] = int(f["provider_id"])
    if f["status"] != "all":
        params["status"] = f["status"]
    if f["capability"] != "all":
        params["capability"] = f["capability"]
    if f["free"]:
        params["free"] = "true"
    return params


def _decorate(rows: list[dict]) -> list[dict]:
    """Status chip classes + cached ping overrides (mirrors TSX per-row updates)."""
    for r in rows:
        st = str(r.get("status") or "unknown")
        ping = _ping_results.get(r.get("id"))
        if ping:
            r["status"] = ping.get("status") or st
            r["latency_ms"] = ping.get("latency_ms", r.get("latency_ms"))
            r["http_status"] = ping.get("http_status", r.get("http_status"))
            r["detail"] = ping.get("detail") or r.get("detail")
            r["checked_at"] = int(time.time() * 1000)
            st = str(r.get("status") or "unknown")
        style = STATUS_STYLES.get(st, STATUS_STYLES["unknown"])
        r["chip"] = style["chip"]
        r["dot"] = style["dot"]
        r["capabilities"] = r.get("capabilities") or {}
    return rows


async def _models_ctx(request: Request) -> dict:
    """List context shared by the tab fragment and the live-refresh partial."""
    f = _parse_filters(request.query_params)

    rows: list[dict] = []
    total = 0
    error: str | None = None
    try:
        res = await api.get("/api/admin/models", params=_api_params(f))
        total = res.get("total", 0)
        rows = _decorate(res.get("rows", []) or [])
    except ApiError as e:
        error = e.message
    except Exception as e:  # noqa: BLE001
        error = f"Failed to load models ({e.__class__.__name__})"

    providers: list[dict] = []
    try:
        providers = await api.get("/api/admin/providers")
    except Exception:  # noqa: BLE001 — filter select just loses options
        providers = []

    stats = None
    try:
        stats = await api.get("/api/admin/stats")
    except Exception:  # noqa: BLE001
        stats = None

    candidates = [m for m in rows if m.get("enabled") and m.get("status") != "dead"]
    health_ids = [m["id"] for m in candidates[:HEALTH_CHECK_CAP]]

    active_filters = int(f["provider_id"] != "all") + int(f["status"] != "all") + \
        int(f["capability"] != "all") + int(f["free"])
    has_filters = active_filters > 0 or bool(f["q"])

    return {
        "rows": rows,
        "total": total,
        "error": error,
        "providers": providers,
        "stats": stats,
        "q": f["q"],
        "provider_id": f["provider_id"],
        "status": f["status"],
        "capability": f["capability"],
        "free": f["free"],
        "active_filters": active_filters,
        "has_filters": has_filters,
        "health_ids": health_ids,
        "health_candidates": len(candidates),
    }


async def _meta_base_url() -> str:
    try:
        meta = await api.get("/api/admin/meta")
        return str(meta.get("base_url") or "/v1")
    except Exception:  # noqa: BLE001
        return "/v1"


# --------------------------------------------------------------------------- #
# Fragments
# --------------------------------------------------------------------------- #

@router.get("/partials/tab/models")
async def models_tab(request: Request) -> HTMLResponse:
    ctx = await _models_ctx(request)
    return html(render("tabs/models.html", **ctx))


@router.get("/partials/models/list")
async def models_list(request: Request) -> HTMLResponse:
    ctx = await _models_ctx(request)
    # region=results renders ONLY the results region (#models-results) so
    # filter actions never re-render the focused search input (mobile
    # keyboard safety).
    if request.query_params.get("region") == "results":
        return html(render("partials/models_results.html", **ctx))
    return html(render("partials/models_list.html", **ctx))


@router.get("/partials/models/detail")
async def models_detail(request: Request) -> HTMLResponse:
    mid = _to_int(request.query_params.get("id") or "")
    if mid is None:
        return html(render("partials/models_error.html", message="Invalid model id — refresh the tab and try again."))
    model: dict | None = None
    try:
        res = await api.get("/api/admin/models")
        rows = _decorate(res.get("rows", []) or [])
        model = next((m for m in rows if m.get("id") == mid), None)
    except Exception:  # noqa: BLE001
        model = None
    if model is None:
        return html(render(
            "partials/models_error.html",
            message="Model not found — the catalogue may have changed. Refresh the tab and try again.",
        ))

    base_url = await _meta_base_url()
    curl = (
        f"curl {base_url}/chat/completions \\\n"
        f"  -H \"Authorization: Bearer $NOVA_API_KEY\" \\\n"
        f"  -H \"Content-Type: application/json\" \\\n"
        f"  -d '{{\n"
        f"    \"model\": \"{model.get('exposed_id', '')}\",\n"
        f"    \"messages\": [{{ \"role\": \"user\", \"content\": \"Hello, NovaRouter\" }}]\n"
        f"  }}'"
    )
    return html(render(
        "partials/models_detail.html",
        m=model,
        curl=curl,
        base_url=base_url,
        mid=model["id"],
        exposed=model.get("exposed_id") or "",
        enabled=bool(model.get("enabled")),
    ))


# --------------------------------------------------------------------------- #
# Mutations
# --------------------------------------------------------------------------- #

@router.post("/ui/models/toggle")
async def toggle_model(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    mid = _to_int(form.get("id"))
    enabled = truthy(form.get("enabled"))
    exposed = _form_str(form, "exposed") or "model"
    if mid is None:
        return _toast("Invalid model id", "error")
    try:
        await api.patch(f"/api/admin/models/{mid}", json={"enabled": enabled})
    except ApiError as e:
        return _toast(e.message, "error")
    except Exception as e:  # noqa: BLE001
        return _toast(f"Failed to update model ({e.__class__.__name__})", "error")

    message = f"{exposed} {'enabled' if enabled else 'disabled'}"
    if _form_str(form, "context") == "dialog":
        # Keep the open detail dialog consistent: swap its own switch in place.
        frag = render("partials/models_toggle.html", mid=mid, exposed=exposed, enabled=enabled)
        return html(frag, toast={"message": message, "type": "success"}, refresh=True)
    return _toast(message)


@router.post("/ui/models/ping")
async def ping_model(request: Request) -> JSONResponse:
    """Real health probe — consumed by the inline Ping / Health check JS."""
    mid = _to_int(request.query_params.get("id") or "")
    if mid is None:
        return JSONResponse({"ok": False, "error": "Invalid model id"})
    try:
        res = await api.post(f"/api/admin/models/{mid}/ping")
    except ApiError as e:
        return JSONResponse({"ok": False, "error": e.message})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"Ping failed ({e.__class__.__name__})"})
    _ping_results[mid] = res
    return JSONResponse(res)


@router.post("/ui/models/disable-unreachable")
async def disable_unreachable_models(request: Request) -> HTMLResponse:
    """Bulk-disable every model whose last health check marked it dead."""
    try:
        res = await api.post("/api/admin/models/disable-unreachable")
    except ApiError as e:
        return _toast(e.message, "error")
    except Exception as e:  # noqa: BLE001
        return _toast(f"Failed to disable unreachable models ({e.__class__.__name__})", "error")

    disabled = int(res.get("disabled") or 0)
    if disabled == 0:
        return _toast("No unreachable models — nothing to disable", "info")
    names = ", ".join(str(x) for x in (res.get("exposed_ids") or [])[:6])
    more = f" …+{disabled - 6}" if disabled > 6 else ""
    return _toast(
        f"Disabled {disabled} unreachable model(s): {names}{more} — the gateway and agent will skip them"
    )


@router.post("/ui/models/sync")
async def sync_models(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    pid = _to_int(form.get("provider_id"))
    try:
        res = await api.post(
            "/api/admin/models/sync", params={"provider_id": pid} if pid else None
        )
    except ApiError as e:
        return _toast(e.message, "error")
    except Exception as e:  # noqa: BLE001
        return _toast(f"Catalogue sync failed ({e.__class__.__name__})", "error")

    synced = res.get("synced", 0)
    new_added = res.get("new_added", 0)
    updated = res.get("updated") or 0
    errors = res.get("errors") or []
    updated_txt = f", {updated} refreshed" if updated else ""
    if errors:
        message = (
            f"Synced {synced} models ({new_added} new{updated_txt}) · {len(errors)} provider(s) failed — "
            + " · ".join(str(x) for x in errors[:3])
        )
        ttype = "info"
    else:
        message = f"Live catalogue synced — {synced} models discovered, {new_added} new{updated_txt}"
        ttype = "success"
    return _toast(message, ttype)
