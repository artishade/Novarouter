# NovaRouter — Deep Project Map (Python/FastAPI era)

> Python-port reality. The historical Next.js layout (`src/app/api/**`, `src/lib/**`)
> in `api-contract.md` no longer exists in this repo — the Python modules below are
> the ports of those files. Use this map to find code without re-scanning.

## Process topology

```
┌────────────────────────── one process: python3 main.py ──────────────────────────┐
│  FastAPI (0.0.0.0:$PORT)                                                        │
│   ├─ /v1/* + /api/v1/*        routers/gateway.py        (OpenAI-spec gateway)   │
│   ├─ /api/admin/*             routers/admin_*.py        (JSON admin APIs)       │
│   ├─ /api/agent/*             routers/agent_api.py      (agent tasks/tools)     │
│   ├─ / + /partials/*          ui/shell.py + ui/tabs/*   (Jinja2 BFF dashboard)  │
│   └─ /static                  static/ (nova.css, app.js)                        │
│                                                                                  │
│  engine sidecar (separate process): bun/node engine/index.js on 127.0.0.1:3099  │
│   └─ free-model chat stream / search / reader (no API key needed)               │
└──────────────────────────────────────────────────────────────────────────────────┘
```

The UI is a **self-BFF**: `ui/api_client.py` calls this same process's JSON API
over HTTP (`/api/admin/...`), so UI behaviour always matches the gateway. The
gateway log middleware in `main.py` also records every `/v1/*` call to `RequestLog`
(via `nova/gwlog.py`).

## Module-by-module (nova/ — the core library)

| Module | Responsibility / key facts |
| --- | --- |
| `config.py` | Env handling: `PORT`, `DATABASE_URL` normalization (Prisma-style `file:` / `postgres://` accepted), `IS_POSTGRES`, `PUBLIC_BASE_URL`/`public_base_url()`, `ENGINE_SIDECAR_PORT` (3099, steps aside if it collides with app port), `CORS_ALLOW_ORIGINS`, `slugify()` |
| `database.py` | SQLAlchemy engine + `SessionLocal`; `get_db()` FastAPI dependency; `ping()`; `ensure_sqlite_dir()` |
| `models.py` | All ORM rows (single source of schema truth): Provider, ProviderKey, Model, ModelRoute, ClientKey, RequestLog, TerminalCommand, SystemConfig (KV), StorageProviderRow, StorageFile, AgentTask, AgentStep, ProviderSession. `utcnow()` helper |
| `bootstrap.py` | First-boot: additive table creation + seeds NovaFree engine + gateway config. Never destructive, never mock data |
| `kv.py` | SystemConfig KV helpers: `get_config`, `set_config`, `get_config_number`, `get_gpu_providers`, `set_gpu_providers` |
| `engine.py` | Sidecar lifecycle: `start_sidecar()` / `stop_sidecar()` / `health_check()`; spawns bun/node `engine/index.js`; degrades honestly if absent |
| `provider_transport.py` | Wire-format translators: `openai_to_anthropic`, `anthropic_to_openai`, `anthropic_url`, `anthropic_headers` (incl. tool calls/results) |
| `discovery.py` | Live model discovery: `discover_provider_models(provider)`, `aggregate_discovery()` for `?discover=1` |
| `syncengine.py` | Catalogue sync: `sync_provider(provider)` upserts models from upstream `/models` |
| `freemodels.py` | Loads `engine/free-models.json` (snapshot of ClawLabsAI/free-ai-models); feeds NovaFree tiers `nova/pro`, `nova/air`, `nova/mini` |
| `terminal.py` | **Simulated** allowlist sandbox executor (~1200 lines). No child processes; outputs synthesized from real OS telemetry (/proc, platform) + live DB state. Only subprocess ever run: `--version` probes of node/bun/npm. Allowlist + dangerous-token blocklist (exit 126) / unknown (exit 127). Persists TerminalCommand, capped 200 rows |
| `pty_session.py` | Real PTY session manager for Terminal tab sessions: `stop_all()` on shutdown, OSC 7 cwd reporting, max 8 sessions, 30-min idle reap |
| `agent.py` | Autonomous runner: plan (LLM strict JSON) → execute tool → persist AgentStep → repeat until `finish`/step limit → final summary. Tools: web_search, read_url, terminal, gateway_stats, storage_scan, discover_models, write_file, read_file, edit_file, mkdir. Each DB touch opens its own SessionLocal; cancellation checked per loop. Async callable handed to FastAPI BackgroundTasks |
| `storage_lib.py` | Storage providers (Firebase/Supabase/B2/R2/GitHub…), backups, file ops |
| `gwlog.py` | `record_request()` — compact RequestLog writes from the gateway middleware |
| `synclog.py` | Catalogue-sync audit logging |

## routers/ — the HTTP surface

| File | Mounted at | Contains |
| --- | --- | --- |
| `gateway.py` | `/v1` + `/api/v1` | `GET /models` (+`?discover=1`), `POST /chat/completions` (SSE streaming + non-streaming, full fallback pipeline), `POST /completions` (legacy), `POST /messages` + `/messages/count_tokens` (Anthropic format), `POST /embeddings`. Pooled httpx clients; tunable timeouts `NOVA_CONNECT_TIMEOUT` (5s), `NOVA_UPSTREAM_READ_TIMEOUT` (600s — long thinking runs), `NOVA_NONSTREAM_READ_TIMEOUT` (300s); provider-key cache (30s TTL) |
| `anthropic_adapter.py` | (helper) | Native Anthropic Messages passthrough used by `/v1/messages` |
| `admin_providers.py` | `/api/admin/providers` | CRUD, presets, real connectivity probe (`/test`), signin/signout sessions |
| `admin_models.py` | `/api/admin/models` | List/filter, enable toggle, ping, catalogue sync |
| `admin_keys.py` | `/api/admin/keys` | Upstream key CRUD + bulk import + clear cooldowns |
| `admin_client_keys.py` | `/api/admin/client-keys` | Gateway client keys (`nova-sk-…`) |
| `admin_routes.py` | `/api/admin/routes` | Fallback chains CRUD + `/preview` (Stage 1/2/3 timeline) |
| `admin_terminal.py` | `/api/admin/terminal` | History/exec/clear + PTY session endpoints under `/pty/*` |
| `admin_compute.py` | `/api/admin/compute` | GPU/compute provider toggles + config |
| `admin_storage.py` | `/api/admin/storage` | Providers, connect/disconnect, files, backup |
| `admin_misc.py` | `/api/admin` | meta, stats, analytics, logs, and other misc |
| `agent_api.py` | `/api/agent` | Task create/list/get/cancel + tools list |
| `_common.py` | (helpers) | `json_body`, `as_str`, `as_num`, `parse_int`, `epoch_ms`, `mask_key`, `not_found`, `invalid_id` |

## ui/ — the Python frontend

| File | Role |
| --- | --- |
| `shell.py` | `GET /` renders `base.html` (sidebar + empty tab container + footer); `/partials/footer-stats`; favicon |
| `tabs/__init__.py` | Tab registry: each module exports `router` with `GET /partials/tab/<key>`; register new tabs here |
| `tabs/*.py` | overview, console, providers, models, routes, keys, storage, analytics, logs, terminal. Optional `POST /ui/<key>/...` action endpoints returning HTML fragments |
| `api_client.py` | Async httpx client to the app's own JSON API (self-BFF); module-level `api` instance |
| `render.py` | Jinja2 `render()` / `html()` helpers (templates dir, HTMLResponse) |
| `format.py` | fmtNum / fmtBytes / timeAgo / maskKey style helpers ported from the TS original |

## Data flow examples

**Chat completion:** client → `POST /v1/chat/completions` → resolve ModelRoute (public_id match, else `*`) → Stage 1 direct upstream (enabled key) → Stage 2..N explicit fallback chain → final stage NovaFree engine (`nova_engine` client → sidecar) → response `model` = requested id, `_nova` = truth (upstream_model, provider, stage, spoofed) → `RequestLog` row.

**Dashboard interaction:** browser HTMX → `GET /partials/tab/models` → `ui/tabs/models.py` renders `tabs/models.html` → fragment swapped into `#tab-content`; buttons POST to `/ui/models/...` → BFF calls `/api/admin/models/...` → re-render partial.

**Agent task:** `POST /api/agent/tasks` → BackgroundTasks → `nova/agent.py` loop → steps streamed/persisted → summary.

## Extension points (where to add things)

- **New admin JSON endpoint** → `routers/admin_<domain>.py`; mount prefix in `main.py` (admin_router section). Reuse `_common.py` helpers.
- **New gateway endpoint** → `routers/gateway.py`; wire-format helpers in `nova/provider_transport.py`.
- **New dashboard tab** → `ui/tabs/<key>.py` (export `router`, `GET /partials/tab/<key>`), register in `ui/tabs/__init__.py`, template `templates/tabs/<key>.html`, partials in `templates/partials/`.
- **New DB entity** → class in `nova/models.py` (bootstrap creates missing tables additively); KV-only values can just use `nova/kv.py` SystemConfig helpers instead.
- **New free provider/model source** → `engine/free-models.json` + `nova/freemodels.py`.
- **New agent tool** → action in `nova/agent.py` `ALLOWED_ACTIONS` + executor branch + tools list in `routers/agent_api.py`.
