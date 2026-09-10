"""Runtime configuration. Everything overridable via env vars.

Serverless-safe: nothing here writes to disk at import time when the
filesystem is read-only (Vercel). SQLite is only used as a local fallback;
on serverless the Postgres backend (DATABASE_URL) is the source of truth.
"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("NOVA_DATA_DIR") or (BASE_DIR / "data"))
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()  # postgres://... -> Postgres backend
STATIC_DIR = BASE_DIR / "app" / "static"

# Vercel sets VERCEL=1 for all serverless builds/invocations.
SERVERLESS = bool(os.environ.get("VERCEL"))

# Only create local dirs when actually running with the SQLite backend.
def _use_sqlite() -> bool:
    return not DATABASE_URL

if _use_sqlite():
    if SERVERLESS:
        # Deployed on Vercel without DATABASE_URL: SQLite would silently wipe
        # data on every cold start. Refuse loudly instead.
        raise RuntimeError(
            "DATABASE_URL is not set on a serverless deployment. NovaRouter needs a "
            "Postgres URL (e.g. Neon) when hosted on Vercel - SQLite only persists "
            "on a real filesystem. Set DATABASE_URL and redeploy."
        )
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Read-only FS without DATABASE_URL: fall back to /tmp so the process
        # can still boot (e.g. inside sandboxes) instead of crashing at import.
        DATA_DIR = Path("/tmp/novarouter-data")
        DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.environ.get("NOVA_DB") or (DATA_DIR / "nova.db"))

HOST = os.environ.get("NOVA_HOST", "127.0.0.1")
PORT = int(os.environ.get("NOVA_PORT", "8080"))

# Upstream request behaviour
REQUEST_TIMEOUT = float(os.environ.get("NOVA_TIMEOUT", "180"))
CONNECT_TIMEOUT = float(os.environ.get("NOVA_CONNECT_TIMEOUT", "15"))
MAX_KEY_ATTEMPTS = int(os.environ.get("NOVA_MAX_KEY_ATTEMPTS", "4"))

# Cooldown (seconds) applied to an upstream key after specific failures
COOLDOWN_429 = int(os.environ.get("NOVA_COOLDOWN_429", "60"))
COOLDOWN_402 = int(os.environ.get("NOVA_COOLDOWN_402", "1800"))
COOLDOWN_5XX = int(os.environ.get("NOVA_COOLDOWN_5XX", "30"))
LOG_RETENTION = int(os.environ.get("NOVA_LOG_RETENTION", "5000"))

_ADMIN_TOKEN_FILE = DATA_DIR / "admin_token.txt"


def _load_admin_token() -> str:
    token = os.environ.get("NOVA_ADMIN_TOKEN")
    if token:
        return token
    if _use_sqlite():
        # Local mode: generate once and persist to the data dir.
        if _ADMIN_TOKEN_FILE.exists():
            existing = _ADMIN_TOKEN_FILE.read_text().strip()
            if existing:
                return existing
        token = "nova-admin-" + secrets.token_urlsafe(24)
        _ADMIN_TOKEN_FILE.write_text(token)
        try:
            _ADMIN_TOKEN_FILE.chmod(0o600)
        except OSError:
            pass
        return token
    # Serverless mode with no env token: any random value would change on
    # every cold start and silently lock the operator out. Fail explicitly
    # instead - require_admin() returns 503 with a clear message.
    return ""


ADMIN_TOKEN = _load_admin_token()
