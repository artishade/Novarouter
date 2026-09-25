#!/bin/sh
# NovaRouter container entrypoint.
#
# - Auto-detects the database provider from DATABASE_URL:
#     file:...                      -> SQLite (default)
#     postgres:// or postgresql://  -> Postgres (Neon, Supabase, Aiven, RDS...)
# - Generates the matching Prisma client, syncs the schema, seeds demo data on first boot.
# - Pooled Postgres hosts (Neon/Supabase "...pooler..." endpoints) get pgbouncer=true
#   appended automatically so Prisma works in transaction-pooling mode.
set -e

cd /app

# Run the Prisma CLI via its REAL path.
# node_modules/.bin/prisma is normally a symlink to ../prisma/build/index.js, but
# Docker COPY dereferences symlinks — the copied CLI then resolves its embedded
# WASM files against node_modules/.bin/ and crashes with:
#   ENOENT .../node_modules/.bin/prisma_schema_build_bg.wasm
# Running node_modules/prisma/build/index.js directly keeps WASM resolution correct.
PRISMA_CLI="bun /app/node_modules/prisma/build/index.js"

provider="sqlite"
case "$DATABASE_URL" in
  postgres://* | postgresql://*)
    provider="postgres"
    case "$DATABASE_URL" in
      *pooler.*)
        case "$DATABASE_URL" in
          *pgbouncer=*) : ;;
          *)
            case "$DATABASE_URL" in
              *\?*) DATABASE_URL="$DATABASE_URL&pgbouncer=true" ;;
              *)    DATABASE_URL="$DATABASE_URL?pgbouncer=true" ;;
            esac
            export DATABASE_URL
            ;;
        esac
        ;;
    esac
    ;;
esac

echo "[nova] NovaRouter starting (database provider: $provider)"

if [ "$provider" = "postgres" ]; then
  # Self-healing fallback in case the postgres schema variant is missing.
  if [ ! -f prisma/schema.postgres.prisma ]; then
    echo "[nova] generating prisma/schema.postgres.prisma from the SQLite schema..."
    sed 's/provider = "sqlite"/provider = "postgresql"/' prisma/schema.prisma > prisma/schema.postgres.prisma
  fi
  echo "[nova] generating Prisma client for postgresql..."
  $PRISMA_CLI generate --schema prisma/schema.postgres.prisma
  echo "[nova] syncing schema to the Postgres database..."
  $PRISMA_CLI db push --schema prisma/schema.postgres.prisma --accept-data-loss --skip-generate
else
  echo "[nova] syncing SQLite schema..."
  $PRISMA_CLI db push --schema prisma/schema.prisma --accept-data-loss --skip-generate
fi

if [ "${NOVA_SEED:-1}" = "1" ]; then
  bun prisma/ensure-seed.ts
else
  echo "[nova] NOVA_SEED=0 — skipping demo data seeding"
fi

echo "[nova] launching Next.js standalone server on port ${PORT:-3000}"
exec bun server.js
