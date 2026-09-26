"""Overview tab — Python UI port of src/components/nova/tabs/OverviewTab.tsx.

Blueprint sections (verbatim parity):
  (a) KPI grid — Total Requests 24h / Tokens Processed / Avg Latency / Error Rate,
      each with a delta chip vs the previous poll (TSX keeps prev poll in state;
      here the previous snapshot is remembered process-side for ~15s, which
      mirrors the TSX remount/poll cadence).
  (b) NovaFree engine banner — badges + "Try in Console" navigation.
  (c) Provider Health — top 6 providers (color dot, ok/total models, keys,
      cooling + signed-in badges, disabled enabled-switch).
  (d) Recent Activity — last 8 request logs with status badges.
  (e) Quick actions — Clear Cooldowns (mutation), Run Health Check (toast),
      Open Console (navigation).

Endpoints:
  GET  /partials/tab/overview          — live fragment (data-live, nova:refresh)
  POST /ui/overview/clear-cooldowns    — forwards to /api/admin/keys/clear-cooldowns

Note: the JSON/terminal/GPU panels are NOT part of OverviewTab.tsx — they live in
ConsoleTab.tsx (ui/tabs/console.py scope).
"""
from __future__ import annotations

import math
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from ui.api_client import api
from ui.format import fmt_num, time_ago
from ui.render import html, render

router = APIRouter(tags=["ui:overview"])

# TSX polls stats every 5s and keeps the previous snapshot for the KPI delta
# chips. Server-side parity: remember the last rendered snapshot; treat it as
# stale (delta-less, like a fresh TSX mount) after 15s.
_prev_stats: dict[str, Any] | None = None
_prev_at: float = 0.0

# Overview variant of the TSX statusBadgeClass (Logs has an extra 3xx tier).
_STATUS_CLASS = (
    lambda s: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
    if 200 <= s < 300
    else "border-amber-500/30 bg-amber-500/10 text-amber-300"
    if s < 500
    else "border-rose-500/30 bg-rose-500/10 text-rose-300"
)


def _js_round(v: Any) -> int:
    """Math.round parity."""
    try:
        return int(math.floor(float(v) + 0.5))
    except (TypeError, ValueError):
        return 0


def _delta(
    now: Any, before: Any, *, lower_is_better: bool = False, unit: str = ""
) -> dict[str, Any] | None:
    """DeltaChip parity: None → no chip; d == 0 → no chip."""
    if before is None or now is None:
        return None
    try:
        d = float(now) - float(before)
    except (TypeError, ValueError):
        return None
    if d == 0:
        return None
    good = d < 0 if lower_is_better else d > 0
    sign = "+" if d > 0 else "-"
    return {"good": good, "up": d > 0, "text": f"{sign}{fmt_num(abs(d))}{unit}"}


def _kpi_cards(stats: dict[str, Any], prev: dict[str, Any] | None) -> list[dict[str, Any]]:
    def before(key: str) -> Any:
        return prev.get(key) if prev else None

    return [
        {
            "icon": "activity",
            "label": "Total Requests 24h",
            "value": fmt_num(stats.get("total_requests")),
            "sub": f"{fmt_num(stats.get('total_tokens_in'))} in / {fmt_num(stats.get('total_tokens_out'))} out",
            "delta": _delta(stats.get("total_requests"), before("total_requests")),
        },
        {
            "icon": "coins",
            "label": "Tokens Processed",
            "value": fmt_num(stats.get("total_tokens")),
            "sub": "prompt + completion tokens",
            "delta": _delta(stats.get("total_tokens"), before("total_tokens")),
        },
        {
            "icon": "timer",
            "label": "Avg Latency",
            "value": f"{_js_round(stats.get('avg_latency_ms'))}ms",
            "sub": "end-to-end, last 24h",
            "delta": _delta(
                stats.get("avg_latency_ms"),
                before("avg_latency_ms"),
                lower_is_better=True,
                unit="ms",
            ),
        },
        {
            "icon": "shield-alert",
            "label": "Error Rate",
            "value": f"{float(stats.get('error_rate') or 0):.2f}%",
            "sub": f"{fmt_num(stats.get('dead_models'))} dead models",
            "delta": _delta(
                stats.get("error_rate"),
                before("error_rate"),
                lower_is_better=True,
                unit="%",
            ),
        },
    ]


def _recent_view(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recent Activity rows — status badge, model, latency·via, time-ago."""
    return [
        {
            "id": l.get("id"),
            "status": l.get("status"),
            "status_class": _STATUS_CLASS(int(l.get("status") or 0)),
            "model": l.get("model") or "—",
            "lat_via": f"{l.get('latency_ms')}ms · {l.get('via')}",
            "ago": time_ago(l.get("ts")),
        }
        for l in logs
    ]


@router.get("/partials/tab/overview")
async def overview_tab() -> HTMLResponse:
    global _prev_stats, _prev_at

    # Primary dataset — a failure here renders the retryable error panel.
    stats: Any = None
    err_message = "Gateway API unreachable"
    try:
        stats = await api.get("/api/admin/stats")
    except Exception as err:  # ApiError or transport failure
        err_message = getattr(err, "message", None) or "Gateway API unreachable"
    if not isinstance(stats, dict):
        return html(render("partials/overview_error.html", message=err_message))

    # Secondary datasets — degraded individually (TSX Promise.allSettled parity).
    meta: Any = None
    providers: Any = None
    recent: Any = None
    try:
        meta = await api.get("/api/admin/meta")
    except Exception:
        meta = None
    try:
        providers = await api.get("/api/admin/providers")
    except Exception:
        providers = None
    try:
        logs = await api.get("/api/admin/logs", params={"limit": 50})
        recent = logs[:8] if isinstance(logs, list) else None
    except Exception:
        recent = None

    prev = _prev_stats if (time.monotonic() - _prev_at) < 15 else None
    _prev_stats = stats
    _prev_at = time.monotonic()

    return html(
        render(
            "tabs/overview.html",
            kpis=_kpi_cards(stats, prev),
            meta=meta if isinstance(meta, dict) else None,
            providers=providers if isinstance(providers, list) else None,
            recent=_recent_view(recent) if isinstance(recent, list) else None,
        )
    )


@router.post("/ui/overview/clear-cooldowns")
async def clear_cooldowns() -> HTMLResponse:
    """Quick action: clear cooldown state on all upstream keys (TSX clearCooldowns)."""
    try:
        result = await api.post("/api/admin/keys/clear-cooldowns")
        cleared = int((result or {}).get("cleared", 0))
        message = f"Cooldowns cleared on {cleared} key{'' if cleared == 1 else 's'}"
        return html("", toast={"message": message, "type": "success"}, refresh=True)
    except Exception as err:
        message = getattr(err, "message", None) or "Failed to clear cooldowns"
        return html("", toast={"message": message, "type": "error"})
