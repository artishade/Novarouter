# ⚡ NovaRouter v2

**One API key, every provider, every modality.** NovaRouter is a self-hosted AI
API gateway that fans out to OpenAI-compatible *and* Anthropic-native
providers — with key rotation, cooldown-aware failover, **model fallback with
identity spoofing**, per-client rate limits, usage tracking, and a built-in
dashboard + playground.

## The fallback promise

Agents ask for a specific model id (`claude-opus-4-6`, `gpt-5-codex`, ...).
When that model is unusable — dead key, retired upstream, never registered —
NovaRouter silently retries on another model and **returns the response with
the model id the client selected**. Claude Desktop, Codex, and every other
agent keep working through model outages without knowing anything changed.

```
client → POST /v1/messages  model=claude-opus-4-6
           │
           ├─ stage 1: claude-opus-4-6 providers        ✗ 500 / not found
           ├─ stage 2: explicit fallback chain          (dashboard "Fallbacks" tab)
           ├─ stage 3: auto-picked healthy stand-in      (capability-matched)
           ▼
         200 OK  model="claude-opus-4-6"     ← the id the client asked for
                 _nova.upstream_model=...    ← the model that actually answered
```

The same promise covers **media**: when the request carries images, video,
audio or documents the selected model can't read, a capable stand-in serves
it first — same spoofed id, so agents never hit "this model doesn't support
image input" errors. See [Media routing](#media-routing-agents-never-hit-model-cant-read-images).

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

## Model fallback + identity spoofing

Define routes in the dashboard (Fallbacks tab) or via the admin API:

```bash
# claude-opus-4-6 -> try these, in order, when the real one is unusable
curl -X POST $BASE/admin/api/routes -H "X-Admin-Token: $ADMIN" -d '{
  "public_id": "claude-opus-4-6",
  "fallbacks": "anthropic/claude-sonnet-4, groq/llama-4, openai/gpt-5",
  "auto": true,
  "note": "opus outage 2026-09"
}'

# '*' = the default chain appended after every model's own chain
curl -X POST $BASE/admin/api/routes -H "X-Admin-Token: $ADMIN" -d '{
  "public_id": "*",
  "fallbacks": "groq/llama-4",
  "auto": true
}'
```

Behavior:

- **Stages**: the requested model's own providers first; if all are unusable,
  the explicit chain top-to-bottom; then (with `auto: true`) capability-matched
  healthy stand-ins — ranked by matching tools/reasoning/vision and latency.
- **Spoofing**: the response `model` field — and every SSE chunk — always
  carries the requested id. `_nova.upstream_model` in the body and the `via`
  column in Logs reveal what really served the request.
- **Unknown models still work**: a route on `claude-opus-4-6` serves requests
  for that id even when no provider lists it. `/v1/models` advertises these
  ids so agents' pickers show them.
- **Opt-out**: per request with `{"nova":{"spoof_model":false}}`; globally with
  `NOVA_SPOOF_MODEL=0`.
- **Preview** a model's full routing plan: `GET /admin/api/routes/preview?model=...`

Env vars: `NOVA_AUTO_FALLBACK` (default `1`), `NOVA_SPOOF_MODEL` (default `1`),
`NOVA_FALLBACK_MAX` (default `3`, auto-picked stand-ins per request).

## Media routing (agents never hit "model can't read images")

Suppose the selected model can't read images, video, audio or documents —
NovaRouter detects the media parts in the request, and when the selected
model lacks the capability (`vision` / `audio_in`), the request is served
by a capable stand-in model instead. The response still carries the
requested model id, so the AI agent never sees an error or a model switch.

- **Detects**: `image_url` / `input_image` / anthropic `image` blocks,
  `video_url` / `input_video`, `input_audio` / anthropic `audio` blocks,
  OpenAI `file` parts, anthropic `document` blocks — in `/v1/chat/completions`,
  `/v1/messages` and `/v1/responses` (translated too).
- **Inlines plain text**: `.txt`, `.md`, `.csv`, `.json`, code files and
  plain-text document blocks become text parts — every model can read them,
  no reroute needed.
- **Normalizes images**: image `file` parts become `image_url` data URIs;
  PDFs stay file/document blocks for native readers.
- **Cross-protocol**: media blocks are translated between OpenAI and
  Anthropic shapes in both directions, so a vision stand-in behind either
  protocol can serve the rerouted request.
- **Respects client keys**: stand-ins are only chosen from models the
  client key is allowed to use.
- **Opt-out**: globally with `NOVA_MEDIA_ROUTING=0` (the selected model then
  gets the request as-is, upstream behavior unchanged).

Env var: `NOVA_MEDIA_ROUTING` (default `1`). The route preview
(`GET /admin/api/routes/preview?model=...`) shows each model's blind spots
and which stand-ins would take over.

## Deployments

- **Docker**: `docker build -t novarouter .` → run with `DATABASE_URL` (Postgres)
- **Fly.io / Render**: config files included (`fly.toml`, `render.yaml`)
- **Vercel**: `app.main:app` entrypoint; requires `DATABASE_URL` (e.g. Neon)

Env vars: `DATABASE_URL`, `NOVA_ADMIN_TOKEN`, `NOVA_TIMEOUT`,
`NOVA_MAX_KEY_ATTEMPTS`, `NOVA_COOLDOWN_429/402/5XX`, `NOVA_LOG_RETENTION`,
`NOVA_AUTO_FALLBACK`, `NOVA_SPOOF_MODEL`, `NOVA_FALLBACK_MAX`, `NOVA_MEDIA_ROUTING`.

## Tests

```bash
python -m pytest tests/test_basic.py -q              # core suite
python -m pytest tests/test_model_fallback.py -q     # fallback + spoofing
python tests/verify_v2.py                # translation unit tests (12)
python tests/verify_endpoints.py         # route smoke tests (9)
python tests/verify_e2e.py                # full E2E vs mock upstream (13)
```

MIT licensed.