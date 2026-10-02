"""NovaRouter terminal service — the Root@Build terminal, hosted on its own.

The interactive terminal is the one part of NovaRouter that wants its own
machine: it hands out real root shells, so it can be sized, restarted and
network-isolated independently of the gateway. This module is that separate
host, and it is the only entrypoint you need if you deploy `terminal/` alone:

    python3 -m terminal.service        # binds 0.0.0.0:$NOVA_TERMINAL_PORT
    sh ./terminal/run.sh               # same thing, with dependency install

It serves exactly the routes the dashboard already calls (the same
`terminal/api.py` the gateway mounts), so pointing the app at it
changes nothing about the UI or the API contract:

    NOVA_TERMINAL_URL=http://terminal-host:3100   # on the NovaRouter side
    NOVA_TERMINAL_TOKEN=<shared secret>           # on BOTH sides

NOVA_TERMINAL_URL unset (the default) means the gateway keeps the terminal
in-process and this module is simply not run. See `terminal/link.py` for
the seam, and `agent-ctx/project-map.md` for the operational notes.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from nova.config import PROJECT_ROOT
from terminal import link as terminal_link
from terminal.api import router as terminal_api_router
from terminal.config import TERMINAL_SERVICE_PORT, TERMINAL_SERVICE_TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("terminal.service")

STARTED_AT = time.time()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # This process always owns its shells: pin the local link so a stray
    # NOVA_TERMINAL_URL in the environment can't make the service proxy to
    # itself (or to a second copy of itself).
    terminal_link.pin(terminal_link.LocalLink())
    try:
        await terminal_link.current().ensure_default()
    except Exception as err:  # never block boot on the warmup shell
        log.warning("default cloud shell warmup skipped: %s", err)
    log.info("NovaRouter terminal service up on 0.0.0.0:%s (root of %s)",
             TERMINAL_SERVICE_PORT, PROJECT_ROOT)
    yield
    try:
        await terminal_link.current().close_all()
    except Exception as err:
        log.warning("shell teardown reported: %s", err)


app = FastAPI(
    title="NovaRouter Terminal Service",
    version="2.0.0-python",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redirect_slashes=False,
)

# The service hands out root shells, so a shared secret is the difference
# between "private" and "anyone on the network". Unset is only sane on
# loopback, and is called out loudly at boot.
TOKEN_HEADERS = ("x-nova-terminal-token", "authorization")


@app.middleware("http")
async def require_token(request: Request, call_next):
    if request.url.path in ("/health", "/api/health"):
        return await call_next(request)
    if not TERMINAL_SERVICE_TOKEN:
        return await call_next(request)
    offered = (request.headers.get(TOKEN_HEADERS[0]) or "").strip()
    if not offered:
        auth = (request.headers.get(TOKEN_HEADERS[1]) or "").strip()
        offered = auth.removeprefix("Bearer ").strip()
    if offered != TERMINAL_SERVICE_TOKEN:
        return JSONResponse(
            {"error": "A valid X-Nova-Terminal-Token header is required.", "code": "unauthorized"},
            status_code=401,
        )
    return await call_next(request)


# The same contract the gateway serves under /api/admin/terminal/pty.
app.include_router(terminal_api_router, prefix="/terminal/pty", tags=["terminal"])


@app.get("/health")
@app.get("/api/health")
async def health():
    terminal = terminal_link.current()
    try:
        state = await terminal.snapshot()
        sessions = len(state.get("sessions") or [])
        ok = True
    except Exception:
        state, sessions, ok = {}, 0, False
    return JSONResponse({
        "ok": ok,
        "status": "healthy" if ok else "degraded",
        "service": "novarouter-terminal",
        "runtime": "python",
        "uptime_s": int(time.time() - STARTED_AT),
        "sessions": sessions,
        "active": state.get("active"),
        "max_sessions": state.get("max_sessions"),
        "build_root": str(PROJECT_ROOT),
    })


if __name__ == "__main__":
    import uvicorn

    # Only reachable as `python3 -m terminal.service`: the `-m` form puts the
    # repo root on sys.path, which is what the `terminal.*` imports above need.
    if not TERMINAL_SERVICE_TOKEN:
        log.warning("=" * 74)
        log.warning("NOVA_TERMINAL_TOKEN is not set — this service grants root")
        log.warning("shells to anyone who can reach it. Set the same token on both")
        log.warning("sides before exposing it beyond loopback.")
        log.warning("=" * 74)

    # Binds 0.0.0.0 so it is reachable as its own host; PORT is never reused.
    uvicorn.run(app, host="0.0.0.0", port=TERMINAL_SERVICE_PORT,
                log_level="info", access_log=False)
