# NovaRouter container image (Fly.io or any Docker host)
# Build: docker build -t novarouter .
# Run: docker run -p 8080:8080 -v novarouter-data:/data novarouter
# Starts as root only to fix ownership of the mounted /data volume (Fly mounts
# fresh volumes as root:root), then drops to an unprivileged user.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

RUN apt-get update && apt-get install -y --no-install-recommends gosu curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY docker-entrypoint.sh /docker-entrypoint.sh

RUN useradd --system --home-dir /srv --shell /usr/sbin/nologin nova && mkdir -p /data && chown -R nova:nova /data && chmod +x /docker-entrypoint.sh

ENV NOVA_HOST=0.0.0.0 NOVA_PORT=8080 NOVA_DATA_DIR=/data

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD curl -fsS http://127.0.0.1:${NOVA_PORT:-${PORT:-8080}}/healthz || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
