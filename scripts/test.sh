#!/bin/sh
# NovaRouter — full offline check: byte-compile, Python tests, dashboard JS tests.
#
# The Python suite needs the same dependencies the server does, so the install
# step is shared with scripts/preview.sh. Runs entirely offline: the provider
# protocol tests use httpx MockTransport, and the gateway needs no keys.
set -e

cd "$(dirname "$0")/.."

PY=${PYTHON:-python3}

echo "[novarouter] test: installing python dependencies"
"$PY" -m pip install --no-cache-dir --disable-pip-version-check --quiet -r requirements.txt \
  || "$PY" -m pip install --no-cache-dir --disable-pip-version-check --quiet --user -r requirements.txt

echo "[novarouter] test: byte-compiling"
"$PY" -m compileall -q main.py maintenance.py nova routers ui terminal && echo "lint ok"

echo "[novarouter] test: python suite"
"$PY" -m unittest discover -s tests -p "test_*.py" -v

if command -v node >/dev/null 2>&1; then
  echo "[novarouter] test: dashboard navigation suite"
  node --test tests/test_app_navigation.cjs
  echo "[novarouter] test: terminal workspace suite"
  node --test tests/test_terminal_workspace.cjs
  echo "[novarouter] test: standalone terminal console suite"
  node --test tests/test_terminal_console.cjs
else
  echo "[novarouter] test: node not available, skipping the JS suite"
fi

echo "[novarouter] test: all checks passed"
