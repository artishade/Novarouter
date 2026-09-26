"""Database bootstrap — ADDITIVE-ONLY, idempotent, NON-DESTRUCTIVE.

Data-safety contract (the "git push wiped my data" fix):
  1. Missing tables are created via `create_all` — purely additive schema sync
     (works identically on SQLite and Postgres). Existing columns/rows are
     never dropped or altered destructively.
  2. An EMPTY database gets the real minimum seeded once: the built-in
     NovaFree engine (provider + its 3 engine models), `gateway_started_at`,
     and the real free-GPU service catalogue. Nothing fake.
  3. A NON-EMPTY database is NEVER modified, cleaned or purged — not at boot,
     not after a deploy, not ever. Providers, models, keys, routes, configs,
     logs and files survive every restart and every `git push` (as long as
     DATABASE_URL points at a persistent store, e.g. Postgres/Neon on Render).
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import engine
from .kv import get_config, set_config, set_config_json
from .models import (
    ALL_TABLES,
    Base,
    Model,
    Provider,
    StorageProviderRow,
)

log = logging.getLogger("nova.bootstrap")

NOVA_ENGINE_MODELS = [
    {"modelId": "nova-air", "exposedId": "nova/air", "displayName": "Nova Air",
     "ctx": 32768, "maxOut": 8192,
     "caps": {"tools": True, "vision": True, "reasoning": True},
     "description": "Built-in free engine. Balanced speed and quality, always available."},
    {"modelId": "nova-mini", "exposedId": "nova/mini", "displayName": "Nova Mini",
     "ctx": 16384, "maxOut": 4096,
     "caps": {"tools": False, "vision": False, "reasoning": False},
     "description": "Built-in free engine. Ultra-low latency for quick tasks."},
    {"modelId": "nova-pro", "exposedId": "nova/pro", "displayName": "Nova Pro",
     "ctx": 65536, "maxOut": 16384,
     "caps": {"tools": True, "vision": True, "reasoning": True},
     "description": "Built-in free engine. Deep reasoning with the largest context."},
]

# Real free-GPU service catalogue (real providers, real free tiers) — same
# nature as the provider preset catalogue: configuration, not telemetry.
GPU_CATALOGUE = [
    {"id": "novafree_gpu", "name": "NovaFree Compute", "gpu": "shared", "vram_gb": 8,
     "free_tier": "Built-in gateway compute", "status": "available", "connected": True,
     "hours_free": "unlimited", "region": "global", "note": "Built-in pool used by the NovaFree engine."},
    {"id": "colab", "name": "Google Colab", "gpu": "T4 16GB", "vram_gb": 16,
     "free_tier": "Free tier GPU sessions", "status": "available", "connected": False,
     "hours_free": "~4h sessions", "region": "us/eu", "note": "Attach via notebook + ngrok bridge."},
    {"id": "kaggle", "name": "Kaggle Notebooks", "gpu": "T4 x2 / P100", "vram_gb": 16,
     "free_tier": "30h/week free GPU", "status": "available", "connected": False,
     "hours_free": "30h/week", "region": "us", "note": "Attach via kernel + API bridge."},
    {"id": "lightning", "name": "Lightning AI", "gpu": "T4 16GB", "vram_gb": 16,
     "free_tier": "22 free GPU hours monthly", "status": "available", "connected": False,
     "hours_free": "22h/month", "region": "us/eu", "note": "Free studio credits on signup."},
    {"id": "paperspace", "name": "Paperspace Gradient", "gpu": "M4000 / Free GPU", "vram_gb": 8,
     "free_tier": "Free GPU notebooks", "status": "available", "connected": False,
     "hours_free": "6h sessions", "region": "us/eu", "note": "Free tier notebooks with periodic availability."},
]


def create_schema() -> None:
    """Create missing tables only — never drops, never alters existing data."""
    Base.metadata.create_all(bind=engine)


def bootstrap_minimum(db: Session) -> None:
    """Seed the real minimum into an empty database. Additive-only:
    existing rows (user providers, keys, configs, logs…) are never touched."""
    provider_count = db.scalar(select(func.count()).select_from(Provider)) or 0
    if provider_count == 0:
        log.info("empty database — bootstrapping the real minimum…")
        novafree = Provider(
            key="novafree", name="NovaFree Engine", kind="builtin",
            baseUrl="internal://nova-engine", prefix="nova/", priority=1,
            color="#10b981",
            freeTier="Built-in — every model here is free, no key required",
            docsUrl="https://github.com/artishade/Novarouter",
        )
        db.add(novafree)
        db.flush()
        for m in NOVA_ENGINE_MODELS:
            db.add(Model(
                providerId=novafree.id, modelId=m["modelId"], exposedId=m["exposedId"],
                displayName=m["displayName"], isFree=True,
                contextLength=m["ctx"], maxOutput=m["maxOut"],
                capabilities=_json_caps(m["caps"]), description=m["description"],
            ))
        db.commit()
        log.info("bootstrapped: NovaFree Engine (3 models)")
    else:
        log.info("database intact — %s provider(s) found, nothing touched "
                 "(boot is additive-only; deploys never wipe data)", provider_count)

    # Missing-but-expected config keys are filled in (add-only, never overwritten).
    if get_config(db, "gateway_started_at") is None:
        set_config(db, "gateway_started_at", str(_now_ms()))

    # Storage catalogue: only when the table is completely empty.
    if not db.scalar(select(func.count()).select_from(StorageProviderRow)):
        _seed_gpu_catalogue(db)


def _json_caps(caps: dict) -> str:
    import json

    return json.dumps({"tools": False, "vision": False, "reasoning": False, **caps})


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _seed_gpu_catalogue(db: Session) -> None:
    db.add(StorageProviderRow(
        id="local_disk", name="Local Disk", type="local", status="connected",
        isBuiltin=True, active=True, freeTier="Uses the server's own disk",
        quotaMb=0, region="local", docsUrl=None, authUrl=None,
    ))
    set_config_json(db, "gpu_providers", GPU_CATALOGUE)
    db.commit()


def run_bootstrap() -> None:
    create_schema()
    with Session(engine) as db:
        bootstrap_minimum(db)


__all__ = ["run_bootstrap", "create_schema", "ALL_TABLES"]
