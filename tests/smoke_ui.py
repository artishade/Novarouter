"""UI smoke test — renders every dashboard tab fragment in-process.

    python3 tests/smoke_ui.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        failures.append(f"{label}{(' — ' + detail) if detail else ''}")
        print(f"  FAIL {label}{(' — ' + detail) if detail else ''}")


TAB_CHECKS = {
    "overview": ["tab-overview", "NovaFree Engine", "Provider Health"],
    "console": ["console-root", "console-input", "Nova Console"],  # legacy console tab still renders
    "terminal": ["tab-terminal", "term-view", "Root@Build", "Nova Console", "ws-config"],
    "providers": ["providers-root"],
    "models": ["models-root"],
    "routes": ["rt-root"],
    "keys": ["keys-main"],
    "storage": ["st-root"],
    "analytics": ["tab-analytics"],
    "logs": ["tab-logs"],
}


async def main() -> int:
    # The UI is a BFF over real HTTP loopback (ui/api_client.py →
    # 127.0.0.1:$PORT), so the app must actually LISTEN for fragments to
    # render. Run a real uvicorn server on the test port.
    os.environ["PORT"] = os.environ.get("PORT", "3210")
    port = int(os.environ["PORT"])
    import main as app_module

    app = app_module.app
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())
    for _ in range(60):
        await asyncio.sleep(0.25)
        if server.started:
            break
    else:
        print("server failed to start")
        return 1
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=60.0) as client:
            res = await client.get("/")
            html = res.text
            check(res.status_code == 200, "shell renders", str(res.status_code))
            check('data-tab="terminal"' in html, "sidebar has the merged console entry")
            check("app.js?v=5" in html, "shell loads the updated app.js")

            # Standalone cloud workspace page (opened in a new browser tab).
            res = await client.get("/terminal")
            page = res.text
            check(res.status_code == 200, "/terminal renders", str(res.status_code))
            for needle in ("Root@Build", "Nova Console", "term-view", "ws-config"):
                check(needle in page, f"/terminal: contains {needle!r}")

            # Permanent cloud build config.
            res = await client.get("/api/build/info")
            data = res.json()
            ids = {p.get("id") for p in data.get("providers", [])}
            check(res.status_code == 200 and {"cloudflare_r2", "google_cloud_storage", "aws_s3"} <= ids,
                  "build targets include R2 / GCS / S3", str(sorted(ids)))
            check(bool(data.get("tools")), "agent tools permanently registered", str(data.get("tools"))[:120])

            for tab, needles in TAB_CHECKS.items():
                res = await client.get(f"/partials/tab/{tab}")
                body = res.text
                check(res.status_code == 200, f"/partials/tab/{tab} renders", str(res.status_code))
                for needle in needles:
                    check(needle in body, f"{tab}: contains {needle!r}")

            # terminal exec through the BFF
            res = await client.post("/ui/terminal/exec", json={"command": "whoami"})
            data = res.json()
            check(res.status_code == 200 and isinstance(data.get("output"), str),
                  "terminal exec works", str(data)[:120])
            print(f"       whoami → {data.get('output')!r} (root-user env: server's real uid)")

            res = await client.post("/ui/terminal/exec", json={"command": "free -m"})
            data = res.json()
            check(res.status_code == 200 and "Swap:" in (data.get("output") or ""),
                  "free -m reports real swap", str(data.get('output'))[:200])

            res = await client.post("/ui/terminal/exec", json={"command": "nova boost"})
            data = res.json()
            check(res.status_code == 200 and "memory" in (data.get("output") or "").lower(),
                  "nova boost is a real memory report now", str(data.get('output'))[:160])

            # Real shell freedom: pipes + arbitrary binaries
            res = await client.post("/ui/terminal/exec", json={"command": "echo nova | tr a-z A-Z"})
            data = res.json()
            check(data.get("code") == 0 and "NOVA" in (data.get("output") or ""),
                  "real shell: pipes work", str(data)[:120])
            res = await client.post("/ui/terminal/exec", json={"command": "git --version"})
            data = res.json()
            check(data.get("code") == 0 and "git version" in (data.get("output") or ""),
                  "real shell: git runs", str(data)[:120])
            res = await client.post("/ui/terminal/exec", json={"command": "cd / && pwd"})
            data = res.json()
            check(data.get("code") == 0, "compound commands run in bash", str(data)[:120])
            res = await client.post("/ui/terminal/exec", json={"command": "rm -rf /"})
            data = res.json()
            check(data.get("code") == 126, "host-destroying command still refused", str(data)[:120])

            res = await client.get("/api/admin/terminal/history")
            data = res.json()
            mc = data.get("memory_config") or {}
            check("v8_heap_mb" not in mc and "total_mb" in mc,
                  "history memory_config is real telemetry (no v8 fiction)", str(mc)[:160])
            check((data.get("system") or {}).get("user") in ("root",),
                  "system.user reports the real (root) user", str((data.get('system') or {}).get('user')))
    finally:
        server.should_exit = True
        await serve_task

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all UI checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
