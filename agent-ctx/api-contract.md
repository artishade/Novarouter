# NovaRouter Next.js — API CONTRACT (v1)

All agents MUST follow this contract exactly. DB fields are camelCase (Prisma); **API JSON is snake_case**.
DB access: `import { db } from '@/lib/db'`. Shared TS types: `src/lib/types.ts`. Client wrapper: `src/lib/api.ts` (already written — do not modify without orchestrator approval).
Helper: `src/lib/format.ts` has fmtNum/fmtBytes/fmtMb/fmtUptime/timeAgo/fmtClock/fmtDate/maskKey/cx.

Conventions:
- Route handlers: `export async function GET/POST/PATCH/DELETE(req: NextRequest, ctx?)` in `src/app/api/**/route.ts`.
- Errors: `NextResponse.json({ error: 'message' }, { status: 4xx/5xx })`.
- Provider "session" concept: table `ProviderSession` keyed by providerKey (one row per provider, latest wins).
- SystemConfig is a KV table (`key`, value string). Helpers suggested in `src/lib/server/config.ts` (2-b creates it; others may import after it exists — if importing early, guard).
- IDs in URLs: numeric ids for providers/models/keys/client-keys/routes/storage files; string keys for storage providers & compute providers; cuid for agent tasks.

## 1. Core admin (Agent 2-a) — `src/app/api/admin/**`

- `GET  /api/admin/meta` → MetaConfig (types.ts): version '2.0.0', base_url (`<origin>/v1`), storage 'hybrid (local + cloud)', fallback {auto:true, spoof_model:true, max:3}, cooldowns {429:60, 402:300, 5xx:30}, scheduler {check_interval:3600}, hedging {delay:2}, cache {ttl:600, max_entries:1000}, limits {file_max_mb:25, batch_max_items:50000}, presets (see below), kinds ['openai','anthropic','gemini','builtin'], statuses ['healthy','cooling','dead','unknown'], admin_token.
- `GET  /api/admin/stats` → GatewayStats. active_keys = enabled ProviderKeys; healthy/active/dead models from Model.status+enabled; connected_providers = providers having a connected ProviderSession; cache_hit_rate & error_rate & avg_latency from RequestLog (last 24h; cache_hit_rate = via='cache' share, 0 if none); uptime_s from SystemConfig 'gateway_started_at'; memory from `os`+`process.memoryUsage()` (rss_mb, heap used/total, pct = used/total*100); v8 from SystemConfig v8_heap_mb/swap_mb.
- `GET  /api/admin/analytics?hours=24|168|720&group_by=hour|day` → AnalyticsResponse. timeseries buckets (bucket label 'HH:00' for hour, 'MM-DD' for day, ascending, zero-filled); top_models (model, reqs, tokens=tokens_in+tokens_out), top_providers (provider_name), top_clients (client_name); summary with p50/p90/p99 latency (nearest-rank on latencyMs), error stats, spoofed_fallbacks_count (spoofed=true), cache_hits (via='cache').
- `GET  /api/admin/providers` → Provider[]: map all Provider rows + counts (key_count, model_count, ok_count = models status healthy, cooling_count = status cooling) + latest session per provider (session or null).
- `GET  /api/admin/providers/presets` → ProviderPreset[] — built-in catalogue: openrouter, groq, cerebras, gemini (Google AI Studio), mistral, github-models, nvidia, together, deepseek, xai (xAI Grok, base https://api.x.ai/v1, prefix 'xai/', auth https://console.x.ai), fireworks (https://api.fireworks.ai/inference/v1, prefix 'fireworks/'), openai, anthropic, ollama, novafree. Include base_url/prefix/key_hint/free_tier/docs_url/auth_url/requires_signin/color/priority.
- `POST /api/admin/providers` {name, kind?, base_url?, prefix?, priority?, api_keys? (multiline), free_tier?, docs_url?, auth_url?} → {id, keys_added}. Generate key slug from name. If preset exists by name (case-insens) reuse its data.
- `PATCH /api/admin/providers/[id]` {enabled?, priority?, base_url?, prefix?, name?} → {ok}
- `DELETE /api/admin/providers/[id]` → {ok} (cascade removes models/keys). Block deleting key='novafree' with 400.
- `POST /api/admin/providers/[id]/test` → {ok, provider, key_count, latency_ms, status, message}. For providers with keys: attempt real `GET {base_url}/models` with first key (timeout 6s, no throw); set status 'healthy'/'error' from HTTP result, latency measured. For kind 'builtin' → {ok:true, status:'healthy', message:'Built-in engine online'}. For zero keys → {ok:false, status:'no_keys', message:'No API keys configured — add a key or use Sign In'}.
- `POST /api/admin/providers/[id]/signin` {username?} → creates/refreshes ProviderSession for that provider (username default: provider key + '-operator', plan 'free', token_mask from newest key or 'pat-••••', connected_at now) → {ok, session}. If provider has auth_url=null → 400 'This provider does not require sign-in'.
- `POST /api/admin/providers/[id]/signout` → delete session → {ok}
- `GET  /api/admin/keys?provider_id=` → UpstreamKey[] (api_key_preview = first 8 + '…' + last 4; include provider_name). Desc by created_at.
- `POST /api/admin/keys` {provider_id, api_key, label?, weight?} → {id}
- `POST /api/admin/keys/bulk` {provider_id, keys (newline/comma separated), label_prefix?} → {added, ids[]}
- `DELETE /api/admin/keys/[id]` → {ok}
- `POST /api/admin/keys/clear-cooldowns` → {ok, cleared} (set cooldownUntil null on all)
- `GET  /api/admin/client-keys` → ClientKey[] (desc created_at)
- `POST /api/admin/client-keys` {name, allowed_models?, rpm_limit?, tpd_limit?} → ClientKey. token = 'nova-sk-' + 32 hex (crypto.randomUUID().replaceAll('-','') x2 sliced).
- `PATCH /api/admin/client-keys/[id]` (enabled?, name?, rpm_limit?, tpd_limit?, allowed_models?) → {ok}
- `DELETE /api/admin/client-keys/[id]` → {ok}
- `GET  /api/admin/models?provider_id&status&search&capability&free` → {total, rows: Model[]} — joined provider_name/provider_color; capabilities parsed JSON → object. Order: provider priority asc, then exposed_id. search matches exposed_id/display_name/model_id (contains, case-insens). capability one of tools|vision|reasoning. free=true → is_free.
- `PATCH /api/admin/models/[id]` {enabled} → {ok}
- `POST /api/admin/models/[id]/ping` → {ok, status, latency_ms, http_status, detail}. If provider kind='builtin' → always healthy, latency 300-700 random. If provider has keys → real GET {base_url}/models with key, 6s timeout; healthy on 2xx else dead. No keys → status stays 'unknown', detail 'No upstream key configured', http_status 0, ok:false. Update Model row (status/latency/checkedAt/httpStatus). detail: on error put short message.
- `POST /api/admin/models/sync?provider_id=` → {ok, synced, new_added}. Simulated catalogue refresh: for each provider with ≥1 key (or builtin), ensure the seeded models exist (upsert by providerId+modelId); new_added = number created; synced = total examined. Do NOT fetch network.
- `GET  /api/admin/routes` → ModelRoute[] (fallbacks parsed to string[])
- `POST /api/admin/routes` {public_id, fallbacks: string[], auto?, note?} → {id}
- `PATCH /api/admin/routes/[id]` → {ok}; `DELETE /api/admin/routes/[id]` → {ok}
- `GET  /api/admin/routes/preview?model=` → RoutePreview: stages = [Stage 1 direct providers (if any model/exposed_id match, list their exposed_ids), Stage 2 explicit chain (route fallbacks or '*' route), Stage 3 auto stand-ins (healthy enabled free models, take up to 3, exclude already listed)]; direct=bool; spoof_model from SystemConfig.
- `GET  /api/admin/logs?limit=100` → RequestLog[] desc by ts (ts as epoch ms number).

## 2. Terminal / Compute / Storage (Agent 2-b) — `src/app/api/admin/{terminal,compute,storage}/**`

Create `src/lib/server/config.ts` first: `getConfig(key)`, `setConfig(key, value)`, `getGpuProviders()` (parse SystemConfig 'gpu_providers' JSON → ComputeProvider[] with enabled flag), `setGpuProviders(list)`. Export also `MEMORY_KEYS`.

- `GET /api/admin/terminal/history` → TerminalHistoryResponse:
  - cwd = SystemConfig 'terminal_cwd' or '/workspace'
  - history: last 50 TerminalCommand asc → items {id, command, output, exit_code, duration_ms, cwd, timestamp: epoch ms}
  - system from `os`: platform, release, arch, hostname, uptime_s, totalmem_mb, freemem_mb, cpus (count), load_pct (avg load × 100/cpus, clamp 0-99), node_version (process.version)
  - memory_config {v8_heap_mb, swap_mb, boost_applied_at (epoch ms | null)}
  - gpu {enabled, strategy, total, enabled (enabled flag count), connected}
- `POST /api/admin/terminal/exec` {command, cwd?} → ExecResult. SAFE EXECUTOR — allowlist approach:
  - Parse first token. Allowed: ls, pwd, cat (workspace-relative only), df, free, ps, uname, whoami, uptime, date, echo, node -v/--version, bun --version, npm -v, env (filtered), which, help, clear, plus `nova` subcommands.
  - Implement realistic outputs: `free -m` real os values + configured swap row; `df -h` static realistic fs table incl. /workspace; `ps aux` realistic table (node server, prisma engine, bun); `ls -la` fake but stable workspace listing (workspace/ models/ logs/ storage/ .env nova.config.json); `cat nova.config.json` pretty JSON of SystemConfig; `cat .env` masked.
  - `nova` subcommands: `nova status` (stats one-liner), `nova models [n]` (top models table), `nova boost <mb>` (updates v8_heap_mb via setConfig, prints new limit), `nova gpu` (GPU pool table from config), `nova storage` (storage providers table), `nova keys` (client keys count), `nova help`.
  - Unknown → exit 127, stderr `bash: <cmd>: command not found — type 'help' for allowed commands`.
  - DANGEROUS tokens (rm, sudo, kill, chmod, curl, bash, sh, |, ;, &&, >, <, backtick, $()) → exit 126 stderr `sandbox: command blocked by NovaRouter safety policy`.
  - Persist every executed command row (TerminalCommand), cap table to 200 rows (delete oldest).
  - `clear` → {ok, output:'', code:0}.
  - Return duration_ms measured. `help` output lists allowed commands.
- `POST /api/admin/terminal/clear` → delete all TerminalCommand rows → {ok}
- `POST /api/admin/terminal/boost-ram` {heap_mb, swap_mb} → BoostRamResult: capture heap_before_mb (process.memoryUsage().heapUsed/1MB), write SystemConfig v8_heap_mb/swap_mb/boost_applied_at, heap_after_mb = recompute heapUsed, swap_success true, swap_output realistic `swapon: /nova/swapfile: 4096M` style message, message: `⚡ Boost applied — Node V8 old-space limit set to ${heap_mb} MB + ${swap_mb} MB virtual swap. New terminal sessions spawn with: node --max-old-space-size=${heap_mb}`.
- `GET /api/admin/compute/providers` → ComputeProvider[] (from SystemConfig gpu_providers, each with enabled boolean)
- `POST /api/admin/compute/providers/[id]/toggle` {enabled} → {ok, message} (flip flag in JSON, persist; message mentions provider name and free tier; unknown id → 404)
- `GET /api/admin/compute/config` → GpuConfig {enabled (gpu_enabled), strategy (gpu_strategy), providers, total_vram_gb (sum all), enabled_vram_gb (sum enabled)}
- `POST /api/admin/compute/config` {enabled?, strategy?} → persist → {ok, config: GpuConfig}
- `GET /api/admin/storage/info` → StorageInfo: providers from StorageProvider table (snake_case), active_provider = row with active=true (default 'local_disk'), files = StorageFile rows (NO data field) desc createdAt; usage computed from active provider files.
- `POST /api/admin/storage/config` {provider_key, config?} → set active=true on that row, false others; if config provided merge into row.config JSON. → {ok, active_provider}
- `POST /api/admin/storage/connect` {provider_key, config} → merge config into row, set status='connected', lastError null → {ok, status:'connected', message}
- `POST /api/admin/storage/disconnect` {provider_key} → status='disconnected', active=false unless local_disk → {ok}
- `POST /api/admin/storage/providers` (add custom) {name, type, endpoint?, bucket_name?, access_key?, secret_key?, region?, free_tier?} → create StorageProvider id=slug(name)+suffix, status='pending' → {ok, provider_key}
- `POST /api/admin/storage/test-connection` {provider_key} → {ok, status, latency_ms, message}: local_disk/builtin → connected always ok. Cloud types: if row has non-empty config relevant fields → status 'connected' ok true; else 200 with ok:false status 'missing_config' message 'Add credentials first — use Connect'. Set lastTestAt. latency = measured random 40-400.
- `POST /api/admin/storage/files` — multipart FormData (file) OR JSON {name, mime, data_base64}; provider_key field optional (default active provider). Enforce ≤ 5MB. Store base64 in data, size real bytes. → {ok, file {id, name, size, mime}}
- `GET /api/admin/storage/files/[id]` → {ok, file {name, mime, data_base64}} for download; 404 if missing.
- `DELETE /api/admin/storage/files/[id]` → {ok}
- `POST /api/admin/storage/backup` → creates StorageFile snapshot JSON of {providers, models, routes, client_keys, config, exported_at} named `backup-<yyyy-mm-dd>-<hhmm>.json` in active provider → {ok, file_name, size_bytes, provider, message}

## 3. Gateway + Agent (Agent 2-c) — `src/app/api/v1/**`, `src/app/api/agent/**`

Gateway:
- `GET /api/v1/models` → {object:'list', data: GatewayModelEntry[]} — all enabled models (exposed_id as id, owned_by provider key, capabilities, is_free, status).
- `POST /api/v1/chat/completions` {model, messages, stream?}: NON-STREAM only (ignore stream flag; respond JSON). Pipeline:
  1. Resolve route: ModelRoute for model (public_id or '*' default chain) — explicit fallbacks list.
  2. Stage 1: if requested model exists and its provider has an enabled key and kind != 'builtin' → attempt real upstream OpenAI-format call (`{base_url}/chat/completions`, modelId, Bearer key, 15s timeout, temperature passthrough). On 2xx → log via='upstream', return response mapped to OpenAI shape (model field = REQUESTED model id — spoofing; add `_nova {upstream_model, provider, fallback:false, cached:false, spoofed:true, stage:1}`).
  3. If kind='builtin' or model is nova/* → skip upstream.
  4. Stage 2+: explicit chain models (same rules).
  5. FINAL FALLBACK — NovaFree engine: use z-ai-web-dev-sdk (BACKEND ONLY): `const zai = await ZAI.create(); const completion = await zai.chat.completions.create({ messages, thinking: {type:'disabled'} })`; content = completion.choices[0]?.message?.content. tokens_in/out: estimate (chars/4). upstream_model 'nova-engine'. provider 'NovaFree Engine'. spoofed = (requested != upstream). via='nova-engine'. If SDK throws → 502 {error} with helpful message.
  6. Always create RequestLog row (model=requested, upstreamModel, providerName, status 200/500, latencyMs, tokens, via, spoofed) .
  7. Response shape: {id: 'chatcmpl-'+random, object:'chat.completion', model: REQUESTED id, choices:[{index:0, message:{role:'assistant', content}, finish_reason:'stop'}], usage:{...}, _nova:{...}}.
  - Errors → log row status>=400 with error text, respond {error}.

Agent (autonomous, real LLM via z-ai-web-dev-sdk):
- `GET /api/agent/tools` → [{id:'web_search', name:'Web Search', description:'Search the live web for up-to-date information', icon:'Globe'}, {id:'read_url', name:'Page Reader', description:'Read and extract any web page', icon:'BookOpen'}, {id:'terminal', name:'Sandbox Terminal', description:'Run safe diagnostics in the gateway sandbox', icon:'TerminalSquare'}, {id:'gateway_stats', name:'Gateway Telemetry', description:'Inspect live gateway stats, models and routes', icon:'Activity'}, {id:'storage_scan', name:'Storage Scanner', description:'Scan configured storage providers and files', icon:'HardDrive'}]
- `POST /api/agent/tasks` {goal, max_steps?=6, model?='auto'} → create AgentTask (status 'queued', steps empty) → {id, status}. Then KICK OFF async run WITHOUT awaiting: call the exported runner `runAgentTask(taskId)` imported from `src/lib/server/agent.ts` (same file, fire-and-forget with .catch). Respond immediately.
- `src/lib/server/agent.ts` — export async function runAgentTask(taskId: string): loop:
  1. Load task; set status 'running', startedAt.
  2. Build messages: system = expert autonomous agent prompt with tool list + rules; user = goal + (numbered results so far).
  3. Ask LLM (ZAI.create() → chat.completions.create, thinking disabled) for STRICT JSON: {"action": "web_search|read_url|terminal|gateway_stats|storage_scan|finish", "title": "...", "query_or_url_or_command": "...", "reason": "..."} — parse defensively (strip code fences).
  4. Execute tool (web_search → zai.functions.invoke('web_search',{query, num:5}) map to title/url/snippet; read_url → zai.functions.invoke('page_reader',{url}) extract title + first 1500 chars text; terminal → dynamic import of safe exec from '@/lib/server/terminal-exec' (2-b's executor) with try/catch fallback to os stats string; gateway_stats → db counts summary string; storage_scan → storage summary string).
  5. Persist AgentStep per plan+execution (action, title, description=reason, detail=trimmed result ≤2000 chars, latencyMs).
  6. Repeat until action='finish' or step limit; then final LLM call: 'Write the final answer/summary in markdown, 200 words max' → task.summary; status 'completed' (or 'failed' with error on exception; 'cancelled' honored via DB check each loop).
  7. finishedAt, save. All steps wrapped in try/catch — a failing tool records an error step and continues; LLM JSON errors record a 'think' step with raw text and continue once, then fail gracefully.
- `GET /api/agent/tasks` → AgentTask[] desc createdAt (with steps included, asc by stepNumber).
- `GET /api/agent/tasks/[id]` → AgentTask (404 if missing).
- `POST /api/agent/tasks/[id]/cancel` → set status 'cancelled' (if running, loop will stop) → {ok}.

## Frontend (Agents 3-a..3-d) — src/components/nova/**

Imports available: `@/components/ui/*` (shadcn: button card badge tabs input textarea select dialog dropdown-menu switch slider progress separator scroll-area tooltip sonner table skeleton alert etc.), `lucide-react`, `framer-motion`, `recharts`, `@/lib/api`, `@/lib/types`, `@/lib/format` (fmtNum/fmtBytes/fmtMb/fmtUptime/timeAgo/fmtClock/fmtDate/maskKey/cx). Toasts: `import { toast } from 'sonner'`.
Style: page bg #080c14, panels `bg-[#0d1322]/80 border-slate-800`, accent emerald-400/500; secondary purple for RAM/boost, amber for warnings, rose for errors/dead, sky only for tiny info chips. Font mono for ids/commands. Rounded-xl, subtle hover transitions, framer-motion fade/slide on tab switch. NO indigo/blue theme colors.
Components directory `src/components/nova/`:
- 3-a owns: Dashboard.tsx (shell: tab state, data polling via api, renders Sidebar+Header+active tab+StatusFooter; exports TabKey type), Sidebar.tsx, Header.tsx, StatusFooter.tsx, tabs/OverviewTab.tsx, tabs/AnalyticsTab.tsx, tabs/LogsTab.tsx.
- 3-b owns: tabs/PlaygroundTab.tsx, tabs/AgentTab.tsx.
- 3-c owns: tabs/ProvidersTab.tsx, tabs/ModelsTab.tsx, tabs/RoutesTab.tsx, tabs/KeysTab.tsx.
- 3-d owns: tabs/StorageTab.tsx, tabs/TerminalTab.tsx.

Tab component contract — every tab receives props:
```tsx
export function XTab({ meta, stats, onRefresh }: { meta: MetaConfig | null; stats: GatewayStats | null; onRefresh: () => void; })
```
Tabs that need more (PlaygroundTab gets `models: Model[]`; ProvidersTab/ModelsTab/RoutesTab/KeysTab/StorageTab/TerminalTab fetch their own data via api + call onRefresh() after mutations; LogsTab gets `logs: RequestLog[]`; AnalyticsTab gets `meta, stats, onRefresh`). Dashboard passes exactly these.
Each tab MUST export a named export matching `export function <Name>Tab(props)`.

TabKey union: 'overview' | 'playground' | 'agent' | 'providers' | 'models' | 'routes' | 'keys' | 'storage' | 'terminal' | 'analytics' | 'logs'.

## Seed recap (already in DB)
13 providers (novafree builtin + 12 real), 35 models (real IDs, free-tier heavy), 3 fallback routes, 3 client keys, 160 realistic request logs over 48h, 8 storage providers (local_disk + novacache connected; firebase/supabase/r2/b2/github pending), 4 storage files, 2 provider sessions (novafree + openrouter connected), SystemConfig with v8_heap_mb=2048 swap_mb=2048 gpu config (7 providers incl. colab/kaggle/lightning/modal/hf_zerogpu/studiolab/novafree_gpu).
