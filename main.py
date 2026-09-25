"""NovaRouter — Python server entrypoint (FastAPI + uvicorn).

Binds to 0.0.0.0:$PORT (Render assigns PORT dynamically; local default 3000).
Owns:
  • /health, /api/health          — health checks for uptime pings
  • /v1/* + /api/v1/*             — OpenAI-compatible gateway (with CORS)
  • /api/admin/* , /api/agent/*   — dashboard backend (port of the TS routes)
  • everything else               — proxied to the Next.js dashboard process
                                    (dev: `next dev`, prod: standalone server.js)
"""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
import subprocess
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRouter
from starlette.background import BackgroundTask

from nova import engine as nova_engine
from nova.bootstrap import run_bootstrap
from nova.config import CORS_ALLOW_ORIGINS, PORT, UI_COMMAND, UI_PORT, UI_TARGET
from nova.database import ensure_sqlite_dir, ping

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("nova.main")

STARTED_AT = time.time()
ui_proc: subprocess.Popen | None = None


# --------------------------------------------------------------------------- #
# Dashboard UI subprocess
# --------------------------------------------------------------------------- #

def start_ui() -> None:
    global ui_proc
    if ui_proc is not None:
        return
    cmd = UI_COMMAND.strip()
    if not cmd:
        # Dev default: Next dev server on the internal UI port.
        cmd = f"bun run next dev -p {UI_PORT}"
    argv = shlex.split(cmd)
    env = {**os.environ, "PORT": str(UI_PORT), "HOSTNAME": "127.0.0.1"}
    try:
        ui_proc = subprocess.Popen(argv, env=env, cwd=os.getcwd())  # noqa: S603
        log.info("dashboard UI spawned: %s (target %s)", " ".join(argv), UI_TARGET)
    except FileNotFoundError as err:
        log.warning("could not spawn dashboard UI (%s): %s — API-only mode", argv[0], err)
        ui_proc = None


def stop_ui() -> None:
    global ui_proc
    if ui_proc is not None:
        ui_proc.terminate()
        try:
            ui_proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            ui_proc.kill()
        ui_proc = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_sqlite_dir()
    run_bootstrap()
    await nova_engine.start_sidecar()
    start_ui()
    yield
    await nova_engine.stop_sidecar()
    stop_ui()


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
# Dashboard proxy — everything that is not an API path goes to the UI process
# --------------------------------------------------------------------------- #

PROXIED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]

HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer",
              "proxy-authenticate", "proxy-authorization", "upgrade",
              "proxy-connection", "host", "content-length"}

_ui_client: httpx.AsyncClient | None = None


def ui_client() -> httpx.AsyncClient:
    global _ui_client
    if _ui_client is None or _ui_client.is_closed:
        # read=None: SSE/HMR streams stay open; cleanup happens via aclose.
        _ui_client = httpx.AsyncClient(
            base_url=UI_TARGET,
            timeout=httpx.Timeout(connect=10.0, read=None, write=60.0, pool=120.0),
        )
    return _ui_client


@app.api_route("/{path:path}", methods=PROXIED_METHODS, include_in_schema=False)
async def proxy_ui(request: Request, path: str):
    # API paths must never leak into the proxy (they are all registered above).
    if path.startswith(("api/", "v1/", "health")) or path in ("api", "v1", "health"):
        return JSONResponse({"error": "Not found"}, status_code=404)

    url = httpx.URL(path="/" + path, query=request.url.query.encode("utf-8"))
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}

    body = await request.body() if request.method in ("POST", "PUT", "PATCH", "DELETE") else None

    client = ui_client()
    try:
        req = client.build_request(request.method, url, headers=headers, content=body)
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError:
        return HTMLResponse(
            "<!doctype html><html><body style='font-family:system-ui;display:flex;"
            "align-items:center;justify-content:center;height:100vh;margin:0'>"
            "<div style='text-align:center'><h1>NovaRouter</h1>"
            "<p>Gateway API is running, but the dashboard UI is starting…</p>"
            "<p>Refresh in a few seconds.</p></div></body></html>",
            status_code=503,
        )

    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}

    # STREAM everything through (SSE, HMR, big assets) — never buffer.
    async def relay():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        headers=resp_headers,
        background=BackgroundTask(upstream.aclose),
    )


@app.websocket("/{path:path}")
async def proxy_ws(_ws, _path: str):  # pragma: no cover — dashboard uses HTTP only
    await _ws.close(code=1013)


if __name__ == "__main__":
    import uvicorn

    # requirement #1: bind 0.0.0.0:$PORT — PORT comes from the environment
    # (Render assigns it dynamically). No hardcoded platform port anywhere.
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info", access_log=False)
