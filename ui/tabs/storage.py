"""Storage tab — buckets, files and backups.

Python/HTMX port of src/components/nova/tabs/StorageTab.tsx. All data comes
from /api/admin/storage/** (info/providers/files/backup); every mutation
forwards there and answers with HTMX toasts + fragment refreshes, so error
messages and wire shapes stay byte-identical with the JSON API.

Endpoints:
  GET    /partials/tab/storage        main fragment (banner + provider grid + files)
  GET    /ui/storage/connect-form     per-provider credential <dialog> partial
  GET    /ui/storage/custom-form      add-custom-provider <dialog> partial
  POST   /ui/storage/connect          → POST /api/admin/storage/connect
  POST   /ui/storage/disconnect       → POST /api/admin/storage/disconnect
  POST   /ui/storage/default          → POST /api/admin/storage/config (active provider)
  POST   /ui/storage/test             → POST /api/admin/storage/test-connection
  POST   /ui/storage/upload           multipart → POST /api/admin/storage/files (5 MB cap)
  POST   /ui/storage/backup           → POST /api/admin/storage/backup (receipt dialog)
  POST   /ui/storage/custom-provider  → POST /api/admin/storage/providers
  DELETE /ui/storage/files/{file_id}  → DELETE /api/admin/storage/files/{file_id}
"""
from __future__ import annotations

import asyncio
import math
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ui.api_client import ApiError, api
from ui.format import fmt_bytes
from ui.render import form_dict, html, render

router = APIRouter(tags=["ui:storage"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # storage endpoint hard cap (5 MB)

# CONNECT_DEFS — verbatim port of the TSX CONNECT_DEFS catalogue.
CONNECT_DEFS: dict[str, dict[str, Any]] = {
    "firebase": {
        "fields": [
            {"key": "project_id", "label": "Project ID", "placeholder": "nova-router"},
            {"key": "api_key", "label": "Web API Key", "password": True, "placeholder": "AIza…"},
            {"key": "bucket_name", "label": "Storage Bucket", "placeholder": "nova-router.appspot.com"},
        ],
        "link_label": "Open Firebase Console",
        "note": "Sign in to the Firebase console with the Google account that owns the project, then copy the Web API key from Project Settings.",
    },
    "supabase": {
        "fields": [
            {"key": "project_url", "label": "Project URL", "placeholder": "https://xyzcompany.supabase.co"},
            {"key": "anon_key", "label": "Anon Key", "password": True, "placeholder": "eyJhbGciOi…"},
            {"key": "bucket_name", "label": "Bucket Name", "placeholder": "nova-storage"},
        ],
        "link_label": "Open Supabase Dashboard",
        "note": "Find the project URL and anon key under Project Settings → API in the Supabase dashboard.",
    },
    "cloudflare_r2": {
        "fields": [
            {"key": "account_id", "label": "Account ID", "placeholder": "32-char hex id"},
            {"key": "access_key_id", "label": "Access Key ID"},
            {"key": "secret_access_key", "label": "Secret Access Key", "password": True},
            {"key": "bucket_name", "label": "Bucket Name", "placeholder": "nova"},
        ],
        "link_label": "Open Cloudflare R2 Dashboard",
        "note": "Create an R2 API token with Object Read & Write permission and paste the access key pair here.",
    },
    "backblaze_b2": {
        "fields": [
            {"key": "key_id", "label": "Key ID"},
            {"key": "application_key", "label": "Application Key", "password": True},
            {"key": "bucket_name", "label": "Bucket Name", "placeholder": "nova-backups"},
        ],
        "link_label": "Open Backblaze B2 Console",
        "note": "Create a Master or limited Application Key in the B2 console — application keys are shown only once.",
    },
    "github": {
        "fields": [
            {"key": "username", "label": "Username", "placeholder": "octocat"},
            {"key": "personal_access_token", "label": "Personal Access Token", "password": True, "placeholder": "ghp_…"},
            {"key": "repo", "label": "Repository (owner/name)", "placeholder": "octocat/nova-storage"},
        ],
        "link_label": "Create token on GitHub",
        "note": "Generate a fine-grained personal access token with Contents read & write permission for the storage repository.",
    },
}

# CUSTOM_TYPES + EMPTY_CUSTOM defaults from the TSX custom-provider dialog.
CUSTOM_TYPES = ["s3", "supabase", "firebase", "github", "webdav", "local"]
CUSTOM_FIELDS = [
    ("name", "Name", "My S3 Bucket"),
    ("endpoint", "Endpoint", "https://s3.us-east-1.amazonaws.com"),
    ("bucket", "Bucket", "nova-storage"),
    ("access_key", "Access Key", ""),
    ("secret_key", "Secret", ""),
    ("region", "Region", "us-east-1"),
    ("free_tier", "Free Tier Note", "5 GB free, no card required"),
]

_PROVIDER_ICONS = {
    "local": "hard-drive",
    "local_disk": "hard-drive",
    "builtin_cloud": "cloud-cog",
    "firebase": "flame",
    "supabase": "database",
    "github": "github",  # rendered as an inline brand SVG (lucide dropped brand icons)
    "webdav": "folder-sync",
    "backblaze_b2": "database",
}


# --------------------------------------------------------------------------- #
# Template helpers (passed into the Jinja context as callables)
# --------------------------------------------------------------------------- #

def provider_icon(ptype: str) -> str:
    return _PROVIDER_ICONS.get(ptype or "", "cloud")


def status_badge(status: str) -> dict[str, str]:
    """statusBadgeCls() parity."""
    mapping = {
        "connected": {"cls": "border-emerald-500/40 bg-emerald-500/10 text-emerald-300", "label": "connected"},
        "pending": {"cls": "border-amber-500/40 bg-amber-500/10 text-amber-300", "label": "pending"},
        "error": {"cls": "border-rose-500/40 bg-rose-500/10 text-rose-300", "label": "error"},
    }
    return mapping.get(status) or {
        "cls": "border-slate-600/50 bg-slate-500/10 text-slate-400",
        "label": status or "disconnected",
    }


def file_icon(f: dict) -> str:
    """fileIconFor() parity."""
    mime = (f.get("mime") or "").lower()
    name = (f.get("name") or "").lower()
    if "json" in mime or name.endswith(".json"):
        return "braces"
    if "csv" in mime or name.endswith(".csv"):
        return "table"
    if "markdown" in mime or name.endswith(".md"):
        return "file-text"
    return "file"


def connect_def_for(p: dict) -> dict | None:
    return CONNECT_DEFS.get(p.get("type") or "") or CONNECT_DEFS.get(p.get("id") or "")


def js_round_pct(x: float) -> int:
    """Math.round + min/max clamp parity for percentage displays."""
    return max(0, min(100, int(math.floor(x + 0.5))))


def _toast(message: str, kind: str) -> dict[str, str]:
    return {"message": message, "type": kind}


async def _safe_get(path: str, **kw: Any) -> Any:
    try:
        return await api.get(path, **kw)
    except Exception:  # noqa: BLE001 — optional payloads degrade to None
        return None


def _err_message(err: BaseException) -> str:
    if isinstance(err, ApiError):
        return err.message
    return f"API request failed: {err.__class__.__name__}"


async def _provider_from_info(key: str) -> dict | None:
    info = await _safe_get("/api/admin/storage/info")
    providers = (info or {}).get("providers") or []
    return next((p for p in providers if p.get("id") == key), None)


# --------------------------------------------------------------------------- #
# GET /partials/tab/storage — main fragment
# --------------------------------------------------------------------------- #

@router.get("/partials/tab/storage")
async def storage_tab() -> HTMLResponse:
    info, meta, stats = await asyncio.gather(
        _safe_get("/api/admin/storage/info"),
        _safe_get("/api/admin/meta"),
        _safe_get("/api/admin/stats"),
    )
    meta_d = meta if isinstance(meta, dict) else None
    stats_d = stats if isinstance(stats, dict) else None
    if not isinstance(info, dict):
        # TSX: "Storage telemetry unavailable — the gateway did not respond."
        return html(
            render(
                "tabs/storage.html",
                error="Storage telemetry unavailable — the gateway did not respond.",
                info=None,
                meta=meta_d,
                stats=stats_d,
                provider_names={},
                card_pcts={},
                usage_pct=0,
                heap_pct=None,
                status_badge=status_badge,
                provider_icon=provider_icon,
                file_icon=file_icon,
                connect_def_for=connect_def_for,
            )
        )

    providers = info.get("providers") or []
    active = next((p for p in providers if p.get("active")), None)
    usage = info.get("usage") or {}
    quota_mb = float((active or {}).get("quota_mb") or 0)
    has_quota = bool(active) and quota_mb > 0
    usage_pct = js_round_pct(float(usage.get("pct") or 0)) if has_quota else 0
    heap_pct = js_round_pct(float((stats_d or {}).get("memory", {}).get("pct") or 0)) if stats_d else None

    card_pcts = {
        p.get("id"): (
            js_round_pct(float(p.get("usage_mb") or 0) / float(p["quota_mb"]) * 100)
            if float(p.get("quota_mb") or 0) > 0
            else 0
        )
        for p in providers
    }
    provider_names = {p.get("id"): p.get("name") or p.get("id") for p in providers}

    return html(
        render(
            "tabs/storage.html",
            error=None,
            info=info,
            meta=meta_d,
            stats=stats_d,
            active=active,
            has_quota=has_quota,
            usage_pct=usage_pct,
            heap_pct=heap_pct,
            provider_names=provider_names,
            card_pcts=card_pcts,
            status_badge=status_badge,
            provider_icon=provider_icon,
            file_icon=file_icon,
            connect_def_for=connect_def_for,
        )
    )


# --------------------------------------------------------------------------- #
# GET /ui/storage/connect-form — per-provider credential dialog
# --------------------------------------------------------------------------- #

@router.get("/ui/storage/connect-form")
async def storage_connect_form(request: Request) -> HTMLResponse:
    key = request.query_params.get("provider") or ""
    provider = await _provider_from_info(key)
    if provider is None:
        return html("", toast=_toast(f"Unknown storage provider: {key}", "error"))
    d = connect_def_for(provider)
    if d is None:
        return html("", toast=_toast(f"{provider.get('name')} has no credential fields", "error"))
    frag = render(
        "partials/storage_connect.html",
        provider=provider,
        fields=d["fields"],
        note=d["note"],
        link_label=d["link_label"],
        link_url=provider.get("auth_url") or provider.get("docs_url"),
        error=None,
        values={},
    )
    return html(frag)


# --------------------------------------------------------------------------- #
# POST /ui/storage/connect
# --------------------------------------------------------------------------- #

@router.post("/ui/storage/connect")
async def storage_connect(request: Request) -> HTMLResponse:
    form = await request.form()
    key = str(form.get("provider_key") or "")
    provider = await _provider_from_info(key)
    if provider is None:
        return html("", toast=_toast(f"Unknown storage provider: {key}", "error"))
    d = connect_def_for(provider)
    if d is None:
        return html("", toast=_toast(f"{provider.get('name')} has no credential fields", "error"))

    config: dict[str, str] = {}
    values: dict[str, str] = {}
    for f in d["fields"]:
        v = str(form.get(f["key"]) or "").strip()
        values[f["key"]] = v
        if v:
            config[f["key"]] = v

    def frag(error: str | None) -> str:
        return render(
            "partials/storage_connect.html",
            provider=provider,
            fields=d["fields"],
            note=d["note"],
            link_label=d["link_label"],
            link_url=provider.get("auth_url") or provider.get("docs_url"),
            error=error,
            values=values,
        )

    if not config:
        msg = "Fill in at least one credential field before connecting"
        return html(frag(msg), toast=_toast(msg, "error"))
    try:
        await api.post("/api/admin/storage/connect", json={"provider_key": key, "config": config})
    except ApiError as err:
        return html(frag(err.message), toast=_toast(err.message, "error"))
    return html("", toast=_toast(f"{provider.get('name')} connected", "success"), refresh=True)


# --------------------------------------------------------------------------- #
# POST /ui/storage/disconnect  ·  POST /ui/storage/default  ·  POST /ui/storage/test
# --------------------------------------------------------------------------- #

@router.post("/ui/storage/disconnect")
async def storage_disconnect(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    key = str(form.get("provider_key") or "")
    provider = await _provider_from_info(key)
    name = (provider or {}).get("name") or key
    try:
        await api.post("/api/admin/storage/disconnect", json={"provider_key": key})
    except ApiError as err:
        return html("", toast=_toast(err.message, "error"), refresh=True)
    return html("", toast=_toast(f"{name} disconnected", "success"), refresh=True)


@router.post("/ui/storage/default")
async def storage_default(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    key = str(form.get("provider_key") or "")
    provider = await _provider_from_info(key)
    name = (provider or {}).get("name") or key
    try:
        await api.post("/api/admin/storage/config", json={"provider_key": key})
    except ApiError as err:
        return html("", toast=_toast(err.message, "error"), refresh=True)
    return html("", toast=_toast(f"{name} is now the active storage provider", "success"), refresh=True)


@router.post("/ui/storage/test")
async def storage_test(request: Request) -> HTMLResponse:
    form = await form_dict(request)
    key = str(form.get("provider_key") or "")
    provider = await _provider_from_info(key)
    name = (provider or {}).get("name") or key
    try:
        res = await api.post("/api/admin/storage/test-connection", json={"provider_key": key})
    except ApiError as err:
        return html("", toast=_toast(err.message, "error"), refresh=True)
    res = res or {}
    if res.get("ok"):
        return html(
            "",
            toast=_toast(f"{name}: {res.get('status')} in {res.get('latency_ms')} ms", "success"),
            refresh=True,
        )
    return html("", toast=_toast(f"{name}: {res.get('message')}", "error"), refresh=True)


# --------------------------------------------------------------------------- #
# POST /ui/storage/upload — multipart forward (5 MB cap)
# --------------------------------------------------------------------------- #

@router.post("/ui/storage/upload")
async def storage_upload(request: Request) -> HTMLResponse:
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001
        return html("", toast=_toast("Invalid multipart body", "error"))
    file = form.get("file")
    if file is None or isinstance(file, str):
        return html("", toast=_toast('No file provided — attach a "file" field', "error"))

    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        # TSX client-side message; the JSON API 413 has its own wording.
        return html(
            "",
            toast=_toast(f"File exceeds the 5 MB storage upload limit ({fmt_bytes(len(payload))})", "error"),
        )

    filename = file.filename or "upload.bin"
    mime = file.content_type or "application/octet-stream"
    try:
        res = await api.post(
            "/api/admin/storage/files",
            files={"file": (filename, payload, mime)},  # filename preserved
        )
    except ApiError as err:
        return html("", toast=_toast(err.message, "error"))

    created = (res or {}).get("file") or {}
    name = created.get("name") or filename
    size = created.get("size") or len(payload)
    return html("", toast=_toast(f"Uploaded {name} ({fmt_bytes(size)})", "success"), refresh=True)


# --------------------------------------------------------------------------- #
# POST /ui/storage/backup — snapshot + receipt dialog
# --------------------------------------------------------------------------- #

@router.post("/ui/storage/backup")
async def storage_backup() -> HTMLResponse:
    try:
        res = await api.post("/api/admin/storage/backup")
    except ApiError as err:
        return html("", toast=_toast(err.message, "error"))
    res = res or {}
    toast = _toast(
        f"Backup created: {res.get('file_name')} ({fmt_bytes(res.get('size_bytes') or 0)})",
        "success",
    )
    return html(render("partials/storage_receipt.html", receipt=res), toast=toast, refresh=True)


# --------------------------------------------------------------------------- #
# GET /ui/storage/custom-form  ·  POST /ui/storage/custom-provider
# --------------------------------------------------------------------------- #

def _custom_form(values: dict[str, str], error: str | None, sel_type: str) -> HTMLResponse:
    return html(
        render(
            "partials/storage_custom.html",
            fields=CUSTOM_FIELDS,
            custom_types=CUSTOM_TYPES,
            sel_type=sel_type,
            values=values,
            error=error,
        )
    )


@router.get("/ui/storage/custom-form")
async def storage_custom_form() -> HTMLResponse:
    return _custom_form({}, None, CUSTOM_TYPES[0])


@router.post("/ui/storage/custom-provider")
async def storage_custom_provider(request: Request) -> HTMLResponse:
    form = await request.form()
    values = {key: str(form.get(key) or "") for key, _label, _ph in CUSTOM_FIELDS}
    sel_type = str(form.get("type") or CUSTOM_TYPES[0])
    if sel_type not in CUSTOM_TYPES:
        sel_type = CUSTOM_TYPES[0]
    name = values.get("name", "").strip()

    if not name:
        msg = "Provider name is required"
        return html(_custom_form(values, msg, sel_type).body, toast=_toast(msg, "error"))

    payload: dict[str, str] = {"name": name, "type": sel_type}
    # TSX maps empty strings to undefined — empty optionals are omitted.
    for form_key, api_key in (
        ("endpoint", "endpoint"),
        ("bucket", "bucket_name"),
        ("access_key", "access_key"),
        ("secret_key", "secret_key"),
        ("region", "region"),
        ("free_tier", "free_tier"),
    ):
        v = values.get(form_key, "").strip()
        if v:
            payload[api_key] = v

    try:
        res = await api.post("/api/admin/storage/providers", json=payload)
    except ApiError as err:
        return html(_custom_form(values, err.message, sel_type).body, toast=_toast(err.message, "error"))

    provider_key = (res or {}).get("provider_key") or name
    return html(
        "",
        toast=_toast(f"Provider {provider_key} created (pending — connect it to activate)", "success"),
        refresh=True,
    )


# --------------------------------------------------------------------------- #
# DELETE /ui/storage/files/{file_id}
# --------------------------------------------------------------------------- #

@router.delete("/ui/storage/files/{file_id}")
async def storage_file_delete(file_id: str) -> HTMLResponse:
    files = await _safe_get("/api/admin/storage/files") or []
    row = next((f for f in files if str(f.get("id")) == str(file_id)), None)
    name = (row or {}).get("name") or f"file {file_id}"

    try:
        await api.delete(f"/api/admin/storage/files/{file_id}")
    except ApiError as err:
        return html("", toast=_toast(err.message, "error"), refresh=True)
    return html("", toast=_toast(f"Deleted {name}", "success"), refresh=True)
