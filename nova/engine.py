"""NovaFree engine client — talks to the free-model sidecar (engine/index.js).

The sidecar is spawned automatically on the loopback interface only. It needs
no configuration and no API key: it routes onto the free model catalogue in
`engine/free-models.json` (keyless Pollinations/OVHcloud by default, OpenRouter
/ZeroLimitAI once their key is present). Every method raises EngineUnavailable
when the sidecar cannot run, and the gateway degrades honestly (upstreams only).
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess

import httpx

from .config import ENGINE_SIDECAR_PORT, ENGINE_SIDECAR_URL, PROJECT_ROOT

log = logging.getLogger("nova.engine")

ENGINE_DIR = PROJECT_ROOT / "engine"
ENGINE_SCRIPT = ENGINE_DIR / "index.js"

_sidecar_proc: subprocess.Popen | None = None
_sidecar_lock = asyncio.Lock()


class EngineUnavailable(RuntimeError):
    pass


async def start_sidecar() -> bool:
    """Spawn the engine sidecar; returns True when it reports healthy.

    The engine is config-free (free-ai-models based), so the only requirements
    are a JS runtime and the engine files — no credentials or config files.
    """
    global _sidecar_proc
    async with _sidecar_lock:
        if await health_check(timeout=1.0):
            return True
        runtime = shutil.which("bun") or shutil.which("node")
        if runtime is None:
            log.warning("engine sidecar: no bun/node runtime found — builtin engine disabled")
            return False
        if not ENGINE_SCRIPT.exists():
            log.warning("engine sidecar: %s missing — builtin engine disabled", ENGINE_SCRIPT)
            return False
        env = {**os.environ, "ENGINE_PORT": str(ENGINE_SIDECAR_PORT)}
        _sidecar_proc = subprocess.Popen(  # noqa: S603 — fixed argv
            [runtime, str(ENGINE_SCRIPT)],
            cwd=str(PROJECT_ROOT),  # engine/ is dependency-free; cwd keeps logs in the project
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        for _ in range(20):  # up to ~4s for boot
            await asyncio.sleep(0.2)
            if await health_check(timeout=1.0):
                log.info("engine sidecar up on %s (pid %s)", ENGINE_SIDECAR_URL, _sidecar_proc.pid)
                return True
            if _sidecar_proc.poll() is not None:
                log.warning("engine sidecar exited with code %s — builtin engine disabled", _sidecar_proc.returncode)
                _sidecar_proc = None
                return False
        log.warning("engine sidecar did not become healthy in time — builtin engine disabled")
        return False


async def stop_sidecar() -> None:
    global _sidecar_proc
    if _sidecar_proc is not None:
        _sidecar_proc.terminate()
        try:
            _sidecar_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _sidecar_proc.kill()
        _sidecar_proc = None


async def health_check(timeout: float = 2.0) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.get(f"{ENGINE_SIDECAR_URL}/health")
            return res.status_code == 200
    except Exception:
        return False


async def _post(path: str, payload: dict, timeout: float) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(f"{ENGINE_SIDECAR_URL}{path}", json=payload)
    except httpx.HTTPError as err:
        raise EngineUnavailable(f"engine sidecar unreachable: {err}") from err


async def chat(
    messages: list[dict],
    thinking_disabled: bool = True,
    timeout: float = 60.0,
    tools: list | None = None,
    tool_choice: str | dict | None = None,
    model: str | None = None,
) -> dict:
    """Non-streaming completion through the builtin engine (OpenAI-shaped).

    `model` is the model the client asked for — the engine resolves it onto a
    free model (a `nova/*` tier alias picks the best available free model, and
    a real free model id is honoured directly).
    """
    payload: dict = {"messages": messages}
    if model:
        payload["model"] = model
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    res = await _post("/chat", payload, timeout)
    if res.status_code != 200:
        try:
            detail = res.json().get("error", res.text)
        except Exception:
            detail = res.text
        raise EngineUnavailable(f"engine error: {detail}")
    return res.json()


async def chat_stream(
    messages: list[dict],
    thinking_disabled: bool = True,
    timeout: float = 120.0,
    tools: list | None = None,
    tool_choice: str | dict | None = None,
    model: str | None = None,
):
    """Streaming completion — yields raw SSE `data:` payload strings."""
    payload: dict = {"messages": messages, "stream": True}
    if model:
        payload["model"] = model
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    try:
        client = httpx.AsyncClient(timeout=timeout)
        req = client.build_request("POST", f"{ENGINE_SIDECAR_URL}/chat", json=payload)
        response = await client.send(req, stream=True)
    except httpx.HTTPError as err:
        raise EngineUnavailable(f"engine sidecar unreachable: {err}") from err

    if response.status_code != 200:
        body = (await response.aread()).decode("utf-8", "replace")
        await response.aclose()
        await client.aclose()
        raise EngineUnavailable(f"engine error: {body[:200]}")

    async def iterator():
        try:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    yield line[6:]
            yield "[DONE]"
        finally:
            await response.aclose()
            await client.aclose()

    return iterator()


async def web_search(query: str, timeout: float = 25.0) -> list[dict]:
    res = await _post("/search", {"query": query}, timeout)
    if res.status_code != 200:
        try:
            detail = res.json().get("error", res.text)
        except Exception:
            detail = res.text
        raise EngineUnavailable(f"search error: {detail}")
    return res.json().get("results", [])


async def read_url(url: str, timeout: float = 30.0) -> dict:
    res = await _post("/read_url", {"url": url}, timeout)
    if res.status_code != 200:
        try:
            detail = res.json().get("error", res.text)
        except Exception:
            detail = res.text
        raise EngineUnavailable(f"read_url error: {detail}")
    return res.json()
