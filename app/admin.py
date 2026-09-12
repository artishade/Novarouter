"""Admin API behind a single admin token. Backs the dashboard UI."""
import json
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import adapters, checker, config, db, extensions, store

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
    rpm_limit: int = 0
    tpd_limit: int = 0


class ClientKeyPatch(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    allowed_models: Optional[str] = None
    rpm_limit: Optional[int] = None
    tpd_limit: Optional[int] = None


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


class RouteIn(BaseModel):
    public_id: str
    fallbacks: Union[str, List[str]]
    auto: bool = True
    note: str = ""
    enabled: bool = True


class RoutePatch(BaseModel):
    public_id: Optional[str] = None
    fallbacks: Optional[Union[str, List[str]]] = None
    auto: Optional[bool] = None
    enabled: Optional[bool] = None
    note: Optional[str] = None


# ----------------------------------------------------------------- extensions
class ExtensionIn(BaseModel):
    kind: str
    name: str
    description: str = ""
    config: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ExtensionPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


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
        "fallback": {
            "auto": config.AUTO_FALLBACK,
            "spoof_model": config.SPOOF_MODEL,
            "max": config.FALLBACK_MAX,
        },
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
    return store.create_client_key(body.name, body.allowed_models, body.rpm_limit, body.tpd_limit)


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
    capability: str = "",
    limit: int = 500,
):
    require_admin(request)
    rows = store.list_models(
        provider_id=provider_id, status=status, free_only=free_only,
        search=search, capability=capability,
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


# ----------------------------------------------------------------- model routes (fallbacks)
@router.get("/routes")
async def get_routes(request: Request):
    require_admin(request)
    return store.list_routes()


@router.post("/routes")
async def create_route(request: Request, body: RouteIn):
    """Register a fallback chain: public_id -> ordered fallback models."""
    require_admin(request)
    try:
        rid = store.add_route(body.public_id, body.fallbacks, body.auto, body.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001 - unique violation etc.
        raise HTTPException(status_code=400, detail=f"route '{body.public_id}' already exists")
    if not body.enabled:
        store.update_route(rid, enabled=False)
    return {"id": rid}


@router.patch("/routes/{rid}")
async def patch_route(request: Request, rid: int, body: RoutePatch):
    require_admin(request)
    if not db.one("SELECT id FROM model_routes WHERE id=?", (rid,)):
        raise HTTPException(status_code=404, detail="route not found")
    try:
        store.update_route(rid, **body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/routes/{rid}")
async def remove_route(request: Request, rid: int):
    require_admin(request)
    store.delete_route(rid)
    return {"ok": True}


@router.get("/routes/preview")
async def preview_route(request: Request, model: str = ""):
    """What would happen for a request asking for `model`: stages laid bare."""
    require_admin(request)
    if not model:
        raise HTTPException(status_code=400, detail="query param 'model' is required")

    chain, allow_auto = store.fallback_chain(model)
    direct = store.resolve_model(model)
    stages = []
    if direct:
        stages.append({"label": model, "models": [m for _p, m in direct]})
    for fid in chain:
        cands = store.resolve_model(fid)
        if cands:
            stages.append({"label": fid, "models": [m for _p, m in cands]})
    auto = []
    if allow_auto and config.AUTO_FALLBACK:
        auto = store.auto_fallback_targets(model, limit=config.FALLBACK_MAX)
    # media routing preview: capabilities of the requested model + which
    # stand-ins would take over when a request carries media it can't read
    caps = store.model_row_capabilities(model)
    media_needs = [k for k in ("vision", "audio_in") if not caps.get(k)]
    media_targets = []
    if config.MEDIA_ROUTING and media_needs:
        media_targets = store.auto_fallback_targets(
            model, limit=config.FALLBACK_MAX,
            need_caps={k: True for k in media_needs},
        )
    return {
        "model": model,
        "direct": bool(direct),
        "explicit_chain": chain,
        "auto_allowed": allow_auto and config.AUTO_FALLBACK,
        "auto_targets": auto,
        "stages": stages,
        "spoof_model": config.SPOOF_MODEL,
        "media_routing": {
            "enabled": config.MEDIA_ROUTING,
            "model_caps": caps,
            "blind_spots": media_needs,
            "stand_ins": media_targets,
        },
    }


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


# ----------------------------------------------------------------- extensions
@router.get("/extensions")
async def get_extensions(request: Request, kind: Optional[str] = None, reveal: bool = False):
    require_admin(request)
    return extensions.list_extensions(kind, reveal=reveal)


@router.post("/extensions")
async def create_extension(request: Request, body: ExtensionIn):
    require_admin(request)
    try:
        eid = extensions.add_extension(body.kind, body.name, body.description, body.config, body.enabled)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    out = {"id": eid, "kind": body.kind}
    if body.kind == "mcp":
        try:
            res = await extensions.refresh_mcp_tools(eid)
            out.update(res)
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)[:200]
    return out


@router.patch("/extensions/{eid}")
async def patch_extension(request: Request, eid: int, body: ExtensionPatch):
    require_admin(request)
    ext = extensions.get_extension(eid)
    if not ext:
        raise HTTPException(status_code=404, detail="extension not found")
    fields = body.model_dump(exclude_none=True)
    if "config" in fields:
        fields["config"] = extensions.preserve_secret(fields["config"], ext.get("config") or {})
    try:
        extensions.update_extension(eid, **fields)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/extensions/{eid}")
async def remove_extension(request: Request, eid: int):
    require_admin(request)
    if not extensions.get_extension(eid):
        raise HTTPException(status_code=404, detail="extension not found")
    extensions.delete_extension(eid)
    return {"ok": True}


@router.post("/extensions/{eid}/refresh")
async def refresh_extension(request: Request, eid: int):
    """MCP: re-run tools/list and replace stored tools. Skill: re-sync its tool."""
    require_admin(request)
    ext = extensions.get_extension(eid)
    if not ext:
        raise HTTPException(status_code=404, detail="extension not found")
    if ext["kind"] == "mcp":
        return await extensions.refresh_mcp_tools(eid)
    if ext["kind"] == "skill":
        return {"tools": extensions.sync_skill_tool(eid), "error": ""}
    return {"tools": 0, "error": "plugins have no auto-discovery; tools are defined by you"}


@router.patch("/extensions/{eid}/tools/{tid}")
async def patch_ext_tool(request: Request, eid: int, tid: int, enabled: bool):
    require_admin(request)
    extensions.set_tool_enabled(tid, enabled)
    return {"ok": True}


@router.delete("/extensions/{eid}/tools/{tid}")
async def remove_ext_tool(request: Request, eid: int, tid: int):
    require_admin(request)
    extensions.delete_tool(tid)
    return {"ok": True}


@router.post("/extensions/{eid}/tools")
async def add_ext_tool(request: Request, eid: int):
    """Manually register a plugin tool (JSON body: name, description, parameters)."""
    require_admin(request)
    if not extensions.get_extension(eid):
        raise HTTPException(status_code=404, detail="extension not found")
    try:
        body = await request.json()
        tid = extensions.register_tool(
            eid,
            body.get("name") or "",
            body.get("description") or "",
            body.get("parameters") or {"type": "object", "properties": {}},
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": tid}


@router.post("/extensions/{eid}/test")
async def test_extension(request: Request, eid: int):
    """Fire one real call against the first enabled tool of the extension."""
    require_admin(request)
    ext = extensions.get_extension(eid)
    if not ext:
        raise HTTPException(status_code=404, detail="extension not found")
    tools = [t for t in ext.get("tools", []) if t.get("enabled")]
    if not tools:
        raise HTTPException(status_code=400, detail="extension has no enabled tools")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - empty body is fine
        body = {}
    args = body.get("arguments") if isinstance(body, dict) else None
    ok, content = await extensions.call_tool(tools[0]["tool_name"], args)
    return {"ok": ok, "output": content[:2000]}


@router.post("/extensions/import")
async def import_extensions(request: Request):
    """Bulk import extensions from a JSON payload (dashboard upload)."""
    require_admin(request)
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid JSON")
    items = payload.get("extensions") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="expected {extensions:[...]} or [...]")

    added = {"extensions": 0, "tools": 0, "errors": []}
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        name = str(item.get("name") or "").strip()
        cfg = item.get("config") or {}
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except ValueError:
                cfg = {}
        try:
            eid = extensions.add_extension(
                kind, name, item.get("description") or "", cfg,
                bool(item.get("enabled", True)),
            )
            added["extensions"] += 1
        except Exception as e:  # noqa: BLE001
            added["errors"].append(f"{name}: {e}"[:200])
            continue
        if kind == "mcp":
            try:
                res = await extensions.refresh_mcp_tools(eid)
                added["tools"] += res.get("tools", 0)
                if res.get("error"):
                    added["errors"].append(f"{name}: {res['error']}"[:200])
            except Exception as e:  # noqa: BLE001
                added["errors"].append(f"{name} refresh: {e}"[:200])
        elif kind == "skill":
            added["tools"] += 1
        elif kind == "plugin" and isinstance(item.get("tools"), list):
            for t in item["tools"]:
                if isinstance(t, dict) and t.get("name"):
                    try:
                        extensions.register_tool(eid, t["name"], t.get("description") or "",
                                                 t.get("parameters"))
                        added["tools"] += 1
                    except Exception:  # noqa: BLE001
                        continue
    return added


@router.get("/extensions/export")
async def export_extensions(request: Request):
    """Export full extension configs (secrets included) for backup/migration."""
    require_admin(request)
    rows = extensions.list_extensions(reveal=True)
    return {"extensions": rows}


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