"""NovaRouter Python core — configuration and environment handling.

The server binds to 0.0.0.0:$PORT (Render assigns PORT dynamically — never
hardcoded). DATABASE_URL accepts Prisma-style values (`file:…`, `postgres://`,
pgbouncer query params) and normalizes them for SQLAlchemy.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- #
# Public base URL (requirement #2)
# --------------------------------------------------------------------------- #
# Every externally-visible base_url reference (README examples, .env.example,
# the meta descriptor served to the dashboard) points at the hosted domain.
HOSTED_DOMAIN = "https://novarouter.onrender.com"

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def public_base_url(request_base: str | None = None) -> str:
    """Best-known public origin of this deployment.

    Priority: PUBLIC_BASE_URL env → incoming request origin (correct for any
    host, including the Render domain) → the hosted domain constant.
    """
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    if request_base:
        return request_base.rstrip("/")
    return HOSTED_DOMAIN


# --------------------------------------------------------------------------- #
# Database URL normalization
# --------------------------------------------------------------------------- #

_PRISMA_QUERY_KEYS = {"pgbouncer", "connection_limit", "pool_timeout", "sslaccept", "sslmode"}


def normalize_database_url(raw: str | None) -> str:
    """Translate Prisma-style DATABASE_URL values into SQLAlchemy URLs."""
    url = (raw or "").strip()
    if not url:
        return f"sqlite:///{(PROJECT_ROOT / 'db' / 'custom.db').as_posix()}"

    # Prisma SQLite form: file:./db/custom.db | file:../db/custom.db | file:/abs/path.db
    if url.startswith("file:"):
        path = url[5:]
        if not path.startswith("/"):
            path = str((PROJECT_ROOT / path).resolve())
        return f"sqlite:///{path}"

    parsed = urlparse(url)
    if parsed.scheme == "postgres":
        parsed = parsed._replace(scheme="postgresql")

    # Drop Prisma-specific pool params that psycopg2 does not understand.
    if parsed.scheme.startswith("postgresql"):
        query = [(k, v) for k, v in parse_qsl(parsed.query) if k not in _PRISMA_QUERY_KEYS]
        parsed = parsed._replace(query=urlencode(query))

    return urlunparse(parsed)


DATABASE_URL_RAW = os.environ.get("DATABASE_URL", "")
DATABASE_URL = normalize_database_url(DATABASE_URL_RAW)
IS_POSTGRES = DATABASE_URL.startswith("postgresql")

# --------------------------------------------------------------------------- #
# Runtime tuning (V8-heap equivalents are kept as gateway config keys)
# --------------------------------------------------------------------------- #

ENGINE_SIDECAR_PORT = int(os.environ.get("NOVA_ENGINE_PORT", "3099"))
ENGINE_SIDECAR_URL = f"http://127.0.0.1:{ENGINE_SIDECAR_PORT}"

# CORS (requirement #4): browser clients may call the API directly.
CORS_ALLOW_ORIGINS = [
    o.strip()
    for o in os.environ.get("CORS_ALLOW_ORIGINS", "*").split(",")
    if o.strip()
]

PORT = int(os.environ.get("PORT", "3000"))  # Render injects PORT dynamically


def engine_runtime() -> str | None:
    """Locate a JS runtime able to host the z-ai engine sidecar."""
    from shutil import which

    for candidate in ("bun", "node"):
        if which(candidate):
            return candidate
    return None


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "provider"
