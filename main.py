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
    try:
        from ui.api_client import api as _ui_api
        await _ui_api.close()
    except Exception:
        pass
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
# Gateway request logging — every /v1/* and /api/v1/* call gets a line with
# method, path, client, status, duration and (for /v1/messages) a compact
# summary of what Claude Code actually sent (tools? tool_result blocks?).
# Errors and 4xx/5xx are logged at WARNING with the response body snippet so
# agent-session failures can be diagnosed without attaching a debugger.
# --------------------------------------------------------------------------- #

GATEWAY_PREFIXES = ("/v1", "/api/v1")

def _summarize_anthropic_payload(payload: dict) -> str:
    msgs = payload.get("messages") or []
    tools = payload.get("tools") or []
    n_tool_use = n_tool_result = n_thinking = 0
    for m in msgs:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    bt = b.get("type")
                    if bt == "tool_use": n_tool_use += 1
                    elif bt == "tool_result": n_tool_result += 1
                    elif bt in ("thinking", "redacted_thinking"): n_thinking += 1
    tool_names = ",".join(str(t.get("name", "")) for t in tools if isinstance(t, dict))[:120]
    return (
        f"model={payload.get('model')} stream={payload.get('stream')} "
        f"msgs={len(msgs)} tool_defs={len(tools)}[{tool_names}] "
        f"tool_use={n_tool_use} tool_result={n_tool_result} thinking={n_thinking} "
        f"max_tokens={payload.get('max_tokens')}"
    )

@app.middleware("http")
async def gateway_request_log(request: Request, call_next):
    path = request.url.path
    if not path.startswith(GATEWAY_PREFIXES):
        return await call_next(request)
    started = time.time()
    body_snip = ""
    if path.endswith("/messages") or path.endswith("/count_tokens"):
        try:
            raw = await request.body()
            if raw:
                import json as _json
                try:
                    body_snip = _summarize_anthropic_payload(_json.loads(raw))
                except Exception:
                    body_snip = f"<unparsed {len(raw)}B>"
                # FastAPI caches body for downstream handlers
                request._body = raw  # noqa: SLF001
        except Exception:
            body_snip = "<body read failed>"
    response = await call_next(request)
    dur_ms = int((time.time() - started) * 1000)
    line = (f"{request.client.host if request.client else '-'} "
            f"{request.method} {path} -> {response.status_code} {dur_ms}ms")
    if body_snip:
        line += f" | {body_snip}"
    if response.status_code >= 400:
        log.warning("gateway %s", line)
    else:
        log.info("gateway %s", line)
    try:
        from nova import gwlog
        gwlog.record_request(
            request.client.host if request.client else None,
            request.method, path, response.status_code, dur_ms, body_snip,
        )
    except Exception:
        pass
    return response


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
