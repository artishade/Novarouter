"""Build API — permanent cloud terminal configuration surface (/api/build).

  GET  /info     → what the standalone /terminal page renders: identity,
                   tools (permanently registered agent toolbox), providers
                   (permanent cloud build targets + custom ones), engine
                   runtime, and the live PTY session snapshot.
  GET  /providers → permanent build-target catalogue with saved credentials
                    masked (never returns secret values to the browser).
  POST /providers  {id | name,type, config…} → upsert config for a permanent
                    preset, or add a new custom build target.
  POST /tools/register → {ok, tools} — re-run the permanent registration.
"""
from __future__ import annotations

import asyncio
import json
import logging
import platform

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from nova.build_registry import BUILD_PROVIDER_BY_ID, BUILD_PROVIDERS, ensure_build_providers
from nova.database import get_db
from nova.models import StorageProviderRow
from nova.storage_lib import parse_config, slugify
from routers.agent_api import TOOLS

log = logging.getLogger("nova.build_api")

# Reasoning-effort choices surfaced in the workspace chatbox. Values pass
# through the gateway verbatim (`reasoning_effort` is already in the gateway's
# PASSTHROUGH_FIELDS), so OpenAI-compatible and native Anthropic upstreams
# both honour them.
REASONING_EFFORTS = [
    {"id": "minimal", "label": "Minimal", "hint": "fastest, no extended thinking"},
    {"id": "low", "label": "Low", "hint": "light reasoning"},
    {"id": "medium", "label": "Medium", "hint": "balanced (default)"},
    {"id": "high", "label": "High", "hint": "deepest reasoning, slower"},
]

router = APIRouter()

_SECRET_FIELDS = {"access_key", "secret_key", "token", "api_key", "password"}


def _masked(value: str) -> str:
    if len(value) > 12:
        return f"{value[:4]}…{value[-4:]}"
    return "••••••"


@router.get("/info")
def build_info(db: Session = Depends(get_db)):
    from nova.agent import TOOLS_DOC  # local import — agent module is heavy

    ensure_build_providers(db)
    rows = db.scalars(
        select(StorageProviderRow).order_by(StorageProviderRow.createdAt.asc())
    ).all()

    providers: list[dict] = []
    for preset in BUILD_PROVIDERS:
        row = db.get(StorageProviderRow, preset["id"])
        cfg = parse_config(row.config) if row is not None else {}
        fields = [
            {
                "name": f["name"],
                "label": f["label"],
                "placeholder": f.get("placeholder", ""),
                "required": bool(f.get("required")),
                "secret": bool(f.get("secret")),
                "value": _masked(str(cfg.get(f["name"]))) if f["name"] in _SECRET_FIELDS and cfg.get(f["name"]) else (cfg.get(f["name"]) or ""),
            }
            for f in preset["fields"]
        ]
        providers.append({
            "id": preset["id"],
            "name": preset["name"],
            "type": preset["type"],
            "icon": preset["icon"],
            "docs_url": preset["docs_url"],
            "free_tier": preset["free_tier"],
            "status": (row.status if row is not None else "pending"),
            "active": bool(row.active) if row is not None else False,
            "configured": any(cfg.get(f["name"]) for f in preset["fields"]),
            "fields": fields,
        })

    custom = [
        {
            "id": row.id,
            "name": row.name,
            "type": row.type,
            "icon": "cloud",
            "docs_url": row.docsUrl,
            "free_tier": row.freeTier,
            "status": row.status,
            "active": bool(row.active),
            "configured": bool(parse_config(row.config)),
            "fields": [],
        }
        for row in rows
        if row.id not in BUILD_PROVIDER_BY_ID and row.id != "local_disk"
    ]

    runtime = platform.python_implementation() + " " + platform.python_version()
    return JSONResponse({
        "identity": {"prompt": "~ Root@Build", "os": "Debian Linux", "access": "root"},
        "engine": {"runtime": runtime},
        "tools": [
            {"id": t.get("id"), "name": t.get("name"), "description": TOOLS_DOC.get(t.get("id"), t.get("description")), "icon": t.get("icon")}
            for t in TOOLS
        ],
        "providers": providers + custom,
    })


@router.post("/providers")
async def save_build_provider(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        body = {}

    key = body.get("id")
    if isinstance(key, str) and key in BUILD_PROVIDER_BY_ID:
        preset = BUILD_PROVIDER_BY_ID[key]
        row = db.get(StorageProviderRow, key)
        if row is None:
            ensure_build_providers(db)
            row = db.get(StorageProviderRow, key)
        if row is None:
            return JSONResponse({"error": "Could not prepare that build target"}, status_code=500)
        row.name = preset["name"]
        row.type = preset["type"]
        if isinstance(row.docsUrl, str) is False and preset["docs_url"]:
            row.docsUrl = preset["docs_url"]
        provider_key = key
    else:
        # Custom build target — same shape as the storage custom provider.
        name = (body.get("name") or "").strip() if isinstance(body.get("name"), str) else ""
        if not name:
            return JSONResponse({"error": "name is required for a new build target"}, status_code=400)
        slug = slugify(name)
        provider_key = slug
        i = 2
        while db.get(StorageProviderRow, provider_key) is not None:
            provider_key = f"{slug}-{i}"
            i += 1
        row = StorageProviderRow(
            id=provider_key,
            name=name,
            type=(body.get("type") or "s3_compatible") if isinstance(body.get("type"), str) else "s3_compatible",
            status="pending",
            isBuiltin=False,
            active=False,
        )
        db.add(row)

    incoming = body.get("config") if isinstance(body.get("config"), dict) else {}
    clean: dict = {}
    for k, v in incoming.items():
        if not isinstance(k, str):
            continue
        if not isinstance(v, str):
            continue
        if k in _SECRET_FIELDS and (not v.strip() or "•" in v or "…" in v):
            continue  # never persist a masked placeholder back (untouched edit form)
        clean[k.strip()] = v.strip()

    merged = parse_config(row.config)
    merged.update(clean)
    row.config = json.dumps(merged, ensure_ascii=False)
    if clean:
        row.status = "connected"
        row.lastError = None
    db.commit()
    return JSONResponse({"ok": True, "id": provider_key, "status": row.status})


@router.get("/models")
async def build_models(provider_id: int | None = None):
    """Picker data for the workspace chatbox: every enabled model grouped by
    provider, plus the reasoning-effort choices."""
    from nova.models import Model as ModelRow
    from nova.database import SessionLocal

    def _load() -> list[dict]:
        with SessionLocal() as db:
            stmt = (
                select(ModelRow)
                .where(ModelRow.enabled.is_(True))
                .options(joinedload(ModelRow.provider))
                .order_by(ModelRow.providerId, ModelRow.exposedId)
            )
            if provider_id is not None:
                stmt = stmt.where(ModelRow.providerId == provider_id)
            rows = db.scalars(stmt).unique().all()
            out: dict[int, dict] = {}
            for m in rows:
                prov = m.provider
                if prov is None:
                    continue
                bucket = out.setdefault(
                    prov.id,
                    {"provider_id": prov.id, "provider_name": prov.name, "color": prov.color, "models": []},
                )
                bucket["models"].append({
                    "exposed_id": m.exposedId,
                    "display_name": m.displayName,
                    "is_free": bool(m.isFree),
                    "reasoning": bool((m.capabilities or "").find('"reasoning": true') >= 0),
                })
            return list(out.values())

    try:
        groups = await asyncio.to_thread(_load)
    except Exception:
        # Never break the chatbox on a transient DB hiccup, but keep the
        # failure visible — a silent [] here once hid a missing import.
        log.exception("/api/build/models: model picker load failed")
        groups = []
    return JSONResponse({"groups": groups, "reasoning_efforts": REASONING_EFFORTS})


@router.post("/tools/register")
def register_tools(db: Session = Depends(get_db)):
    from nova.build_registry import register_builtin_tools

    register_builtin_tools(db)
    return JSONResponse({"ok": True, "tools": [t.get("id") for t in TOOLS]})
