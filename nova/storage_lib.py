"""Storage helper library — Python port of the helpers used by the TypeScript
storage routes (src/app/api/admin/storage/**).

Parity notes:
- Dates serialize as epoch milliseconds (JS `Date.getTime()`); DB datetimes are
  naive UTC (see nova.models.utcnow).
- Rows keep their Prisma camelCase column names (StorageProviderRow/StorageFile).
- The TypeScript backend stores EVERY file — local disk AND cloud providers —
  in the StorageFile table with a base64 payload; cloud provider rows only ever
  carried credentials metadata + a status flag. No direct cloud upload
  (firebase/supabase/s3/github/webdav REST) was implemented upstream, so none
  is invented here: this port keeps that exact, honest behavior.
"""
from __future__ import annotations

import base64
import json
import math
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import StorageFile, StorageProviderRow

MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB sandbox limit (files route)

# Config fields that count as "credentials present" for cloud providers
# (test-connection route parity).
CONFIG_FIELDS = (
    "endpoint", "bucket_name", "access_key", "secret_key", "token",
    "api_key", "username", "password", "url", "storage_bucket", "server_url",
)


def epoch_ms(dt: datetime | None) -> int | None:
    """JS `date.getTime()` — DB stores naive UTC datetimes."""
    if dt is None:
        return None
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def wire_num(value: float) -> float | int:
    """JS numbers serialize integral values without a decimal part (0 vs 0.0)."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def js_round(value: float) -> int:
    """Math.round parity (half away from zero)."""
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def parse_config(raw: str | None) -> dict:
    """JSON.parse(row.config) with the TS silent-fallback to {}."""
    try:
        parsed = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def merge_config(existing: dict, incoming: dict) -> dict:
    """{...existing, ...incoming} shallow merge."""
    return {**existing, **incoming}


def map_storage_provider(row) -> dict:
    """mapStorageProvider from storage/info/route.ts (StorageProviderInfo)."""
    return {
        "id": row.id,
        "name": row.name,
        "type": row.type,
        "status": row.status,
        "is_builtin": bool(row.isBuiltin),
        "active": bool(row.active),
        "free_tier": row.freeTier,
        "usage_mb": wire_num(float(row.usageMb or 0)),
        "quota_mb": wire_num(float(row.quotaMb or 0)),
        "region": row.region,
        "docs_url": row.docsUrl,
        "auth_url": row.authUrl,
        "last_test_at": epoch_ms(row.lastTestAt),
        "last_error": row.lastError,
    }


def map_storage_file(row) -> dict:
    """mapStorageFile from storage/info/route.ts (StorageFileInfo)."""
    return {
        "id": row.id,
        "name": row.name,
        "path": row.path,
        "size": row.size,
        "mime": row.mime,
        "provider_key": row.providerKey,
        "created_at": epoch_ms(row.createdAt),
    }


def slugify(name: str) -> str:
    """providers/route.ts slugify: lowercase, non-alnum → '-', trim dashes."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower())
    slug = re.sub(r"^-+|-+$", "", slug)
    return slug or "provider"


def mask_secret(secret: str) -> str:
    """backup/route.ts maskSecret: `abcdef…wxyz` or dots for short values."""
    return f"{secret[:6]}…{secret[-4:]}" if len(secret) > 12 else "••••••••"


def b64_to_bytes(value: str) -> bytes:
    """Lenient base64 decode (Node `Buffer.from(s, 'base64')` parity):
    whitespace/non-alphabet characters are ignored, padding is repaired."""
    core = re.sub(r"[^A-Za-z0-9+/]", "", value or "")
    if not core:
        return b""
    core += "=" * ((4 - len(core) % 4) % 4)
    try:
        return base64.b64decode(core)
    except Exception:
        return b""


def bytes_to_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def save_storage_file(
    db: Session,
    *,
    name: str,
    mime: str,
    provider_key: str,
    data_b64: str,
    path: str = "/",
) -> StorageFile:
    """Persist a file exactly like the TS routes do — a StorageFile row whose
    `data` column carries the base64 payload (works for every provider type)."""
    row = StorageFile(
        name=name,
        path=path,
        size=len(b64_to_bytes(data_b64)),
        mime=mime,
        providerKey=provider_key,
        data=data_b64,
    )
    db.add(row)
    db.commit()
    return row


def to_iso_z(dt: datetime) -> str:
    """JS `new Date().toISOString()` — millisecond precision, trailing Z."""
    return dt.replace(tzinfo=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ensure_local_disk(db: Session) -> StorageProviderRow:
    """The built-in Local Disk provider is the system-wide fallback (active
    provider everywhere in the TS code, upload target, backup target). The
    legacy TS demo purge (ensure-seed.ts) deleted every StorageProvider row
    including this built-in, so old deployments lost it. Restore it when
    missing — same values as nova.bootstrap._seed_gpu_catalogue. Idempotent.
    """
    row = db.get(StorageProviderRow, "local_disk")
    if row is None:
        row = StorageProviderRow(
            id="local_disk",
            name="Local Disk",
            type="local",
            status="connected",
            isBuiltin=True,
            active=True,
            freeTier="Uses the server's own disk",
            quotaMb=0,
            region="local",
        )
        db.add(row)
        db.commit()
    return row
