#!/bin/sh
set -e

echo "[nova] NovaRouter starting..."

# Resolve the SQLite path from DATABASE_URL (e.g. file:/app/db/custom.db -> /app/db/custom.db)
DB_PATH="${DATABASE_URL#file:}"

if [ -f "$DB_PATH" ]; then
  echo "[nova] existing database found at $DB_PATH — skipping init"
else
  echo "[nova] no database at $DB_PATH — creating schema..."
  ./node_modules/.bin/prisma db push --accept-data-loss --skip-generate

  if [ "${NOVA_SEED:-1}" = "1" ]; then
    echo "[nova] seeding realistic demo data (set NOVA_SEED=0 to skip)..."
    bun prisma/seed.ts
  else
    echo "[nova] NOVA_SEED=0 — leaving the database empty"
  fi
fi

echo "[nova] launching Next.js standalone server on port ${PORT:-3000}"
exec bun server.js
