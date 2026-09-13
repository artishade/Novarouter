"""FastAPI application entrypoint.

Routes
------
  /v1/*            OpenAI-compatible gateway (needs a gateway API key)
  /admin/api/*     admin JSON API (needs X-Admin-Token)
  /                dashboard UI
  /healthz         unauthenticated liveness probe

The same `app` object is served by:
  * uvicorn locally           -> `novarouter serve` or uvicorn app.main:app
  * Vercel (Python runtime)   -> tool.vercel.entrypoint = "app.main:app"
"""
import asyncio
import contextlib
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import admin, batch, checker, config, db, gateway


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    bg: List["asyncio.Task[None]"] = []
    # periodic availability checks (NOVA_CHECK_INTERVAL, 0 = off)
    if config.CHECK_INTERVAL > 0:
        bg.append(asyncio.create_task(checker.scheduler_loop(config.CHECK_INTERVAL)))
    # batch expiry sweep: runs every 60s but only does work when batches exist
    async def batch_sweep() -> None:
        while True:
            try:
                await asyncio.sleep(60)
                batch.sweep_expired()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - sweep must never die
                continue

    bg.append(asyncio.create_task(batch_sweep()))
    yield
    for t in bg:
        t.cancel()
    await gateway.close_client()


app = FastAPI(
    title="NovaRouter",
    description="Multi-provider AI API gateway: chat, tools, vision, thinking, images, video, audio, "
                "Responses + Anthropic APIs — with key rotation, failover, and a model availability checker.",
    version="2.0.0",
    lifespan=lifespan,
)

# The dashboard is a same-origin static page; CORS is open so local agents
# and browser tools can call the gateway; the real protection is the API keys.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(gateway.router, prefix="/v1", tags=["gateway"])
app.include_router(admin.router, prefix="/admin/api", tags=["admin"])


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "novarouter", "storage": "postgres" if db.USE_POSTGRES else "sqlite"}


@app.get("/", include_in_schema=False)
async def dashboard():
    index = config.STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"error": "dashboard assets missing"}, status_code=500)


if config.STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")