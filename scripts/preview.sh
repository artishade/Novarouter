#!/usr/bin/env bash
# NovaRouter preview launcher — installs the Python dependencies, then runs
# the FastAPI app on 0.0.0.0:$PORT.
# POSIX-safe: the Freebuff preview runner executes it with `sh` (dash),
# so no bash-only features (pipefail, [[ ]], source) may appear here.
set -eu
cd "$(dirname "$0")/.."

PY="python3"
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
fi

# The sandbox and most CI images ship Python without this project's packages,
# and their install step only has a Node toolchain, so the dependencies have to
# be installed here — in the run step — before the app can import fastapi.
# Already-satisfied requirements make this a fast no-op on a warm start.
if ! "$PY" -c "import fastapi, uvicorn, sqlalchemy, jinja2, httpx" >/dev/null 2>&1; then
  echo "[novarouter] preview: installing python dependencies into ${PY}"
  if [ "$PY" != "python3" ]; then
    "$PY" -m pip install --no-cache-dir --disable-pip-version-check --quiet -r requirements.txt
  else
    python3 -m venv .venv 2>/dev/null && PY=".venv/bin/python" \
      || python3 -m pip install --no-cache-dir --disable-pip-version-check --quiet -r requirements.txt \
      || python3 -m pip install --no-cache-dir --disable-pip-version-check --quiet --user -r requirements.txt
  fi
fi

# Stop a gateway left behind by an earlier preview run. A hard restart cannot
# run the shutdown hook, so the old process keeps holding its port and the
# preview then answers with stale code.
for pid in $(ps -eo pid,args | grep '[p]ython3 main.py' | awk '{print $1}'); do
  [ "$pid" = "$$" ] && continue
  [ "$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)" = "$PWD" ] || continue
  echo "[novarouter] preview: stopping stale gateway process ${pid}"
  kill "$pid" 2>/dev/null || true
done

# The engine sidecar is a private loopback service and is not part of the
# preview. Sandbox preview port detection advertises a loopback listener in
# preference to the public port, so with the sidecar running the preview URL
# resolves to the sidecar (404 {"error":"not found"}) instead of the
# dashboard. Keeping it off leaves the app as the only candidate; the gateway
# then degrades to the configured upstreams, as it does anywhere the sidecar
# cannot run. Production (Docker/Render) is unaffected.
: "${NOVA_ENGINE_DISABLED:=1}"
export NOVA_ENGINE_DISABLED

echo "[novarouter] preview: ${PY} on 0.0.0.0:${PORT:-3000}"
exec "$PY" main.py
