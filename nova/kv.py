"""SystemConfig KV helpers — port of src/lib/server/config.ts."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from .models import SystemConfig


def get_config(db: Session, key: str) -> str | None:
    row = db.get(SystemConfig, key)
    return row.value if row else None


def set_config(db: Session, key: str, value: str) -> None:
    row = db.get(SystemConfig, key)
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key=key, value=value))
    db.commit()


def get_config_json(db: Session, key: str, fallback):
    raw = get_config(db, key)
    if raw is None or raw == "":
        return fallback
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def set_config_json(db: Session, key: str, value) -> None:
    set_config(db, key, json.dumps(value))


def get_config_number(db: Session, key: str, default: float) -> float:
    raw = get_config(db, key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# --------------------------------------------------------------------------- #
# Compute (GPU) providers — port of getGpuProviders/setGpuProviders
# --------------------------------------------------------------------------- #

_DEFAULT_ENABLED_IDS = {"novafree_gpu"}


def get_gpu_providers(db: Session) -> list[dict]:
    raw = get_config_json(db, "gpu_providers", [])
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for entry in raw:
        p = entry if isinstance(entry, dict) else {}
        pid = str(p.get("id", "provider"))
        out.append(
            {
                "id": pid,
                "name": str(p.get("name", pid)),
                "gpu": str(p.get("gpu", "—")),
                "vram_gb": int(p.get("vram_gb", 0) or 0),
                "free_tier": str(p.get("free_tier", "")),
                "status": str(p.get("status", "available")),
                "connected": bool(p.get("connected")),
                "hours_free": str(p.get("hours_free", "")),
                "region": str(p.get("region", "global")),
                "note": None if p.get("note") is None else str(p.get("note")),
                "enabled": bool(p["enabled"]) if isinstance(p.get("enabled"), bool) else pid in _DEFAULT_ENABLED_IDS,
            }
        )
    return out


def set_gpu_providers(db: Session, providers: list[dict]) -> None:
    set_config_json(db, "gpu_providers", providers)
