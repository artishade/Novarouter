#!/usr/bin/env sh
# NovaRouter terminal service launcher — runs the Root@Build terminal as its
# own host, separate from the gateway. Everything it needs is in `terminal/`.
#
#   sh ./terminal/run.sh
#
# Then point the gateway at it:
#   NOVA_TERMINAL_URL=http://127.0.0.1:3100
#   NOVA_TERMINAL_TOKEN=<shared secret>   # set on BOTH sides
#
# POSIX-safe: the runner may execute this with `sh` (dash), so no bash-only
# features (pipefail, [[ ]], source) may appear here. Never reuses the app's
# HTTP port — NOVA_TERMINAL_PORT is the terminal's own.
set -eu
cd "$(dirname "$0")/.."

PY="python3"
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
fi

# Same reasoning as scripts/preview.sh: the install step only has a Node
# toolchain, so the Python dependencies are installed here before the service
# can import fastapi. A warm start makes this a fast no-op.
if ! "$PY" -c "import fastapi, uvicorn, sqlalchemy, httpx" >/dev/null 2>&1; then
  echo "[novarouter] terminal: installing python dependencies into ${PY}"
  if [ "$PY" != "python3" ]; then
    "$PY" -m pip install --no-cache-dir --disable-pip-version-check --quiet -r requirements.txt
  else
    python3 -m venv .venv 2>/dev/null && PY=".venv/bin/python" \
      || python3 -m pip install --no-cache-dir --disable-pip-version-check --quiet -r requirements.txt \
      || python3 -m pip install --no-cache-dir --disable-pip-version-check --quiet --user -r requirements.txt
  fi
fi

# Stop a service left behind by an earlier run; a hard restart cannot fire the
# shutdown hook, and the old process would keep holding the port.
for pid in $(ps -eo pid,args | grep '[t]erminal\.service' | awk '{print $1}'); do
  [ "$pid" = "$$" ] && continue
  [ "$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)" = "$PWD" ] || continue
  echo "[novarouter] terminal: stopping stale service process ${pid}"
  kill "$pid" 2>/dev/null || true
done

echo "[novarouter] terminal: ${PY} on 0.0.0.0:${NOVA_TERMINAL_PORT:-3100}"
# `-m` (never `python3 terminal/service.py`): the module form puts the repo root
# on sys.path, which is what `from terminal...` needs.
exec "$PY" -m terminal.service
