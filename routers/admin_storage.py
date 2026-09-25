"""Admin storage manager — Python port of src/app/api/admin/storage/**.

Endpoints (mounted under /api/admin/storage by main.py):
  GET  /info               → StorageInfo (active provider, providers, files, usage)
  POST /config             {provider_key, config?}  → set active provider
  POST /connect            {provider_key, config}   → status connected
  POST /disconnect         {provider_key}           → status disconnected
  POST /providers          {name, type, …}          → add custom provider
  POST /test-connection    {provider_key}           → {ok, status, latency_ms, message}
  GET  /files              → file list (same shape as info.files)*
  POST /files              multipart FormData upload OR JSON {name, mime, data_base64}
  GET  /files/{file_id}    → {ok, file:{name, mime, data_base64}}
  DELETE /files/{file_id}  → {ok}
  POST /backup             → gateway state snapshot stored as a StorageFile

* The TypeScript files/route.ts only exported POST (the dashboard lists files
  via /info). The port spec calls for a GET list too, so a real one is added;
  its shape is identical to info.files.

Files are always stored as StorageFile rows with base64 payloads (the TS
backend never implemented direct cloud uploads — see nova/storage_lib.py).
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from nova.models import (
    ClientKey,
    Model,
    ModelRoute,
    Provider,
    StorageFile,
    StorageProviderRow,
    SystemConfig,
    utcnow,
)
from nova.storage_lib import (
    CONFIG_FIELDS,
    MAX_FILE_BYTES,
    b64_to_bytes,
    bytes_to_b64,
    ensure_local_disk,
    js_round,
    map_storage_file,
    map_storage_provider,
    mask_secret,
    merge_config,
    parse_config,
    slugify,
    to_iso_z,
    wire_num,
)
from nova.database import get_db

router = APIRouter()


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

async def _json_body(request: Request) -> tuple[dict | None, JSONResponse | None]:
    """Parse the JSON body like the TS storage routes: any parse failure is a
    400 `Invalid JSON body`; a valid non-object body behaves as {} downstream
    (object property access in TS yields undefined)."""
    try:
        body = await request.json()
    except Exception:
        return None, JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    return (body if isinstance(body, dict) else {}), None


def _str_field(body: dict, key: str) -> str:
    value = body.get(key)
    return value if isinstance(value, str) else ""


def _unknown_provider(key: str) -> JSONResponse:
    return JSONResponse({"error": f"Unknown storage provider: {key}"}, status_code=404)


def _active_provider_row(db: Session) -> StorageProviderRow | None:
    return db.scalars(
        select(StorageProviderRow).where(StorageProviderRow.active.is_(True))
    ).first()


# --------------------------------------------------------------------------- #
# GET /info → StorageInfo
# --------------------------------------------------------------------------- #

@router.get("/info")
def storage_info(db: Session = Depends(get_db)):
    ensure_local_disk(db)
    providers = db.scalars(
        select(StorageProviderRow).order_by(StorageProviderRow.createdAt.asc())
    ).all()
    files = db.scalars(select(StorageFile).order_by(StorageFile.createdAt.desc())).all()

    active = next((p for p in providers if p.active), None) or next(
        (p for p in providers if p.id == "local_disk"), None
    )
    active_key = active.id if active else "local_disk"

    active_files = [f for f in files if f.providerKey == active_key]
    total_mb = js_round(sum(f.size for f in active_files) / 1048576 * 100) / 100
    quota_mb = active.quotaMb if active else 0
    pct = min(100, js_round(total_mb / quota_mb * 10000) / 100) if quota_mb and quota_mb > 0 else 0

    return JSONResponse({
        "active_provider": active_key,
        "providers": [map_storage_provider(p) for p in providers],
        "files": [map_storage_file(f) for f in files],
        "usage": {
            "files": len(active_files),
            "total_mb": wire_num(total_mb),
            "quota_mb": wire_num(float(quota_mb or 0)),
            "pct": wire_num(pct),
        },
    })


# --------------------------------------------------------------------------- #
# POST /config {provider_key, config?} → set active provider
# --------------------------------------------------------------------------- #

@router.post("/config")
async def set_active_provider(request: Request, db: Session = Depends(get_db)):
    body, err = await _json_body(request)
    if err is not None:
        return err
    key = _str_field(body, "provider_key")
    if not key:
        return JSONResponse({"error": "provider_key is required"}, status_code=400)

    if key == "local_disk":
        ensure_local_disk(db)
    row = db.get(StorageProviderRow, key)
    if row is None:
        return _unknown_provider(key)

    row.active = True
    incoming = body.get("config")
    if isinstance(incoming, dict):
        merged = merge_config(parse_config(row.config), incoming)
        row.config = json.dumps(merged, ensure_ascii=False)

    db.query(StorageProviderRow).filter(StorageProviderRow.id != key).update(
        {"active": False}, synchronize_session=False
    )
    db.commit()

    return JSONResponse({"ok": True, "active_provider": key})


# --------------------------------------------------------------------------- #
# POST /connect {provider_key, config} → status connected
# --------------------------------------------------------------------------- #

@router.post("/connect")
async def connect_provider(request: Request, db: Session = Depends(get_db)):
    body, err = await _json_body(request)
    if err is not None:
        return err
    key = _str_field(body, "provider_key")
    if not key:
        return JSONResponse({"error": "provider_key is required"}, status_code=400)

    if key == "local_disk":
        ensure_local_disk(db)
    row = db.get(StorageProviderRow, key)
    if row is None:
        return _unknown_provider(key)

    incoming = body.get("config") if isinstance(body.get("config"), dict) else {}
    merged = merge_config(parse_config(row.config), incoming)

    row.config = json.dumps(merged, ensure_ascii=False)
    row.status = "connected"
    row.lastError = None
    db.commit()

    # Only string/number config fields count (JS typeof check — booleans don't).
    field_count = sum(
        1
        for k in incoming
        if isinstance(merged.get(k), (str, int, float)) and not isinstance(merged.get(k), bool)
    )
    suffix = "" if field_count == 1 else "s"
    return JSONResponse({
        "ok": True,
        "status": "connected",
        "message": f"{row.name} connected — {field_count} config field{suffix} saved",
    })


# --------------------------------------------------------------------------- #
# POST /disconnect {provider_key} → {ok}
# --------------------------------------------------------------------------- #

@router.post("/disconnect")
async def disconnect_provider(request: Request, db: Session = Depends(get_db)):
    body, err = await _json_body(request)
    if err is not None:
        return err
    key = _str_field(body, "provider_key")
    if not key:
        return JSONResponse({"error": "provider_key is required"}, status_code=400)

    if key == "local_disk":
        ensure_local_disk(db)
    row = db.get(StorageProviderRow, key)
    if row is None:
        return _unknown_provider(key)

    # local_disk is always mounted — it keeps its active flag.
    row.status = "disconnected"
    if row.id != "local_disk":
        row.active = False
    db.commit()

    return JSONResponse({"ok": True})


# --------------------------------------------------------------------------- #
# POST /providers — add a custom storage provider
# --------------------------------------------------------------------------- #

@router.post("/providers")
async def add_custom_provider(request: Request, db: Session = Depends(get_db)):
    body, err = await _json_body(request)
    if err is not None:
        return err

    raw_name = body.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)

    raw_type = body.get("type")
    provider_type = raw_type.strip() if isinstance(raw_type, str) and raw_type.strip() else "webdav"

    # Unique provider key: slug + numeric suffix when needed.
    slug = slugify(name)
    provider_id = slug
    i = 2
    while db.get(StorageProviderRow, provider_id) is not None:
        provider_id = f"{slug}-{i}"
        i += 1

    def opt(value) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    config: dict[str, str] = {}
    for field in ("endpoint", "bucket_name", "access_key", "secret_key", "region"):
        value = opt(body.get(field))
        if value:
            config[field] = value
    region = opt(body.get("region"))

    db.add(StorageProviderRow(
        id=provider_id,
        name=name,
        type=provider_type,
        config=json.dumps(config, ensure_ascii=False),
        status="pending",
        isBuiltin=False,
        active=False,
        freeTier=opt(body.get("free_tier")),
        region=region,
    ))
    db.commit()

    return JSONResponse({"ok": True, "provider_key": provider_id})


# --------------------------------------------------------------------------- #
# POST /test-connection {provider_key} → {ok, status, latency_ms, message}
# --------------------------------------------------------------------------- #

@router.post("/test-connection")
async def test_connection(request: Request, db: Session = Depends(get_db)):
    body, err = await _json_body(request)
    if err is not None:
        return err
    key = _str_field(body, "provider_key")
    if not key:
        return JSONResponse({"error": "provider_key is required"}, status_code=400)

    if key == "local_disk":
        ensure_local_disk(db)
    row = db.get(StorageProviderRow, key)
    if row is None:
        return _unknown_provider(key)

    is_builtin = bool(row.isBuiltin) or row.type in ("local", "builtin_cloud")
    latency = random.randint(2, 12) if is_builtin else random.randint(40, 400)

    config = parse_config(row.config)
    has_config = any(
        k in CONFIG_FIELDS and isinstance(v, str) and v.strip() for k, v in config.items()
    )

    if is_builtin:
        ok, status = True, "connected"
        message = f"{row.name} is a built-in provider — always available on this host ({latency}ms)"
    elif has_config:
        ok, status = True, "connected"
        message = f"{row.name} connection healthy — credentials accepted ({latency}ms round trip)"
    else:
        ok, status = False, "missing_config"
        message = "Add credentials first — use Connect"

    row.lastTestAt = utcnow()
    row.lastError = None if ok else "missing_config — no credentials saved"
    db.commit()

    return JSONResponse({"ok": ok, "status": status, "latency_ms": latency, "message": message})


# --------------------------------------------------------------------------- #
# GET /files — file list (same shape as info.files; see module docstring)
# --------------------------------------------------------------------------- #

@router.get("/files")
def list_files(db: Session = Depends(get_db)):
    rows = db.scalars(select(StorageFile).order_by(StorageFile.createdAt.desc())).all()
    return JSONResponse([map_storage_file(r) for r in rows])


# --------------------------------------------------------------------------- #
# POST /files — multipart FormData (file[, provider_key]) OR JSON upload
# --------------------------------------------------------------------------- #

@router.post("/files")
async def upload_file(request: Request, db: Session = Depends(get_db)):
    content_type = request.headers.get("content-type", "")
    provider_key_input: str | None = None
    name: str
    mime: str
    b64: str
    size: int

    if "multipart/form-data" in content_type:
        try:
            form = await request.form()
        except Exception:
            return JSONResponse({"error": "Invalid multipart body"}, status_code=400)
        file = form.get("file")
        if file is None or isinstance(file, str):
            return JSONResponse(
                {"error": 'No file provided — attach a "file" field'}, status_code=400
            )
        pk = form.get("provider_key")
        provider_key_input = pk.strip() if isinstance(pk, str) and pk.strip() else None

        payload = await file.read()
        size = len(payload)
        b64 = bytes_to_b64(payload)
        name = file.filename or "upload.bin"
        mime = file.content_type or "application/octet-stream"
    else:
        body, err = await _json_body(request)
        if err is not None:
            return err
        raw_name = body.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            return JSONResponse({"error": "name is required"}, status_code=400)
        raw_b64 = body.get("data_base64")
        if not isinstance(raw_b64, str) or len(raw_b64) == 0:
            return JSONResponse({"error": "data_base64 is required"}, status_code=400)
        raw_mime = body.get("mime")
        mime = raw_mime if isinstance(raw_mime, str) and raw_mime else "application/octet-stream"
        pk = body.get("provider_key")
        provider_key_input = pk.strip() if isinstance(pk, str) and pk.strip() else None

        name = raw_name.strip()
        payload = b64_to_bytes(raw_b64)
        size = len(payload)
        b64 = bytes_to_b64(payload)

    if size > MAX_FILE_BYTES:
        return JSONResponse(
            {"error": "File too large — sandbox limit is 5 MB"}, status_code=413
        )

    ensure_local_disk(db)
    if provider_key_input:
        if db.get(StorageProviderRow, provider_key_input) is None:
            return _unknown_provider(provider_key_input)
        provider_key = provider_key_input
    else:
        active = _active_provider_row(db)
        provider_key = active.id if active else "local_disk"

    created = StorageFile(
        name=name, path="/", size=size, mime=mime, providerKey=provider_key, data=b64
    )
    db.add(created)
    db.commit()

    return JSONResponse({
        "ok": True,
        "file": {"id": created.id, "name": created.name, "size": created.size, "mime": created.mime},
    })


# --------------------------------------------------------------------------- #
# GET/DELETE /files/{file_id}
# --------------------------------------------------------------------------- #

def _parse_file_id(raw: str) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


@router.get("/files/{file_id}")
def download_file(file_id: str, db: Session = Depends(get_db)):
    fid = _parse_file_id(file_id)
    if fid is None:
        return JSONResponse({"error": "Invalid file id"}, status_code=400)

    row = db.get(StorageFile, fid)
    if row is None:
        return JSONResponse({"error": "File not found"}, status_code=404)

    return JSONResponse({
        "ok": True,
        "file": {"name": row.name, "mime": row.mime, "data_base64": row.data or ""},
    })


@router.delete("/files/{file_id}")
def delete_file(file_id: str, db: Session = Depends(get_db)):
    fid = _parse_file_id(file_id)
    if fid is None:
        return JSONResponse({"error": "Invalid file id"}, status_code=400)

    row = db.get(StorageFile, fid)
    if row is None:
        return JSONResponse({"error": "File not found"}, status_code=404)

    db.delete(row)
    db.commit()
    return JSONResponse({"ok": True})


# --------------------------------------------------------------------------- #
# POST /backup — snapshot gateway state as a JSON StorageFile
# --------------------------------------------------------------------------- #

@router.post("/backup")
def create_backup(db: Session = Depends(get_db)):
    ensure_local_disk(db)
    providers = db.scalars(
        select(Provider)
        .options(joinedload(Provider.keys))
        .order_by(Provider.priority.asc(), Provider.id.asc())
    ).unique().all()
    models = db.scalars(select(Model).options(joinedload(Model.provider))).all()
    routes = db.scalars(select(ModelRoute).order_by(ModelRoute.id.asc())).all()
    client_keys = db.scalars(select(ClientKey).order_by(ClientKey.createdAt.asc())).all()
    config_rows = db.scalars(select(SystemConfig).order_by(SystemConfig.key.asc())).all()
    active_row = _active_provider_row(db)
    fallback_row = db.get(StorageProviderRow, "local_disk")

    active_provider = active_row or fallback_row
    provider_key = active_provider.id if active_provider else "local_disk"
    provider_name = active_provider.name if active_provider else "NovaVault (Local Disk)"

    snapshot = {
        "providers": [
            {
                "key": p.key,
                "name": p.name,
                "kind": p.kind,
                "base_url": p.baseUrl,
                "prefix": p.prefix,
                "priority": p.priority,
                "enabled": p.enabled,
                "color": p.color,
                "free_tier": p.freeTier,
                "keys": [
                    {
                        "label": k.label,
                        "api_key": mask_secret(k.apiKey),
                        "weight": k.weight,
                        "enabled": k.enabled,
                    }
                    for k in p.keys
                ],
            }
            for p in providers
        ],
        "models": [
            {
                "exposed_id": m.exposedId,
                "model_id": m.modelId,
                "provider": m.provider.key if m.provider else "",
                "status": m.status,
                "enabled": m.enabled,
                "is_free": m.isFree,
            }
            for m in models
        ],
        "routes": [
            {
                "public_id": r.publicId,
                "fallbacks": _parse_fallbacks(r.fallbacks),
                "auto": r.auto,
                "enabled": r.enabled,
                "note": r.note,
            }
            for r in routes
        ],
        "client_keys": [
            {
                "name": k.name,
                "token": mask_secret(k.token),
                "enabled": k.enabled,
                "rpm_limit": k.rpmLimit,
                "tpd_limit": k.tpdLimit,
            }
            for k in client_keys
        ],
        "config": {c.key: c.value for c in config_rows},
        "exported_at": to_iso_z(datetime.now(timezone.utc)),
    }

    payload = json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8")
    size = len(payload)
    now = datetime.now(timezone.utc)
    file_name = (
        f"backup-{now.year:04d}-{now.month:02d}-{now.day:02d}-"
        f"{now.hour:02d}{now.minute:02d}.json"
    )

    db.add(StorageFile(
        name=file_name,
        path="/backups",
        size=size,
        mime="application/json",
        providerKey=provider_key,
        data=bytes_to_b64(payload),
    ))
    db.commit()

    return JSONResponse({
        "ok": True,
        "file_name": file_name,
        "size_bytes": size,
        "provider": provider_key,
        "message": (
            f"Backup snapshot saved to {provider_name} — {size / 1024:.1f} KB · "
            f"{len(models)} models · {len(routes)} routes"
        ),
    })


def _parse_fallbacks(raw: str) -> list:
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []
