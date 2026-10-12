"""Jinja2 rendering for the Python UI module.

`render(template, **ctx)` returns an HTML string; `html()` wraps it in an
HTMLResponse with optional HX-Trigger headers (toast + refresh conventions
consumed by static/app.js).
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from nova.config import PROJECT_ROOT
from ui.format import (
    cx,
    fmt_bytes,
    fmt_clock,
    fmt_date,
    fmt_mb,
    fmt_num,
    fmt_uptime,
    mask_key,
    time_ago,
)

TEMPLATES_DIR = PROJECT_ROOT / "templates"

env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(("html", "xml")),
    trim_blocks=True,
    lstrip_blocks=True,
)
env.filters.update(
    {
        "fmt_num": fmt_num,
        "fmt_bytes": fmt_bytes,
        "fmt_mb": fmt_mb,
        "fmt_uptime": fmt_uptime,
        "time_ago": time_ago,
        "fmt_clock": fmt_clock,
        "fmt_date": fmt_date,
        "mask_key": mask_key,
        "cx": cx,
    }
)


def render(template: str, /, **ctx: Any) -> str:
    return env.get_template(template).render(**ctx)


def html(
    fragment: str,
    *,
    status_code: int = 200,
    toast: dict[str, str] | None = None,
    refresh: bool = False,
    headers: dict[str, str] | None = None,
) -> HTMLResponse:
    """HTMLResponse with optional HX-Trigger side effects.

    toast: {"message": "...", "type": "success|error|info"} → app.js toast.
    refresh: True → the app fires a global `nova:refresh` (live widgets reload).
    """
    all_headers = dict(headers or {})
    triggers: dict[str, Any] = {}
    if toast:
        triggers["nova:toast"] = toast
    if refresh:
        triggers["nova:refresh"] = {}
    if triggers:
        all_headers["HX-Trigger"] = json.dumps(triggers)
    return HTMLResponse(fragment, status_code=status_code, headers=all_headers)


async def form_dict(request: Request) -> dict[str, Any]:
    """Flat dict of an HTMX form submission (urlencoded or multipart)."""
    form = await request.form()
    out: dict[str, Any] = {}
    for key, value in form.multi_items():
        out[key] = value
    return out


def truthy(value: Any) -> bool:
    """HTML form checkbox/hidden semantics."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "on", "yes", "")
