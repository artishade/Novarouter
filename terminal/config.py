"""Terminal service configuration — the knobs that split host from terminal.

The interactive terminal is real shell access, so it can be split out of the
gateway process and hosted on its own (bigger machine, own port, own
lifecycle) while the app stays connected to it:

    NOVA_TERMINAL_URL unset → in-process: `terminal.pty.manager` inside the app
                              (the default, and what single-host deployments
                              want).
    NOVA_TERMINAL_URL set   → remote: the app proxies every terminal route to
                              a service running `python3 -m terminal.service`
                              (see `terminal/link.py`).

These live here rather than in `nova/config.py` because they belong to the
terminal: a host that only deploys `terminal/` still needs to read them, and
the rest of the app should learn about the terminal solely through
`terminal.*` imports.
"""
from __future__ import annotations

import os

from nova.config import PORT  # the app's own HTTP port — never shared

# --------------------------------------------------------------------------- #
# Host: where this process binds when it is the terminal service
# --------------------------------------------------------------------------- #
# The service never shares the app's HTTP port, for the same reason the engine
# sidecar doesn't: Freebuff/Render point their proxy at a single port, so a
# second listener there makes the proxy answer with the wrong service. 3100
# gives way only if the app itself is on it.
TERMINAL_SERVICE_PORT = int(os.environ.get("NOVA_TERMINAL_PORT") or (3100 if PORT != 3100 else 3101))

# --------------------------------------------------------------------------- #
# Client: how the app reaches a terminal hosted somewhere else
# --------------------------------------------------------------------------- #
TERMINAL_SERVICE_URL = os.environ.get("NOVA_TERMINAL_URL", "").rstrip("/")
# Shared secret. Set it on BOTH sides: the service hands out root shells, so an
# open one must never be reachable — an unset token only suits loopback.
TERMINAL_SERVICE_TOKEN = os.environ.get("NOVA_TERMINAL_TOKEN", "")