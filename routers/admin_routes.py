"""Admin fallback routes — CRUD + 3-stage pipeline preview.

Python port of:
  src/app/api/admin/routes/route.ts         (GET + POST)
  src/app/api/admin/routes/[id]/route.ts    (PATCH + DELETE)
  src/app/api/admin/routes/preview/route.ts (GET ?model=)

Mounted at /api/admin/routes by main.py.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from nova.database import get_db
from nova.models import Model as ModelRow, ModelRoute, SystemConfig

from ._common import epoch_ms, invalid_id, not_found, parse_fallbacks, parse_int

router = APIRouter()


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #

def to_route(r: ModelRoute) -> dict:
    return {
        "id": r.id,
        "public_id": r.publicId,
        "fallbacks": parse_fallbacks(r.fallbacks),
        "auto": r.auto,
        "enabled": r.enabled,
        "note": r.note,
        "created_at": epoch_ms(r.createdAt),
    }


# --------------------------------------------------------------------------- #
# GET /api/admin/routes — fallback route chains
# --------------------------------------------------------------------------- #

@router.get("")
def list_routes(db: Session = Depends(get_db)):
    rows = db.scalars(select(ModelRoute).order_by(ModelRoute.createdAt.asc(), ModelRoute.id.asc())).all()
    return JSONResponse([to_route(r) for r in rows])


# --------------------------------------------------------------------------- #
# POST /api/admin/routes — create a fallback chain
# --------------------------------------------------------------------------- #

@router.post("")
def create_route(body: dict = Body(default={}), db: Session = Depends(get_db)):
    public_id = body["public_id"].strip() if isinstance(body.get("public_id"), str) else ""
    if not public_id:
        return JSONResponse({"error": "public_id is required"}, status_code=400)

    raw = body.get("fallbacks")
    fallbacks = (
        [s.strip() for s in raw if isinstance(s, str) and s.strip() != ""] if isinstance(raw, list) else []
    )

    existing = db.scalars(select(ModelRoute).where(ModelRoute.publicId == public_id)).first()
    if existing is not None:
        return JSONResponse({"error": f"A route with id '{public_id}' already exists"}, status_code=400)

    created = ModelRoute(
        publicId=public_id,
        fallbacks=json.dumps(fallbacks),
        auto=body["auto"] if isinstance(body.get("auto"), bool) else True,
        note=body["note"] if isinstance(body.get("note"), str) else "",
    )
    db.add(created)
    db.commit()

    return JSONResponse({"id": created.id})


# --------------------------------------------------------------------------- #
# GET /api/admin/routes/preview — resolve the 3-stage fallback pipeline
# (defined before /{id} routes; GET-only so no conflict, but keep it early)
# --------------------------------------------------------------------------- #

@router.get("/preview")
def preview_route(req: Request, db: Session = Depends(get_db)):
    model = (req.query_params.get("model") or "*").strip() or "*"

    models = db.scalars(
        select(ModelRow).where(ModelRow.enabled.is_(True)).options(joinedload(ModelRow.provider))
    ).unique().all()
    route = db.scalars(select(ModelRoute).where(ModelRoute.publicId == model)).first()
    star_route = db.scalars(select(ModelRoute).where(ModelRoute.publicId == "*")).first()
    cfg_spoof = db.get(SystemConfig, "spoof_model")

    # Stage 1 — direct providers that serve this model (by exposed or upstream id).
    seen: set[str] = set()
    stage1: list[str] = []
    for m in models:
        if m.exposedId == model or m.modelId == model:
            if m.exposedId not in seen:
                seen.add(m.exposedId)
                stage1.append(m.exposedId)

    # Stage 2 — explicit fallback chain (matched route, else the '*' default chain).
    effective_route = route if route is not None else star_route
    explicit_chain = parse_fallbacks(effective_route.fallbacks) if effective_route else []
    auto_allowed = effective_route.auto if effective_route else True

    # Stage 3 — auto stand-ins: healthy, enabled, free models not already listed.
    listed = {model, *stage1, *explicit_chain}
    if auto_allowed:
        candidates = [
            m for m in models
            if m.status == "healthy" and m.enabled and m.isFree and m.exposedId not in listed
        ]
        candidates.sort(key=lambda m: (m.provider.key, m.exposedId))
        auto_targets = [m.exposedId for m in candidates[:3]]
    else:
        auto_targets = []

    preview = {
        "model": model,
        "direct": len(stage1) > 0,
        "explicit_chain": explicit_chain,
        "auto_allowed": auto_allowed,
        "auto_targets": auto_targets,
        "stages": [
            {"label": "Stage 1 — Direct providers", "models": stage1},
            {"label": "Stage 2 — Fallback chain", "models": explicit_chain},
            {"label": "Stage 3 — Auto stand-ins", "models": auto_targets},
        ],
        "spoof_model": (cfg_spoof.value in ("1", "true")) if cfg_spoof is not None else True,
    }

    return JSONResponse(preview)


# --------------------------------------------------------------------------- #
# PATCH /api/admin/routes/{id} — update a fallback route
# --------------------------------------------------------------------------- #

@router.patch("/{id}")
def update_route(id: str, body: dict = Body(default={}), db: Session = Depends(get_db)):
    route_id = parse_int(id)
    if route_id is None:
        return invalid_id("Invalid route id")

    existing = db.get(ModelRoute, route_id)
    if existing is None:
        return not_found("Route not found")

    if isinstance(body.get("public_id"), str) and body["public_id"].strip() != "":
        public_id = body["public_id"].strip()
        if public_id != existing.publicId:
            clash = db.scalars(select(ModelRoute).where(ModelRoute.publicId == public_id)).first()
            if clash is not None:
                return JSONResponse({"error": f"A route with id '{public_id}' already exists"}, status_code=400)
            existing.publicId = public_id

    if isinstance(body.get("fallbacks"), list):
        fallbacks = [s.strip() for s in body["fallbacks"] if isinstance(s, str) and s.strip() != ""]
        existing.fallbacks = json.dumps(fallbacks)

    if isinstance(body.get("auto"), bool):
        existing.auto = body["auto"]
    if isinstance(body.get("enabled"), bool):
        existing.enabled = body["enabled"]
    if isinstance(body.get("note"), str):
        existing.note = body["note"]

    db.commit()

    return JSONResponse({"ok": True})


# --------------------------------------------------------------------------- #
# DELETE /api/admin/routes/{id} — remove a fallback route
# --------------------------------------------------------------------------- #

@router.delete("/{id}")
def delete_route(id: str, db: Session = Depends(get_db)):
    route_id = parse_int(id)
    if route_id is None:
        return invalid_id("Invalid route id")

    existing = db.get(ModelRoute, route_id)
    if existing is None:
        return not_found("Route not found")

    db.delete(existing)
    db.commit()

    return JSONResponse({"ok": True})
