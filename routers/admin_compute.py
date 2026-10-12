"""Compute (GPU) admin routes — port of:
  src/app/api/admin/compute/providers/route.ts            → GET  /providers
  src/app/api/admin/compute/providers/[id]/toggle/route.ts → POST /providers/{id}/toggle
  src/app/api/admin/compute/config/route.ts                → GET/POST /config
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from nova.database import get_db
from nova.kv import get_config, get_gpu_providers, set_config, set_gpu_providers

router = APIRouter()


def _build_gpu_config(db: Session) -> dict:
    providers = get_gpu_providers(db)
    gpu_enabled_raw = get_config(db, "gpu_enabled")
    strategy_raw = get_config(db, "gpu_strategy")
    return {
        "enabled": gpu_enabled_raw not in ("0", "false"),
        "strategy": strategy_raw or "quota_aware",
        "providers": providers,
        "total_vram_gb": sum(p.get("vram_gb") or 0 for p in providers),
        "enabled_vram_gb": sum(
            (p.get("vram_gb") or 0) for p in providers if p.get("enabled")
        ),
    }


# --------------------------------------------------------------------------- #
# GET /providers → ComputeProvider[]
# --------------------------------------------------------------------------- #


@router.get("/providers")
def providers(db: Session = Depends(get_db)):
    return get_gpu_providers(db)


# --------------------------------------------------------------------------- #
# POST /providers/{provider_id}/toggle {enabled?} → {ok, message}
# --------------------------------------------------------------------------- #


@router.post("/providers/{provider_id}/toggle")
async def toggle_provider(provider_id: str, request: Request, db: Session = Depends(get_db)):
    body: dict = {}
    try:
        parsed = await request.json()
        if isinstance(parsed, dict):
            body = parsed
    except Exception:
        pass  # empty/absent body → flip current flag

    providers_list = get_gpu_providers(db)
    idx = next((i for i, p in enumerate(providers_list) if p["id"] == provider_id), -1)
    if idx == -1:
        return JSONResponse({"error": f"Unknown compute provider: {provider_id}"}, status_code=404)

    enabled = (
        body["enabled"]
        if isinstance(body.get("enabled"), bool)
        else not providers_list[idx]["enabled"]
    )
    providers_list[idx] = {**providers_list[idx], "enabled": enabled}
    set_gpu_providers(db, providers_list)

    message = (
        f"{providers_list[idx]['name']} {'enabled' if enabled else 'disabled'}"
        f" — free tier: {providers_list[idx]['free_tier']}"
    )
    return {"ok": True, "message": message}


# --------------------------------------------------------------------------- #
# GET /config → GpuConfig
# --------------------------------------------------------------------------- #


@router.get("/config")
def get_gpu_config(db: Session = Depends(get_db)):
    return _build_gpu_config(db)


# --------------------------------------------------------------------------- #
# POST /config {enabled?, strategy?} → {ok, config}
# --------------------------------------------------------------------------- #


@router.post("/config")
async def set_gpu_config(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        body = {}

    if isinstance(body.get("enabled"), bool):
        set_config(db, "gpu_enabled", "1" if body["enabled"] else "0")
    if isinstance(body.get("strategy"), str) and body["strategy"].strip():
        set_config(db, "gpu_strategy", body["strategy"].strip())

    return {"ok": True, "config": _build_gpu_config(db)}
