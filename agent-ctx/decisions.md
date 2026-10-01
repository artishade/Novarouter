# NovaRouter — Decision Log (why things are this way)

Intent-preservation memory. If a change would contradict an entry here, check
with the user before overriding, or add a new dated entry explaining the pivot.

## Platform pivots

- **2026-09 · Python port (current).** The whole app is FastAPI + Jinja2 + HTMX.
  The previous TypeScript implementation (and an intermediate Next.js 16 rebuild
  documented in `worklog.md` / `agent-ctx/api-contract.md`) is history — kept
  only as the API-shape reference. Reason: single runtime, no Node build step,
  one process serving UI + API.
- **UI = self-BFF, server-rendered.** `ui/` calls its own process's JSON API
  over HTTP instead of querying the DB directly. Reason: UI behaviour can never
  drift from gateway behaviour; tab modules stay thin; fragments (not JSON)
  drive HTMX updates.
- **Gateway parity with the TS original** (pipeline stages, spoofing, `_nova`
  metadata, RequestLog) was an explicit porting requirement — treat divergences
  as bugs, not improvements.

## Tricky tradeoffs worth remembering

- **Simulated sandbox executor** (`nova/terminal.py`): no child processes by
  design; outputs are synthesized from real OS telemetry + DB state. The only
  allowed subprocess is a `--version` probe of node/bun/npm. Interactive work
  goes through real PTYs (`nova/pty_session.py`) instead. Don't "fix" the
  executor by spawning shells — that's the vulnerability it exists to avoid.
- **Upstream read timeout is 600s** (`NOVA_UPSTREAM_READ_TIMEOUT`): long
  thinking runs legally sit silent for minutes; a 60s timeout caused
  mid-stream ReadTimeout failures in Claude Code. Tunable via env, default high.
- **Pooled httpx clients** (`UPSTREAM_CLIENT` / `NONSTREAM_CLIENT` in
  `routers/gateway.py`): fresh clients per request pay TCP+TLS per call; the
  pool keeps warm connections. Reuse them; don't instantiate per request.
- **Provider-key cache (30s TTL)** in `gateway.py`: avoids a DB roundtrip per
  upstream attempt. Acceptable staleness by design.
- **Identity spoofing**: the response `model` is always the requested id; the
  real upstream is in `_nova`. Clients (incl. Claude Code) depend on this.
- **Engine sidecar port** never shares the app's HTTP port (Freebuff preview
  proxy points at the single app port; a second listener there makes the proxy
  answer with the sidecar's 404). Default 3099, steps aside on collision
  (`ENGINE_SIDECAR_PORT` in `nova/config.py`).
- **Sandbox preview sets `NOVA_ENGINE_DISABLED=1`** so the preview URL resolves
  to the dashboard, not the sidecar. Production (Docker/Render) unaffected.
- **Additive-only bootstrap**: schema sync creates missing tables; demo-data
  seeds from legacy deployments are cleaned, never created. Reason: deploys must
  never wipe user data (SQLite-in-container is ephemeral; Postgres recommended).
- **Ephemeral-DB warning** is intentional UX (banner + `/health` storage block):
  it tells users to set a Postgres `DATABASE_URL` before their data vanishes.
- **Prisma-style DATABASE_URL normalization** (`file:…`, `postgres://`,
  pgbouncer params, explicit psycopg2/psycopg3 dialect pinning) exists so the
  same connection string works across local/Docker/Render/SQLAlchemy versions.
- **Both `/v1/*` and `/api/v1/*`** mount the same gateway router: OpenAI-SDK
  clients expect `/v1`, some internal tooling uses `/api/v1`. Keep both.
- **Tab registry pattern** (`ui/tabs/__init__.py` loops over modules exporting
  `router`): adding a tab must not require editing `main.py`.

## Keep memory accurate

- When a decision is reversed or a big feature lands, append a dated entry here
  and update `AGENTS.md`/`project-map.md` — that's the whole job of the
  `nova-memory-sync` skill.

## 2026-10-01 — Custom MCP servers are the console's plugin system
- **One table (`McpServer`), two transports.** A plugin is either a streamable
  HTTP MCP endpoint or a local stdio process. Legacy HTTP+SSE (GET stream +
  POST side channel) is deliberately not implemented: every current server
  speaks streamable HTTP, and a half-working second transport is worse than an
  honest two. Reason: plugins must not turn the gateway into a transport zoo.
- **Discovery is cached on the row, never probed at prompt time.** The agent
  prompt is built from the `tools/list` snapshot, so a slow or dead plugin can
  never stall a task — it simply is not offered. "Test"/"Refresh" re-probe on
  demand. Reason: prompt building is on the task's critical path.
- **stdio children are cached (max 8), argv-split, `shell=False`.** A plugin
  boots in ~350 ms; reusing the child keeps repeated agent steps cheap. Nothing
  runs through a shell — `shlex.split` + `create_subprocess_exec` only.
  `mcp_client.close_all()` runs on shutdown, next to `pty_session.stop_all()`.
- **Header values are credentials.** They are stored, masked on every read, and
  a re-save whose value is still masked keeps the stored one — the browser
  never holds the real token, so rotating it means typing a new one.
- **One registry, two consumers.** `nova/mcp_registry.py` backs both the console
  drawer (`/ui/console/plugins*`) and the agent (`mcp_call` action), so what
  the user sees in the UI is exactly what the planner can call.
