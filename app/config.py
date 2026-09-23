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

# Vercel sets VERCEL=1; Render sets RENDER=true for all services.
SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("RENDER"))

# Only create local dirs when actually running with the SQLite backend.
def _use_sqlite() -> bool:
    return not DATABASE_URL

if _use_sqlite():
    if SERVERLESS:
        # Render (like Vercel) has an ephemeral filesystem on its default/free
        # tiers - SQLite would silently wipe on every restart/deploy. Refuse
        # loudly instead. (A paid Render instance with a persistent disk can
        # set NOVA_DATA_DIR to the disk mount and NOVA_ALLOW_EPHEMERAL=1.)
        platform = "Render" if os.environ.get("RENDER") else "Vercel"
        allow = os.environ.get("NOVA_ALLOW_EPHEMERAL") == "1"
        if not allow:
            raise RuntimeError(
                f"DATABASE_URL is not set on a {platform} deployment. NovaRouter needs "
                f"a Postgres URL (e.g. Neon or {platform} Postgres) when hosted on {platform} - "
                f"the default instances have no persistent disk, so SQLite data would not "
                f"survive restarts. Set DATABASE_URL and redeploy. (Only if you attached a "
                f"persistent disk: set NOVA_DATA_DIR to its mountPath and "
                f"NOVA_ALLOW_EPHEMERAL=1.)"
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

# Extensions (MCP servers / skills / plugins)
TOOL_TIMEOUT = float(os.environ.get("NOVA_TOOL_TIMEOUT", "90"))     # per tool call
TOOL_MAX_HOPS = int(os.environ.get("NOVA_TOOL_MAX_HOPS", "5"))       # agentic loop rounds
TOOL_AUTO = os.environ.get("NOVA_TOOL_AUTO", "1") == "1"            # auto-inject+execute tools

# Cooldown (seconds) applied to an upstream key after specific failures
COOLDOWN_429 = int(os.environ.get("NOVA_COOLDOWN_429", "60"))
COOLDOWN_402 = int(os.environ.get("NOVA_COOLDOWN_402", "1800"))
COOLDOWN_5XX = int(os.environ.get("NOVA_COOLDOWN_5XX", "30"))
LOG_RETENTION = int(os.environ.get("NOVA_LOG_RETENTION", "5000"))

# Model fallback + identity spoofing:
# when the requested model is unusable (dead key / retired / not registered),
# silently retry on a fallback model but keep reporting the requested id.
AUTO_FALLBACK = os.environ.get("NOVA_AUTO_FALLBACK", "1") == "1"   # auto-pick a healthy stand-in when no route covers the model
SPOOF_MODEL = os.environ.get("NOVA_SPOOF_MODEL", "1") == "1"     # responses carry the model id the client asked for
FALLBACK_MAX = int(os.environ.get("NOVA_FALLBACK_MAX", "3"))       # max auto-picked fallback models per request

# Media routing: when a request carries media (images / audio / video /
# documents) the selected model can't read, serve it with a capable
# stand-in model instead — spoofed id, so the agent never sees an error.
MEDIA_ROUTING = os.environ.get("NOVA_MEDIA_ROUTING", "1") == "1"

# Scheduled availability checks: NOVA_CHECK_INTERVAL seconds between
# automatic background model checks (0 = off, the default — manual only).
CHECK_INTERVAL = int(os.environ.get("NOVA_CHECK_INTERVAL", "0"))

# Request hedging: when the primary provider hasn't answered within
# NOVA_HEDGE_DELAY seconds, race a second provider and take the first
# 200 (0 = off). Cuts tail latency when the primary is slow, not dead.
HEDGE_DELAY = float(os.environ.get("NOVA_HEDGE_DELAY", "0"))

# Parallel model racing: when multiple providers/models can serve the same
# requested model, fire them all at once and take the first success (0 = off,
# sequential failover). When ON, also enables racing fallback stages.
PARALLEL_MODELS = os.environ.get("NOVA_PARALLEL_MODELS", "1") == "1"

# Per-request API key passthrough: allow clients to supply an upstream API key
# directly in the request via the X-Nova-Provider-Key header (format:
# provider_name=api_key, one per line) or in the payload nova.provider_keys
# dict. Enables ad-hoc use of models that have no pre-registered keys.
ALLOW_REQUEST_KEYS = os.environ.get("NOVA_ALLOW_REQUEST_KEYS", "1") == "1"

# Response caching: serve identical chat requests from an in-memory cache
# for NOVA_CACHE_TTL seconds (0 = off). NOVA_CACHE_MAX caps entries (LRU).
CACHE_TTL = int(os.environ.get("NOVA_CACHE_TTL", "0"))
CACHE_MAX = int(os.environ.get("NOVA_CACHE_MAX", "500"))

# Files API: max upload size in MB.
FILE_MAX_MB = int(os.environ.get("NOVA_FILE_MAX_MB", "200"))

# Batch API: max requests per batch + worker concurrency per batch job.
BATCH_MAX_ITEMS = int(os.environ.get("NOVA_BATCH_MAX_ITEMS", "50000"))
BATCH_CONCURRENCY = int(os.environ.get("NOVA_BATCH_CONCURRENCY", "4"))

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
