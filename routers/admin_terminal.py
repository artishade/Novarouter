"""Terminal admin routes — port of:
  src/app/api/admin/terminal/exec/route.ts        → POST /exec
  src/app/api/admin/terminal/history/route.ts     → GET  /history
  src/app/api/admin/terminal/clear/route.ts       → POST /clear
  src/app/api/admin/terminal/boost-ram/route.ts   → POST /boost-ram

Sandbox executor lives in nova/terminal.py (port of terminal-exec.ts).
"""
from __future__ import annotations

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

from nova import terminal as sandbox
from nova.database import get_db
from nova.kv import get_config, get_config_number, get_gpu_providers, set_config
from nova.models import TerminalCommand

router = APIRouter()


def now_ms() -> int:
    return int(time.time() * 1000)


def ts_ms(dt: datetime | None) -> int:
    """Prisma stored naive UTC datetimes → epoch ms."""
    if dt is None:
        return 0
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _int_if(value: float) -> float | int:
    return int(value) if value == int(value) else value


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
def history(db: Session = Depends(get_db)):
    cwd_raw = get_config(db, "terminal_cwd")
    boost_raw = get_config(db, "boost_applied_at")
    heap = _int_if(get_config_number(db, "v8_heap_mb", 2048))
    swap = _int_if(get_config_number(db, "swap_mb", 2048))
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

    boost_applied_at: int | None = None
    if boost_raw is not None:
        try:
            n = float(boost_raw)
            if math.isfinite(n) and n > 0:
                boost_applied_at = int(n)
        except ValueError:
            pass

    return {
        "cwd": cwd_raw or "/workspace",
        "history": history,
        "system": {
            "platform": platform.system().lower(),
            "release": platform.release(),
            "arch": platform.machine(),
            "hostname": socket.gethostname(),
            "uptime_s": int(sandbox.uptime_seconds()),
            "totalmem_mb": sandbox.totalmem_mb(),
            "freemem_mb": sandbox.freemem_mb(),
            "cpus": cpus,
            "load_pct": load_pct,
            "node_version": sandbox.node_version_string(),
        },
        "memory_config": {
            "v8_heap_mb": heap,
            "swap_mb": swap,
            "boost_applied_at": boost_applied_at,
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

    heap_before_mb = sandbox.r1(sandbox.rss_kb() / 1024)

    set_config(db, "v8_heap_mb", str(heap))
    set_config(db, "swap_mb", str(swap))
    set_config(db, "boost_applied_at", str(now_ms()))

    heap_after_mb = sandbox.r1(sandbox.rss_kb() / 1024)

    return {
        "ok": True,
        "message": (
            f"⚡ Boost applied — Node V8 old-space limit set to {heap} MB + {swap} MB virtual swap. "
            f"New terminal sessions spawn with: node --max-old-space-size={heap}"
        ),
        "v8_heap_mb": heap,
        "swap_mb": swap,
        "totalmem_mb": sandbox.totalmem_mb(),
        "freemem_mb": sandbox.freemem_mb(),
        "heap_before_mb": heap_before_mb,
        "heap_after_mb": heap_after_mb,
        "swap_success": True,
        "swap_output": (
            f"swapon: /nova/swapfile: {swap}M\n"
            "swapon: /nova/swapfile: successfully enabled (priority 10)"
        ),
    }
