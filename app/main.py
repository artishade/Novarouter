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
import contextlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import admin, config, db, gateway


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    yield
    await gateway.close_client()


app = FastAPI(
    title="NovaRouter",
    description="Lightweight multi-provider AI API gateway with key rotation and a model availability checker.",
    version="1.0.0",
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