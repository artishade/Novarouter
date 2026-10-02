# `terminal/` — NovaRouter's terminal, one path

Everything the project does with an interactive shell lives in this directory.
Nothing terminal-related exists outside it, and the rest of the app reaches it
only through `terminal.*` imports:

```python
from terminal import link                                   # the seam (LocalLink/RemoteLink)
from terminal.api import router as terminal_router          # the HTTP contract
from terminal.sandbox import execute_command                # one-shot allowlist exec
```

That one-way dependency is the whole point: because the feature is a single
directory, you can deploy **only this one** on its own machine and point the
project at it.

## Layout

| File | Role |
| --- | --- |
| `pty.py` | Real PTY sessions behind the Terminal tab sessions: max 8 concurrent, OSC 7 cwd tracking, 30-min idle reap, labelled agent tabs |
| `sandbox.py` | Simulated allowlist executor for one-shot commands (`/exec`). No child processes; dangerous tokens exit 126, unknown exit 127 |
| `link.py` | The seam. `LocalLink` (shells in this process) / `RemoteLink` (HTTP + SSE to another host), chosen by configuration |
| `api.py` | The HTTP contract — mounted by **both** hosts, so they cannot drift |
| `service.py` | The standalone host: `python3 -m terminal.service` |
| `config.py` | `NOVA_TERMINAL_PORT` / `NOVA_TERMINAL_URL` / `NOVA_TERMINAL_TOKEN` |
| `run.sh` | Launcher (`bun run terminal`) |

## Run it on its own

```bash
NOVA_TERMINAL_PORT=3100 \
NOVA_TERMINAL_TOKEN=<shared secret> \
sh ./terminal/run.sh          # or: python3 -m terminal.service
```

Then connect the project to it:

```bash
NOVA_TERMINAL_URL=http://terminal-host:3100 \
NOVA_TERMINAL_TOKEN=<same secret> \
python3 main.py
```

`NOVA_TERMINAL_URL` unset (the default) means the project keeps the terminal
in-process and none of this is run — the same shells, the same routes, one
process.

Notes:
- Use `python3 -m terminal.service`, not `python3 terminal/service.py`: the
  module form puts the repo root on `sys.path`, which the `terminal.*` imports
  need.
- `NOVA_TERMINAL_TOKEN` gates real root shells. `/health` stays open (that is
  the point of a health endpoint); everything else needs
  `X-Nova-Terminal-Token` or `Authorization: Bearer …`. An unset token is only
  sane on loopback and is called out loudly at boot.
- `NOVA_BUILD_ROOT` decides where shells start (default `/app/build`). Point
  both hosts at the same workspace if you want one set of files.

## Routes (identical on both hosts)

`GET/POST /sessions`, `POST /activate`, `POST /rename`, `GET /stream` (SSE),
`POST /input`, `POST /resize`, `POST /stop`, `POST /run` — mounted by the
gateway at `/api/admin/terminal/pty/*` and by this service at `/terminal/pty/*`.

Errors always carry a stable `code` (`session_gone`, `session_limit`,
`session_start_failed`, `session_write_failed`, `link_unavailable`) so a remote
failure reads exactly like a local one.