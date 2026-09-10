"""Admin API behind a single admin token. Backs the dashboard UI."""
import json
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import adapters, checker, config, db, store

router = APIRouter()

PRESETS = [
    {"name": "openrouter", "base_url": "https://openrouter.ai/api/v1", "kind": "openai", "prefix": "", "key_hint": "sk-or-v1-..."},
    {"name": "groq", "base_url": "https://api.groq.com/openai/v1", "kind": "openai", "prefix": "groq/", "key_hint": "gsk_..."},
    {"name": "deepseek", "base_url": "https://api.deepseek.com/v1", "kind": "openai", "prefix": "deepseek/", "key_hint": "sk-..."},
    {"name": "together", "base_url": "https://api.together.xyz/v1", "kind": "openai", "prefix": "together/", "key_hint": "tgp_v1_..."},
    {"name": "cerebras", "base_url": "https://api.cerebras.ai/v1", "kind": "openai", "prefix": "cerebras/", "key_hint": "csk-..."},
    {"name": "mistral", "base_url": "https://api.mistral.ai/v1", "kind": "openai", "prefix": "mistral/", "key_hint": "..."},
    {"name": "xai", "base_url": "https://api.x.ai/v1", "kind": "openai", "prefix": "xai/", "key_hint": "xai-..."},
    {"name": "fireworks", "base_url": "https://api.fireworks.ai/inference/v1", "kind": "openai", "prefix": "fireworks/", "key_hint": "fw_..."},
    {"name": "openai", "base_url": "https://api.openai.com/v1", "kind": "openai", "prefix": "openai/", "key_hint": "sk-proj-..."},
    {"name": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "kind": "openai", "prefix": "gemini/", "key_hint": "AIza..."},
    {"name": "anthropic", "base_url": "https://api.anthropic.com/v1", "kind": "anthropic", "prefix": "anthropic/", "key_hint": "sk-ant-..."},
    {"name": "ollama-local", "base_url": "http://127.0.0.1:11434/v1", "kind": "openai", "prefix": "local/", "key_hint": "ollama (any placeholder works)"},
]


def require_admin(request: Request) -> None:
    token = request.headers.get("x-admin-token") or ""
    if not token:
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not config.ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="NOVA_ADMIN_TOKEN is not configured. Set it in your environment "
            "(e.g. Vercel project env vars) and redeploy.",
        )
    if token != config.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="invalid admin token")


def _base_url(request: Request) -> str:
    """FIX: derive the gateway base URL from the incoming request so the
    dashboard shows a working URL both locally and behind Vercel's proxy."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host:
        return f"http://{config.HOST}:{config.PORT}/v1"
    proto = request.headers.get("x-forwarded-proto") or ("https" if request.url.scheme == "https" else "http")
    if request.url.scheme == "http" and "x-forwarded-proto" not in request.headers:
        proto = "http"
    return f"{proto}://{host}/v1"


# ----------------------------------------------------------------- schemas
class ProviderIn(BaseModel):
    name: str
    base_url: str
    kind: str = "openai"
    prefix: str = ""
    enabled: bool = True
    extra_headers: Dict[str, str] = Field(default_factory=dict)
    # First API key(s) for this provider, added in the same request.
    # Accepts one key, a newline/comma separated blob, or a list.
    api_keys: Union[str, List[str]] = ""
    key_label_prefix: str = "key"


class ProviderPatch(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    kind: Optional[str] = None
    prefix: Optional[str] = None
    enabled: Optional[bool] = None
    extra_headers: Optional[Dict[str, str]] = None


class UpstreamKeyIn(BaseModel):
    provider_id: int
    api_key: str
    label: str = ""
    weight: int = 1


class UpstreamKeyBulkIn(BaseModel):
    provider_id: int
    keys: str          # newline / comma separated
    label_prefix: str = "key"


class UpstreamKeyPatch(BaseModel):
    label: Optional[str] = None
    api_key: Optional[str] = None
    weight: Optional[int] = None
    enabled: Optional[bool] = None


class ClientKeyIn(BaseModel):
    name: str
    allowed_models: str = ""


class ClientKeyPatch(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    allowed_models: Optional[str] = None


class CheckIn(BaseModel):
    provider_ids: Optional[List[int]] = None
    free_only: bool = False
    only_unknown: bool = False
    model_filter: str = ""
    limit: int = 0
    workers: int = 6
    timeout: float = 30.0
    retry: int = 0
    sync_first: bool = True


# ----------------------------------------------------------------- meta
@router.get("/meta")
async def meta(request: Request):
    require_admin(request)
    return {
        "presets": PRESETS,
        "kinds": list(adapters.KNOWN_KINDS),
        "statuses": [
            adapters.OK, adapters.RATE_LIMITED, adapters.NEEDS_CREDIT,
            adapters.NO_ACCESS, adapters.BAD_REQUEST, adapters.SERVER_ERROR,
            adapters.NETWORK_ERROR, "UNKNOWN",
        ],
        "base_url": _base_url(request),
        "storage": "postgres" if db.USE_POSTGRES else "sqlite",
        "cooldowns": {
            "429": config.COOLDOWN_429,
            "402": config.COOLDOWN_402,
            "5xx": config.COOLDOWN_5XX,
        },
    }


@router.get("/stats")
async def stats(request: Request):
    require_admin(request)
    return store.stats()


@router.get("/logs")
async def logs(request: Request, limit: int = 100):
    require_admin(request)
    return store.recent_logs(min(max(limit, 1), 500))


# ----------------------------------------------------------------- providers
@router.get("/providers")
async def get_providers(request: Request):
    require_admin(request)
    return store.list_providers(include_keys=True)


@router.post("/providers")
async def create_provider(request: Request, body: ProviderIn):
    require_admin(request)
    try:
        pid = store.add_provider(
            body.name, body.base_url, body.kind, body.prefix, body.extra_headers, body.enabled
        )
    except Exception as e:  # noqa: BLE001 - surface uniqueness/validation errors
        raise HTTPException(status_code=400, detail=str(e))
    raw = body.api_keys if isinstance(body.api_keys, str) else "\n".join(body.api_keys)
    key_ids = store.add_upstream_keys_bulk(pid, raw, body.key_label_prefix)
    return {"id": pid, "keys_added": len(key_ids), "key_ids": key_ids}


@router.patch("/providers/{pid}")
async def patch_provider(request: Request, pid: int, body: ProviderPatch):
    require_admin(request)
    try:
        store.update_provider(pid, **body.model_dump(exclude_none=True))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/providers/{pid}")
async def remove_provider(request: Request, pid: int):
    require_admin(request)
    store.delete_provider(pid)
    return {"ok": True}


# ----------------------------------------------------------------- upstream keys
@router.get("/keys")
async def get_keys(request: Request, provider_id: Optional[int] = None):
    require_admin(request)
    return store.list_upstream_keys(provider_id)


@router.post("/keys")
async def create_key(request: Request, body: UpstreamKeyIn):
    require_admin(request)
    try:
        kid = store.add_upstream_key(body.provider_id, body.api_key, body.label, body.weight)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": kid}


@router.post("/keys/bulk")
async def create_keys_bulk(request: Request, body: UpstreamKeyBulkIn):
    require_admin(request)
    ids = store.add_upstream_keys_bulk(body.provider_id, body.keys, body.label_prefix)
    if not ids:
        raise HTTPException(status_code=400, detail="no valid API keys found in input")
    return {"added": len(ids), "ids": ids}


@router.patch("/keys/{kid}")
async def patch_key(request: Request, kid: int, body: UpstreamKeyPatch):
    require_admin(request)
    store.update_upstream_key(kid, **body.model_dump(exclude_none=True))
    return {"ok": True}


@router.delete("/keys/{kid}")
async def remove_key(request: Request, kid: int):
    require_admin(request)
    store.delete_upstream_key(kid)
    return {"ok": True}


@router.post("/keys/clear-cooldowns")
async def clear_cooldowns(request: Request, provider_id: Optional[int] = None):
    require_admin(request)
    store.clear_cooldowns(provider_id)
    return {"ok": True}


# ----------------------------------------------------------------- client keys
@router.get("/client-keys")
async def get_client_keys(request: Request, reveal: bool = False):
    require_admin(request)
    return store.list_client_keys(reveal=reveal)


@router.post("/client-keys")
async def create_client_key(request: Request, body: ClientKeyIn):
    require_admin(request)
    return store.create_client_key(body.name, body.allowed_models)


@router.patch("/client-keys/{kid}")
async def patch_client_key(request: Request, kid: int, body: ClientKeyPatch):
    require_admin(request)
    store.update_client_key(kid, **body.model_dump(exclude_none=True))
    return {"ok": True}


@router.delete("/client-keys/{kid}")
async def remove_client_key(request: Request, kid: int):
    require_admin(request)
    store.delete_client_key(kid)
    return {"ok": True}


# ----------------------------------------------------------------- models
@router.get("/models")
async def get_models(
    request: Request,
    provider_id: Optional[int] = None,
    status: Optional[str] = None,
    free_only: bool = False,
    search: str = "",
    limit: int = 500,
):
    require_admin(request)
    rows = store.list_models(
        provider_id=provider_id, status=status, free_only=free_only, search=search
    )
    return {"total": len(rows), "rows": rows[: max(1, limit)]}


@router.post("/models/sync")
async def sync_models(request: Request, provider_id: Optional[int] = None):
    require_admin(request)
    ids = [provider_id] if provider_id else None
    return await checker.sync_all_models(ids)


@router.patch("/models/{mid}")
async def patch_model(request: Request, mid: int, enabled: bool):
    require_admin(request)
    store.toggle_model(mid, enabled)
    return {"ok": True}


@router.post("/models/prune-dead")
async def prune_dead(request: Request):
    """Disable every model that is not currently reachable."""
    require_admin(request)
    # FIX: db.execute returns lastrowid (useless for UPDATE) - use the rowcount primitive.
    n = db.execute_rowcount(
        "UPDATE models SET enabled=0 WHERE status NOT IN ('OK','RATE_LIMITED','UNKNOWN')"
    )
    return {"ok": True, "affected": n}


# ----------------------------------------------------------------- checker
@router.post("/check")
async def start_check(request: Request, body: CheckIn):
    require_admin(request)
    job_id = await checker.start_background_check(
        provider_ids=body.provider_ids,
        free_only=body.free_only,
        only_unknown=body.only_unknown,
        model_filter=body.model_filter,
        limit=body.limit,
        workers=body.workers,
        timeout=body.timeout,
        retry=body.retry,
        sync_first=body.sync_first,
        scope="selected providers" if body.provider_ids else "all providers",
    )
    return {"job_id": job_id}


@router.get("/check/{job_id}")
async def check_status(request: Request, job_id: str):
    require_admin(request)
    job = checker.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get("/check")
async def check_jobs(request: Request):
    require_admin(request)
    return checker.list_jobs()


@router.post("/check/{job_id}/cancel")
async def check_cancel(request: Request, job_id: str):
    require_admin(request)
    return {"ok": checker.cancel_job(job_id)}


# ----------------------------------------------------------------- import/export
@router.get("/export")
async def export_config(request: Request, include_secrets: bool = False):
    require_admin(request)
    providers = store.list_providers()
    for p in providers:
        p["keys"] = store.list_upstream_keys(p["id"], reveal=include_secrets)
    return {
        "providers": providers,
        "client_keys": store.list_client_keys(reveal=include_secrets),
    }


@router.post("/import")
async def import_config(request: Request):
    """Import providers + keys from an export payload (secrets required)."""
    require_admin(request)
    payload = await request.json()
    added = {"providers": 0, "keys": 0}
    for p in payload.get("providers") or []:
        try:
            extra = p.get("extra_headers") or {}
            if isinstance(extra, str):
                extra = json.loads(extra or "{}")
            pid = store.add_provider(
                p["name"], p["base_url"], p.get("kind", "openai"), p.get("prefix", ""), extra,
                bool(p.get("enabled", True)),
            )
            added["providers"] += 1
        except Exception:  # noqa: BLE001 - provider already exists
            existing = db.one("SELECT id FROM providers WHERE name=?", (p.get("name"),))
            if not existing:
                continue
            pid = existing["id"]
        for k in p.get("keys") or []:
            if not k.get("api_key"):
                continue
            try:
                store.add_upstream_key(pid, k["api_key"], k.get("label", ""), k.get("weight", 1))
                added["keys"] += 1
            except Exception:  # noqa: BLE001
                continue
    return added