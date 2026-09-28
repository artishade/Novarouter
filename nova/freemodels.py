"""Free AI model catalogue — base model list from ClawLabsAI/free-ai-models.

This is the model base for the built-in NovaFree engine. The bundled snapshot
lives at ``engine/free-models.json`` — the exact same file the engine sidecar
reads — and can be refreshed from the upstream repository on demand.

Nothing here needs credentials: the keyless providers (Pollinations, OVHcloud)
always work, and OpenRouter/ZeroLimitAI are used automatically once their key is
set in the environment.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from .config import PROJECT_ROOT

log = logging.getLogger("nova.freemodels")

CATALOGUE_PATH = PROJECT_ROOT / "engine" / "free-models.json"
UPSTREAM_URL = "https://raw.githubusercontent.com/ClawLabsAI/free-ai-models/main/data/models.json"
SOURCE_REPO = "https://github.com/ClawLabsAI/free-ai-models"

_cache: dict = {"mtime": None, "data": None}


def _read_file() -> dict:
    try:
        raw = CATALOGUE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError) as err:
        log.warning("free model catalogue unreadable (%s) — using empty catalogue", err)
        return {"updated_at": None, "source_repo": SOURCE_REPO, "providers": {}, "models": []}
    if not isinstance(data, dict):
        return {"updated_at": None, "source_repo": SOURCE_REPO, "providers": {}, "models": []}
    data.setdefault("providers", {})
    data.setdefault("models", [])
    return data


def load_catalogue() -> dict:
    """Return the bundled catalogue, re-reading it only when the file changes."""
    try:
        mtime = CATALOGUE_PATH.stat().st_mtime
    except OSError:
        mtime = None
    if _cache["data"] is None or _cache["mtime"] != mtime:
        _cache["data"] = _read_file()
        _cache["mtime"] = mtime
    return _cache["data"]


def provider_info(key: str) -> dict | None:
    return load_catalogue().get("providers", {}).get(key)


def chat_models() -> list[dict]:
    """Every chat-capable free model, in the catalogue's quality order."""
    models = load_catalogue().get("models", [])
    return [m for m in models if isinstance(m, dict) and m.get("kind", "chat") == "chat" and m.get("route")]


def find_free_model(model_id: str | None) -> dict | None:
    if not model_id:
        return None
    needle = str(model_id).strip().lower()
    for m in load_catalogue().get("models", []):
        if isinstance(m, dict) and str(m.get("id", "")).lower() == needle:
            return m
    return None


def _capabilities(model: dict) -> dict:
    modalities = model.get("modalities") or []
    if isinstance(modalities, str):
        modalities = [modalities]
    modalities = {str(m).lower() for m in modalities}
    model_id = str(model.get("id", "")).lower()
    return {
        "tools": True,
        "vision": bool(modalities & {"image", "vision"}),
        "reasoning": "reasoning" in model_id or "think" in model_id,
    }


# --------------------------------------------------------------------------- #
# Engine model rows (shape shared by nova.bootstrap and nova.syncengine)
# --------------------------------------------------------------------------- #

# Friendly built-in tiers. They stay exposed so existing clients/UI keep
# working; the engine resolves each one onto the best free model available.
ENGINE_ALIASES = [
    {
        "modelId": "nova-pro",
        "exposedId": "nova/pro",
        "displayName": "Nova Pro",
        "ctx": 65536,
        "maxOut": 16384,
        "caps": {"tools": True, "vision": True, "reasoning": True},
        "description": "Built-in free engine. Best available free model, largest context.",
    },
    {
        "modelId": "nova-air",
        "exposedId": "nova/air",
        "displayName": "Nova Air",
        "ctx": 32768,
        "maxOut": 8192,
        "caps": {"tools": True, "vision": True, "reasoning": True},
        "description": "Built-in free engine. Balanced speed and quality, always available.",
    },
    {
        "modelId": "nova-mini",
        "exposedId": "nova/mini",
        "displayName": "Nova Mini",
        "ctx": 16384,
        "maxOut": 4096,
        "caps": {"tools": True, "vision": False, "reasoning": False},
        "description": "Built-in free engine. Ultra-low latency keyless model for quick tasks.",
    },
]


def free_model_rows() -> list[dict]:
    """Free catalogue entries in the engine-model shape used by the DB seeders."""
    rows: list[dict] = []
    for m in chat_models():
        model_id = str(m.get("id") or "").strip()
        if not model_id:
            continue
        rate = m.get("rate_limit")
        provider = m.get("provider") or "free"
        description = f"Free model from {provider}"
        description += f" · {rate}" if rate else " · free tier"
        duration = m.get("zo_score")
        if isinstance(duration, (int, float)):
            description += f" · quality score {duration}/100"
        rows.append(
            {
                "modelId": model_id,
                "exposedId": model_id,
                "displayName": str(m.get("name") or model_id),
                "ctx": int(m.get("context_window") or 0),
                "maxOut": int(m.get("max_output") or 0),
                "caps": _capabilities(m),
                "description": description,
                "isFree": True,
            }
        )
    return rows


def nova_engine_models() -> list[dict]:
    """Aliases first (so defaults resolve fast), then the free catalogue."""
    return [*ENGINE_ALIASES, *free_model_rows()]


async def refresh_catalogue(timeout: float = 15.0) -> dict:
    """Pull the live ``data/models.json`` and merge it into the bundled snapshot.

    Only the ``models`` list (and its timestamp) is replaced — the local
    ``providers`` routing table is always preserved, because the upstream data
    has no knowledge of NovaRouter's upstream URLs.
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.get(UPSTREAM_URL)
        res.raise_for_status()
        incoming = res.json()
    if not isinstance(incoming, dict) or not isinstance(incoming.get("models"), list):
        raise ValueError("upstream free-model catalogue has an unexpected shape")

    local = dict(load_catalogue())
    local_providers = local.get("providers", {})

    # Keep our route table and attach route info for any brand-new models.
    new_models = []
    for m in incoming["models"]:
        if not isinstance(m, dict):
            continue
        entry = dict(m)
        old = find_free_model(entry.get("id"))
        route = (old or {}).get("route")
        if route:
            entry["route"] = route
        elif entry.get("kind", "chat") == "chat":
            entry.setdefault("route", {"provider": "openrouter", "model": entry.get("id")})
        new_models.append(entry)

    if new_models:
        local["models"] = new_models
    local["updated_at"] = incoming.get("updated_at") or local.get("updated_at")
    local["sources"] = incoming.get("sources") or local.get("sources")
    local["providers"] = local_providers
    local["source_repo"] = SOURCE_REPO
    local["source_data"] = UPSTREAM_URL
    local["total_free_models"] = len(new_models)

    CATALOGUE_PATH.write_text(json.dumps(local, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _cache["data"] = None  # force a re-read on next access
    _cache["mtime"] = None
    return local


__all__ = [
    "CATALOGUE_PATH",
    "SOURCE_REPO",
    "UPSTREAM_URL",
    "chat_models",
    "find_free_model",
    "free_model_rows",
    "load_catalogue",
    "nova_engine_models",
    "provider_info",
    "refresh_catalogue",
]
