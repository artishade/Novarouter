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

# ---------- Stage 3: self-contained Prisma CLI ----------
# The runner can't reuse the app's node_modules (Next standalone only ships the
# traced runtime deps), and copying bare package dirs breaks the CLI:
# @prisma/config requires transitive deps like `effect` that only a real
# `bun install prisma` resolves. This stage produces a complete CLI install.
FROM oven/bun:1 AS prisma-cli
WORKDIR /cli
COPY --from=builder /app/node_modules/prisma/package.json /tmp/prisma-pkg.json
# Pin the CLI to the exact version resolved in the app's lockfile (zero drift)
RUN echo "{\"dependencies\":{\"prisma\":\"$(bun -e "process.stdout.write(String(JSON.parse(await Bun.file('/tmp/prisma-pkg.json').text()).version))")\"}}" > package.json \
  && bun install --production

# ---------- Stage 4: runtime ----------
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
# @prisma scope from the app install (client + engines with native binaries)
COPY --from=builder /app/node_modules/@prisma ./node_modules/@prisma
# Prisma CLI + ALL its transitive deps (effect, c12, chokidar, ...) from a real install
COPY --from=prisma-cli /cli/node_modules ./node_modules
COPY --from=builder /app/prisma ./prisma
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# SQLite lives on a volume so data survives container restarts
RUN mkdir -p /app/db
VOLUME /app/db

EXPOSE 3000
ENTRYPOINT ["docker-entrypoint.sh"]
