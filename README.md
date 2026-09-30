# NovaRouter

**A self-hosted AI API gateway with a built-in autonomous agent — now on Python (FastAPI).**

NovaRouter fans out requests across many AI providers (including fully free ones), applies fallback + cooldown strategies, manages keys/models/routes, and ships with a unified **Nova Console** where one chatbox can answer questions, run shell commands, and execute agent tasks.

![stack](https://img.shields.io/badge/Python-FastAPI-009688) ![stack](https://img.shields.io/badge/SQLAlchemy-2-orange) ![stack](https://img.shields.io/badge/UI-Jinja2%20%2B%20HTMX-emerald)

**100% Python — backend and frontend.** The dashboard UI is a Python module (`ui/`, Jinja2 templates + HTMX + Alpine) rendered and served by the same FastAPI process. No Node frontend, no React build step.

---

## 🌐 Hosted gateway (base URL for external use)

| What | Value |
| --- | --- |
| **Gateway base URL** | `https://novarouter.onrender.com/v1` |
| **Health check** | `https://novarouter.onrender.com/health` |
| **Dashboard** | `https://novarouter.onrender.com` |

The base URL is also advertised live by `GET /api/admin/meta` (`base_url` field). Set `PUBLIC_BASE_URL` to override, otherwise the server always uses the incoming request origin — which on Render is your hosted domain automatically.

---

## ✨ Features

| Area | What you get |
| --- | --- |
| 🐍 Python server | FastAPI + SQLAlchemy — binds `0.0.0.0:$PORT` (Render assigns PORT dynamically, never hardcoded), CORS enabled for browser clients, `/health` for uptime pings |
| 🤖 Autonomous Agent | Task queue, background execution, tool use (web search, page reader, terminal, live model discovery), task history & cancellation |
| 💬 Nova Console | **One chatbox for everything** — questions → AI chat with fallback telemetry, `$ cmd` → sandbox shell, `! task` → agent execution |
| 🧠 Free AI providers | Built-in presets (OpenRouter, Groq, GitHub Models, Google AI Studio, Mistral, Ollama, …) with health checks, weights, cooldowns |
| 🔀 Smart routing | Fallback chains, identity spoofing, request logs, analytics dashboards |
| 📡 OpenAI-spec API | `/v1/models`, `/v1/chat/completions` (**streaming SSE + non-streaming**), `/v1/completions` (legacy), `/v1/messages` (Anthropic format), `/v1/embeddings`, live discovery |
| 🗄️ Storage manager | Firebase + free storage providers (Supabase, Backblaze B2, Cloudflare R2, GitHub…), dashboard sign-in/connect flows, file browser, backups |
| ⚙️ Compute config | Gateway memory booster, in-terminal RAM booster, free compute providers (Colab, Kaggle…), **GPU pool config** (attach/detach, VRAM) |
| 🔑 Key management | Multi-key pools per provider, bulk import, cooldown tracking, client keys for the gateway |
| 🎨 UI/UX | Dark terminal aesthetic (emerald/slate), responsive mobile-first layout, sticky footer — **Python-rendered UI (Jinja2 + HTMX)** |

---

## 🚀 Quickstart

### Run with Docker (recommended)

```bash
git clone https://github.com/artishade/Novarouter.git
cd Novarouter
docker compose up -d
```

Open **http://localhost:3000** (or `http://localhost:$PORT` if you overrode it). Done.

- The server binds `0.0.0.0:$PORT` — `PORT` is read from the environment (Render injects it dynamically; default 3000).
- On first start the app creates the schema and bootstraps the real minimum: the built-in NovaFree engine (the `nova/mini`, `nova/air`, `nova/pro` tiers plus every free model from [ClawLabsAI/free-ai-models](https://github.com/ClawLabsAI/free-ai-models)) + gateway config. **No mock data is ever seeded.**
- Data persists in the `nova-db` Docker volume.

Prefer plain Docker?

```bash
docker build -t novarouter .
docker run -d -p 3000:3000 -v nova-db:/app/db novarouter
```

### Deploy to Render.com (or any Docker host)

1. **New → Web Service** → connect this repo → **Runtime: Docker** (no build/start commands needed — the image self-initializes).
2. Add an environment variable:
   - `DATABASE_URL` — **strongly recommended: a free Postgres URL** (Neon, Supabase, Aiven, …) — see [Keep your data across deploys](#-keep-your-data-across-deploys-important). Prisma-style values (`postgres://…`, `pgbouncer` params) are accepted and normalized automatically.
   - Or SQLite: `file:/app/db/custom.db` (⚠ the container filesystem is ephemeral on Render — without Postgres every deploy resets your data).
   - Optional: `PUBLIC_BASE_URL=https://novarouter.onrender.com` (the request origin is used when unset).
   - Optional: `CORS_ALLOW_ORIGINS=*` (default) or a comma-separated origin list.
3. Set the **Health Check Path** to `/health`.
4. Deploy. Render injects `PORT` automatically; the server binds `0.0.0.0:$PORT`.

### 💾 Keep your data across deploys (IMPORTANT)

**Why data can disappear:** without `DATABASE_URL`, the server falls back to SQLite at `/app/db/custom.db` — a file **inside the container**. Render replaces the container on every `git push` (and free instances restart on idle), so providers, models, keys, routes and configs would reset each time. The dashboard shows a warning banner and `/health` reports `"storage": {"persistent": false}` until this is fixed.

**The fix (one time, free, ~2 minutes):**

1. Create a free project at [neon.tech](https://neon.tech) (or Supabase/Aiven) and copy the connection string — it looks like `postgresql://user:password@ep-xxx.aws.neon.tech/neondb?sslmode=require`.
2. Render dashboard → your NovaRouter service → **Environment** → add `DATABASE_URL` = that string. Prisma-style `postgres://…` URLs and `pgbouncer`/`connection_limit` params are handled automatically.
3. Save & redeploy. On first boot the app creates every table it needs (additive schema sync) and seeds the built-in NovaFree engine if the database is empty.

From then on **deploys never touch your data**: bootstrapping is strictly additive — it creates missing tables and seeds only what's missing, and never deletes, resets or "purges" anything. New deploys only bring new features, bug fixes and additive schema updates.

> Alternative: a Render **persistent disk** (paid plans) mounted at `/var/data` with `DATABASE_URL=file:/var/data/custom.db` works too — but the free Postgres route is recommended.

### Run locally (Python)

```bash
git clone https://github.com/artishade/Novarouter.git
cd Novarouter
pip install -r requirements.txt
cp .env.example .env
python3 main.py             # http://localhost:3000 — UI + API in one process
```

`python3 main.py` starts the FastAPI server on `0.0.0.0:$PORT`. It serves **everything**: the OpenAI-compatible gateway, the admin/agent JSON APIs, and the dashboard UI itself (Jinja2 templates rendered by the `ui/` Python package — HTMX for interactivity, no Node dev server, no proxy).

### Scripts

`bun` is only a task runner here — the app itself is pure Python, and the scripts work with `npm run` too.

| Command | What it does |
| --- | --- |
| `bun run install:py` | Install `requirements.txt` (the only dependency step the project needs) |
| `bun run preview` | Install dependencies if needed, then start the server on `0.0.0.0:$PORT` |
| `bun run test` | Byte-compile, then the Python suite (`tests/test_*.py`) and the dashboard JS suite |
| `bun run dev` / `bun run start` | Start the server and tee the log to `dev.log` / `server.log` |
| `bun run lint` | `compileall` syntax check |

`scripts/preview.sh` is the entrypoint used by hosted previews (Freebuff Cloud): it installs the Python dependencies, reaps a gateway left behind by an earlier run, and binds `0.0.0.0:$PORT`. Because those hosts install dependencies with a Node-only toolchain, the Python install has to live in the run step rather than in an install hook.

> **Sandbox preview:** the preview also sets `NOVA_ENGINE_DISABLED=1`. The built-in engine sidecar is a private loopback service, and hosted preview port detection advertises a loopback listener in preference to the public port — with the sidecar running, the preview URL resolves to the sidecar instead of the dashboard. Turning it off in the sandbox keeps the preview URL on the dashboard; the gateway then degrades to your configured upstreams, exactly as it does anywhere the sidecar cannot run. Production (Docker/Render) is unaffected.

---

## 🆓 Free AI model base

The built-in **NovaFree engine** needs no API key and no configuration. Its model catalogue is a snapshot of [**ClawLabsAI/free-ai-models**](https://github.com/ClawLabsAI/free-ai-models), bundled at `engine/free-models.json` and exposed through the gateway as real model ids (plus three convenience tiers):

| Tier | Routes onto (best free model first) |
| --- | --- |
| `nova/pro` | Nemotron 3 Ultra, Inkling, Qwen3.8-27B, Pollinations |
| `nova/air` | Qwen3.8-27B, Gemma-4-31B, Pollinations |
| `nova/mini` | Pollinations `openai-fast`, Liquid LFM |

Every free catalogue model is also callable directly by its real id (`qwen/qwen3.8-27b:free`, `google/gemma-4-31b-it:free`, …). Responses always report the model you asked for, with the real upstream free model in the `_nova` metadata (`upstream_model`, `provider`, `stage`).

**Keyless by default** (Pollinations, OVHcloud). Optional keys unlock higher rate limits — set them in the dashboard or as environment variables:

| Provider | Env var | Free tier |
| --- | --- | --- |
| OpenRouter | `OPENROUTER_API_KEY` | your own free key, ~20 req/min · 50 req/day |
| ZeroLimitAI | `ZEROLIMIT_API_KEY` | free key |
| OVHcloud AI Endpoints | `OVH_AI_TOKEN` | keyless, frequently rate-limited |

Refresh the bundled snapshot from upstream with `POST /api/admin/models/sync` (the built-in provider is synced from the catalogue, no network calls), or regenerate `engine/free-models.json` from `data/models.json` in the source repository. The engine degrades honestly: if no free route is reachable the gateway falls back to your configured upstreams instead of fabricating a reply.

---

## 🔌 Gateway API

### Connect upstream providers

In **Providers → Add Provider**, choose **Cloudflare Worker**, **Custom gateway API**, **xAI Grok**, or **Anthropic (native)**. A provider's **base URL points upstream**; it is not the NovaRouter client-facing `/v1` URL shown above. Paste an upstream API key during setup or add one later from the provider's **Keys** panel.

| Choice | Protocol and base URL |
| --- | --- |
| Cloudflare Worker | OpenAI-compatible Worker that you deployed, e.g. `https://your-worker.your-subdomain.workers.dev/v1`. Configure its actual URL and token; the Worker must implement the requested API operations. |
| Custom gateway API | OpenAI-compatible gateway root such as `https://gateway.example.com/v1`. Use its upstream URL, not NovaRouter's own URL (which would create a loop). |
| xAI Grok | OpenAI-compatible `https://api.x.ai/v1` with an xAI API key. |
| Anthropic (native) | Native Anthropic Messages API at `https://api.anthropic.com` with an Anthropic API key; **do not** append `/v1` or select an OpenAI-compatible proxy for this choice. |

Enter the API **root**, not a full `/chat/completions`, `/messages`, or `/models` URL. Model discovery calls the upstream catalogue; some custom gateways or Workers do not provide `/models`. If discovery fails, the provider still exists: verify its URL/key and use **Models → Sync Catalogue** to retry. A missing key may also prevent discovery and requests. Provider protocol selection does not change the gateway's client-facing API.

Point any OpenAI/Anthropic SDK at the hosted base URL:

```bash
BASE=https://novarouter.onrender.com/v1

# Models (DB catalogue) — live discovery available too
curl $BASE/models
curl "$BASE/models?discover=1"            # query every provider's real /models endpoint
curl "$BASE/models?discover=1&free=1"     # free-tier models only

# Chat completions — non-streaming
curl $BASE/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"nova/air","messages":[{"role":"user","content":"hi"}]}'

# Chat completions — STREAMING (SSE)
curl -N $BASE/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"nova/air","stream":true,"messages":[{"role":"user","content":"Count to 5"}]}'

# Legacy completions
curl $BASE/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"nova/air","prompt":"Say hello"}'

# Anthropic-format messages (native passthrough for Anthropic providers)
curl $BASE/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"your-model","max_tokens":256,"messages":[{"role":"user","content":"hi"}]}'

# Embeddings (via OpenAI-compatible / Gemini providers)
curl $BASE/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"text-embedding-3-small","input":"hello world"}'

# Health check (Render uptime pings)
curl https://novarouter.onrender.com/health
```

All endpoints exist under both `/v1/*` and `/api/v1/*`. Responses carry `_nova` metadata showing the upstream provider, stage, and fallback state. CORS is enabled — browser apps can call the API directly.

Admin APIs live under `/api/admin/*` (providers, models, keys, routes, storage, terminal, compute, analytics, logs, meta). Agent APIs under `/api/agent/*` (tasks, tools).

---

## 🖥️ Using the Nova Console

Everything lives in one chatbox — input is routed automatically:

| You type | What happens |
| --- | --- |
| `Why is my fallback chain failing?` | Gateway AI answers in chat, with live provider/model telemetry chips |
| `$ free -m` | Runs a real command in the sandbox executor, shows output + exit code |
| `$ nova gpu kaggle on` | Attaches a GPU from the Kaggle pool to your compute config |
| `$ nova boost 512` | Raises the gateway memory booster by 512 MB |
| `$ nova agents` | Lists autonomous agent task history |
| `/boost 256`, `/models`, `/gpu`, `/storage`, `/help` | Slash shortcuts for terminal-side config |
| `! Audit the storage providers and report failures` | Delegates to the autonomous agent — watch steps stream live until the task completes |
| Natural language task | Detected as a task → auto-delegates to the agent |

---

## ⬛ Terminal

The Terminal tab runs a real shell on the gateway host and manages it as a
set of independent sessions, the same way a native terminal app does.

- **Command mode** (first tab) — one-shot commands run through the executor and are saved to history.
- **Session tabs** — each **New session** spawns its own `bash` on a pseudo-terminal, so `ssh`, `git clone`, `sudo` and `vim` all behave normally. Up to 8 run at once; switch, rename (double-click a tab) or close them freely.
- **Live state** — the shell reports its working directory (OSC 7), so a tab follows your `cd` and the tab label tracks the current folder until you name it yourself.
- **Real rendering** — output is drawn with xterm.js: full ANSI colour, selection, `Ctrl+Shift+C` copy, and window resize is forwarded to the PTY.
- Idle sessions are reaped after 30 minutes and everything is torn down on shutdown.

```bash
curl -s localhost:3000/api/admin/terminal/pty/sessions          # state snapshot
curl -s -XPOST localhost:3000/api/admin/terminal/pty/sessions -d '{"cols":120,"rows":32}'
curl -sN localhost:3000/api/admin/terminal/pty/stream?session=<id>   # output + cd + exit events
```

---

## 🗂️ Project Structure

```
main.py            FastAPI entrypoint — 0.0.0.0:$PORT, CORS, /health, API routers, UI
nova/              config, SQLAlchemy models (Prisma-layout compatible), bootstrap,
                   engine sidecar client, live discovery, model sync, terminal sandbox,
                   agent runtime, storage lib
routers/           gateway (/v1), admin CRUD, admin misc, terminal, compute, storage, agent APIs
ui/                the Python frontend module — shell + 9 dashboard tabs,
                   server-rendered fragments consumed by HTMX (BFF over the JSON API)
templates/         Jinja2 templates (base shell, tab fragments, partials)
static/            nova.css, app.js (HTMX wiring, toasts, streaming chat JS), logo
engine/            NovaFree engine sidecar (bun/node, zero npm deps) — routes onto the
                   free-ai-models catalogue (free-models.json): chat stream, search, reader
db/                SQLite database file (bootstrap at first boot)
Dockerfile         python:3.12-slim runtime + bun sidecar (UI is pure Python — no node build)
docker-compose.yml one-command deploy with persistent volume
```

## 🔒 A note on data

No mock/demo data ships with the project: the dashboard starts empty (plus the built-in engine) and fills with **real** data as you add providers, sync live model catalogues, and use the gateway. Legacy deployments seeded with demo data are cleaned automatically on first boot.

## 📄 License

MIT
