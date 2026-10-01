"""UI smoke test — renders every dashboard tab fragment in-process.

    python3 tests/smoke_ui.py
"""
from __future__ import annotations

import asyncio
import os
import re
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
    "terminal": ["tab-terminal", "term-view", "Root@Build", "Nova Console", "ws-config",
                 "ws-landing", "term-sheet-close", "ws-keyrow", "data-sheet-open=\"term\"",
                 "data-sheet-open=\"chat\""],
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
            for needle in ("Root@Build", "Nova Console", "term-view", "ws-config",
                           "ws-landing", "term-sheet-close", "ws-keyrow",
                           "term-cmdbar", "term-promptbar", "term-interrupt", "term-keys-next",
                           'data-sheet-open="term"', 'data-sheet-open="chat"',
                           "terminal_workspace.js?v=6"):
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

            # Mobile workspace CSS: the two rules that broke phones. Tailwind's
            # `.flex` used to beat the UA `[hidden]` rule (drawer stuck open) and
            # `.ws-sheet > * { height: 100% }` stacked every panel row below the
            # fold, so nothing was tappable.
            res = await client.get("/static/nova.css")
            css = res.text
            check(res.status_code == 200, "nova.css serves", str(res.status_code))
            check("[hidden] { display: none !important; }" in css,
                  "nova.css forces [hidden] to win over Tailwind display utilities")
            check(".ws-sheet > * { min-height: 0; }" in css and "height: 100%" not in css.split(".ws-sheet")[1][:400],
                  "mobile sheet children are not forced to 100% height")
            check("env(safe-area-inset-bottom" in css, "key row respects the iOS home indicator")

            # Phone terminal chrome: compact session strip, command bar,
            # prompt bar and a real extra-keys grid (7 columns, uppercase).
            check(".ws-term-chrome" in css and ".ws-hide-mobile" in css
                  and ".ws-chip" in css and ".ws-prompt-badge" in css,
                  "mobile terminal chrome is styled (chips, prompt badge, grid)")
            check("grid-template-columns: repeat(7" in css and ".ws-key.is-wide" in css,
                  "extra-key grid is a 7-column phone grid with a wide space key")

            # Workspace JS: sheet switching + the xterm fit guard. A fixed
            # sheet always reports offsetParent === null, which used to make
            # fit() a no-op on phones.
            res = await client.get("/static/terminal_workspace.js")
            js = res.text
            check(res.status_code == 200, "terminal_workspace.js serves", str(res.status_code))
            check("if (!view.clientWidth || !view.clientHeight) return;" in js and "if (!view.offsetParent)" not in js,
                  "xterm fit() is not gated on offsetParent (breaks on fixed sheets)")
            check("data-sheet-open" in js and "landing.classList.toggle('is-hidden'" in js,
                  "mobile landing buttons and close handles are wired")
            check("watchAgentTerminal" in js and "newestAgentTab" in js,
                  "chatbox pulls the workspace over to the agent's live session")
            # One keystroke per POST can arrive out of order and mangle words
            # on a phone keyboard, so input has to be queued and coalesced.
            check("sendQueue" in js and "flushSend" in js and "sendBusy" in js,
                  "keystrokes are sent in order instead of one request per key")
            # xterm paints on a canvas: without help there is no way to select
            # (and so no way to copy) terminal text on a phone.
            check("selectAt" in js and "term.select(" in js and "term.selectLines(" in js,
                  "long-press selects terminal text so it can be copied")
            check("pasteClipboard" in js and "clipboard.readText" in js,
                  "key row can paste the clipboard into the shell")
            check("selectAllOutput" in js and "data-watch-agent" in js,
                  "key row selects all output; agent report can open its tab")
            check("reportHtml" in js and "outcomeOf" in js and "working…" not in js,
                  "agent chatbox renders a clean task report, not terminal logs")
            check("if (mqMobile.matches) { openSheet = 'term'; }" not in js,
                  "phones no longer force the terminal sheet open at load")

            # The soft keyboard must not swallow the helper-key row.
            check("visualViewport" in js and "'--kb'" in js,
                  "workspace JS publishes the keyboard inset for the sheet")
            check("bottom: var(--kb, 0px);" in css,
                  "mobile sheet bottom edge follows the keyboard inset")

            # xterm's helper textarea is what raises the keyboard; if its
            # stylesheet 404s it renders as a white box and every keystroke
            # shows up twice, above the real terminal.
            check(".xterm-helper-textarea" in css,
                  "nova.css hides xterm's helper textarea even without the CDN stylesheet")
            res = await client.get("/terminal")
            page2 = res.text
            check("@xterm/xterm@5.5.0/css/xterm.css" in page2 and "/lib/xterm.css" not in page2,
                  "/terminal points at the real xterm stylesheet path")
            check("interactive-widget=resizes-content" in page2,
                  "viewport meta lets the layout shrink for the keyboard")
            check("agent task — it runs its commands live in the terminal tab" in page2
                  or "hand a task to the agent — it runs in its own terminal tab" in page2,
                  "chatbox tells the user agent tasks run in the terminal")
            for needle in ("Long-press the terminal to select text", "ws-term-chrome",
                           "ws-hide-mobile", "term-cmdbar", "term-promptbar", "term-interrupt",
                           "term-envsetup", "term-kbd", "term-keys-next", "term-keys-label",
                           "term-prompt-badge"):
                check(needle in page2, f"/terminal: contains {needle!r}")
            # The grid itself is rendered from JS so the layout is switchable.
            for needle in ("KEY_LAYOUTS", "renderKeyRow", "nova.term.keylayout", 'data-select="1"',
                           'data-paste="1"', "PageUp", "Backspace", "stickyMod"):
                check(needle in js, f"workspace JS: contains {needle!r}")
            check("PGUP" in js and "PGDN" in js and "SPACE" in js and "^A" in js,
                  "key layouts carry the terminal's PageUp/PageDown/space/^A keys")
            # Each layout must fill exactly two rows of the 7-column grid, or
            # the last row ends up with a single orphaned key.
            grid = js.split("const KEY_LAYOUTS")[1].split("const keyRow")[0]
            for chunk in grid.split("id: '")[1:]:
                name = chunk.split("'")[0]
                entries = re.findall(r"\['(?:key|text|ctrl|mod|paste|sel)'[^\]]*\]", chunk)
                cells = sum(2 if "'SPACE'" in e else 1 for e in entries)
                check(cells == 14 and 13 <= len(entries) <= 14,
                      f"key layout {name!r} fills two 7-column rows ({len(entries)} keys, {cells} cells)")
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
