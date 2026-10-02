# syntax=docker/dockerfile:1
# ============================================================================ #
# NovaRouter Terminal — standalone image.
#
# This builds the terminal ALONE: the build context is this directory, the
# image contains `terminal/` and its three dependencies, and nothing else. No
# NovaRouter gateway, no database, no dashboard — just the shells, their HTTP
# contract, and the Agentbox AI agent.
#
#   docker build -t novarouter-terminal ./terminal
#   docker run -p 3100:3100 -e NOVA_TERMINAL_TOKEN=<secret> novarouter-terminal
#
# Point a NovaRouter app at it with NOVA_TERMINAL_URL — or skip the app
# entirely and talk to this host (and its /agent) directly.
# ============================================================================ #
FROM python:3.12-slim

# bash is the shell every session spawns; ca-certificates is what makes
# `curl`, `pip` and the agent's model endpoint work from inside a session.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl git procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so a code change doesn't reinstall the world. This file
# lives in the build context (terminal/), and installs into the image root.
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

# The package keeps its name so `python3 -m terminal.service` works exactly as
# it does in the full checkout.
COPY . /app/terminal

# Where shells start (NOVA_BUILD_ROOT). Never the app's own source tree.
RUN mkdir -p /app/build
ENV PYTHONUNBUFFERED=1 \
    NOVA_BUILD_ROOT=/app/build \
    NOVA_TERMINAL_PORT=3100

# The terminal's own port — never the gateway's, for the same reason the
# engine sidecar never shares it.
EXPOSE 3100

WORKDIR /app
CMD ["python3", "-m", "terminal.service"]