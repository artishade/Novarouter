# syntax=docker/dockerfile:1
# ============================================================================ #
# NovaRouter — Python server (FastAPI) with the Next.js dashboard served by a
# managed child process, plus a bun sidecar for the built-in NovaFree engine.
# The server binds 0.0.0.0:$PORT (Render assigns PORT dynamically).
# ============================================================================ #

# --------------------------------------------------------------------------- #
# Stage 1 — build the Next.js dashboard (standalone output)
# --------------------------------------------------------------------------- #
FROM oven/bun:1 AS ui-builder
WORKDIR /app

COPY package.json bun.lock* ./
RUN bun install --frozen-lockfile || bun install

COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN bun run build

# --------------------------------------------------------------------------- #
# Stage 2 — engine sidecar dependencies (z-ai-web-dev-sdk for the NovaFree engine)
# --------------------------------------------------------------------------- #
FROM oven/bun:1 AS engine-deps
WORKDIR /engine
COPY engine/package.json ./
RUN bun install --production

# --------------------------------------------------------------------------- #
# Stage 3 — Python runtime + bun (for the UI child process and engine sidecar)
# --------------------------------------------------------------------------- #
FROM python:3.12-slim AS runner

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies first (best layer caching)
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

# Dashboard UI (Next standalone) + static assets
COPY --from=ui-builder /app/.next/standalone /app/ui
COPY --from=ui-builder /app/.next/static /app/ui/.next/static
COPY --from=ui-builder /app/public /app/ui/public

# Engine sidecar (z-ai SDK)
COPY engine/index.js /app/engine/index.js
COPY --from=engine-deps /engine/node_modules /app/engine/node_modules

# Python application sources
COPY main.py ./
COPY nova ./nova
COPY routers ./routers

# Database + engine runtime configuration
RUN mkdir -p /app/db
VOLUME /app/db
ENV PYTHONUNBUFFERED=1 \
    DATABASE_URL=file:/app/db/custom.db \
    NOVA_UI_COMMAND="bun /app/ui/server.js" \
    NOVA_UI_PORT=3001 \
    NOVA_UI_TARGET=http://127.0.0.1:3001 \
    ENGINE_PORT=3099

EXPOSE 3000

# requirement #1: main.py binds 0.0.0.0:$PORT — PORT comes from the
# environment (Render injects it dynamically; default 3000 locally).
CMD ["python3", "main.py"]
