# syntax=docker/dockerfile:1

# ---------- Stage 1: dependencies ----------
FROM oven/bun:1 AS deps
WORKDIR /app
COPY package.json bun.lock ./
# Install dependencies (Prisma client is generated explicitly in the builder stage)
RUN bun install --frozen-lockfile

# ---------- Stage 2: build ----------
FROM oven/bun:1 AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
# Placeholder so PrismaClient can be constructed during build-time module evaluation
ENV DATABASE_URL=file:/tmp/build-placeholder.db
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN bunx prisma generate
# package.json "build" = next build + copy static/public into .next/standalone
RUN bun run build

# ---------- Stage 3: runtime ----------
FROM oven/bun:1 AS runner
WORKDIR /app
# Prisma engines need openssl + CA certs
RUN apt-get update -qq \
  && apt-get install -y -qq --no-install-recommends openssl ca-certificates \
  && rm -rf /var/lib/apt/lists/*

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    DATABASE_URL=file:/app/db/custom.db

# Next.js standalone server (server.js + pruned node_modules + .next/static + public)
COPY --from=builder /app/.next/standalone ./
# Overlay the Prisma CLI + schemas + seed scripts so the entrypoint can initialize the DB.
# The entrypoint invokes the CLI via its REAL path (node_modules/prisma/build/index.js).
# Do NOT copy or symlink node_modules/.bin/prisma here:
#   - the standalone output has no node_modules/.bin directory (a RUN ln would fail),
#   - and COPY dereferences symlinks anyway, which breaks the CLI's internal WASM
#     resolution (ENOENT prisma_schema_build_bg.wasm).
COPY --from=builder /app/node_modules/prisma ./node_modules/prisma
COPY --from=builder /app/node_modules/@prisma ./node_modules/@prisma
COPY --from=builder /app/prisma ./prisma
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# SQLite lives on a volume so data survives container restarts
RUN mkdir -p /app/db
VOLUME /app/db

EXPOSE 3000
ENTRYPOINT ["docker-entrypoint.sh"]
