"""Live model discovery — Python port of src/lib/server/model-discovery.ts.

Queries each enabled provider's real /models endpoint so the gateway,
dashboard and Nova Agent see what is ACTUALLY available upstream right now.

Wire formats handled:
  • OpenAI-compatible providers → GET {base}/models (Bearer)
  • Gemini                      → GET {base}/models?key=…
  • Anthropic                   → GET {base}/v1/models (x-api-key + version)
  • Ollama (local)              → GET {base-no-/v1}/api/tags
  • novafree (builtin)          → local DB catalogue (no network)

Results are cached in-memory for DISCOVERY_TTL_MS; network calls are bounded
and failures are reported per-provider (never raised).
"""
from __future__ import annotations

import time
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .models import Model, Provider

DISCOVERY_TTL_MS = 5 * 60 * 1000
FETCH_TIMEOUT_S = 8.0

KEYLESS_PROVIDERS = {"openrouter"}
FREE_PROVIDERS = {"novafree", "ollama"}

_cache: dict[str, tuple[float, list[dict]]] = {}


def _to_number(v: Any) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n == n else None  # NaN guard


def _mark_free(provider_key: str, model_id: str, prompt: float | None, completion: float | None) -> bool:
    if provider_key in FREE_PROVIDERS:
        return True
    if model_id.endswith(":free"):
        return True
    return (prompt or 0) == 0 and (completion or 0) == 0


def _extract_list(data: Any) -> list[dict]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "models", "body"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _join_url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def _from_openai_compatible(raw: dict, provider_key: str, provider_id: int, provider_name: str) -> dict:
    mid = str(raw.get("id") or raw.get("name") or raw.get("model") or "").strip()
    pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
    prompt = _to_number(pricing.get("prompt")) or _to_number((raw.get("pricing") or {}).get("input"))
    completion = _to_number(pricing.get("completion")) or _to_number((raw.get("pricing") or {}).get("output"))

    def norm(price: float | None) -> float | None:
        if price is not None and 0 < price < 0.01:
            return round(price * 1_000_000, 3)
        return price

    return {
        "id": mid,
        "object": "model",
        "owned_by": provider_key,
        "provider_id": provider_id,
        "provider_name": provider_name,
        "display_name": raw.get("name") if isinstance(raw.get("name"), str) and raw.get("name") != mid else None,
        "context_length": _to_number(raw.get("context_length")) or _to_number(raw.get("context_window")) or _to_number(raw.get("max_model_len")),
        "max_output": _to_number(raw.get("max_completion_tokens")) or _to_number(raw.get("max_output_tokens")),
        "pricing_prompt": norm(prompt),
        "pricing_completion": norm(completion),
        "is_free": _mark_free(provider_key, mid, prompt, completion),
    }


async def _fetch_json(url: str, headers: dict | None = None) -> Any:
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S) as client:
        res = await client.get(url, headers={"Accept": "application/json", **(headers or {})})
        res.raise_for_status()
        return res.json()


async def _discover_openai_compatible(base, key, provider_key, provider_id, provider_name) -> list[dict]:
    headers: dict = {}
    if key and provider_key not in KEYLESS_PROVIDERS:
        headers["Authorization"] = f"Bearer {key}"
    data = await _fetch_json(_join_url(base, "models"), headers)
    return [
        m
        for m in (_from_openai_compatible(r, provider_key, provider_id, provider_name) for r in _extract_list(data))
        if m["id"]
    ]


async def _discover_gemini(base, key, provider_id, provider_name) -> list[dict]:
    if not key:
        raise ValueError("no API key configured")
    data = await _fetch_json(_join_url(base, f"models?key={key}&pageSize=200"))
    out = []
    for raw in _extract_list(data):
        full_name = str(raw.get("name", ""))
        mid = full_name.removeprefix("models/")
        methods = raw.get("supportedGenerationMethods") if isinstance(raw.get("supportedGenerationMethods"), list) else []
        out.append({
            "id": mid,
            "object": "model",
            "owned_by": "gemini",
            "provider_id": provider_id,
            "provider_name": provider_name,
            "display_name": raw.get("displayName") if isinstance(raw.get("displayName"), str) else None,
            "context_length": _to_number(raw.get("inputTokenLimit")),
            "max_output": _to_number(raw.get("outputTokenLimit")),
            "pricing_prompt": None,
            "pricing_completion": None,
            "is_free": len(methods) == 0 or "generateContent" in methods,
        })
    return [m for m in out if m["id"]]


async def _discover_anthropic(base, key, provider_id, provider_name) -> list[dict]:
    if not key:
        raise ValueError("no API key configured")
    data = await _fetch_json(
        _join_url(base, "v1/models?limit=100"),
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    out = []
    for raw in _extract_list(data):
        mid = str(raw.get("id", "")).strip()
        out.append({
            "id": mid,
            "object": "model",
            "owned_by": "anthropic",
            "provider_id": provider_id,
            "provider_name": provider_name,
            "display_name": raw.get("display_name") if isinstance(raw.get("display_name"), str) else None,
            "context_length": _to_number(raw.get("context_window")),
            "max_output": None,
            "pricing_prompt": None,
            "pricing_completion": None,
            "is_free": _mark_free("anthropic", mid, None, None),
        })
    return [m for m in out if m["id"]]


async def _discover_ollama(base, provider_id, provider_name) -> list[dict]:
    import re

    root = re.sub(r"/v1/?$", "", base)
    data = await _fetch_json(_join_url(root, "api/tags"))
    out = []
    for raw in _extract_list(data):
        mid = str(raw.get("name") or raw.get("model") or "").strip()
        out.append({
            "id": mid,
            "object": "model",
            "owned_by": "ollama",
            "provider_id": provider_id,
            "provider_name": provider_name,
            "display_name": mid,
            "context_length": None,
            "max_output": None,
            "pricing_prompt": None,
            "pricing_completion": None,
            "is_free": True,
        })
    return [m for m in out if m["id"]]


def _discover_novafree_sync(db: Session, provider_id: int, provider_name: str) -> list[dict]:
    rows = db.scalars(
        select(Model).where(Model.providerId == provider_id, Model.enabled.is_(True)).order_by(Model.exposedId)
    ).all()
    return [
        {
            "id": m.modelId,
            "object": "model",
            "owned_by": "novafree",
            "provider_id": provider_id,
            "provider_name": provider_name,
            "display_name": m.displayName or m.exposedId,
            "context_length": m.contextLength,
            "max_output": m.maxOutput,
            "pricing_prompt": None,
            "pricing_completion": None,
            "is_free": True,
        }
        for m in rows
    ]


async def _discover_one(db: Session, p: Provider, api_key: str | None) -> dict:
    start = time.monotonic()
    try:
        if p.kind == "builtin":
            models = _discover_novafree_sync(db, p.id, p.name)
        elif p.key == "gemini":
            models = await _discover_gemini(p.baseUrl, api_key, p.id, p.name)
        elif p.kind == "anthropic":
            models = await _discover_anthropic(p.baseUrl, api_key, p.id, p.name)
        elif p.key == "ollama":
            models = await _discover_ollama(p.baseUrl, p.id, p.name)
        else:
            models = await _discover_openai_compatible(p.baseUrl, api_key, p.key, p.id, p.name)
        return {
            "provider": p.key, "provider_id": p.id, "ok": True, "count": len(models),
            "models": models, "duration_ms": int((time.monotonic() - start) * 1000), "cached": False,
        }
    except Exception as err:
        return {
            "provider": p.key, "provider_id": p.id, "ok": False, "count": 0, "models": [],
            "error": str(err)[:200], "duration_ms": int((time.monotonic() - start) * 1000), "cached": False,
        }


def _load_providers(db: Session, provider_key: str | None) -> list[Provider]:
    stmt = (
        select(Provider)
        .where(Provider.enabled.is_(True))
        .options(joinedload(Provider.keys))
        .order_by(Provider.priority, Provider.id)
    )
    if provider_key:
        stmt = stmt.where(Provider.key == provider_key)
    return list(db.scalars(stmt).unique().all())


def _no_key_result(p: Provider) -> dict:
    return {
        "provider": p.key, "provider_id": p.id, "ok": False, "count": 0, "models": [],
        "error": "no enabled API key — add one in Dashboard → Providers",
        "duration_ms": 0, "cached": False,
    }


async def discover_provider_models(db: Session, provider_key: str | None = None) -> list[dict]:
    cache_key = provider_key or "all"
    hit = _cache.get(cache_key)
    if hit and (time.monotonic() * 1000 - hit[0]) < DISCOVERY_TTL_MS:
        return [dict(r, cached=True) for r in hit[1]]

    providers = _load_providers(db, provider_key)
    results: list[dict] = []
    for p in providers:
        needs_key = p.kind != "builtin" and p.key not in KEYLESS_PROVIDERS
        api_key = next((k.apiKey for k in p.keys if k.enabled), None)
        if needs_key and not api_key:
            results.append(_no_key_result(p))
        else:
            results.append(await _discover_one(db, p, api_key))

    _cache[cache_key] = (time.monotonic() * 1000, results)
    return results


async def discover_provider_fresh(db: Session, provider_id: int) -> dict:
    """Live discovery for ONE provider, bypassing the cache (models-sync)."""
    p = db.get(Provider, provider_id)
    if p is None:
        return {
            "provider": f"#{provider_id}", "provider_id": provider_id, "ok": False,
            "count": 0, "models": [], "error": "provider not found",
            "duration_ms": 0, "cached": False,
        }
    needs_key = p.kind != "builtin" and p.key not in KEYLESS_PROVIDERS
    api_key = next((k.apiKey for k in p.keys if k.enabled), None)
    if needs_key and not api_key:
        return _no_key_result(p)
    return await _discover_one(db, p, api_key)


def aggregate_discovery(results: list[dict]) -> dict:
    data = [m for r in results for m in r["models"]]
    return {
        "object": "list",
        "discover": True,
        "cached": any(r.get("cached") for r in results),
        "providers_queried": len(results),
        "providers_ok": sum(1 for r in results if r["ok"]),
        "total_models": len(data),
        "free_models": sum(1 for m in data if m.get("is_free")),
        "data": data,
        "meta": [
            {
                "provider": r["provider"], "ok": r["ok"], "count": r["count"],
                **({"error": r["error"]} if r.get("error") else {}),
                "duration_ms": r["duration_ms"], "cached": r.get("cached", False),
            }
            for r in results
        ],
    }


def summarize_for_agent(results: list[dict], filter_note: str | None = None) -> str:
    ok_results = [r for r in results if r["ok"]]
    total = sum(r["count"] for r in ok_results)
    free = sum(sum(1 for m in r["models"] if m.get("is_free")) for r in ok_results)
    lines = [
        f"Live model discovery (/v1/models?discover=1){f' [filter: {filter_note}]' if filter_note else ''}:",
        f"Providers reachable: {len(ok_results)}/{len(results)} · models: {total} total · {free} free",
    ]
    for r in results:
        if not r["ok"]:
            lines.append(f"• {r['provider']}: unavailable — {r.get('error', 'error')}")
            continue
        free_count = sum(1 for m in r["models"] if m.get("is_free"))
        sample = ", ".join(m["id"] + (" (free)" if m.get("is_free") else "") for m in r["models"][:6])
        more = f" …+{r['count'] - 6} more" if r["count"] > 6 else ""
        lines.append(f"• {r['provider']}: {r['count']} models ({free_count} free) — {sample}{more}")
    return "\n".join(lines)[:1800]
