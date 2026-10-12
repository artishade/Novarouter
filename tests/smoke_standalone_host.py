"""Manual smoke: the terminal host as it behaves once deployed alone.

Boots terminal/service.py from a copy of terminal/ (no nova, no database),
fetches the page the way a browser would, and drives the same calls the
console's client makes. Run it with:

    .venv/bin/python tests/smoke_standalone_host.py
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

PY = os.path.abspath(".venv/bin/python")
ROOT = "/tmp/standalone-host"
PORT = 3110
TOKEN = "standalone-secret"

os.makedirs(ROOT, exist_ok=True)
shutil.rmtree(f"{ROOT}/terminal", ignore_errors=True)
shutil.copytree("terminal", f"{ROOT}/terminal",
                ignore=shutil.ignore_patterns("__pycache__"))

kids = []


def spawn(args, env=None):
    proc = subprocess.Popen(args, cwd=ROOT, env={**os.environ, **(env or {})},
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    kids.append(proc)
    return proc


def get(path, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.status, res.read().decode("utf-8", "replace"), dict(res.headers)
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8", "replace"), dict(err.headers)


def post(path, body, headers=None):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method="POST", data=data,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


AUTH = {"X-Nova-Terminal-Token": TOKEN}

try:
    spawn([PY, "-m", "terminal.service"],
          {"NOVA_TERMINAL_PORT": str(PORT), "NOVA_TERMINAL_TOKEN": TOKEN,
           "NOVA_BUILD_ROOT": f"{ROOT}/build"})
    for _ in range(60):
        try:
            get("/health")
            break
        except Exception:
            time.sleep(1)

    print("-- what a browser sees --")
    status, body, headers = get("/")
    print(f"  GET /                  {status} {headers.get('Content-Type','')}"
          f" ({len(body)} bytes)")
    for needle in ('xterm.js', 'id="tabs"', 'id="split"', 'id="zoomIn"',
                   'id="expand"', 'class="mobile-switch"', "/static/console.js"):
        print(f"    has {needle:<22} {needle in body}")
    status, js, headers = get("/static/console.js")
    print(f"  GET /static/console.js {status} {headers.get('Content-Type','')} ({len(js)} bytes)")
    print(f"  GET /agent             {get('/agent')[0]}   (docs link, same console)")

    print("-- the gate --")
    print(f"  /terminal/pty/sessions no token -> {get('/terminal/pty/sessions')[0]}"
          f"   with token -> {get('/terminal/pty/sessions', AUTH)[0]}")

    print("-- a real shell, exactly as the console drives it --")
    status, listing = get("/terminal/pty/sessions", AUTH)[0], json.loads(get("/terminal/pty/sessions", AUTH)[1])
    session = (listing.get("sessions") or [{}])[0].get("id")
    print(f"  sessions                {status} {[(s['label'], s['cwd']) for s in listing.get('sessions', [])]}")
    status, out = post("/terminal/pty/run",
                       {"command": "echo standalone-console-ok; hostname; pwd", "timeout": 30}, AUTH)
    print(f"  run                     {status} exit={out.get('code')} {out.get('output','').split()[:3]}")

    # The stream the page's EventSource opens: first frame is the backlog.
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/terminal/pty/stream?session={session}&offset=0", headers=AUTH)
    try:
        with urllib.request.urlopen(req, timeout=6) as res:
            frame = res.read(400).decode("utf-8", "replace")
        print(f"  stream first frame      {res.status} {frame[:90]}…")
    except Exception as err:
        print(f"  stream first frame      {type(err).__name__}: {err}")

    print("-- the agent, as the page calls it --")
    status, body = post("/agent/chat", {"message": "say hi"}, AUTH)
    print(f"  chat without provider   {status} {body.get('code')}")
    print(f"  /agent page             {get('/agent')[0]}")
finally:
    for proc in kids:
        proc.send_signal(signal.SIGTERM)
    time.sleep(1)
    for proc in kids:
        proc.kill()
    shutil.rmtree(ROOT, ignore_errors=True)