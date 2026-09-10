# ⚡ NovaRouter v2

**One API key, every provider, every modality.** NovaRouter is a self-hosted AI
API gateway that fans out to OpenAI-compatible *and* Anthropic-native
providers — with key rotation, cooldown-aware failover, per-client rate
limits, usage tracking, and a built-in dashboard + playground.

## What's new in v2

| Area | v1 | v2 |
|---|---|---|
| Chat | ✅ | ✅ + tools, vision, thinking/reasoning everywhere |
| Anthropic native | text only | full: tools, images, thinking, system, streaming |
| `/v1/messages` | ❌ | ✅ — Claude Code / Anthropic SDK on ANY provider |
| `/v1/responses` | ❌ | ✅ — Codex CLI & Responses API clients |
| Images | ❌ | ✅ `/v1/images/generations` + `/edits` |
| Video | ❌ | ✅ `/v1/videos` (+ polling) — sora-2 style upstreams |
| Audio | ❌ | ✅ TTS (`/v1/audio/speech`) + STT (transcriptions/translations) |
| Moderations | ❌ | ✅ |
| Model metadata | name only | capabilities: tools/vision/reasoning/image/video/tts/… |
| Client keys | model allowlist | + RPM limits, daily token quotas, usage totals |
| Thinking control | ❌ | `reasoning_effort`, `:thinking` suffix, `thinking` blocks |

## Endpoints

```
/v1/chat/completions        OpenAI chat (all providers, tools + vision + thinking)
/v1/completions             legacy text completions
/v1/embeddings              embeddings
/v1/responses               Responses API (translated bidirectionally, incl. SSE)
/v1/messages                Anthropic Messages API (native or translated)
/v1/images/generations      image generation
/v1/images/edits            image editing
/v1/videos                  video generation (POST creates, GET {id} polls)
/v1/audio/speech            TTS — binary audio out
/v1/audio/transcriptions    STT — multipart upload
/v1/audio/translations      STT translate
/v1/moderations             text moderation
/v1/models                  catalogue with capabilities + health
```

## Quick start

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8080
# dashboard: http://127.0.0.1:8080  (admin token printed on first start)
```

Point any OpenAI SDK at it:

```python
from openai import OpenAI
c = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="nova-...")
print(c.chat.completions.create(
    model="groq/llama-3.3-70b-versatile",
    messages=[{"role": "user", "content": "hi"}],
).choices[0].message.content)
```

Claude Code on any provider (Anthropic SDK shape):

```python
import anthropic
c = anthropic.Anthropic(base_url="http://127.0.0.1:8080/v1", api_key="nova-...")
m = c.messages.create(model="groq/llama-3.3-70b-versatile", max_tokens=100,
                      messages=[{"role": "user", "content": "hi"}])
```

## Thinking / reasoning (every convention works)

```jsonc
// any of these enable reasoning:
{"model": "claude-sonnet-4", "reasoning_effort": "high", ...}
{"model": "deepseek/deepseek-r1:thinking", ...}          // OpenRouter suffix
{"model": "x", "thinking": {"type": "enabled", "budget_tokens": 8192}, ...}
```

Anthropic upstreams get native thinking blocks; OpenAI upstreams get
`reasoning_effort`; DeepSeek-style `reasoning_content` is passed through as
`reasoning` on every response.

## Deployments

- **Docker**: `docker build -t novarouter .` → run with `DATABASE_URL` (Postgres)
- **Fly.io / Render**: config files included (`fly.toml`, `render.yaml`)
- **Vercel**: `app.main:app` entrypoint; requires `DATABASE_URL` (e.g. Neon)

Env vars: `DATABASE_URL`, `NOVA_ADMIN_TOKEN`, `NOVA_TIMEOUT`,
`NOVA_MAX_KEY_ATTEMPTS`, `NOVA_COOLDOWN_429/402/5XX`, `NOVA_LOG_RETENTION`.

## Tests

```bash
python -m pytest tests/test_basic.py -q   # core suite (9 tests)
python tests/verify_v2.py                # translation unit tests (12)
python tests/verify_endpoints.py         # route smoke tests (9)
python tests/verify_e2e.py                # full E2E vs mock upstream (13)
```

MIT licensed.