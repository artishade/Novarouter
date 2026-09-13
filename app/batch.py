"""Batch API engine: OpenAI-style async bulk jobs.

A batch consumes a JSONL input file (uploaded via /v1/files with purpose
"batch"), runs every request through the normal gateway fan-out (so model
fallback / key rotation / spoofing all apply), and writes a JSONL output
file the client fetches via /v1/files/{id}/content.

In-process worker: a batch runs only while the process lives. On restart,
"in_progress" batches stay parked — the sweep marks them expired after 24h.
"""
import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional

from . import config, db, store

_running: Dict[str, asyncio.Task] = {}
_running_lock = asyncio.Lock()


def _parse_jsonl(raw: bytes) -> List[Dict[str, Any]]:
    """Parse the batch input file. Each line: {"custom_id": ..., "body": {chat request}}."""
    out: List[Dict[str, Any]] = []
    for line in (raw.decode("utf-8", errors="replace").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def create_batch(client: Dict[str, Any], input_file_id: str, metadata_extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Validate the input file and register the batch. Raises ValueError on bad input."""
    content = store.get_file_content(input_file_id, client["id"])
    if content is None:
        raise ValueError(f"input file '{input_file_id}' not found")
    items = _parse_jsonl(content)
    if not items:
        raise ValueError("input file contains no valid JSONL request lines")
    if len(items) > config.BATCH_MAX_ITEMS:
        raise ValueError(f"batch exceeds the {config.BATCH_MAX_ITEMS} request limit")
    for i, item in enumerate(items):
        body = item.get("body")
        if not isinstance(body, dict) or not body.get("model"):
            raise ValueError(f"line {i + 1}: missing 'body' with a 'model' field")
    model = str(items[0].get("body", {}).get("model") or "")
    batch = store.create_batch(client["id"], model, len(items), input_file_id)
    batch["_items"] = items
    return batch


async def run_batch(batch_id: str, client: Dict[str, Any], items: List[Dict[str, Any]]) -> None:
    """Process every item; results land in an output JSONL file row."""
    from . import gateway  # late import: gateway imports batch-free modules only

    started = time.time()
    created = store.create_file(
        client["id"], f"batch-{batch_id}-output.jsonl", "batch_output", b"",
    )
    output_id = created["id"]
    store.update_batch(batch_id, output_file_id=output_id, status="in_progress")

    done = failed = 0
    sem = asyncio.Semaphore(max(1, config.BATCH_CONCURRENCY))

    async def one(item: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal done, failed
        custom_id = item.get("custom_id") or f"req-{uuid.uuid4().hex[:12]}"
        body = dict(item.get("body") or {})
        body["stream"] = False
        _dispatch_error = ""
        async with sem:
            # check cancellation between items
            current = store.get_batch(batch_id)
            if not current or current.get("status") != "in_progress":
                return {}
            try:
                result = await gateway.dispatch(
                    None, "chat",
                    payload_override=body,
                    client_override=client,
                    endpoint_label="batch",
                )
            except Exception as e:  # noqa: BLE001 - one bad item must not kill the batch
                result = None
                _dispatch_error = f"{type(e).__name__}: {e}"[:200]
        status_code = 200
        resp_body: Dict[str, Any] = {}
        error_msg = ""
        if isinstance(result, gateway.JSONResponse) and result.status_code == 200:
            try:
                resp_body = json.loads(result.body)
            except (ValueError, TypeError):
                resp_body = {}
        elif isinstance(result, gateway.JSONResponse):
            status_code = result.status_code
            try:
                resp_body = json.loads(result.body)
            except (ValueError, TypeError):
                resp_body = {}
            err_obj = resp_body.get("error") or {}
            error_msg = err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj)
        else:
            status_code = 502
            error_msg = _dispatch_error or "unexpected dispatch result"
        line = {
            "id": batch_id,
            "custom_id": custom_id,
            "response": {
                "status_code": status_code,
                "body": resp_body,
                "request_id": f"req_{uuid.uuid4().hex[:24]}",
            },
        }
        if error_msg:
            line["error"] = error_msg
        if status_code == 200:
            done += 1
        else:
            failed += 1
        return line

    try:
        lines = await asyncio.gather(*(one(item) for item in items))
        # a cancelled batch leaves gaps -> drop empty rows
        payload = "".join(
            json.dumps(ln, ensure_ascii=False) + "\n"
            for ln in lines if ln
        )
        store.append_file_content(output_id, payload.encode("utf-8"))
        current = store.get_batch(batch_id) or {}
        if current.get("status") == "in_progress":
            store.update_batch(
                batch_id, status="completed", completed_at=time.time(),
                done=done, failed=failed,
            )
        else:
            store.update_batch(batch_id, done=done, failed=failed)
    except Exception as e:  # noqa: BLE001
        store.update_batch(
            batch_id, status="failed", error=f"{type(e).__name__}: {e}"[:300],
            done=done, failed=failed, completed_at=time.time(),
        )
    finally:
        async with _running_lock:
            _running.pop(batch_id, None)


def start(batch_id: str, client: Dict[str, Any], items: List[Dict[str, Any]]) -> None:
    """Spawn the batch worker task (fire and forget)."""
    async def _guard():
        await run_batch(batch_id, client, items)

    task = asyncio.create_task(_guard())
    _running[batch_id] = task


async def cancel(batch_id: str, client_key_id: int) -> Dict[str, Any]:
    """Mark cancelled; the worker notices before its next item."""
    row = store.get_batch(batch_id)
    if not row or row.get("client_key_id") != client_key_id:
        return {}
    if row.get("status") in ("completed", "failed", "cancelled", "expired"):
        return row
    store.update_batch(batch_id, status="cancelled", completed_at=time.time())
    return store.get_batch(batch_id) or row


def sweep_expired() -> int:
    """Expire batches past their window. Called from the periodic loop."""
    return store.expire_stale_batches()
