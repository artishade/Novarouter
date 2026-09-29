#!/usr/bin/env bash
# NovaRouter preview launcher — runs the FastAPI app on 0.0.0.0:$PORT.
# POSIX-safe: the Freebuff preview runner executes it with `sh` (dash),
# so no bash-only features (pipefail, [[ ]], source) may appear here.
set -eu
cd "$(dirname "$0")/.."

PY="python3"
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
fi

exec "$PY" main.py
