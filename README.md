# NovaRouter

**A self-hosted AI API gateway with a built-in autonomous agent — rebuilt on Next.js 16.**

NovaRouter fans out requests across many AI providers (including fully free ones), applies fallback + cooldown strategies, manages keys/models/routes, and ships with a unified **Nova Console** where one chatbox can answer questions, run shell commands, and execute agent tasks.

![stack](https://img.shields.io/badge/Next.js-16-black) ![stack](https://img.shields.io/badge/Prisma-SQLite-16a34a) ![stack](https://img.shields.io/badge/UI-shadcn%2Fui-slate)

---

## ✨ Features

| Area | What you get |
| --- | --- |
| 🤖 Autonomous Agent | Task queue, live step streaming, tool use (web search, page reader), task history & cancellation |
| 💬 Nova Console | **One chatbox for everything** — questions → AI chat with fallback telemetry, `$ cmd` → sandbox shell, `! task` → agent execution |
| 🧠 Free AI providers | Built-in presets (OpenRouter, Groq, GitHub Models, Google AI Studio, Mistral, Ollama, …) with health checks, weights, cooldowns |
| 🔀 Smart routing | Fallback chains, identity spoofing, request logs, analytics dashboards |
| 🗄️ Storage manager | Firebase + free storage providers (Supabase, Backblaze B2, Cloudflare R2, GitHub…), dashboard sign-in/connect flows, file browser, backups |
| ⚙️ Compute config | Node.js V8 heap expansion, in-terminal RAM booster, free compute providers (Colab, Kaggle…), **GPU pool config** (attach/detach, VRAM) |
| 🔑 Key management | Multi-key pools per provider, bulk import, cooldown tracking, client keys for the gateway |
| 🎨 UI/UX | Dark terminal aesthetic (emerald/slate), responsive mobile-first layout, sticky footer, shadcn/ui |

---

## 🚀 Quickstart

### Run with Docker (recommended)

```bash
git clone https://github.com/artishade/Novarouter.git
cd Novarouter
docker compose up -d
```

Open **http://localhost:3000**. Done.

- On first start the entrypoint creates the SQLite schema and seeds realistic demo data automatically (13 providers / 35 models / 160 request logs).
- Set `NOVA_SEED=0` to start with an empty database.
- Data persists in the `nova-db` Docker volume.

Prefer plain Docker?

```bash
docker build -t novarouter .
docker run -d -p 3000:3000 -v nova-db:/app/db novarouter
```

### Deploy to Render.com (or any Docker host)

1. **New → Web Service** → connect this repo → **Runtime: Docker** (build command / start command not needed — the image self-initializes on boot).
2. Add an environment variable:
   - `DATABASE_URL` — **recommended: a free Postgres URL** (Neon, Supabase, Aiven, …).
     Postgres URLs are **auto-detected**: on boot the container switches the Prisma schema provider to `postgresql`, regenerates the client and syncs the schema — zero manual migration steps.
   - Or SQLite: `file:/app/db/custom.db` (note: the container filesystem is ephemeral on free plans — prefer Postgres, or mount a disk at `/app/db` where supported).
   - Optional: `NOVA_SEED=0` to start with an empty database.
3. Deploy. Render injects `PORT` automatically and the server binds `0.0.0.0`.

Notes:
- Pooled endpoints (`...pooler...` hosts, e.g. Neon/Supabase poolers) get `pgbouncer=true` appended automatically for transaction-mode pooling compatibility.
- On first boot the entrypoint syncs the schema and seeds demo data only if the database is empty (so restarts/redeploys never duplicate data).

### Run with Bun (dev)

```bash
git clone https://github.com/artishade/Novarouter.git
cd Novarouter
bun install
cp .env.example .env        # SQLite path (resolved relative to prisma/schema.prisma)
bun run db:push             # create schema
bun prisma/seed.ts          # seed realistic demo data
bun run dev                 # http://localhost:3000
```

---

## 🖥️ Using the Nova Console

Everything lives in one chatbox — input is routed automatically:

| You type | What happens |
| --- | --- |
| `Why is my fallback chain failing?` | Gateway AI answers in chat, with live provider/model telemetry chips |
| `$ free -m` | Runs a real command in the sandbox executor, shows output + exit code |
| `$ nova gpu kaggle on` | Attaches a GPU from the Kaggle pool to your compute config |
| `$ nova boost 512` | Expands the Node.js V8 heap / RAM booster by 512 MB |
| `$ nova agents` | Lists autonomous agent task history |
| `/boost 256`, `/models`, `/gpu`, `/storage`, `/help` | Slash shortcuts for terminal-side config |
| `! Audit the storage providers and report failures` | Delegates to the autonomous agent — watch steps stream live until the task completes |
| Natural language task | Detected as a task → auto-delegates to the agent |

---

## 🔌 Gateway API

Drop-in OpenAI-compatible endpoints:

```bash
curl http://localhost:3000/api/v1/models

curl http://localhost:3000/api/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"your-model","messages":[{"role":"user","content":"hi"}]}'
```

### Live model discovery (`/v1/models?discover=1`)

Query each provider's **real** `/models` endpoint right now — OpenAI-compatible,
Gemini, Anthropic and Ollama wire formats are handled and normalized
(per-provider status, context lengths, pricing, free flags). Cached 5 min.

```bash
curl 'http://localhost:3000/api/v1/models?discover=1'                # all providers
curl 'http://localhost:3000/api/v1/models?discover=1&provider=groq'  # one provider
curl 'http://localhost:3000/api/v1/models?discover=1&free=1'         # free-tier only
```

The **Nova Agent** uses the same capability as its `discover_models` tool — ask it
*"which free models are available right now?"* or give it a goal like
*"find the model with the largest context window under $1/1M tokens"* and it will
discover, compare and report live results.

Admin APIs live under `/api/admin/*` (providers, models, keys, routes, storage, terminal, compute, analytics, logs). Agent APIs under `/api/agent/*` (tasks, tools).

---

## 🗂️ Project Structure

```
prisma/            schema.prisma + realistic seed (demo placeholder keys only)
src/app/api/       gateway (v1), admin, agent route handlers
src/components/    Dashboard tabs (Overview, Console, Providers, Models, Routes, Keys, Storage, Analytics, Logs)
src/lib/server/    agent runtime, terminal executor, config
db/                SQLite database file
Dockerfile         multi-stage build (bun + Next standalone + auto DB init)
docker-compose.yml one-command deploy with persistent volume
```

---

## 🔒 A note on seed data

Seed provider keys are **obvious placeholders** (`sk-or-v1-SEED-DEMO-PLACEHOLDER-…`) — never real secrets. Add your own keys in **Dashboard → Providers** or paste them via the Keys tab.

## 📄 License

MIT
