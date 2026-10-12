"""Shared helpers for the admin CRUD routers (ported from the TS route files)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

_INT_RE = re.compile(r"^[+-]?\d+$")


def epoch_ms(dt: datetime | None) -> int | None:
    """Prisma stored naive UTC datetimes — serialize as epoch MILLISECONDS."""
    if dt is None:
        return None
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def parse_int(raw: str | None) -> int | None:
    """Safe id parse — None when not a plain integer (TS `Number.isInteger`)."""
    if raw is None or not _INT_RE.match(raw.strip()):
        return None
    return int(raw.strip())


def mask_key(key: str) -> str:
    """first 8 + '…' + last 4 (contract's key mask format)."""
    if len(key) <= 12:
        return f"{key[:4]}…"
    return f"{key[:8]}…{key[-4:]}"


async def json_body(req: Request) -> dict:
    """Tolerant body parse — invalid/empty bodies become {} (TS parity)."""
    try:
        parsed = await req.json()
    except Exception:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def as_str(v: Any) -> str | None:
    """TS `typeof v === 'string' && v.trim() !== '' ? v.trim() : undefined`."""
    if isinstance(v, str) and v.strip() != "":
        return v.strip()
    return None


def as_num(v: Any) -> float | None:
    """TS `Number.isFinite(typeof v === 'number' ? v : Number(v)) ? n : undefined`."""
    if isinstance(v, bool):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n == n and abs(n) != float("inf") else None


def invalid_id(error: str) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=400)


def not_found(error: str) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=404)


def parse_caps(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_fallbacks(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if isinstance(parsed, list):
        return [v for v in parsed if isinstance(v, str)]
    return []
