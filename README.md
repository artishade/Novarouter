# NovaRouter ⚡

A lightweight, self-hostable **multi-provider AI API gateway** with:

- **OpenAI-compatible API** — point any OpenAI SDK/tool/agent at it
- **Multi-provider fan-out** — OpenRouter, Groq, DeepSeek, Together, Cerebras, Mistral, xAI, Fireworks, OpenAI, Gemini (OpenAI-compat layer), Anthropic (native translation), local Ollama — or any OpenAI-compatible endpoint
- **Upstream key rotation** — round-robin by weight, automatic cooldowns on 429/402/401/5xx, bulk key import
- **Automatic failover** — same model on multiple providers → healthiest one serves, others back it up
- **Model availability checker** — ping every model, see which are alive/free, disable dead ones
- **Dashboard UI** — providers, keys, models, logs, checker, export/import — all built in, zero frontend build step
- **Streaming** — SSE pass-through for OpenAI-style providers; Anthropic streams are translated to OpenAI chunks on the fly

Storage is dual-mode, covering local, Fly.io/Docker, and Vercel:

| Mode | When | Backend |
|---|---|---|
| Local / self-hosted | default (no `DATABASE_URL`) | SQLite file (`data/nova.db`) |
| Fly.io / Docker | no `DATABASE_URL` + `/data` volume | SQLite on a persistent Fly volume |
| Serverless (Vercel) / any host | `DATABASE_URL` env set | PostgreSQL (e.g. Neon — free tier works) |

---

## Run locally (SQLite)

```bash
git clone <your-repo-url> novarouter && cd novarouter
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8080
```

Open http://127.0.0.1:8080 — the admin token is printed in `data/admin_token.txt` (or set your own with `NOVA_ADMIN_TOKEN=...`).

Quick smoke test:

```bash
curl http://127.0.0.1:8080/healthz
# {"ok":true,"service":"novarouter","storage":"sqlite"}
```

## Deploy to Fly.io (SQLite on a persistent volume) — recommended for a personal gateway

Fly runs NovaRouter as a **real long-lived VM**, so plain SQLite works natively — no Postgres needed. The repo ships everything: `Dockerfile`, `docker-entrypoint.sh` (fixes volume ownership, drops to an unprivileged user), `fly.toml` (HTTP service, health check, 3 GB volume), and `.dockerignore`.

```bash
fly auth signup                      # or: fly auth login
cd novarouter
fly launch                           # reads fly.toml: creates app + novarouter_data volume, deploys
fly secrets set NOVA_ADMIN_TOKEN=your-strong-token
fly deploy                           # subsequent deploys
```

Then open `https://novarouter.fly.dev/` and paste the token. Agents point at:

```
OPENAI_BASE_URL = https://novarouter.fly.dev/v1
OPENAI_API_KEY  = nova-...
```

Notes:
- **Volume**: SQLite lives on `/data` (a Fly volume, `initial_size = 3gb` on first launch). `fly volumes extend` if you outgrow it. Machine deploys keep it; deleting the volume is what deletes data.
- **Autostop/autostart**: enabled with `min_machines_running = 0` — idle machines stop after a few minutes, cold-starting on the next request (~1–2s). Set `min_machines_running = 1` in `fly.toml` if you want zero cold starts.
- **Postgres on Fly is also fine**: set `DATABASE_URL` via `fly secrets set` (e.g. Fly Postgres or Neon) and the gateway switches backends automatically — the volume can then be removed.
- **Logs / shell**: `fly logs`, `fly ssh console` (curl is included in the image).
- Costs: a single shared-cpu-1x 256 MB machine with autostop stays within Fly's free allowances for typical personal use; check [fly.io/pricing](https://fly.io/pricing) for current limits.

### Docker (any host)

```bash
docker build -t novarouter .
docker run -d -p 8080:8080 -v novarouter-data:/data \
  -e NOVA_ADMIN_TOKEN=your-strong-token novarouter
```

## Deploy to Vercel (Postgres)

Vercel Functions are stateless — SQLite resets on every cold start — so the hosted mode uses Postgres. The [Neon](https://neon.tech) free tier is more than enough.

1. **Create a Postgres database** (Neon: sign up → create project → copy the connection string).

2. **Fork/push this repo to GitHub**, then in Vercel: *Add New → Project → Import* the repo.

3. **Set environment variables** (Project → Settings → Environment Variables):

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | your Neon `postgresql://...` string |
   | `NOVA_ADMIN_TOKEN` | your own strong admin token |

   > ⚠️ Both are required on Vercel. Without `DATABASE_URL` data resets on every cold start; without `NOVA_ADMIN_TOKEN` the admin API returns 503 (it must be stable across instances, so it can't be auto-generated on a read-only filesystem).

4. **Deploy.** Vercel auto-detects FastAPI via the `app/main.py` entrypoint (`tool.vercel.entrypoint` in `pyproject.toml`). `vercel.json` raises the function max duration to 300s for long chat streams.

5. Open `https://<project>.vercel.app/`, paste your `NOVA_ADMIN_TOKEN`, add a provider + key, sync models, generate a gateway key, and point your agents at:

   ```
   OPENAI_BASE_URL = https://<project>.vercel.app/v1
   OPENAI_API_KEY  = nova-...   (from Access Keys in the dashboard)
   ```

## Point agents at it

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",   # or your vercel.app URL + /v1
    api_key="nova-...",                     # gateway key from the dashboard
)
resp = client.chat.completions.create(
    model="groq/llama-3.3-70b-versatile",   # prefix/provider namespaced ids
    messages=[{"role": "user", "content": "hi"}],
)
print(resp.choices[0].message.content)
```

## How routing works

- Requested model id is matched against `exposed_id`, then raw upstream id, then `providername/model`.
- Multiple providers serving the same id = failover chain, healthiest/fastest first.
- Within a provider, keys rotate round-robin by weight; 429/402/401/5xx put a key on cooldown and the request moves to the next key/provider.
- Non-retryable upstream errors (e.g. malformed request) return immediately instead of burning other keys.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | *(unset → SQLite)* | Postgres connection string; selects the Postgres backend |
| `NOVA_ADMIN_TOKEN` | *(auto-generated locally)* | Admin/dashboard token. **Required on Vercel.** |
| `NOVA_HOST` / `NOVA_PORT` | `127.0.0.1` / `8080` | Local bind address |
| `NOVA_TIMEOUT` / `NOVA_CONNECT_TIMEOUT` | `180` / `15` | Upstream request timeouts (seconds) |
| `NOVA_MAX_KEY_ATTEMPTS` | `4` | Keys tried per provider per request |
| `NOVA_COOLDOWN_429` / `NOVA_COOLDOWN_402` / `NOVA_COOLDOWN_5XX` | `60` / `1800` / `30` | Key cooldown seconds |
| `NOVA_LOG_RETENTION` | `5000` | Rows kept in the request log |
| `NOVA_DATA_DIR` / `NOVA_DB` | `./data` / `data/nova.db` | SQLite locations (local mode) |

## API surface

| Route | Auth | Purpose |
|---|---|---|
| `POST /v1/chat/completions` | `Authorization: Bearer nova-...` | OpenAI-compatible chat (streaming supported) |
| `POST /v1/completions` / `POST /v1/embeddings` | gateway key | Legacy completions / embeddings |
| `GET /v1/models` | gateway key | Model list with provider + health |
| `/admin/api/*` | `X-Admin-Token` | Full CRUD: providers, keys, client keys, models, checker jobs, logs, import/export |
| `GET /healthz` | none | Liveness probe |

## Security notes

- Upstream provider keys and gateway client keys live in your DB — protect it.
- The admin token gates every `/admin/api/*` route; on Vercel it travels via `X-Admin-Token` over HTTPS only.
- `include_secrets=true` on `/admin/api/export` reveals keys — use only for backups.

## License

MIT — see [LICENSE](LICENSE).# Novarouter
