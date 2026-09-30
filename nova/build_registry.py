"""Cloud build target registry — permanent presets + tooling for the
Root@Build cloud terminal.

Two pieces live here:

  BUILD_PROVIDERS — the permanent catalogue of extendable cloud config
  targets shown in the Nova Console storage panel (Cloudflare R2, Google
  Cloud Storage, AWS S3, Backblaze B2, Azure Blob, plus an S3-compatible
  custom row). Each entry describes which credential fields the web UI
  asks for; values are stored in StorageProviderRow.config by the storage
  routes (masked in backups via mask_secret).

  register_builtin_tools() — the permanent agent tool registration. Runs at
  every gateway boot (and is re-run safe), so background agent tasks can
  ALWAYS rely on the full toolbox (web, page reader, sandbox shell, file
  tools, storage targets, model discovery) being registered — no UI action
  needed, ever.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from .kv import set_config
from .models import StorageProviderRow

log = logging.getLogger("nova.build_registry")

# --------------------------------------------------------------------------- #
# Permanent cloud config targets — extendable from the web UI
# --------------------------------------------------------------------------- #

BUILD_PROVIDERS: list[dict] = [
    {
        "id": "cloudflare_r2",
        "name": "Cloudflare R2",
        "type": "cloudflare_r2",
        "free_tier": "10 GB storage + zero egress fees on the free plan",
        "region": "auto",
        "docs_url": "https://developers.cloudflare.com/r2/api/s3/tokens/",
        "icon": "cloud",
        "fields": [
            {"name": "endpoint", "label": "Account endpoint", "placeholder": "https://<account_id>.r2.cloudflarestorage.com", "required": True},
            {"name": "bucket_name", "label": "Bucket", "placeholder": "my-bucket", "required": True},
            {"name": "access_key", "label": "Access key ID", "placeholder": "", "required": True, "secret": True},
            {"name": "secret_key", "label": "Secret access key", "placeholder": "", "required": True, "secret": True},
        ],
    },
    {
        "id": "google_cloud_storage",
        "name": "Google Cloud Storage",
        "type": "gcs",
        "free_tier": "5 GB US regional storage free every month",
        "region": "us",
        "docs_url": "https://cloud.google.com/storage/docs/creating-buckets",
        "icon": "cloud",
        "fields": [
            {"name": "bucket_name", "label": "Bucket", "placeholder": "my-bucket", "required": True},
            {"name": "token", "label": "Service-account JSON key or OAuth token", "placeholder": '{"type": "service_account", …}', "required": True, "secret": True},
            {"name": "region", "label": "Region", "placeholder": "us-central1", "required": False},
        ],
    },
    {
        "id": "aws_s3",
        "name": "AWS S3",
        "type": "aws_s3",
        "free_tier": "5 GB standard storage free for 12 months",
        "region": "us-east-1",
        "docs_url": "https://docs.aws.amazon.com/AmazonS3/latest/userguide/GetStartedWithS3.html",
        "icon": "cloud",
        "fields": [
            {"name": "bucket_name", "label": "Bucket", "placeholder": "my-bucket", "required": True},
            {"name": "access_key", "label": "Access key ID", "placeholder": "AKIA…", "required": True, "secret": True},
            {"name": "secret_key", "label": "Secret access key", "placeholder": "", "required": True, "secret": True},
            {"name": "region", "label": "Region", "placeholder": "us-east-1", "required": False},
        ],
    },
    {
        "id": "backblaze_b2",
        "name": "Backblaze B2",
        "type": "backblaze_b2",
        "free_tier": "10 GB storage free, S3-compatible API",
        "region": "us-west",
        "docs_url": "https://www.backblaze.com/b2/docs/application_keys.html",
        "icon": "cloud",
        "fields": [
            {"name": "endpoint", "label": "S3 endpoint", "placeholder": "https://s3.us-west-004.backblazeb2.com", "required": True},
            {"name": "bucket_name", "label": "Bucket", "placeholder": "my-bucket", "required": True},
            {"name": "access_key", "label": "Key ID", "placeholder": "", "required": True, "secret": True},
            {"name": "secret_key", "label": "Application key", "placeholder": "", "required": True, "secret": True},
        ],
    },
    {
        "id": "azure_blob",
        "name": "Azure Blob Storage",
        "type": "azure_blob",
        "free_tier": "5 GB LRS storage free for 12 months",
        "region": "global",
        "docs_url": "https://learn.microsoft.com/azure/storage/blobs/storage-blobs-introduction",
        "icon": "cloud",
        "fields": [
            {"name": "endpoint", "label": "Account URL", "placeholder": "https://<account>.blob.core.windows.net", "required": True},
            {"name": "bucket_name", "label": "Container", "placeholder": "my-container", "required": True},
            {"name": "token", "label": "Shared key or SAS token", "placeholder": "", "required": True, "secret": True},
        ],
    },
    {
        "id": "s3_compatible",
        "name": "S3-compatible (custom)",
        "type": "s3_compatible",
        "free_tier": "Any S3-compatible endpoint: MinIO, Wasabi, iDrive e2, …",
        "region": "global",
        "docs_url": None,
        "icon": "cloud",
        "fields": [
            {"name": "endpoint", "label": "Endpoint URL", "placeholder": "https://s3.example.com", "required": True},
            {"name": "bucket_name", "label": "Bucket", "placeholder": "my-bucket", "required": True},
            {"name": "access_key", "label": "Access key ID", "placeholder": "", "required": True, "secret": True},
            {"name": "secret_key", "label": "Secret access key", "placeholder": "", "required": True, "secret": True},
            {"name": "region", "label": "Region", "placeholder": "us-east-1", "required": False},
        ],
    },
]

BUILD_PROVIDER_BY_ID = {p["id"]: p for p in BUILD_PROVIDERS}

# KV key that records the permanent toolbox registration.
BUILTIN_TOOLS_FLAG = "agent_builtin_tools_registered"


def ensure_build_providers(db: Session) -> None:
    """Idempotently seed every permanent build provider row.

    Rows carry no credentials until the user fills them in from the web UI
    (status `pending` → `connected` via the storage connect route). Existing
    rows are never overwritten — user-entered config survives reboots.
    """
    changed = False
    for preset in BUILD_PROVIDERS:
        row = db.get(StorageProviderRow, preset["id"])
        if row is not None:
            continue
        db.add(StorageProviderRow(
            id=preset["id"],
            name=preset["name"],
            type=preset["type"],
            status="pending",
            isBuiltin=False,
            active=False,
            freeTier=preset["free_tier"],
            quotaMb=0,
            region=preset["region"],
            docsUrl=preset["docs_url"],
        ))
        changed = True
    if changed:
        db.commit()
        log.info("registered %d permanent cloud build targets", len(BUILD_PROVIDERS))


def register_builtin_tools(db: Session) -> None:
    """Permanent agent toolbox registration (idempotent, runs every boot).

    The toolbox is ALWAYS present for background agent tasks: web search,
    page reader, sandbox shell + file tools, storage scan of the configured
    build targets, and live model discovery. Nothing to enable from the UI —
    this is the permanent config the user asked for.
    """
    from .agent import ALLOWED_ACTIONS, TOOLS_DOC

    wanted = sorted(ALLOWED_ACTIONS)
    current = set(_tool_flag(db))
    if current == set(wanted):
        return
    set_config(db, BUILTIN_TOOLS_FLAG, json.dumps(wanted))
    log.info("permanent agent toolbox registered: %s", ", ".join(wanted))


def _tool_flag(db: Session) -> list:
    from .kv import get_config_json
    value = get_config_json(db, BUILTIN_TOOLS_FLAG, [])
    return value if isinstance(value, list) else []
