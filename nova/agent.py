"""Nova Agent — autonomous task runner (Python port of src/lib/server/agent.ts).

Loop: plan (LLM, strict JSON) → execute tool → persist AgentStep → repeat until
`finish` or the step limit; then a final LLM call writes the summary. All tool
failures are recorded as error steps and never abort the task; a cancelled
status in the DB stops the loop on the next iteration.

LLM calls go through nova.engine (the z-ai sidecar); every DB touch opens its
own SessionLocal() session — never one session across the whole run. The runner
is an async callable, so routers/agent_api.py can hand it to FastAPI
BackgroundTasks and respond immediately.
"""
from __future__ import annotations

import inspect
import json
import logging
import math
import os
import platform
import re
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import engine as nova_engine
from .database import SessionLocal
from .models import (
    AgentStep,
    AgentTask,
    ClientKey,
    Model,
    Provider,
    RequestLog,
    StorageFile,
    StorageProviderRow,
    utcnow,
)

log = logging.getLogger("nova.agent")

MAX_DETAIL_CHARS = 2000
TERMINAL_DEFAULT_CWD = "/workspace"

ALLOWED_ACTIONS = {
    "web_search",
    "read_url",
    "terminal",
    "gateway_stats",
    "storage_scan",
    "discover_models",
    "finish",
}

AGENT_SYSTEM_PROMPT = """You are Nova Agent, an autonomous research & operations agent inside the NovaRouter AI gateway.

You work toward the user's goal one step at a time using these tools:
- web_search: search the live web for up-to-date information ("query_or_url_or_command" = search query)
- read_url: read and extract any web page ("query_or_url_or_command" = full URL, must start with http)
- terminal: run a safe diagnostic command in the gateway sandbox ("query_or_url_or_command" = command, e.g. "free -m", "df -h", "nova status", "nova gpu")
- gateway_stats: inspect live gateway stats, models and routes (no input needed)
- storage_scan: scan configured storage providers and files (no input needed)
- discover_models: discover which AI models are ACTUALLY available right now from the configured providers via /v1/models ("query_or_url_or_command" = optional filter: a provider key like "groq" or "openrouter", or "free" for free-tier models only; leave empty for all providers)
- finish: the goal is achieved ("query_or_url_or_command" = final key takeaway, optional)

STRICT OUTPUT RULE — respond with JSON only, no prose, no markdown fences, exactly this shape:
{"action": "web_search|read_url|terminal|gateway_stats|storage_scan|discover_models|finish", "title": "short title", "query_or_url_or_command": "...", "reason": "why this step"}

Rules:
1. Exactly one action per response.
2. Titles are short labels (max 60 chars); "reason" explains why this step helps the goal.
3. Prefer web_search then read_url to gather evidence; use terminal/gateway_stats/storage_scan for system questions; use discover_models whenever the goal involves finding, comparing or choosing AI models or providers.
4. Never repeat a step that already succeeded with the same input — read the step log first.
5. As soon as the goal is achieved (or no further step adds value), respond with action "finish"."""

FINAL_SYSTEM_PROMPT = (
    "Write the final answer to the goal in concise markdown (≤200 words), using the evidence gathered."
)


# --------------------------------------------------------------------------- #
# Serialization (snake_case wire format per src/lib/types.ts)
# --------------------------------------------------------------------------- #

def _epoch_ms(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def serialize_agent_task(task: AgentTask) -> dict:
    return {
        "id": task.id,
        "goal": task.goal,
        "status": task.status,
        "model": task.model,
        "max_steps": task.maxSteps,
        "summary": task.summary,
        "error": task.error,
        "created_at": _epoch_ms(task.createdAt),
        "started_at": _epoch_ms(task.startedAt),
        "finished_at": _epoch_ms(task.finishedAt),
        "steps": [
            {
                "id": s.id,
                "step_number": s.stepNumber,
                "action": s.action,
                "title": s.title,
                "description": s.description,
                "detail": s.detail,
                "status": s.status,
                "latency_ms": s.latencyMs,
                "created_at": _epoch_ms(s.createdAt),
            }
            for s in task.steps
        ],
    }


# --------------------------------------------------------------------------- #
# Planning (LLM)
# --------------------------------------------------------------------------- #

@dataclass
class AgentPlan:
    action: str
    title: str
    query: str
    reason: str


def parse_plan(raw: str) -> AgentPlan | None:
    """Defensively parse the planner output: strip code fences, find the first
    {...} block (verbatim port of parsePlan)."""
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.IGNORECASE)
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None

    def as_str(value) -> str:
        return "" if value is None else str(value)

    action = as_str(obj.get("action")).strip()
    if action not in ALLOWED_ACTIONS:
        return None
    return AgentPlan(
        action=action,
        title=as_str(obj.get("title")).strip()[:120] or action,
        query=as_str(obj.get("query_or_url_or_command")).strip(),
        reason=as_str(obj.get("reason")).strip(),
    )


def build_planner_user_prompt(goal: str, steps: list[AgentStep]) -> str:
    if not steps:
        step_log = "(no steps yet — this is the beginning of the task)"
    else:
        lines = []
        for s in steps:
            detail = _squash(s.detail)[:200] or "(no detail)"
            flag = " [FAILED]" if s.status == "error" else ""
            lines.append(f"{s.stepNumber}. [{s.action}{flag}] {s.title} → {detail}")
        step_log = "\n".join(lines)
    return (
        f"# Goal\n{goal}\n\n# Step log so far\n{step_log}\n\n"
        'Decide the next single action. Respond with STRICT JSON per the system rules. '
        'Use action "finish" when the goal is achieved.'
    )


def _squash(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _completion_content(completion) -> str:
    try:
        content = completion["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    return content if isinstance(content, str) else str(content or "")


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #

def strip_html(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def tool_web_search(query: str) -> str:
    q = query or "NovaRouter AI gateway"
    results = await nova_engine.web_search(q)
    if not isinstance(results, list) or not results:
        return f'No results for "{q}".'
    lines = []
    for i, r in enumerate(results):
        r = r if isinstance(r, dict) else {}
        name = r.get("name")
        url = r.get("url")
        snippet = r.get("snippet")
        lines.append(
            f"{i + 1}. {name}\n   {url}\n   {('' if snippet is None else str(snippet)).strip()[:200]}"
        )
    return "\n".join(lines)


async def tool_read_url(url: str) -> str:
    page = await nova_engine.read_url(url)
    data = page if isinstance(page, dict) else {}
    inner = data["data"] if isinstance(data.get("data"), dict) else data
    title = inner.get("title") or url
    text = strip_html(str(inner.get("html") or inner.get("text") or ""))[:1500]
    return f"{title}\n\n{text or '(no extractable text)'}"


def fallback_system_stats(command: str) -> str:
    """OS-level diagnostics when the safe executor module (nova.terminal,
    written by agent 2-c) is not importable yet — port of fallbackSystemStats."""
    try:
        total_mb = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 * 1024))
    except Exception:
        total_mb = 0
    free_mb = _mem_available_mb()
    cpus = os.cpu_count() or 1
    try:
        load = os.getloadavg()[0]
    except Exception:
        load = 0.0
    load_pct = min(99, round(load / max(1, cpus) * 100))
    mem_pct = round(free_mb / max(1, total_mb) * 100)
    uptime_s = _uptime_seconds()
    return "\n".join([
        f"$ {command}",
        "[sandbox fallback] Safe executor module unavailable — basic system diagnostics only:",
        f"Host: {socket.gethostname()} · {platform.system()} {platform.machine()} · Python {sys.version.split()[0]}",
        f"CPUs: {cpus} · Load: {load_pct}%",
        f"Memory: {free_mb} MB free / {total_mb} MB total ({mem_pct}% free)",
        f"Uptime: {uptime_s}s",
    ])


def _mem_available_mb() -> int:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES") / (1024 * 1024))
    except Exception:
        return 0


def _uptime_seconds() -> int:
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as fh:
            return int(float(fh.read().split()[0]))
    except Exception:
        return 0


def _exec_field(res, key: str):
    if isinstance(res, dict):
        return res.get(key)
    return getattr(res, key, None)


async def tool_terminal(command: str) -> str:
    """Run a command through agent 2-c's safe executor. nova.terminal is
    imported LAZILY inside this function (it may not exist yet while 2-c works
    concurrently); if it is unavailable we degrade to honest OS stats, exactly
    like the TypeScript fallback."""
    cmd = command or "nova status"
    try:
        from nova.terminal import execute_command  # noqa: PLC0415 — lazy by design
    except Exception:
        return fallback_system_stats(cmd)

    db: Session = SessionLocal()
    try:
        try:
            res = execute_command(db, cmd, TERMINAL_DEFAULT_CWD)
        except TypeError:
            # signature drift guard — try the 2-arg variant before giving up
            res = execute_command(db, cmd)
        if inspect.isawaitable(res):
            res = await res
    except Exception:
        return fallback_system_stats(cmd)
    finally:
        db.close()

    out = _exec_field(res, "output")
    if out is None:
        parts = [
            str(x)
            for x in (_exec_field(res, "stdout"), _exec_field(res, "stderr"))
            if x and str(x).strip()
        ]
        out = "\n".join(parts)
    out = str(out).strip()
    return f"$ {cmd}\n{out or '(no output)'}"


async def tool_gateway_stats() -> str:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    with SessionLocal() as db:
        providers = db.scalar(select(func.count()).select_from(Provider)) or 0
        enabled_providers = (
            db.scalar(select(func.count()).select_from(Provider).where(Provider.enabled.is_(True))) or 0
        )
        models = db.scalar(select(func.count()).select_from(Model)) or 0
        healthy = (
            db.scalar(
                select(func.count())
                .select_from(Model)
                .where(Model.status == "healthy", Model.enabled.is_(True))
            )
            or 0
        )
        client_keys = db.scalar(select(func.count()).select_from(ClientKey)) or 0
        logs = db.execute(
            select(RequestLog.status, RequestLog.latencyMs).where(RequestLog.ts >= since)
        ).all()

    errors = sum(1 for status, _ in logs if status >= 400)
    avg_latency = _js_round(sum(lat for _, lat in logs) / len(logs)) if logs else 0
    err_pct = f"{(errors / len(logs)) * 100:.1f}" if logs else "0.0"
    return "\n".join([
        "Gateway telemetry (live):",
        f"• Providers: {providers} registered · {enabled_providers} enabled",
        f"• Models: {models} in catalogue · {healthy} healthy",
        f"• Client keys: {client_keys}",
        f"• Requests (24h): {len(logs)} · Errors: {errors} ({err_pct}%) · Avg latency: {avg_latency} ms",
    ])


async def tool_storage_scan() -> str:
    with SessionLocal() as db:
        providers = db.scalars(
            select(StorageProviderRow).order_by(
                StorageProviderRow.active.desc(), StorageProviderRow.id.asc()
            )
        ).all()
        files = db.execute(select(StorageFile.name, StorageFile.size)).all()

    lines = []
    for p in providers:
        flag = "ACTIVE" if p.active else p.status
        usage = (
            f"— {(p.usageMb or 0):.1f}/{(p.quotaMb or 0):.0f} MB"
            if (p.quotaMb or 0) > 0
            else f"— {(p.usageMb or 0):.1f} MB used"
        )
        tier = f" · free tier: {p.freeTier}" if p.freeTier else ""
        lines.append(f"• {p.id} ({p.type}): {flag} {usage}{tier}")

    total_bytes = sum(size for _, size in files)
    return "\n".join([
        "Storage scan:",
        *(lines if lines else ["• (no storage providers configured)"]),
        f"Files stored: {len(files)} (total {total_bytes / (1024 * 1024):.2f} MB)",
    ])


async def tool_discover_models(query: str) -> str:
    """Live model discovery via nova.discovery (port of toolDiscoverModels)."""
    q = (query or "").strip().lower()
    is_free_filter = q in ("free", "free models")
    provider_filter = None if is_free_filter or not q else q
    try:
        from .discovery import discover_provider_models, summarize_for_agent  # noqa: PLC0415

        with SessionLocal() as db:
            results = await discover_provider_models(db, provider_filter)
        if is_free_filter:
            filtered = []
            for r in results:
                if not isinstance(r, dict):
                    continue
                free_models = [m for m in r.get("models", []) if m.get("is_free")]
                filtered.append({**r, "models": free_models, "count": len(free_models)})
            results = filtered
        return summarize_for_agent(results, "free" if is_free_filter else provider_filter)
    except Exception as err:
        return f"Model discovery failed: {err}"


async def execute_tool(plan: AgentPlan) -> str:
    if plan.action == "web_search":
        return await tool_web_search(plan.query)
    if plan.action == "read_url":
        return await tool_read_url(plan.query)
    if plan.action == "terminal":
        return await tool_terminal(plan.query)
    if plan.action == "gateway_stats":
        return await tool_gateway_stats()
    if plan.action == "storage_scan":
        return await tool_storage_scan()
    if plan.action == "discover_models":
        return await tool_discover_models(plan.query)
    raise ValueError(f"Unknown action: {plan.action}")


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _js_round(value: float) -> int:
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


async def run_agent_task(task_id: str) -> None:
    """Fire-and-forget runner — background task; every failure lands in the
    AgentTask row, never raised out of the loop."""
    try:
        with SessionLocal() as db:
            task = db.get(AgentTask, task_id)
            if task is None or task.status in ("cancelled", "completed", "failed"):
                return
            task.status = "running"
            if task.startedAt is None:
                task.startedAt = utcnow()
            task.error = None
            goal = task.goal
            max_steps = max(1, min(task.maxSteps or 8, 24))
            db.commit()

        parse_retried = False  # one retry after an unparseable planner response

        for _ in range(max_steps):
            # Honor cancellation between steps.
            with SessionLocal() as db:
                current = db.get(AgentTask, task_id)
                if current is None or current.status == "cancelled":
                    return
                steps = list(
                    db.scalars(
                        select(AgentStep)
                        .where(AgentStep.taskId == task_id)
                        .order_by(AgentStep.stepNumber)
                    ).all()
                )
            next_step_number = (steps[-1].stepNumber if steps else 0) + 1

            # ── Plan via LLM ──
            plan_start = time.monotonic()
            raw = ""
            plan: AgentPlan | None = None
            try:
                completion = await nova_engine.chat([
                    {"role": "system", "content": AGENT_SYSTEM_PROMPT},
                    {"role": "user", "content": build_planner_user_prompt(goal, steps)},
                ])
                raw = _completion_content(completion)
                plan = parse_plan(raw)
            except Exception as err:
                raw = f"LLM call failed: {err}"
            plan_latency = _elapsed_ms(plan_start)

            if plan is None:
                # Unparseable / failed planner output → record a 'think' step,
                # retry once, then fail.
                if parse_retried:
                    with SessionLocal() as db:
                        t = db.get(AgentTask, task_id)
                        if t is not None:
                            t.status = "failed"
                            t.error = "Agent planner returned unparseable output twice"
                            t.finishedAt = utcnow()
                            db.commit()
                    return
                parse_retried = True
                with SessionLocal() as db:
                    db.add(AgentStep(
                        taskId=task_id,
                        stepNumber=next_step_number,
                        action="think",
                        title="Planning (unparsed response)",
                        description="Planner output was not valid JSON — retrying once.",
                        detail=raw[:MAX_DETAIL_CHARS],
                        status="error",
                        latencyMs=plan_latency,
                    ))
                    db.commit()
                continue
            parse_retried = False

            # ── Finish action ──
            if plan.action == "finish":
                with SessionLocal() as db:
                    db.add(AgentStep(
                        taskId=task_id,
                        stepNumber=next_step_number,
                        action="finish",
                        title=plan.title,
                        description=plan.reason,
                        detail=plan.query or plan.reason or "Goal achieved.",
                        status="info",
                        latencyMs=plan_latency,
                    ))
                    db.commit()
                break

            # ── Execute tool with timing ──
            tool_start = time.monotonic()
            status = "ok"
            try:
                detail = await execute_tool(plan)
            except Exception as err:
                detail = f"Tool error: {err}"
                status = "error"
            tool_latency = _elapsed_ms(tool_start)

            with SessionLocal() as db:
                db.add(AgentStep(
                    taskId=task_id,
                    stepNumber=next_step_number,
                    action=plan.action,
                    title=plan.title,
                    description=plan.reason,
                    detail=detail[:MAX_DETAIL_CHARS],
                    status=status,
                    latencyMs=tool_latency,
                ))
                db.commit()

        # Honor cancellation after the loop as well.
        with SessionLocal() as db:
            after_loop = db.get(AgentTask, task_id)
            if after_loop is None or after_loop.status == "cancelled":
                return

        # ── Final summary via LLM ──
        with SessionLocal() as db:
            all_steps = list(
                db.scalars(
                    select(AgentStep)
                    .where(AgentStep.taskId == task_id)
                    .order_by(AgentStep.stepNumber)
                ).all()
            )
        evidence = (
            "\n\n".join(f"{s.stepNumber}. [{s.action}] {s.title}\n{s.detail}" for s in all_steps)
            or "(no steps recorded)"
        )
        summary = ""
        try:
            completion = await nova_engine.chat([
                {"role": "system", "content": FINAL_SYSTEM_PROMPT},
                {"role": "user", "content": f"# Goal\n{goal}\n\n# Evidence gathered\n{evidence}"},
            ])
            summary = _completion_content(completion).strip()
        except Exception:
            summary = ""

        if not summary:
            # Graceful fallback if the final LLM call fails.
            parts = []
            for s in all_steps:
                line = f"- **{s.title}** ({s.action})"
                if s.detail:
                    line += f": {_squash(s.detail)[:160]}"
                parts.append(line)
            summary = "\n".join(parts) or "Task ended without recorded steps."

        with SessionLocal() as db:
            t = db.get(AgentTask, task_id)
            if t is not None:
                t.status = "completed"
                t.summary = summary
                t.finishedAt = utcnow()
                db.commit()
    except Exception as err:
        try:
            with SessionLocal() as db:
                t = db.get(AgentTask, task_id)
                if t is not None:
                    t.status = "failed"
                    t.error = str(err)
                    t.finishedAt = utcnow()
                    db.commit()
        except Exception:
            pass
