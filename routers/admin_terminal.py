"""Terminal admin routes — port of:
  src/app/api/admin/terminal/exec/route.ts        → POST /exec
  src/app/api/admin/terminal/history/route.ts     → GET  /history
  src/app/api/admin/terminal/clear/route.ts       → POST /clear
  src/app/api/admin/terminal/boost-ram/route.ts   → POST /boost-ram

Plus the real interactive terminal. This file is only the dashboard's glue
(DB-backed exec history + OS telemetry): the terminal feature itself lives
under `terminal/` and is imported from there — nothing here reimplements it.

  terminal/sandbox.py → the simulated allowlist executor behind /exec
  terminal/api.py     → the interactive terminal's HTTP contract, mounted here
                        at `/pty` and shared verbatim with
                        `terminal/service.py`, so the terminal can be hosted as
                        its own service (see `terminal/link.py`) without this
                        file changing:
  GET  /pty/sessions → full state snapshot (sessions + focused one)
  POST /pty/sessions → open a session and focus it
  POST /pty/activate → switch the focused session
  POST /pty/rename   → name a session tab
  GET  /pty/stream   → SSE: output, working-directory and exit events
  POST /pty/input    → keystrokes (\r, \u0003, arrows …)
  POST /pty/resize   → cols/rows
  POST /pty/stop     → close a session
  POST /pty/run      → one command in the agent's own tab
"""
from __future__ import annotations

import logging
import math
import os
import platform
import socket
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from nova.config import PROJECT_ROOT
from nova.database import get_db
from nova.kv import get_config, get_config_number, get_gpu_providers, set_config
from nova.models import TerminalCommand
from terminal import link as terminal_link
from terminal import sandbox
from terminal.api import router as pty_router

router = APIRouter()
log = logging.getLogger("nova.admin_terminal")

# The interactive terminal's contract — in-process or a separately hosted
# service, the routes and responses are the same either way.
router.include_router(pty_router, prefix="/pty")


def now_ms() -> int:
    return int(time.time() * 1000)


def ts_ms(dt: datetime | None) -> int:
    """Prisma stored naive UTC datetimes → epoch ms."""
    if dt is None:
        return 0
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _int_if(value: float) -> float | int:
    return int(value) if value == int(value) else value


async def _live_sessions() -> dict:
    """Live session state for the tab bar.

    Goes through the link, so a terminal hosted as its own service reports
    its sessions here too. A terminal that can't be reached must not take the
    rest of `/history` (OS stats, memory, command history) down with it — the
    empty snapshot just renders as "no sessions yet".
    """
    try:
        state = await terminal_link.current().snapshot()
    except Exception as err:
        log.warning("terminal snapshot unavailable: %s", err)
        return {"sessions": [], "active": None, "max_sessions": 0}
    return state if isinstance(state, dict) else {"sessions": [], "active": None, "max_sessions": 0}


# --------------------------------------------------------------------------- #
# POST /exec {command, cwd?} → ExecResult
# --------------------------------------------------------------------------- #


@router.post("/exec")
async def exec_command(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    command = body.get("command") if isinstance(body, dict) else None
    if not isinstance(command, str) or not command.strip():
        return JSONResponse({"error": "command is required"}, status_code=400)

    raw_cwd = body.get("cwd") if isinstance(body, dict) else None
    cwd_input = raw_cwd if isinstance(raw_cwd, str) and raw_cwd.strip() else None

    result = sandbox.execute_command(db, command, cwd_input)

    # One-shot exec can't answer interactive prompts — point at Live session.
    if result.get("code") not in (0,) and not result.get("ok", True):
        hint = terminal_link.shell_hint_for(command)
        if hint:
            result["hint"] = hint

    # Keep the persisted cwd in sync so terminal/history reflects the session.
    if cwd_input:
        try:
            set_config(db, "terminal_cwd", result["cwd"])
        except Exception:
            pass

    return result


# --------------------------------------------------------------------------- #
# GET /history → TerminalHistoryResponse
# --------------------------------------------------------------------------- #


@router.get("/history")
async def history(db: Session = Depends(get_db)):
    cwd_raw = get_config(db, "terminal_cwd")
    gpu_enabled_raw = get_config(db, "gpu_enabled")
    strategy_raw = get_config(db, "gpu_strategy")
    gpus = get_gpu_providers(db)
    rows_desc = db.scalars(
        select(TerminalCommand).order_by(TerminalCommand.createdAt.desc()).limit(50)
    ).all()

    history = [
        {
            "id": r.id,
            "command": r.command,
            "output": r.output,
            "exit_code": r.exitCode,
            "duration_ms": r.durationMs,
            "cwd": r.cwd,
            "timestamp": ts_ms(r.createdAt),
        }
        for r in reversed(rows_desc)
    ]

    cpus = os.cpu_count() or 1
    load = sum(os.getloadavg()) / 3
    load_pct = min(99, max(0, int(math.floor((load / cpus) * 100 + 0.5))))

    # Real OS identity + memory telemetry (no mock V8/swap config).
    try:
        import pwd as _pwd

        user = _pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        user = os.environ.get("USER") or "root"
    si = sandbox.swapinfo_kb()

    return {
        "cwd": cwd_raw or str(PROJECT_ROOT),
        "history": history,
        "system": {
            "platform": platform.system().lower(),
            "release": platform.release(),
            "arch": platform.machine(),
            "hostname": socket.gethostname(),
            "user": user,
            "is_root": os.getuid() == 0 if hasattr(os, "getuid") else user == "root",
            "uptime_s": int(sandbox.uptime_seconds()),
            "totalmem_mb": sandbox.totalmem_mb(),
            "freemem_mb": sandbox.freemem_mb(),
            "cpus": cpus,
            "load_pct": load_pct,
            "node_version": sandbox.node_version_string(),
        },
        # Real memory telemetry — the old v8_heap/swap keys were runtime fiction
        # in the Python server and are no longer reported.
        "memory_config": {
            "total_mb": sandbox.totalmem_mb(),
            "available_mb": sandbox.freemem_mb(),
            "swap_total_mb": int(math.floor(si["total"] / 1024 + 0.5)),
            "swap_free_mb": int(math.floor(si["free"] / 1024 + 0.5)),
            "process_rss_mb": int(math.floor(sandbox.rss_kb() / 1024 + 0.5)),
        },
        # NOTE: shared type TerminalHistoryResponse.gpu accidentally declares
        # `enabled` twice (boolean flag + number count). Emit the boolean flag
        # as `enabled` and expose the count as the extra `enabled_count` key.
        "gpu": {
            "enabled": gpu_enabled_raw not in ("0", "false"),
            "strategy": strategy_raw or "quota_aware",
            "total": len(gpus),
            "enabled_count": sum(1 for p in gpus if p["enabled"]),
            "connected": sum(1 for p in gpus if p["connected"]),
        },
        # Live session state, so the tab bar renders without a client fetch.
        "sessions": await _live_sessions(),
    }


# --------------------------------------------------------------------------- #
# POST /clear → wipe all terminal history
# --------------------------------------------------------------------------- #


@router.post("/clear")
def clear(db: Session = Depends(get_db)):
    db.query(TerminalCommand).delete()
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# POST /boost-ram {heap_mb, swap_mb} → BoostRamResult
# --------------------------------------------------------------------------- #


@router.post("/boost-ram")
async def boost_ram(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse(
            {"error": "heap_mb (128–65536) and swap_mb (0–65536), in MB, are required"},
            status_code=400,
        )

    def rounded(value) -> int | None:
        try:
            return int(math.floor(float(value) + 0.5))  # Math.round parity
        except (TypeError, ValueError):
            return None  # NaN / undefined

    heap = rounded(body.get("heap_mb"))
    swap = rounded(body.get("swap_mb"))
    if (
        heap is None
        or swap is None
        or heap < 128
        or heap > 65536
        or swap < 0
        or swap > 65536
    ):
        return JSONResponse(
            {"error": "heap_mb (128–65536) and swap_mb (0–65536), in MB, are required"},
            status_code=400,
        )

    # Validation kept for wire compatibility; the response is a real memory
    # report — the Python gateway has no Node heap or swapon to configure.
    si = sandbox.swapinfo_kb()
    return {
        "ok": True,
        "message": (
            f"Memory telemetry — {sandbox.totalmem_mb()} MB RAM "
            f"({sandbox.freemem_mb()} MB available), "
            f"{int(math.floor(si['total'] / 1024 + 0.5))} MB swap "
            f"({int(math.floor(si['free'] / 1024 + 0.5))} MB free). "
            "The Python gateway allocates dynamically — no heap limit to set."
        ),
        "v8_heap_mb": heap,
        "swap_mb": swap,
        "totalmem_mb": sandbox.totalmem_mb(),
        "freemem_mb": sandbox.freemem_mb(),
        "heap_before_mb": sandbox.r1(sandbox.rss_kb() / 1024),
        "heap_after_mb": sandbox.r1(sandbox.rss_kb() / 1024),
        "swap_success": True,
        "swap_output": "swap is reported live from /proc/meminfo — no swapon needed",
    }
