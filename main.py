"""NovaRouter — Python server entrypoint (FastAPI + uvicorn).

Binds to 0.0.0.0:$PORT (Render assigns PORT dynamically; local default 3000).
Owns:
  • /health, /api/health          — health checks for uptime pings
  • /v1/* + /api/v1/*             — OpenAI-compatible gateway (with CORS)
  • /api/admin/* , /api/agent/*   — JSON API (source of truth for the UI)
  • / and /partials/tab/*         — the Python dashboard UI (Jinja2 + HTMX BFF)
                                    rendered by the `ui` package from Python —
                                    no Node/React frontend anymore.

The frontend module is Python: `ui/` renders templates server-side and calls
this same JSON API (self-BFF), so UI behaviour always matches the gateway.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRouter
from fastapi.staticfiles import StaticFiles

from nova import engine as nova_engine
from nova.bootstrap import run_bootstrap
from nova.config import CORS_ALLOW_ORIGINS, IS_POSTGRES, PORT, PROJECT_ROOT
from nova.database import ensure_sqlite_dir, ping

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("nova.main")

STARTED_AT = time.time()


EPHEMERAL_DB_WARNING = (
    "SQLite lives inside the ephemeral container — every deploy (git push) or "
    "restart wipes providers, models, keys, routes and configs. Set DATABASE_URL "
    "to a managed Postgres URL (free at neon.tech) in the Render dashboard to "
    "keep your data forever."
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_sqlite_dir()
    run_bootstrap()
    if not IS_POSTGRES:
        log.warning("=" * 74)
        log.warning("EPHEMERAL DATABASE — no Postgres DATABASE_URL configured.")
        log.warning("%s", EPHEMERAL_DB_WARNING)
        log.warning("=" * 74)
    await nova_engine.start_sidecar()
    log.info("NovaRouter up on 0.0.0.0:%s (Python UI + API)", PORT)
    yield
    await nova_engine.stop_sidecar()


app = FastAPI(
    title="NovaRouter Gateway",
    version="2.0.0-python",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redirect_slashes=False,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,       # requirement #4 — browser clients allowed
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Health (requirement #3) — for Render uptime pings
# --------------------------------------------------------------------------- #

async def health_payload() -> dict:
    db_ok = await asyncio.to_thread(ping)
    engine_ok = await nova_engine.health_check(timeout=1.0)
    return {
        "ok": db_ok,
        "status": "healthy" if db_ok else "degraded",
        "service": "novarouter",
        "runtime": "python",
        "version": "2.0.0",
        "uptime_s": int(time.time() - STARTED_AT),
        "db": "ok" if db_ok else "error",
        "engine": "up" if engine_ok else "down",
        "storage": {
            "driver": "postgres" if IS_POSTGRES else "sqlite",
            "persistent": IS_POSTGRES,
            "warning": None if IS_POSTGRES else EPHEMERAL_DB_WARNING,
        },
    }


@app.get("/health")
@app.get("/api/health")
async def health():
    return JSONResponse(await health_payload())


@app.head("/health")
async def health_head():
    return Response(status_code=200)


# --------------------------------------------------------------------------- #
# API routers (gateway mounted at both /v1 and /api/v1)
# --------------------------------------------------------------------------- #

from routers import (  # noqa: E402
    admin_client_keys,
    admin_compute,
    admin_keys,
    admin_misc,
    admin_models,
    admin_providers,
    admin_routes,
    admin_storage,
    admin_terminal,
    agent_api,
    gateway,
)

admin_router = APIRouter(prefix="/api/admin")
admin_router.include_router(admin_providers.router, prefix="/providers", tags=["providers"])
admin_router.include_router(admin_models.router, prefix="/models", tags=["models"])
admin_router.include_router(admin_keys.router, prefix="/keys", tags=["keys"])
admin_router.include_router(admin_routes.router, prefix="/routes", tags=["routes"])
admin_router.include_router(admin_client_keys.router, prefix="/client-keys", tags=["client-keys"])
admin_router.include_router(admin_terminal.router, prefix="/terminal", tags=["terminal"])
admin_router.include_router(admin_compute.router, prefix="/compute", tags=["compute"])
admin_router.include_router(admin_storage.router, prefix="/storage", tags=["storage"])
admin_router.include_router(admin_misc.router, tags=["misc"])

agent_router = APIRouter(prefix="/api/agent")
agent_router.include_router(agent_api.router, tags=["agent"])

app.include_router(gateway.router, prefix="/v1", tags=["gateway"])
app.include_router(gateway.router, prefix="/api/v1", include_in_schema=False, tags=["gateway"])
app.include_router(admin_router)
app.include_router(agent_router)

# --------------------------------------------------------------------------- #
# Python UI (frontend module) — shell + tab fragments from `ui/`
# --------------------------------------------------------------------------- #

from ui.shell import shell_router  # noqa: E402
from ui.tabs import ui_router  # noqa: E402

app.include_router(shell_router)
app.include_router(ui_router)
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")


if __name__ == "__main__":
    import uvicorn

    # requirement #1: bind 0.0.0.0:$PORT — PORT comes from the environment
    # (Render assigns it dynamically). No hardcoded platform port anywhere.
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info", access_log=False)
