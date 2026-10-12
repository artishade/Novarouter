# RouterX

**One endpoint in front of every AI provider — with automatic failover that actually uses the providers you configured.**

RouterX is a self-hosted, OpenAI- and Anthropic-compatible AI gateway. Point your SDKs (or **Claude Code**) at one base URL and RouterX fans every request out across the providers you've added, walks a fallback chain when one fails, keeps a per-provider key pool with cooldowns, and finally falls back to **RouterX Free** — a zero-key free-model engine so the gateway never leaves a request unanswered. It ships with a 10-tab dashboard, a chat console, and a real cloud terminal with an autonomous agent.

![stack](https://img.shields.io/badge/Python-FastAPI-009688) ![stack](https://img.shields.io/badge/SQLAlchemy-2-orange) ![stack](https://img.shields.io/badge/UI-Jinja2%20%2B%20HTMX-emerald) ![stack](https://img.shields.io/badge/Claude%20Code-ready-8b5cf6) ![license](https://img.shields.io/badge/license-MIT-blue)

**100% Python** — the backend and the dashboard are one FastAPI process (Jinja2 + HTMX + Alpine, no Node build step). The only JavaScript in the project is the `static/app.js` wiring the dashboard, the terminal client, and the tiny zero-dependency free-model engine sidecar.

---

## Why RouterX?

| Problem | What RouterX does about it |
| --- | --- |
| 🔑 **Provider sprawl** | Add OpenRouter, Groq, Anthropic, xAI, a Cloudflare Worker, or any OpenAI-compatible gateway once. One base URL serves every model, every SDK, no code changes. |
| 💥 **A provider goes down mid-task** | Requests fail over through your explicit chain **and then across every other healthy provider** before ever giving up — the client keeps asking for one model id and still gets an answer. |
| 🧊 **Freezes / "request failed" on long runs** | Long-thinking models go quiet for minutes. RouterX keeps the stream alive with heartbeats, so Claude Code and streaming clients don't die at the 10–15s mark. |
| 🧩 **Nobody can find the right model** | 2,300+ models are searchable right where you build a fallback route — type `claude`, `gpt-4`, `qwen` and click. |
| 🧠 **You don't want to babysit keys** | Per-provider key pools, weights, cooldowns, and a (provider, model) lockout registry that backs off failures and clears itself the instant a provider recovers. |
| 🆓 **Free models, honestly** | A built-in **RouterX Free** engine serves a free-model catalogue with **no API key at all**, and tells you the truth via `_nova` when it's used. |
| 🖥️ **You want it on your own box** | One `docker compose up`. SQLite by default, Postgres when you want persistence, optional separate terminal host. |

---

## See it work

**Auto-fallback: a failed provider rolls onto the next healthy one, then the free engine.** The client never sees the failure.

![RouterX auto-fallback demo](docs/fallback-demo.svg)

> If GitHub doesn't animate SVG, the static frames still show the flow: request → direct (429) → auto stand-in (200) → response keeps the requested model id.

### Screens

| Dashboard | Fallback-route builder (with model search) | Console |
| --- | --- | --- |
| ![Overview](docs/screenshots/overview.svg) | ![Routes](docs/screenshots/routes.svg) | ![Console](docs/screenshots/console.svg) |

*(These are vector previews of the dashboard's dark-terminal UI; run it locally to see the live HTMX version.)*

---

## Architecture

![RouterX architecture](docs/architecture.svg)

```mermaid
flowchart TD
    C["Clients<br/>OpenAI SDK · Claude Code · curl · dashboard"] --> G["RouterX Gateway (FastAPI)<br/>/v1/* &amp; /api/v1/* · SSE streaming"]
    G --> R["Routing brain — nova/routing.py<br/>policies → exposure → tags → lockouts → strategy"]
    R --> F{"Fallback chain"}
    F -->|1 direct| P1["Provider transport (pooled httpx)<br/>OpenAI-compatible · Anthropic · Worker · custom"]
    F -->|2 explicit chain| P1
    F -->|3 auto stand-ins| P2["Every other healthy, enabled provider"]
    F -->|4 last resort| E["RouterX Free engine (JS sidecar)<br/>free-ai-models catalogue, no key"]
    G --> DB[("SQLAlchemy 2<br/>Providers · Models · Keys · Routes · RequestLog")]
    G -. optional .-> T["Terminal (hostable alone)<br/>PTY shells · sandbox · Agentbox agent"]
```

A request is ordered by a pure function (`nova/routing.py`, an OmniRoute port) and then executed as a fallback chain: **direct model → your explicit route chain → auto stand-ins → the free engine**. Every stage is recorded, so `_nova.routing` can always explain *why* a request was answered by the provider it was.

---

## ✨ Features

| Area | What you get |
| --- | --- |
| 🔀 **Real cross-provider failover** | Direct → explicit chain → **auto stand-ins across every other healthy, enabled provider** → free engine. Opt out per route with the `auto` switch. |
| 💬 **Claude Code &amp; Anthropic-native** | `/v1/messages` + `/v1/messages/count_tokens`, full streaming event order, tool_use/tool_result passthrough, and raw passthrough to Anthropic-kind providers. |
| 🔌 **OpenAI-spec API** | `/v1/models`, `/v1/chat/completions` (SSE + non-streaming), `/v1/completions`, `/v1/messages`, `/v1/embeddings`, live discovery. Every endpoint also exists under `/api/v1/*`. |
| 🛡️ **Streams that survive** | Heartbeats while an upstream is silent + 600s read tolerance, so long prefill/thinking runs never trip a proxy timeout mid-answer. |
| 🧭 **Routing brain (OmniRoute)** | Policy engine, glob exposure lists, `metadata.tags` steering, per-(provider, model) cooldowns, and 11 selection strategies (priority, weighted, round-robin, least-used, cost/latency/SLA, last-known-good…). |
| 🔎 **Searchable route builder** | Build a fallback chain by searching the whole catalogue (`/ui/routes/models`) and clicking models in order — plus a 3-stage resolution **preview** that matches runtime exactly. |
| 🧠 **Free models, no key** | Built-in **RouterX Free** engine + convenience tiers (`routerx/pro`, `routerx/air`, `routerx/mini`). |
| 🔌 **MCP plugins** | Register your own MCP servers (streamable HTTP or local stdio); their tools join the agent's toolbox and a built-in "run this tool" form. |
| 🗂️ **Huge catalogue** | The OmniRoute registry (**223 providers / 2,300+ models**) seeds on first boot and fills with live discovery. |
| 🔑 **Key management** | Multi-key pools per provider, bulk import, weights, cooldown tracking, client keys for the gateway. |
| 🖥️ **Console + terminal** | One chatbox (`$ cmd` shell, `! task` agent) and a real xterm.js cloud terminal with an optional standalone deployment. |
| 🗄️ **Storage / compute** | Storage manager (R2, S3, GCS, B2, Azure, Supabase…) and compute config (GPU pool, gateway RAM booster). |
| 🎨 **UI/UX** | Dark terminal aesthetic (emerald/slate), responsive mobile-first layout, server-rendered Jinja2 + HTMX. |

---

## 🚀 Quickstart

### Docker (recommended)

```bash
git clone https://github.com/artishade/Novarouter.git
cd Novarouter
docker compose up -d
```

Open **http://localhost:3000** (or `http://localhost:$PORT`). On first start RouterX creates the schema and bootstraps the real minimum: the built-in free engine + the OmniRoute provider catalogue + gateway config. **No mock data is ever seeded.** Data lives in the `nova-db` volume.

```bash
docker build -t routerx .
docker run -d -p 3000:3000 -v nova-db:/app/db routerx
```

### Local (Python)

```bash
git clone https://github.com/artishade/Novarouter.git
cd Novarouter
pip install -r requirements.txt
cp .env.example .env
python3 main.py             # http://localhost:3000 — UI + API in one process
```

`python3 main.py` binds `0.0.0.0:$PORT` (`PORT` defaults to 3000). It serves **everything**: the gateway, the admin/agent JSON APIs, and the dashboard.

### Deploy (Render or any Docker host)

1. **New → Web Service** → connect the repo → **Runtime: Docker**.
2. Set **Health Check Path** to `/health`.
3. Set `DATABASE_URL` to a free Postgres URL (Neon/Supabase/Aiven) — see below.

### 💾 Keep your data across deploys (important)

Without `DATABASE_URL`, RouterX uses SQLite inside the container, so a redeploy on an ephemeral host resets providers, keys and routes. Point `DATABASE_URL` at a free Postgres instance instead:

```bash
DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require
```

Prisma-style `postgres://…` values and `pgbouncer`/`connection_limit` params are normalized automatically. Bootstrapping is **strictly additive** — it creates missing tables and seeds only what's missing, and never deletes, resets or purges anything. The dashboard shows a warning banner and `/health` reports `"storage": {"persistent": false}` until this is set.

### Scripts

| Command | What it does |
| --- | --- |
| `bun run install:py` | Install `requirements.txt` |
| `bun run preview` | Install deps if needed, then run on `0.0.0.0:$PORT` |
| `bun run terminal` | Run the terminal as its own service |
| `bun run test` | Byte-compile + Python suite + dashboard JS suite |
| `bun run lint` | `compileall` syntax check |

> `bun` is only a task runner; the app is pure Python and the scripts work with `npm run` too.

---

## 🤖 Connect Claude Code (and Claude Desktop)

RouterX speaks the Anthropic Messages API natively, so Claude Code can use the whole provider pool with fallback. Point it at your RouterX base URL:

```bash
export ANTHROPIC_BASE_URL="https://your-routerx-host/v1"     # local: http://localhost:3000/v1
export ANTHROPIC_AUTH_TOKEN="any-value"                       # RouterX auth (optional client key)
export ANTHROPIC_MODEL="anthropic/claude-3-5-sonnet"          # a model id from GET /v1/models
# Claude Code also honours ANTHROPIC_SMALL_FAST_MODEL for lightweight turns.
claude
```

What RouterX implements for the agent loop:

- `POST /v1/messages` — streaming **and** non-streaming, with the full SSE event order (`message_start` → `content_block_start` → `content_block_delta` → `message_delta` → `message_stop`).
- `POST /v1/messages/count_tokens` — Claude Code calls this before every turn; without it the session 404s and dies.
- `tool_use` / `tool_result` blocks are preserved end-to-end (the loop dies on the second turn without them); `thinking`/`redacted_thinking` history is dropped safely.
- If the requested model maps to an **Anthropic-kind provider**, RouterX passes straight through; otherwise it translates to the OpenAI pipeline and back, and the fallback chain still applies.
- Long thinking runs are covered by stream heartbeats, so Claude Code doesn't drop the request mid-answer.

Same endpoint powers any Anthropic SDK:

```bash
curl $BASE/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"anthropic/claude-3-5-sonnet","max_tokens":256,"messages":[{"role":"user","content":"hi"}]}'
```

---

## 🔌 Configure providers &amp; the fallback pipeline

In **Providers → Add Provider** choose **Anthropic (native)**, **OpenAI-compatible**, **Cloudflare Worker**, **Custom gateway API**, or **xAI Grok**. A provider's **base URL points upstream** (not RouterX's own `/v1`). Pick **Anthropic (native)** for `https://api.anthropic.com` with an Anthropic key; enter the API **root**, not a full `/chat/completions` path.

Then, in **Routes**, create a chain. The route builder now has a **model search**: type to filter the whole catalogue and click models in order. `auto` (default on) appends **every other healthy, enabled provider** as stand-ins — so a failure rolls onto your pool, not straight onto the free engine.

```bash
# what the router is doing right now, and what it is skipping (and why)
curl -s localhost:3000/api/admin/routing

# create a fallback chain: public id -> ordered upstream models
curl -s -X POST localhost:3000/api/admin/routes -H 'content-type: application/json' \
  -d '{"public_id":"my-model","fallbacks":["anthropic/claude-3-5-sonnet","groq/llama-3.3-70b"],"auto":true}'

# resolve exactly what a request would do (direct / chain / auto stand-ins)
curl -s "localhost:3000/api/admin/routes/preview?model=my-model"
```

Every chat response explains itself: `_nova.routing` carries the strategy, the ordered candidates, what was excluded and why; `_nova.fallback` is `true` when anything other than the direct model served the request.

---

## 🧭 Routing — the OmniRoute brain

Routing, model routing, provider routing, fallback, MCP scopes, skills and the provider catalogue are ported from [**OmniRoute**](https://github.com/diegosouzapw/OmniRoute) into `nova/routing.py`, `nova/catalog.py`, `nova/mcp_tools.py` and `nova/router_settings.py`. Defaults reproduce the classic stage order; everything is tunable:

```bash
curl -s -X POST localhost:3000/api/admin/routing -H 'content-type: application/json' -d '{
  "strategy": "weighted",
  "policies": [
    {"id":"no-preview","name":"no preview models","type":"access","priority":1,
     "conditions":{"model_pattern":"gpt-*"},"actions":{"block_model":["gpt-4o"]}},
    {"id":"cap","name":"cap output","type":"budget","priority":9,"actions":{"max_tokens":4096}}
  ],
  "chains": {"my/model": [{"id":"groq/llama-3.3-70b","priority":0,"enabled":true}]},
  "denylist": ["internal/*"]
}'
```

| Knob | What it does |
| --- | --- |
| **Strategy** | `priority` (default), `weighted`, `round-robin`, `random`, `least-used`, `cost-optimized`, `rules`/`cost`/`latency`/`sla`/`lkgp` |
| **Policies** | `access` blocks a model by glob (refused, never silently substituted), `routing` prefers providers, `budget` caps output tokens |
| **Tag routing** | `metadata: {"tags":["eu"],"tag_match_mode":"any"}` limits a request to tagged providers |
| **Cooldowns / lockouts** | A failing (provider, model) is backed off and skipped until it recovers; a success clears it instantly (`/api/admin/routing/availability`) |
| **Exposure lists** | Deny/allow globs hide a model from `/v1/models` **and** every candidate pool — it can never sneak back in via a fallback chain |
| **MCP scopes** | `nova/mcp_tools.py` carries OmniRoute's scope table; a credentialed tool is held to the scopes it declares |

---

## 🔌 Gateway API

```bash
BASE=https://your-routerx-host/v1

curl $BASE/models                             # DB catalogue
curl "$BASE/models?discover=1"                # live discovery from every provider
curl "$BASE/models?discover=1&free=1"         # free-tier only

curl $BASE/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"routerx/air","messages":[{"role":"user","content":"hi"}]}'

curl -N $BASE/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"routerx/air","stream":true,"messages":[{"role":"user","content":"Count to 5"}]}'

curl $BASE/messages -H 'Content-Type: application/json' \
  -d '{"model":"anthropic/claude-3-5-sonnet","max_tokens":256,"messages":[{"role":"user","content":"hi"}]}'

curl $BASE/embeddings -H 'Content-Type: application/json' \
  -d '{"model":"text-embedding-3-small","input":"hello world"}'
```

All endpoints exist under both `/v1/*` and `/api/v1/*`. Responses carry `_nova` metadata (upstream provider, stage, fallback state, routing decision). CORS is enabled by default. Admin APIs live under `/api/admin/*`; agent APIs under `/api/agent/*`.

---

## 🖥️ Using the RouterX Console

Everything lives in one chatbox — input is routed automatically:

| You type | What happens |
| --- | --- |
| `Why is my fallback chain failing?` | Gateway AI answers in chat, with live provider/model telemetry chips |
| `$ free -m` | Runs a real command in the sandbox executor, shows output + exit code |
| `$ nova gpu kaggle on` | Attaches a GPU from the Kaggle pool to your compute config |
| `/boost 256`, `/models`, `/gpu`, `/storage`, `/help` | Slash shortcuts for terminal-side config |
| `! Audit the storage providers and report failures` | Delegates to the autonomous agent — steps stream live until it completes |
| `/plugins` | Opens the MCP server drawer — add, test, disable/remove, and run a tool by hand |

---

## 🔌 MCP plugins

The **Plugins** button (or `/plugins`) registers [Model Context Protocol](https://modelcontextprotocol.io) servers. Everything you add is additive — rows are never seeded and never wiped by a deploy.

| Transport | Example | How it runs |
| --- | --- | --- |
| `http` | `https://mcp.example.com/mcp` | Streamable HTTP JSON-RPC, plain-JSON or SSE-framed replies |
| `stdio` | `npx -y @modelcontextprotocol/server-filesystem /app/build` | A local plugin process (argv-split, never a shell), one cached child per server |

Tools join the permanent toolbox, so an agent task can call them the same way it calls the terminal. Headers are stored as credentials, shown masked, and a masked re-save keeps the value.

```bash
curl -X POST localhost:3000/api/admin/mcp/servers/test -H 'Content-Type: application/json' -d '{"id":"deepwiki"}'
curl localhost:3000/api/admin/mcp/tools
```

---

## ⬛ Terminal — the `~ Root@Build` cloud workspace

The sidebar's **RouterX Console** entry opens the merged cloud workspace at **`/terminal`** in a new tab: the chat panel on the left (chat, `$ commands`, `! agent tasks`, `/ shortcuts`) and the cloud terminal on the right.

- **Permanent cloud shell** — every session is a real `bash` on a pseudo-terminal with root access on Debian, branded `~ Root@Build:<cwd>#`.
- **Session tabs** — up to 8 live shells; switch, rename (double-click) or close them.
- **Live state** — OSC 7 working-directory reporting and full ANSI colour via xterm.js.
- **Cloud build config** — add/edit storage targets (R2, GCS, S3, B2, Azure, custom S3) from the UI; secrets stored server-side and always shown masked.

```bash
curl -s localhost:3000/api/admin/terminal/pty/sessions
curl -sN "localhost:3000/api/admin/terminal/pty/stream?session=<id>"
curl -s localhost:3000/api/build/info
```

### Hosting the terminal separately

The whole terminal is one directory, `terminal/`, that imports nothing from `nova/` and needs no database — so hosting it on its own is a deployment decision, not a code change. With `NOVA_TERMINAL_URL` unset it runs in-process (the default).

```bash
docker build -t routerx-terminal ./terminal
docker run -p 3100:3100 -e NOVA_TERMINAL_TOKEN=<shared-secret> routerx-terminal

# then point the gateway at it
NOVA_TERMINAL_URL=http://terminal-host:3100 NOVA_TERMINAL_TOKEN=<shared-secret> bun run dev
```

| Variable | Where | What it does |
| --- | --- | --- |
| `NOVA_TERMINAL_URL` | gateway | Host the terminal separately. Unset = in-process |
| `NOVA_TERMINAL_TOKEN` | **both** | Shared secret for `X-Nova-Terminal-Token`. Gates real root shells — always set it before exposing the terminal beyond loopback |
| `NOVA_TERMINAL_PORT` | terminal | The terminal's own port (default `3100`) |
| `NOVA_BUILD_ROOT` | terminal | Where shells start (default `/app/build`) |
| `NOVA_AGENTBOX_BASE_URL` / `_API_KEY` / `_MODEL` | terminal | Point the standalone Agentbox agent at any OpenAI-compatible endpoint |

`terminal/` ships its own console and AI agent (**Agentbox**), so a terminal deployed alone still gives you shells *and* an assistant.

---

## 🔒 Configuration &amp; notes

Key environment variables (see `.env.example`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `3000` | Bind port (`0.0.0.0:$PORT`) |
| `DATABASE_URL` | `file:./db/custom.db` | SQLite or Postgres (`postgres://…` accepted) |
| `PUBLIC_BASE_URL` | request origin | Advertised base URL in `/api/admin/meta` and client snippets |
| `CORS_ALLOW_ORIGINS` | `*` | Comma-separated origin allow-list |
| `NOVA_ENGINE_PORT` | `3099` | RouterX Free engine sidecar port (never the app port) |
| `NOVA_UPSTREAM_READ_TIMEOUT` | `600` | Max silence between upstream bytes (long thinking runs) |
| `NOVA_SSE_HEARTBEAT` | `10` | Seconds between keep-alive comments while an upstream is silent |
| `NOVA_AUTO_STANDINS` | `6` | Max auto stand-in providers appended to a chain |

**Data:** no mock/demo data ships with the project. The dashboard starts with the built-in engine and the real OmniRoute catalogue (disabled providers, no keys), and fills with **real** data as you enable providers, add keys, sync catalogues, and use the gateway. Everything you add survives every restart and every `git push`.

---

## 🗂️ Project structure

```
main.py            FastAPI entrypoint — 0.0.0.0:$PORT, CORS, /health, API routers, UI
terminal/          the ENTIRE terminal feature, one path, hostable on its own:
                   pty (real PTY sessions) · sandbox (allowlist exec) · link
                   (in-process vs separately hosted) · api (the contract, mounted by
                   BOTH hosts) · agentbox (the AI agent) · web (the console page at `/`)
                   · service (the standalone host) · config · run.sh · Dockerfile
nova/              config, SQLAlchemy models, bootstrap, engine sidecar client, live
                   discovery, model sync, agent runtime, storage lib, MCP client +
                   registry, routing brain (catalog / routing / router_settings / mcp_tools)
routers/           gateway (/v1 + /api/v1), admin CRUD, terminal glue, compute, storage,
                   agent, MCP registry, build APIs, anthropic_adapter (/v1/messages)
ui/                the Python frontend module — shell + dashboard tabs, server-rendered
                   fragments consumed by HTMX (BFF over the JSON API)
templates/         Jinja2 templates (base shell, tab fragments, partials)
static/            nova.css, app.js, terminal_workspace.js, logo
engine/            RouterX Free engine sidecar (bun/node, zero npm deps) + free-models.json
docs/              architecture.svg · fallback-demo.svg · screenshots/
db/                SQLite database file (created at first boot)
Dockerfile         python:3.12-slim runtime + sidecar — no Node build
docker-compose.yml one-command deploy with persistent volume — gateway + terminal host
```

## 🧪 Tests

```bash
sh scripts/test.sh          # byte-compile → Python suite → dashboard JS suite
python3 -m unittest discover -s tests -p 'test_*.py'
```

Fully offline: provider protocol tests use `httpx.MockTransport`, and the gateway needs no keys.

## 📄 License

MIT — see [LICENSE](LICENSE).
