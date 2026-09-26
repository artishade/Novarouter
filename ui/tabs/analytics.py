"""Analytics tab — Python UI port of src/components/nova/tabs/AnalyticsTab.tsx.

Blueprint sections (verbatim parity):
  • Header — summary chips (requests / tokens / errors / spoofed fallbacks /
    live avg latency), gateway version, "sync" button, hours range selector
    (24 / 168 / 720). group_by is derived exactly like the TSX:
    hours >= 720 → 'day' else 'hour'.
  • Requests & Errors — zero-filled buckets rendered as an SVG/CSS bar chart
    (emerald = requests, rose = errors; native tooltips; no chart libraries).
  • Top Models (vertical bars, tokens in + out) + Top Providers (horizontal
    bars, requests routed).
  • Latency Percentiles (p50/p90/p99 nearest-rank) · Status Distribution donut
    (2xx vs errors, SVG) · Cache Hit Rate (big % + progress bar).

Endpoint:
  GET /partials/tab/analytics — re-fetched with ?hours= by the selector / sync
  button (hx-target="#tab-analytics"). Not live-polled, matching the TSX which
  only loads analytics on mount / range change.
"""
from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ui.api_client import api
from ui.render import html, render

router = APIRouter(tags=["ui:analytics"])

RANGES: list[tuple[int, str]] = [(24, "Last 24 hours"), (168, "Last 7 days"), (720, "Last 30 days")]
DONUT_CIRCUMFERENCE = 301.59  # 2πr, r = 48 (viewBox 120)


def _js_round(v: Any) -> int:
    try:
        return int(math.floor(float(v) + 0.5))
    except (TypeError, ValueError):
        return 0


def _short_model(m: str) -> str:
    """TSX shortModel parity: strip the first '/' prefix segment, cap at 16 chars."""
    tail = m.split("/", 1)[1] if "/" in m else m
    return tail if len(tail) <= 16 else tail[:15] + "…"


def _pct(part: float, whole: float) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _series(timeseries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bucket rows for the grouped bar chart (shared Y scale like the TSX area chart)."""
    n = len(timeseries)
    max_reqs = max((int(b.get("reqs") or 0) for b in timeseries), default=0)
    step = max(1, math.ceil(n / 8))  # thin x labels like the recharts axis
    rows: list[dict[str, Any]] = []
    for i, b in enumerate(timeseries):
        reqs = int(b.get("reqs") or 0)
        errors = int(b.get("errors") or 0)
        rows.append(
            {
                "bucket": str(b.get("bucket") or ""),
                "reqs": reqs,
                "errors": errors,
                "req_pct": _pct(reqs, max_reqs),
                "err_pct": _pct(errors, max_reqs),
                "show_label": i % step == 0 or i == n - 1,
            }
        )
    return rows


def _vertical_bars(entries: list[dict[str, Any]], value_key: str, label_key: str, limit: int) -> list[dict[str, Any]]:
    sliced = entries[:limit]
    peak = max((int(e.get(value_key) or 0) for e in sliced), default=0)
    return [
        {
            "label": str(e.get(label_key) or ""),
            "short": _short_model(str(e.get(label_key) or "")) if label_key == "model" else str(e.get(label_key) or ""),
            "value": int(e.get(value_key) or 0),
            "pct": _pct(int(e.get(value_key) or 0), peak),
        }
        for e in sliced
    ]


def _horizontal_bars(entries: list[dict[str, Any]], value_key: str, label_key: str, limit: int) -> list[dict[str, Any]]:
    sliced = entries[:limit]
    peak = max((int(e.get(value_key) or 0) for e in sliced), default=0)
    return [
        {
            "label": str(e.get(label_key) or ""),
            "value": int(e.get(value_key) or 0),
            "pct": _pct(int(e.get(value_key) or 0), peak),
        }
        for e in sliced
    ]


@router.get("/partials/tab/analytics")
async def analytics_tab(request: Request) -> HTMLResponse:
    raw = request.query_params.get("hours")
    hours = int(raw) if raw and raw.isdigit() and int(raw) in (h for h, _ in RANGES) else 24
    group_by = "day" if hours >= 720 else "hour"

    data: Any = None
    err_message = "Gateway API unreachable"
    try:
        data = await api.get("/api/admin/analytics", params={"hours": hours, "group_by": group_by})
    except Exception as err:  # ApiError or transport failure
        err_message = getattr(err, "message", None) or "Gateway API unreachable"
    if not isinstance(data, dict):
        return html(render("partials/analytics_error.html", message=err_message, hours=hours))

    # Shared gateway stats (live avg chip) + meta (version) — optional, TSX parity.
    stats: Any = None
    meta: Any = None
    try:
        stats = await api.get("/api/admin/stats")
    except Exception:
        stats = None
    try:
        meta = await api.get("/api/admin/meta")
    except Exception:
        meta = None

    summary: dict[str, Any] = data.get("summary") or {}
    total = int(summary.get("total_requests") or 0)
    error_count = int(summary.get("error_count") or 0)
    success_count = max(0, total - error_count)

    # Status donut geometry (emerald success / rose errors on a slate track).
    frac = (success_count / total) if total > 0 else 0.0
    seg = round(frac * DONUT_CIRCUMFERENCE, 2)
    donut = {
        "total": total,
        "success": success_count,
        "errors": error_count,
        "seg": f"{seg:.2f}",
        "rest": f"{DONUT_CIRCUMFERENCE - seg:.2f}",
        "offset": f"-{seg:.2f}",
    }

    cache_rate = round((int(summary.get("cache_hits") or 0) / total) * 100, 1) if total > 0 else 0.0

    percentiles = [
        {"label": "p50 latency", "value": f"{_js_round(summary.get('p50_latency_ms'))}ms", "dot": "bg-emerald-400"},
        {"label": "p90 latency", "value": f"{_js_round(summary.get('p90_latency_ms'))}ms", "dot": "bg-amber-400"},
        {"label": "p99 latency", "value": f"{_js_round(summary.get('p99_latency_ms'))}ms", "dot": "bg-rose-400"},
    ]

    return html(
        render(
            "tabs/analytics.html",
            hours=hours,
            group_by=group_by,
            ranges=RANGES,
            summary=summary,
            total=total,
            error_rate_text=f"{float(summary.get('error_rate') or 0):.2f}",
            stats_avg=(f"{_js_round(stats.get('avg_latency_ms'))}ms" if isinstance(stats, dict) else None),
            meta_version=(str(meta.get("version")) if isinstance(meta, dict) else None),
            series=_series(data.get("timeseries") or []),
            top_models=_vertical_bars(data.get("top_models") or [], "tokens", "model", 6),
            top_providers=_horizontal_bars(data.get("top_providers") or [], "reqs", "provider", 7),
            percentiles=percentiles,
            donut=donut,
            cache_rate=f"{cache_rate:.1f}",
            cache_width=min(100, cache_rate),
        )
    )
