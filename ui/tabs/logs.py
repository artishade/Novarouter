"""Logs tab — Python UI port of src/components/nova/tabs/LogsTab.tsx.

Blueprint behaviour (verbatim parity):
  • Toolbar — search input (model/provider/error/upstream_model haystack),
    status class filter (all/2xx/4xx/5xx), via filter (all/upstream/nova-engine/
    fallback/cache), "N of M requests" counter, Refresh button (spin icon).
    Filters are server-side over the fetched window (TSX filters client-side
    over the same /api/admin/logs?limit=100 payload).
  • Table — Time (clock + time-ago tooltip), Endpoint (method chip + path),
    Model, Provider, Via (+ identity-spoofed badge), Status badge (emerald /
    teal 3xx / amber / rose), Latency (rose ≥2000ms, amber ≥800ms), Tokens
    (in / out), expand chevron with a collapsible detail row (error box +
    upstream/client/ts/log-id/via meta). Expansion survives live re-swaps via
    window.novaLogs.
  • Live: root carries data-live + nova:refresh re-fetch with the active
    filters baked into the URL; live swaps are skipped while the search box is
    focused so typing is never clobbered.

Endpoint:
  GET /partials/tab/logs(?q=&status=&via=)
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ui.api_client import api
from ui.format import fmt_clock, fmt_date, fmt_num, time_ago
from ui.render import html, render

router = APIRouter(tags=["ui:logs"])

STATUS_CHOICES = ("all", "2xx", "4xx", "5xx")
VIA_CHOICES = ("all", "upstream", "nova-engine", "fallback", "cache")
METHOD_RE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE)\s+")

# TSX STATUS_CLASS — includes the teal 3xx tier (Overview uses a 2-variant one).
def _status_class(status: int) -> str:
    if 200 <= status < 300:
        return "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
    if status < 400:
        return "border-teal-500/30 bg-teal-500/10 text-teal-300"
    if status < 500:
        return "border-amber-500/30 bg-amber-500/10 text-amber-300"
    return "border-rose-500/30 bg-rose-500/10 text-rose-300"


# TSX VIA_CLASS
VIA_CLASS = {
    "upstream": "border-sky-500/30 bg-sky-500/10 text-sky-300",
    "nova-engine": "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
    "fallback": "border-amber-500/30 bg-amber-500/10 text-amber-300",
    "cache": "border-teal-500/30 bg-teal-500/10 text-teal-300",
}
_VIA_DEFAULT = "border-slate-700 bg-slate-800/40 text-slate-400"


def _latency_class(ms: Any) -> str:
    try:
        ms = float(ms)
    except (TypeError, ValueError):
        return "text-slate-300"
    if ms >= 2000:
        return "text-rose-400"
    if ms >= 800:
        return "text-amber-300"
    return "text-slate-300"


def _matches(log: dict[str, Any], status: str, via: str, q: str) -> bool:
    s = int(log.get("status") or 0)
    if status == "2xx" and not 200 <= s < 300:
        return False
    if status == "4xx" and not 400 <= s < 500:
        return False
    if status == "5xx" and s < 500:
        return False
    if via != "all" and log.get("via") != via:
        return False
    if q:
        hay = " ".join(
            str(log.get(key) or "")
            for key in ("model", "provider_name", "error", "upstream_model")
        ).lower()
        if q.lower() not in hay:
            return False
    return True


def _row_view(log: dict[str, Any]) -> dict[str, Any]:
    endpoint = str(log.get("endpoint") or "")
    match = METHOD_RE.match(endpoint)
    method = match.group(1) if match else None
    path = endpoint[match.end():] if match else endpoint
    return {
        "id": log.get("id"),
        "clock": fmt_clock(log.get("ts")),
        "ago": time_ago(log.get("ts")),
        "method": method,
        "path": path,
        "model": log.get("model") or "—",
        "provider_name": log.get("provider_name") or "—",
        "via": log.get("via") or "—",
        "via_class": VIA_CLASS.get(log.get("via"), _VIA_DEFAULT),
        "spoofed": bool(log.get("spoofed")),
        "status": log.get("status"),
        "status_class": _status_class(int(log.get("status") or 0)),
        "latency_ms": log.get("latency_ms"),
        "latency_class": _latency_class(log.get("latency_ms")),
        "tokens": f"{fmt_num(log.get('tokens_in'))} / {fmt_num(log.get('tokens_out'))}",
        "error": log.get("error") or "",
        "upstream_model": log.get("upstream_model") or "",
        "client_name": log.get("client_name") or "",
        "date": fmt_date(log.get("ts")),
    }


@router.get("/partials/tab/logs")
async def logs_tab(request: Request) -> HTMLResponse:
    qp = request.query_params
    q = (qp.get("q") or "").strip()
    status = qp.get("status") if qp.get("status") in STATUS_CHOICES else "all"
    via = qp.get("via") if qp.get("via") in VIA_CHOICES else "all"

    logs: Any = None
    err_message = "Gateway API unreachable"
    try:
        logs = await api.get("/api/admin/logs", params={"limit": 100})
    except Exception as err:  # ApiError or transport failure
        err_message = getattr(err, "message", None) or "Gateway API unreachable"
    if not isinstance(logs, list):
        return html(render("partials/logs_error.html", message=err_message))

    filtered = [l for l in logs if _matches(l, status, via, q)]
    rows = [_row_view(l) for l in filtered]

    # region=results renders ONLY the results region (#logs-lower) so filter
    # actions never re-render the toolbar / focused search input (mobile
    # keyboard safety).
    if qp.get("region") == "results":
        return html(render("partials/logs_results.html", rows=rows, total=len(logs)))

    return html(
        render(
            "tabs/logs.html",
            rows=rows,
            total=len(logs),
            q=q,
            status=status,
            via=via,
        )
    )
