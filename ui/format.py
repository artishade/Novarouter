"""Formatting helpers for the Python UI — port of src/lib/format.ts.

These are exposed to Jinja as filters and used by tab modules when they
pre-format values for templates. Timestamps everywhere are epoch milliseconds
(the JSON API wire format).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any


def fmt_num(n: Any) -> str:
    """1.2M / 45.3k / 7 — TS fmtNum parity."""
    if n is None:
        return "—"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(round(n))


def fmt_bytes(bytes_value: Any) -> str:
    if not bytes_value:
        return "0 B"
    try:
        value = float(bytes_value)
    except (TypeError, ValueError):
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while value >= 1024 and i < len(units) - 1:
        value /= 1024
        i += 1
    return f"{value:.0f} {units[i]}" if i == 0 else f"{value:.1f} {units[i]}"


def fmt_mb(mb: Any) -> str:
    try:
        mb = float(mb)
    except (TypeError, ValueError):
        return "—"
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb:.1f} MB" if mb < 10 else f"{mb:.0f} MB"


def fmt_uptime(seconds: Any) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "—"
    d, rem = divmod(max(seconds, 0), 86400)
    hh, rem = divmod(rem, 3600)
    mm, _ = divmod(rem, 60)
    if d > 0:
        return f"{d}d {hh}h {mm}m"
    if hh > 0:
        return f"{hh}h {mm}m"
    return f"{mm}m"


def time_ago(ts: Any) -> str:
    """ts is epoch-ms — TS timeAgo parity ('never' for falsy)."""
    if not ts:
        return "never"
    try:
        diff = time.time() * 1000 - float(ts)
    except (TypeError, ValueError):
        return "never"
    if diff < 0:
        return "just now"
    s = int(diff // 1000)
    if s < 60:
        return f"{s}s ago"
    m = s // 60
    if m < 60:
        return f"{m}m ago"
    hh = m // 60
    if hh < 24:
        return f"{hh}h ago"
    d = hh // 24
    return f"{d}d ago"


def fmt_clock(ts: Any) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(float(ts) / 1000, tz=timezone.utc).strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "—"


def fmt_date(ts: Any) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(float(ts) / 1000, tz=timezone.utc).strftime("%b %d, %H:%M")
    except (TypeError, ValueError, OSError):
        return "—"


def mask_key(key: Any) -> str:
    if not key:
        return "—"
    key = str(key)
    if len(key) <= 12:
        return key[:4] + "••••"
    return key[:8] + "••••••••" + key[-4:]


def cx(*parts: Any) -> str:
    """Tailwind class joiner — TS cx parity."""
    return " ".join(p for p in parts if p)
