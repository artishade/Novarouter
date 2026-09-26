# syntax=docker/dockerfile:1
# ============================================================================ #
# NovaRouter — 100% Python server (FastAPI + Jinja2 UI + SQLAlchemy), plus a
# bun sidecar for the built-in NovaFree engine (z-ai-web-dev-sdk).
# The server binds 0.0.0.0:$PORT (Render assigns PORT dynamically).
# ============================================================================ #

# --------------------------------------------------------------------------- #
# Stage 1 — engine sidecar dependencies (z-ai SDK + bun binary for the runner)
# --------------------------------------------------------------------------- #
FROM oven/bun:1 AS engine-deps
WORKDIR /engine
COPY engine/package.json ./
RUN bun install --production

# --------------------------------------------------------------------------- #
# Stage 2 — Python runtime + bun (engine sidecar host)
# --------------------------------------------------------------------------- #
FROM python:3.12-slim AS runner

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies first (best layer caching)
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

# bun binary — hosts the NovaFree engine sidecar (127.0.0.1:$ENGINE_PORT)
COPY --from=engine-deps /usr/local/bin/bun /usr/local/bin/bun

# Engine sidecar (z-ai SDK)
COPY engine/index.js /app/engine/index.js
COPY --from=engine-deps /engine/node_modules /app/engine/node_modules

# Python application: API + frontend module (Jinja2 templates + static assets)
COPY main.py ./
COPY nova ./nova
COPY routers ./routers
COPY ui ./ui
COPY templates ./templates
COPY static ./static

# Database + engine runtime configuration
RUN mkdir -p /app/db
VOLUME /app/db
ENV PYTHONUNBUFFERED=1 \
    DATABASE_URL=file:/app/db/custom.db \
    ENGINE_PORT=3099

EXPOSE 3000

# requirement #1: main.py binds 0.0.0.0:$PORT — PORT comes from the
# environment (Render injects it dynamically; default 3000 locally).
CMD ["python3", "main.py"]
