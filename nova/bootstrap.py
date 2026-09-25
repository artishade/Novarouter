"""Database bootstrap — port of prisma/ensure-seed.ts + prisma/seed.ts.

1. EMPTY database  → bootstrap the real minimum: built-in NovaFree engine
   (provider + its 3 engine models), gateway_started_at, the real free-GPU
   service catalogue. Nothing fake.
2. SEEDED database (older Prisma-era deployment with demo data) → one-time
   surgical purge of every seeded artifact (placeholder keys, simulated logs,
   fake sessions/terminal/storage/client keys, demo agent tasks, seeded preset
   providers that never received a real key).
3. Otherwise → untouched.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import engine
from .kv import set_config
from .models import (
    ALL_TABLES,
    AgentStep,
    AgentTask,
    Base,
    ClientKey,
    Model,
    Provider,
    ProviderKey,
    ProviderSession,
    RequestLog,
    StorageFile,
    StorageProviderRow,
    TerminalCommand,
)

log = logging.getLogger("nova.bootstrap")

SEEDED_PRESET_KEYS = [
    "openrouter", "groq", "gemini", "cerebras", "github-models", "mistral",
    "nvidia", "deepseek", "together", "xai", "fireworks", "ollama", "openai", "anthropic",
]

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
    Base.metadata.create_all(bind=engine)


def bootstrap_empty(db: Session) -> None:
    count = db.scalar(select(func.count()).select_from(Provider)) or 0
    if count > 0:
        log.info("database already has %s provider(s) — checking for legacy demo data", count)
        purge_legacy_demo_data(db)
        return

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
    set_config(db, "gateway_started_at", str(_now_ms()))
    if not db.scalar(select(func.count()).select_from(StorageProviderRow)):
        _seed_gpu_catalogue(db)
    db.commit()
    log.info("bootstrapped: NovaFree Engine (3 models) + gateway config")


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
    from .kv import set_config_json

    set_config_json(db, "gpu_providers", GPU_CATALOGUE)


def purge_legacy_demo_data(db: Session) -> None:
    """One-time cleanup of the old Prisma-era demo seed (guarded by a flag)."""
    from .kv import get_config as kv_get

    if kv_get(db, "demo_data_purged"):
        return

    demo_keys = db.scalars(
        select(ProviderKey).where(ProviderKey.apiKey.contains("SEED-DEMO-PLACEHOLDER"))
    ).all()

    if demo_keys:
        log.info("seeded demo data detected — purging every mock artifact…")
        for k in demo_keys:
            db.delete(k)

        seeded = db.scalars(
            select(Provider).where(
                Provider.key.in_(SEEDED_PRESET_KEYS), Provider.kind != "builtin"
            )
        ).all()
        for p in seeded:
            if not p.keys:  # never received a real key — pure seed noise
                db.delete(p)  # cascades models + keys
                log.info('removed seeded provider "%s" (no real key was ever added)', p.key)

        for table in (RequestLog, ProviderSession, TerminalCommand, StorageFile,
                      ClientKey, AgentStep, AgentTask):
            db.query(table).delete()
        set_config(db, "demo_data_purged", str(_now_ms()))
        db.commit()
        log.info("demo data purged — the dashboard now shows only real data")
    else:
        set_config(db, "demo_data_purged", str(_now_ms()))
        db.commit()


def run_bootstrap() -> None:
    create_schema()
    with Session(engine) as db:
        bootstrap_empty(db)


__all__ = ["run_bootstrap", "create_schema", "ALL_TABLES"]
