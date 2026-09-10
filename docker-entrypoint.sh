#!/bin/sh
# NovaRouter container entrypoint.
#
# Why root at start: Fly.io (and plain Docker) mount volumes/filesystems as
# root. The app itself must NOT run as root, so we fix /data ownership once,
# then drop to the unprivileged 'nova' user via gosu and exec uvicorn.

set -eu

DATA_DIR="${NOVA_DATA_DIR:-/data}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    # chown only when ownership is wrong (fresh Fly volumes mount as root:root).
    # Avoids a slow recursive chown over the whole volume on every boot.
    if [ "$(stat -c '%u:%g' "$DATA_DIR" 2>/dev/null || echo '?')" != "$(id -u nova):$(id -g nova)" ]; then
        chown -R nova:nova "$DATA_DIR" 2>/dev/null || true
    fi

    if [ -n "${DATABASE_URL:-}" ]; then
        echo "[novarouter] DATABASE_URL set - using Postgres backend."
    else
        echo "[novarouter] DATABASE_URL not set - using SQLite at $DATA_DIR"
        # Hard requirement on a VM: SQLite must live on the persistent volume.
        if [ ! -d "$DATA_DIR" ] || [ ! -w "$DATA_DIR" ]; then
            echo "[novarouter] FATAL: data dir $DATA_DIR missing or not writable. Mount a volume at /data (fly volumes create)." >&2
            exit 1
        fi
    fi

    echo "[novarouter] dropping to user 'nova', starting on ${NOVA_HOST:-0.0.0.0}:${NOVA_PORT:-8080}"
    if command -v gosu >/dev/null 2>&1; then
        exec gosu nova:nova python -m uvicorn app.main:app \
            --host "${NOVA_HOST:-0.0.0.0}" \
            --port "${NOVA_PORT:-8080}" \
            --workers "${NOVA_WORKERS:-1}" \
            --proxy-headers
    else
        # gosu missing (rare minimal hosts): fall back to su.
        exec su -s /bin/sh nova -c "python -m uvicorn app.main:app --host ${NOVA_HOST:-0.0.0.0} --port ${NOVA_PORT:-8080} --workers ${NOVA_WORKERS:-1} --proxy-headers"
    fi
else
    # Already unprivileged (local docker run -u, fly ssh sessions, etc.)
    echo "[novarouter] running as $(id -un), starting on ${NOVA_HOST:-0.0.0.0}:${NOVA_PORT:-8080}"
    exec python -m uvicorn app.main:app \
        --host "${NOVA_HOST:-0.0.0.0}" \
        --port "${NOVA_PORT:-8080}" \
        --workers "${NOVA_WORKERS:-1}" \
        --proxy-headers
fi
