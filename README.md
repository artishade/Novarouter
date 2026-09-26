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
- On first start the app creates the schema and bootstraps the real minimum: the built-in NovaFree engine (3 models) + gateway config. **No mock data is ever seeded.**
- Data persists in the `nova-db` Docker volume.

Prefer plain Docker?

```bash
docker build -t novarouter .
docker run -d -p 3000:3000 -v nova-db:/app/db novarouter
```

### Deploy to Render.com (or any Docker host)

1. **New → Web Service** → connect this repo → **Runtime: Docker** (no build/start commands needed — the image self-initializes).
2. Add an environment variable:
   - `DATABASE_URL` — **recommended: a free Postgres URL** (Neon, Supabase, Aiven, …). Existing Prisma-style values (`postgres://…`, `pgbouncer` params) are accepted and normalized automatically.
   - Or SQLite: `file:/app/db/custom.db` (the container filesystem is ephemeral on free plans — prefer Postgres, or mount a disk at `/app/db`).
   - Optional: `PUBLIC_BASE_URL=https://novarouter.onrender.com` (the request origin is used when unset).
   - Optional: `CORS_ALLOW_ORIGINS=*` (default) or a comma-separated origin list.
3. Set the **Health Check Path** to `/health`.
4. Deploy. Render injects `PORT` automatically; the server binds `0.0.0.0:$PORT`.

### Run locally (Python)

```bash
git clone https://github.com/artishade/Novarouter.git
cd Novarouter
pip install -r requirements.txt
cd engine && bun install --production && cd ..   # NovaFree engine sidecar deps
cp .env.example .env
python3 main.py             # http://localhost:3000 — UI + API in one process
```

`python3 main.py` starts the FastAPI server on `0.0.0.0:$PORT`. It serves **everything**: the OpenAI-compatible gateway, the admin/agent JSON APIs, and the dashboard UI itself (Jinja2 templates rendered by the `ui/` Python package — HTMX for interactivity, no Node dev server, no proxy).

---

## 🔌 Gateway API

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
engine/            NovaFree engine sidecar (bun/node) — z-ai SDK bridge: chat stream, web search, page reader
db/                SQLite database file (bootstrap at first boot)
Dockerfile         python:3.12-slim runtime + bun sidecar (UI is pure Python — no node build)
docker-compose.yml one-command deploy with persistent volume
```

## 🔒 A note on data

No mock/demo data ships with the project: the dashboard starts empty (plus the built-in engine) and fills with **real** data as you add providers, sync live model catalogues, and use the gateway. Legacy deployments seeded with demo data are cleaned automatically on first boot.

## 📄 License

MIT
