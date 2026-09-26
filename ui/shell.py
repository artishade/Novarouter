"""UI shell — the dashboard page, tab fragment dispatch hook, footer stats.

The Python frontend is a single page (like the React original): the sidebar
swaps fragments into #tab-content via HTMX. Each tab module owns its own
GET /partials/tab/<key> route; unknown tabs 404.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from nova.config import PROJECT_ROOT
from ui.api_client import ApiError, api
from ui.render import html, render

shell_router = APIRouter(tags=["ui:shell"])


@shell_router.get("/")
async def index() -> HTMLResponse:
    """Dashboard shell — sidebar + header + empty tab container + footer."""
    return html(render("base.html"))


@shell_router.get("/partials/footer-stats")
async def footer_stats() -> HTMLResponse:
    try:
        stats = await api.get("/api/admin/stats")
    except (ApiError, Exception):
        stats = None
    return html(render("partials/footer_stats.html", stats=stats))


@shell_router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "static" / "logo.svg", media_type="image/svg+xml")
