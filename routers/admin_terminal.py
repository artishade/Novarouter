"""Terminal admin routes — port of:
  src/app/api/admin/terminal/exec/route.ts        → POST /exec
  src/app/api/admin/terminal/history/route.ts     → GET  /history
  src/app/api/admin/terminal/clear/route.ts       → POST /clear
  src/app/api/admin/terminal/boost-ram/route.ts   → POST /boost-ram

Plus the real interactive terminal (nova/pty_session.py):
  POST /pty/start    → spawn a persistent PTY shell session
  GET  /pty/stream   → SSE: live output (backlog + incremental)
  POST /pty/input    → keystrokes (\r, \u0003, arrows …)
  POST /pty/resize   → cols/rows
  POST /pty/stop     → terminate the session
  GET  /pty/status    → session list
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import platform
import socket
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from nova import pty_session
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

    # One-shot exec can't answer interactive prompts — point at Live session.
    if result.get("code") not in (0,) and not result.get("ok", True):
        hint = pty_session.shell_hint(command)
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
def history(db: Session = Depends(get_db)):
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
        "cwd": cwd_raw or "/workspace",
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
    }


# --------------------------------------------------------------------------- #
# Real interactive terminal — persistent PTY sessions
# --------------------------------------------------------------------------- #


@router.post("/pty/start")
async def pty_start(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    session_id = f"pty-{uuid.uuid4().hex[:12]}"
    cwd = body.get("cwd") if isinstance(body.get("cwd"), str) else ""
    cols = body.get("cols") if isinstance(body.get("cols"), int) else 120
    rows = body.get("rows") if isinstance(body.get("rows"), int) else 32
    try:
        sess = pty_session.create_session(session_id, cwd=cwd, cols=cols, rows=rows)
    except OSError as err:
        return JSONResponse({"error": f"failed to spawn shell: {err}"}, status_code=500)
    return {"ok": True, "session": sess.id, "info": sess.info()}


@router.get("/pty/stream")
async def pty_stream(request: Request, session: str, offset: int = 0):
    sess = pty_session.get_session(session)
    if sess is None:
        return JSONResponse({"error": "no such session"}, status_code=404)

    async def gen():
        # Portable SSE: poll the session's shared scrollback buffer for
        # incremental output. (The PTY reader thread keeps filling it; no
        # dependence on server-specific response internals.)
        cursor = max(0, int(offset))
        backlog, size = sess.snapshot(cursor)
        if backlog:
            yield f"data: {json.dumps({'o': backlog.decode('utf-8', 'replace')})}\n\n"
            cursor = size
        yield f"data: {json.dumps({'session': sess.id, 'cols': sess.cols, 'rows': sess.rows})}\n\n"

        try:
            while not sess.closed:
                if await request.is_disconnected():
                    break
                data, size = sess.snapshot(cursor)
                if data:
                    cursor = size
                    yield f"data: {json.dumps({'o': data.decode('utf-8', 'replace')})}\n\n"
                else:
                    await asyncio.sleep(0.08)
                    yield f": keep-alive\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        finally:
            return

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/pty/input")
async def pty_input(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    session = body.get("session") if isinstance(body, dict) else None
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(session, str) or not isinstance(data, str):
        return JSONResponse({"error": "session and data are required"}, status_code=400)
    sess = pty_session.get_session(session)
    if sess is None or sess.closed:
        return JSONResponse({"error": "no such session"}, status_code=404)
    try:
        sess.write(data)
    except RuntimeError as err:
        return JSONResponse({"error": str(err)}, status_code=409)
    return {"ok": True}


@router.post("/pty/resize")
async def pty_resize(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    session = body.get("session") if isinstance(body, dict) else None
    sess = pty_session.get_session(session) if isinstance(session, str) else None
    if sess is None or sess.closed:
        return JSONResponse({"error": "no such session"}, status_code=404)
    sess.resize(body.get("cols") if isinstance(body.get("cols"), int) else sess.cols,
                body.get("rows") if isinstance(body.get("rows"), int) else sess.rows)
    return {"ok": True, "cols": sess.cols, "rows": sess.rows}


@router.post("/pty/stop")
async def pty_stop(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    session = body.get("session") if isinstance(body, dict) else None
    if not isinstance(session, str) or not pty_session.stop_session(session):
        return JSONResponse({"error": "no such session"}, status_code=404)
    return {"ok": True}


@router.get("/pty/status")
def pty_status():
    return {"sessions": pty_session.list_sessions()}


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
