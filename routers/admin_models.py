"""Admin model catalogue — list, toggle, health ping and live sync.

Python port of:
  src/app/api/admin/models/route.ts           (GET with filters)
  src/app/api/admin/models/[id]/route.ts      (PATCH toggle)
  src/app/api/admin/models/[id]/ping/route.ts (POST health check — real probe)
  src/app/api/admin/models/sync/route.ts      (POST live catalogue sync)

Mounted at /api/admin/models by main.py.
"""
from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from nova import engine as nova_engine
from nova.database import SessionLocal, get_db
from nova.models import Model as ModelRow, ProviderKey, utcnow
from nova.syncengine import sync_providers

from ._common import epoch_ms, invalid_id, not_found, parse_caps, parse_int

router = APIRouter()

PING_TIMEOUT_S = 6.0

MODEL_STATUSES = ("healthy", "cooling", "dead", "unknown")
CAPABILITIES = ("tools", "vision", "reasoning")


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #

def status_detail(status: str, http_status: int) -> str:
    """Human-readable check detail synthesized from status + last HTTP code."""
    if status == "healthy":
        return f"HTTP {http_status} — upstream OK" if http_status > 0 else "Healthy"
    if status == "cooling":
        return f"HTTP {http_status} — cooling down" if http_status > 0 else "Cooling down"
    if status == "dead":
        return f"HTTP {http_status} — upstream rejected" if http_status > 0 else "Unreachable / offline"
    return f"Last check: HTTP {http_status}" if http_status > 0 else "Not checked yet"


def to_model(m: ModelRow, provider_name: str, provider_color: str) -> dict:
    return {
        "id": m.id,
        "provider_id": m.providerId,
        "provider_name": provider_name,
        "provider_color": provider_color,
        "model_id": m.modelId,
        "exposed_id": m.exposedId,
        "display_name": m.displayName,
        "is_free": m.isFree,
        "status": m.status,
        "http_status": m.httpStatus,
        "detail": status_detail(m.status, m.httpStatus),
        "latency_ms": m.latencyMs,
        "checked_at": epoch_ms(m.checkedAt),
        "enabled": m.enabled,
        "context_length": m.contextLength,
        "max_output": m.maxOutput,
        "capabilities": parse_caps(m.capabilities),
        "price_in": m.priceIn,
        "price_out": m.priceOut,
        "description": m.description,
    }


# --------------------------------------------------------------------------- #
# GET /api/admin/models?provider_id&status&search&capability&free[&limit&offset]
# Ordered by provider priority asc, then exposed_id.
# --------------------------------------------------------------------------- #

@router.get("")
def list_models(req: Request, db: Session = Depends(get_db)):
    sp = req.query_params

    provider_id: int | None = None
    if sp.get("provider_id") is not None:
        provider_id = parse_int(sp.get("provider_id"))

    status_raw = sp.get("status")
    status = status_raw if status_raw in MODEL_STATUSES else None

    free_raw = sp.get("free")
    free: bool | None = None
    if free_raw in ("true", "1"):
        free = True
    elif free_raw in ("false", "0"):
        free = False

    search = (sp.get("search") or "").strip().lower()
    capability = sp.get("capability")
    capability_filter = capability if capability in CAPABILITIES else None

    # Optional pagination (extension — the dashboard fetches without it).
    limit: int | None = parse_int(sp.get("limit"))
    offset = parse_int(sp.get("offset")) or 0
    if limit is not None and limit < 0:
        limit = None
    if offset < 0:
        offset = 0

    stmt = select(ModelRow).options(joinedload(ModelRow.provider))
    if provider_id is not None:
        stmt = stmt.where(ModelRow.providerId == provider_id)
    if status is not None:
        stmt = stmt.where(ModelRow.status == status)
    if free is not None:
        stmt = stmt.where(ModelRow.isFree == free)
    rows = db.scalars(stmt).unique().all()

    filtered = []
    for m in rows:
        if search:
            hay = f"{m.exposedId}\n{m.displayName}\n{m.modelId}".lower()
            if search not in hay:
                continue
        if capability_filter:
            caps = parse_caps(m.capabilities)
            if caps.get(capability_filter) is not True:
                continue
        filtered.append(m)

    filtered.sort(key=lambda m: (m.provider.priority, m.exposedId))

    window = filtered[offset:] if offset > 0 else filtered
    if limit is not None:
        window = window[:limit]

    return JSONResponse({
        "total": len(filtered),
        "rows": [to_model(m, m.provider.name, m.provider.color) for m in window],
    })


# --------------------------------------------------------------------------- #
# POST /api/admin/models/sync — REAL live catalogue sync (must precede /{id})
# --------------------------------------------------------------------------- #

@router.post("/sync")
async def sync_models(req: Request):
    sp = req.query_params
    provider_id: int | None = None
    if sp.get("provider_id") is not None:
        provider_id = parse_int(sp.get("provider_id"))

    with SessionLocal() as db:
        reports = await sync_providers(db, provider_id)

    synced = sum(r["discovered"] for r in reports)
    new_added = sum(r["created"] for r in reports)
    updated = sum(r["updated"] for r in reports)
    failed = [r for r in reports if not r["ok"]]

    payload: dict = {
        "ok": len(failed) == 0 or new_added + updated > 0,
        "synced": synced,
        "new_added": new_added,
        "updated": updated,
        "providers": reports,
    }
    if failed:
        payload["errors"] = [f"{r['provider']}: {r.get('error') or 'failed'}" for r in failed]

    return JSONResponse(payload)


# --------------------------------------------------------------------------- #
# PATCH /api/admin/models/{id} — toggle a model on/off
# --------------------------------------------------------------------------- #

@router.patch("/{id}")
def toggle_model(id: str, body: dict = Body(default={}), db: Session = Depends(get_db)):
    model_id = parse_int(id)
    if model_id is None:
        return invalid_id("Invalid model id")

    model = db.get(ModelRow, model_id)
    if model is None:
        return not_found("Model not found")

    if not isinstance(body.get("enabled"), bool):
        return JSONResponse({"error": "enabled (boolean) is required"}, status_code=400)

    model.enabled = body["enabled"]
    db.commit()

    return JSONResponse({"ok": True})


# --------------------------------------------------------------------------- #
# POST /api/admin/models/{id}/ping — health-check a single model
# builtin → REAL engine check (measured latency); provider with keys → real
# GET {base_url}/models (6s timeout); no keys → status stays 'unknown'.
# Persists status / latencyMs / checkedAt / httpStatus on this model row only.
# --------------------------------------------------------------------------- #

@router.post("/{id}/ping")
async def ping_model(id: str):
    model_id = parse_int(id)
    if model_id is None:
        return invalid_id("Invalid model id")

    with SessionLocal() as db:
        model = db.scalars(
            select(ModelRow).where(ModelRow.id == model_id).options(joinedload(ModelRow.provider))
        ).first()
        if model is None:
            return not_found("Model not found")

        provider = model.provider
        keys = db.scalars(
            select(ProviderKey).where(ProviderKey.providerId == provider.id).order_by(ProviderKey.id)
        ).all()

        if provider.kind == "builtin":
            # Real engine health check — measured latency, no fabricated numbers.
            started = time.monotonic()
            up = await nova_engine.health_check(timeout=3.0)
            latency_ms = int((time.monotonic() - started) * 1000)
            if up:
                ok = True
                status = "healthy"
                http_status = 200
                detail = f"Built-in engine responded in {latency_ms}ms"
            else:
                ok = False
                status = "dead"
                http_status = 0
                detail = "Built-in engine unavailable — the engine sidecar is not responding"
        elif not keys or not provider.baseUrl:
            ok = False
            status = "unknown"
            latency_ms = 0
            http_status = 0
            detail = "No upstream key configured" if not keys else "Provider has no base URL configured"
        else:
            key = next((k.apiKey for k in keys if k.enabled), keys[0].apiKey)
            url = f"{provider.baseUrl.rstrip('/')}/models"
            started = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=PING_TIMEOUT_S) as client:
                    res = await client.get(url, headers={"Authorization": f"Bearer {key}"})
                latency_ms = int((time.monotonic() - started) * 1000)
                http_status = res.status_code
                if 200 <= res.status_code < 300:
                    ok = True
                    status = "healthy"
                    detail = f"HTTP {res.status_code} — upstream OK in {latency_ms}ms"
                else:
                    ok = False
                    status = "dead"
                    detail = f"Upstream returned HTTP {res.status_code}"
            except Exception as err:  # noqa: BLE001 — network errors mark the model dead
                latency_ms = int((time.monotonic() - started) * 1000)
                ok = False
                status = "dead"
                http_status = 0
                detail = (str(err) or "Network error")[:120]

        model.status = status
        model.latencyMs = latency_ms
        model.httpStatus = http_status
        model.checkedAt = utcnow()
        db.commit()

        return JSONResponse({
            "ok": ok,
            "status": status,
            "latency_ms": latency_ms,
            "http_status": http_status,
            "detail": detail,
        })
