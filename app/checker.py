"""Multi-provider model availability checker.

This is the evolved version of the original single-provider
`check_openrouter_models.py`: it now walks EVERY provider registered in the
gateway, uses that provider's own keys (rotating them), and writes results back
into the `models` table so the dashboard and the router can use them.

Runnable two ways:
  * from the dashboard  -> POST /admin/api/check  (background job, live progress)
  * from the CLI        -> `novarouter check --provider groq --free`
"""

import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx

from . import adapters, config, store

# ---------------------------------------------------------------- job registry

_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = asyncio.Lock()


def new_job(total: int, scope: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "id": job_id,
        "scope": scope,
        "state": "running",
        "total": total,
        "done": 0,
        "started_at": time.time(),
        "finished_at": None,
        "counts": {},
        "rows": [],
        "error": "",
    }
    return job_id


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    job = _jobs.get(job_id)
    if not job:
        return None
    # trim rows in the API view; full rows stay in memory
    return {**job, "rows": job["rows"][-200:]}


def list_jobs() -> List[Dict[str, Any]]:
    return [
        {k: v for k, v in j.items() if k != "rows"}
        for j in sorted(_jobs.values(), key=lambda x: x["started_at"], reverse=True)
    ][:20]


def cancel_job(job_id: str) -> bool:
    job = _jobs.get(job_id)
    if job and job["state"] == "running":
        job["state"] = "cancelling"
        return True
    return False


# ---------------------------------------------------------------- single check

async def ping_model(
    client: httpx.AsyncClient,
    provider: Dict[str, Any],
    api_key: str,
    model_id: str,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """One minimal chat call. Returns dict(http_status, status, detail, latency_ms)."""
    payload = adapters.ping_payload(provider, model_id)
    try:
        url, headers, body = adapters.build_request(provider, api_key, "chat", payload)
    except ValueError as e:
        return {"http_status": 0, "status": adapters.BAD_REQUEST, "detail": str(e), "latency_ms": 0}

    started = time.perf_counter()
    try:
        resp = await client.post(url, headers=headers, json=body, timeout=timeout)
        latency = int((time.perf_counter() - started) * 1000)

        if resp.status_code != 200:
            return {
                "http_status": resp.status_code,
                "status": adapters.classify(resp.status_code),
                "detail": adapters.extract_error(resp.status_code, resp.text),
                "latency_ms": latency,
            }

        try:
            data = resp.json()
        except ValueError:
            return {
                "http_status": 200,
                "status": adapters.BAD_REQUEST,
                "detail": "non-JSON 200 response",
                "latency_ms": latency,
            }

        err = adapters.response_has_error(data)
        if err:
            return {"http_status": 200, "status": adapters.NO_ACCESS, "detail": err, "latency_ms": latency}

        if provider.get("kind") == "anthropic":
            reply = adapters._text_of(data.get("content"))
        else:
            choices = data.get("choices") or [{}]
            reply = ((choices[0].get("message") or {}).get("content")) or ""
        return {
            "http_status": 200,
            "status": adapters.OK,
            "detail": (reply or "").strip()[:80],
            "latency_ms": latency,
        }
    except httpx.TimeoutException:
        return {
            "http_status": 0,
            "status": adapters.NETWORK_ERROR,
            "detail": f"timeout after {timeout}s",
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    except Exception as e:  # noqa: BLE001 - checker must never crash the job
        return {
            "http_status": 0,
            "status": adapters.NETWORK_ERROR,
            "detail": f"{type(e).__name__}: {e}"[:160],
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }


# ---------------------------------------------------------------- sync models

async def sync_provider_models(
    client: httpx.AsyncClient, provider: Dict[str, Any], prune: bool = True
) -> Dict[str, Any]:
    """Fetch the provider's model catalogue with any working key and store it."""
    keys = store.pick_keys(provider["id"], limit=len(store.list_upstream_keys(provider["id"])) or 1)
    if not keys:
        return {"provider": provider["name"], "ok": False, "error": "no keys configured", "count": 0}

    last_err = "unknown"
    for key in keys:
        try:
            models = await adapters.list_models(client, provider, key["api_key"])
            count = store.upsert_models(provider["id"], models, prune=prune)
            return {"provider": provider["name"], "ok": True, "count": count, "error": ""}
        except httpx.HTTPStatusError as e:
            last_err = f"HTTP {e.response.status_code}: {adapters.extract_error(e.response.status_code, e.response.text)}"
            store.penalize_key(key["id"], e.response.status_code, last_err)
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"[:160]
    return {"provider": provider["name"], "ok": False, "error": last_err, "count": 0}


async def sync_all_models(provider_ids: Optional[List[int]] = None, prune: bool = True) -> List[Dict[str, Any]]:
    providers = [
        p for p in store.list_providers()
        if (provider_ids is None or p["id"] in provider_ids)
    ]
    out = []
    async with httpx.AsyncClient(timeout=60) as client:
        for p in providers:
            out.append(await sync_provider_models(client, p, prune=prune))
    return out


# ---------------------------------------------------------------- bulk check

async def run_check(
    provider_ids: Optional[List[int]] = None,
    free_only: bool = False,
    only_unknown: bool = False,
    model_filter: str = "",
    limit: int = 0,
    workers: int = 6,
    timeout: float = 30.0,
    retry: int = 0,
    retry_delay: float = 3.0,
    sync_first: bool = True,
    job_id: Optional[str] = None,
    on_row=None,
) -> Dict[str, Any]:
    """Ping every selected model across every selected provider."""
    async with httpx.AsyncClient(timeout=timeout + 10) as client:
        if sync_first:
            await sync_all_models(provider_ids)

        targets: List[Dict[str, Any]] = []
        providers = {
            p["id"]: p for p in store.list_providers()
            if p["enabled"] and (provider_ids is None or p["id"] in provider_ids)
        }
        for pid in providers:
            rows = store.list_models(
                provider_id=pid,
                status="UNKNOWN" if only_unknown else None,
                free_only=free_only,
                search=model_filter,
            )
            targets.extend(rows)
        targets.sort(key=lambda r: (r["provider_name"], r["model_id"]))
        if limit:
            targets = targets[:limit]

        if job_id and job_id in _jobs:
            _jobs[job_id]["total"] = len(targets)

        # pre-resolve a rotating key list per provider
        keys_by_provider = {pid: store.pick_keys(pid, limit=99) for pid in providers}

        sem = asyncio.Semaphore(max(1, workers))
        results: List[Dict[str, Any]] = []
        key_cursor = {pid: 0 for pid in providers}
        cursor_lock = asyncio.Lock()

        async def next_key(pid: int) -> Optional[Dict[str, Any]]:
            pool = keys_by_provider.get(pid) or []
            if not pool:
                return None
            async with cursor_lock:
                idx = key_cursor[pid] % len(pool)
                key_cursor[pid] += 1
            return pool[idx]

        async def check_one(row: Dict[str, Any]) -> None:
            async with sem:
                job = _jobs.get(job_id) if job_id else None
                if job and job["state"] == "cancelling":
                    return

                provider = providers[row["provider_id"]]
                key = await next_key(provider["id"])
                if key is None:
                    outcome = {
                        "http_status": 0,
                        "status": adapters.NO_ACCESS,
                        "detail": "no key configured for provider",
                        "latency_ms": 0,
                    }
                else:
                    outcome = await ping_model(client, provider, key["api_key"], row["model_id"], timeout)
                    attempts = 1
                    while outcome["status"] == adapters.RATE_LIMITED and attempts <= retry:
                        await asyncio.sleep(retry_delay * attempts)
                        alt = await next_key(provider["id"]) or key
                        outcome = await ping_model(client, provider, alt["api_key"], row["model_id"], timeout)
                        attempts += 1

                store.set_model_status(
                    row["id"], outcome["status"], outcome["http_status"],
                    outcome["detail"], outcome["latency_ms"],
                )
                if key:
                    if outcome["status"] == adapters.OK:
                        store.reward_key(key["id"])
                    elif outcome["status"] in (adapters.RATE_LIMITED, adapters.NEEDS_CREDIT, adapters.NO_ACCESS):
                        store.penalize_key(key["id"], outcome["http_status"], outcome["detail"])

                record = {
                    "provider": provider["name"],
                    "model": row["exposed_id"],
                    "upstream_model": row["model_id"],
                    "free": bool(row["is_free"]),
                    **outcome,
                }
                results.append(record)

                if job:
                    job["done"] += 1
                    job["rows"].append(record)
                    job["counts"][outcome["status"]] = job["counts"].get(outcome["status"], 0) + 1
                if on_row:
                    on_row(record)

        await asyncio.gather(*(check_one(r) for r in targets))

    summary = {
        "total": len(results),
        "counts": {},
        "by_provider": {},
    }
    for r in results:
        summary["counts"][r["status"]] = summary["counts"].get(r["status"], 0) + 1
        bucket = summary["by_provider"].setdefault(r["provider"], {})
        bucket[r["status"]] = bucket.get(r["status"], 0) + 1

    if job_id and job_id in _jobs:
        job = _jobs[job_id]
        job["state"] = "done" if job["state"] != "cancelling" else "cancelled"
        job["finished_at"] = time.time()
        job["counts"] = summary["counts"]
        job["summary"] = summary

    return {"summary": summary, "results": results}


async def start_background_check(**kwargs) -> str:
    scope = kwargs.get("scope", "all providers")
    job_id = new_job(0, scope)
    kwargs.pop("scope", None)

    async def runner():
        try:
            await run_check(job_id=job_id, **kwargs)
        except Exception as e:  # noqa: BLE001
            _jobs[job_id]["state"] = "error"
            _jobs[job_id]["error"] = f"{type(e).__name__}: {e}"[:300]
            _jobs[job_id]["finished_at"] = time.time()

    asyncio.create_task(runner())
    return job_id