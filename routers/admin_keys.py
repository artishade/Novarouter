"""Admin upstream API keys — list, add, delete, bulk add, clear cooldowns.

Python port of:
  src/app/api/admin/keys/route.ts                  (GET ?provider_id + POST)
  src/app/api/admin/keys/[id]/route.ts             (DELETE)
  src/app/api/admin/keys/bulk/route.ts             (POST)
  src/app/api/admin/keys/clear-cooldowns/route.ts  (POST)

Mounted at /api/admin/keys by main.py.
"""
from __future__ import annotations

import math
import re
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from nova.database import get_db
from nova.models import Provider, ProviderKey

from ._common import epoch_ms, invalid_id, mask_key, not_found, parse_int

router = APIRouter()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def body_int(v: Any) -> int | None:
    """TS `Number.isInteger(typeof v === 'number' ? v : Number(v))` semantics."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if math.isfinite(v) and v.is_integer() else None
    if isinstance(v, str):
        return parse_int(v)
    return None


def is_real_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #

def to_upstream_key(k: ProviderKey, provider_name: str | None = None) -> dict:
    out: dict = {
        "id": k.id,
        "provider_id": k.providerId,
    }
    if provider_name is not None:
        out["provider_name"] = provider_name
    out.update({
        "label": k.label,
        "api_key_preview": mask_key(k.apiKey),
        "weight": k.weight,
        "enabled": k.enabled,
        "cooldown_until": epoch_ms(k.cooldownUntil),
        "last_error": k.lastError,
        "req_count": k.reqCount,
        "err_count": k.errCount,
        "last_used_at": epoch_ms(k.lastUsedAt),
        "created_at": epoch_ms(k.createdAt),
    })
    return out


# --------------------------------------------------------------------------- #
# GET /api/admin/keys?provider_id= — upstream API keys, descending by created_at
# --------------------------------------------------------------------------- #

@router.get("")
def list_keys(req: Request, db: Session = Depends(get_db)):
    sp = req.query_params
    provider_id: int | None = None
    if sp.get("provider_id") is not None:
        provider_id = parse_int(sp.get("provider_id"))

    stmt = (
        select(ProviderKey)
        .options(joinedload(ProviderKey.provider))
        .order_by(ProviderKey.createdAt.desc(), ProviderKey.id.desc())
    )
    if provider_id is not None:
        stmt = stmt.where(ProviderKey.providerId == provider_id)

    rows = db.scalars(stmt).unique().all()
    return JSONResponse([to_upstream_key(k, k.provider.name if k.provider else None) for k in rows])


# --------------------------------------------------------------------------- #
# POST /api/admin/keys — add a single upstream key
# --------------------------------------------------------------------------- #

@router.post("")
def create_key(body: dict = Body(default={}), db: Session = Depends(get_db)):
    provider_id = body_int(body.get("provider_id"))
    if provider_id is None:
        return JSONResponse({"error": "provider_id is required"}, status_code=400)

    api_key = body["api_key"].strip() if isinstance(body.get("api_key"), str) else ""
    if not api_key:
        return JSONResponse({"error": "api_key is required"}, status_code=400)

    provider = db.get(Provider, provider_id)
    if provider is None:
        return JSONResponse({"error": "Provider not found"}, status_code=404)

    weight = max(1, round(body["weight"])) if is_real_number(body.get("weight")) else 1

    label = body["label"].strip() if isinstance(body.get("label"), str) and body["label"].strip() != "" else "key"

    created = ProviderKey(providerId=provider_id, apiKey=api_key, label=label, weight=weight)
    db.add(created)
    db.commit()

    return JSONResponse({"id": created.id})


# --------------------------------------------------------------------------- #
# DELETE /api/admin/keys/{id} — remove an upstream API key
# --------------------------------------------------------------------------- #

@router.delete("/{id}")
def delete_key(id: str, db: Session = Depends(get_db)):
    key_id = parse_int(id)
    if key_id is None:
        return invalid_id("Invalid key id")

    existing = db.get(ProviderKey, key_id)
    if existing is None:
        return not_found("Key not found")

    db.delete(existing)
    db.commit()

    return JSONResponse({"ok": True})


# --------------------------------------------------------------------------- #
# POST /api/admin/keys/bulk — add many keys (newline/comma separated) at once
# --------------------------------------------------------------------------- #

@router.post("/bulk")
def bulk_add_keys(body: dict = Body(default={}), db: Session = Depends(get_db)):
    provider_id = body_int(body.get("provider_id"))
    if provider_id is None:
        return JSONResponse({"error": "provider_id is required"}, status_code=400)

    provider = db.get(Provider, provider_id)
    if provider is None:
        return JSONResponse({"error": "Provider not found"}, status_code=404)

    raw_keys = body["keys"] if isinstance(body.get("keys"), str) else ""
    keys = [s.strip() for s in re.split(r"[\n,]+", raw_keys) if s.strip()]
    if not keys:
        return JSONResponse({"error": "No keys provided"}, status_code=400)

    label_prefix = body["label_prefix"].strip() \
        if isinstance(body.get("label_prefix"), str) and body["label_prefix"].strip() != "" else "key"

    created_rows = [
        ProviderKey(
            providerId=provider_id,
            apiKey=api_key,
            label=label_prefix if len(keys) == 1 else f"{label_prefix}-{i + 1}",
        )
        for i, api_key in enumerate(keys)
    ]
    db.add_all(created_rows)
    db.commit()

    ids = sorted(k.id for k in created_rows)

    return JSONResponse({"added": len(created_rows), "ids": ids})


# --------------------------------------------------------------------------- #
# POST /api/admin/keys/clear-cooldowns — lift every cooldown on upstream keys
# --------------------------------------------------------------------------- #

@router.post("/clear-cooldowns")
def clear_cooldowns(db: Session = Depends(get_db)):
    # Parity with the TS updateMany (no filter): count is the total number of rows.
    count = db.query(ProviderKey).update(
        {ProviderKey.cooldownUntil: None}, synchronize_session=False
    )
    db.commit()
    return JSONResponse({"ok": True, "cleared": count})
