"""In-memory live sync job registry — powers the "live logs" panel shown in
the Add Provider dialog while the new provider's model catalogue is being
discovered from its real upstream.

Jobs are tiny (bounded line buffer), single-process, and self-prune: a job
sticks around for ~30 min after finishing so slow browsers can still read
the tail. Restarting the server drops the registry (jobs are ephemeral by
design — they only describe an in-flight discovery).
"""
from __future__ import annotations

import threading
import time
from collections import deque

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}
_SEQ = 0

MAX_LINES = 600            # bounded buffer per job
JOB_TTL_S = 30 * 60        # keep finished jobs around for the tab lifetime
LINE_TTL_S = 30 * 60


def _prune_locked() -> None:
    now = time.time()
    stale = [
        jid for jid, j in _JOBS.items()
        if j["done"] and now - (j["done_at"] or j["created_at"]) > JOB_TTL_S
    ]
    for jid in stale:
        _JOBS.pop(jid, None)


def create_job(title: str) -> str:
    global _SEQ
    with _LOCK:
        _SEQ += 1
        jid = f"{int(time.time() * 1000):x}-{_SEQ}"
        _JOBS[jid] = {
            "title": title,
            "lines": deque(maxlen=MAX_LINES),
            "done": False,
            "ok": None,
            "summary": "",
            "created_at": time.time(),
            "done_at": None,
        }
        _prune_locked()
    return jid


def log_line(jid: str, text: str) -> None:
    if not text:
        return
    with _LOCK:
        job = _JOBS.get(jid)
        if job is not None:
            job["lines"].append({
                "t": int(time.time() * 1000),
                "text": str(text)[:400],
            })


def finish_job(jid: str, ok: bool, summary: str) -> None:
    with _LOCK:
        job = _JOBS.get(jid)
        if job is not None:
            job["done"] = True
            job["ok"] = bool(ok)
            job["summary"] = str(summary)[:300]
            job["done_at"] = time.time()


def get_job(jid: str) -> dict | None:
    with _LOCK:
        return _JOBS.get(jid)


def job_lines(jid: str) -> list[dict]:
    with _LOCK:
        job = _JOBS.get(jid)
        return list(job["lines"]) if job else []


__all__ = ["create_job", "log_line", "finish_job", "get_job", "job_lines"]
