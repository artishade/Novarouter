/**
 * NovaRouter realistic seed.
 * Real provider presets, real free-tier model IDs, believable telemetry.
 * Run: bun prisma/seed.ts
 */
import { PrismaClient } from '@prisma/client';

const db = new PrismaClient();

const now = Date.now();
const h = (n: number) => new Date(now - n * 3600_000);
const m = (n: number) => new Date(now - n * 60_000);

async function main() {
  console.log('⚡ Seeding NovaRouter…');

  // Wipe (idempotent seed)
  await db.agentStep.deleteMany();
  await db.agentTask.deleteMany();
  await db.requestLog.deleteMany();
  await db.terminalCommand.deleteMany();
  await db.storageFile.deleteMany();
  await db.storageProvider.deleteMany();
  await db.providerSession.deleteMany();
  await db.clientKey.deleteMany();
  await db.modelRoute.deleteMany();
  await db.model.deleteMany();
  await db.providerKey.deleteMany();
  await db.provider.deleteMany();
  await db.systemConfig.deleteMany();

  /* ---------------- Providers ---------------- */
  const novafree = await db.provider.create({
    data: {
      key: 'novafree', name: 'NovaFree Engine', kind: 'builtin',
      baseUrl: 'internal://nova-engine', prefix: 'nova/', priority: 1,
      color: '#10b981', freeTier: 'Built-in — every model here is free, no key required',
      docsUrl: 'https://github.com/artishade/Novarouter',
    },
  });
  const openrouter = await db.provider.create({
    data: {
      key: 'openrouter', name: 'OpenRouter', kind: 'openai',
      baseUrl: 'https://openrouter.ai/api/v1', prefix: 'openrouter/', priority: 10,
      color: '#8b5cf6', freeTier: 'Dozens of :free models, 200 req/day without credits',
      docsUrl: 'https://openrouter.ai/docs', authUrl: 'https://openrouter.ai/settings/keys',
      requiresAuth: true,
    },
  });
  const groq = await db.provider.create({
    data: {
      key: 'groq', name: 'Groq Cloud', kind: 'openai',
      baseUrl: 'https://api.groq.com/openai/v1', prefix: 'groq/', priority: 10,
      color: '#f97316', freeTier: 'Free tier: 30 req/min, 14,400 req/day',
      docsUrl: 'https://console.groq.com/docs', authUrl: 'https://console.groq.com/keys',
      requiresAuth: true,
    },
  });
  const cerebras = await db.provider.create({
    data: {
      key: 'cerebras', name: 'Cerebras Inference', kind: 'openai',
      baseUrl: 'https://api.cerebras.ai/v1', prefix: 'cerebras/', priority: 20,
      color: '#f59e0b', freeTier: 'Free tier: ~1M tokens/day at 2,000+ tok/s',
      docsUrl: 'https://inference-docs.cerebras.ai', authUrl: 'https://cloud.cerebras.ai',
      requiresAuth: true,
    },
  });
  const gemini = await db.provider.create({
    data: {
      key: 'gemini', name: 'Google AI Studio', kind: 'gemini',
      baseUrl: 'https://generativelanguage.googleapis.com/v1beta', prefix: 'gemini/', priority: 15,
      color: '#22c55e', freeTier: 'Gemini API free tier: 15 RPM, 1,500 req/day (Flash)',
      docsUrl: 'https://ai.google.dev/docs', authUrl: 'https://aistudio.google.com/app/apikey',
      requiresAuth: true,
    },
  });
  const mistral = await db.provider.create({
    data: {
      key: 'mistral', name: 'Mistral La Plateforme', kind: 'openai',
      baseUrl: 'https://api.mistral.ai/v1', prefix: 'mistral/', priority: 30,
      color: '#fb923c', freeTier: 'Free experiment plan: 1 req/s, 500k tokens/min',
      docsUrl: 'https://docs.mistral.ai', authUrl: 'https://console.mistral.ai/api-keys',
      requiresAuth: true,
    },
  });
  const githubModels = await db.provider.create({
    data: {
      key: 'github-models', name: 'GitHub Models', kind: 'openai',
      baseUrl: 'https://models.inference.ai.azure.com', prefix: 'github/', priority: 25,
      color: '#e2e8f0', freeTier: 'Free with any GitHub account — GPT-4o, Llama, Phi, DeepSeek',
      docsUrl: 'https://docs.github.com/en/github-models', authUrl: 'https://github.com/settings/tokens',
      requiresAuth: true,
    },
  });
  const nvidia = await db.provider.create({
    data: {
      key: 'nvidia', name: 'NVIDIA NIM', kind: 'openai',
      baseUrl: 'https://integrate.api.nvidia.com/v1', prefix: 'nvidia/', priority: 35,
      color: '#76b900', freeTier: '1,000 free credits on signup for hosted NIM endpoints',
      docsUrl: 'https://docs.api.nvidia.com', authUrl: 'https://build.nvidia.com',
      requiresAuth: true,
    },
  });
  const deepseek = await db.provider.create({
    data: {
      key: 'deepseek', name: 'DeepSeek API', kind: 'openai',
      baseUrl: 'https://api.deepseek.com/v1', prefix: 'deepseek/', priority: 40,
      color: '#64748b', docsUrl: 'https://api-docs.deepseek.com', authUrl: 'https://platform.deepseek.com/api_keys',
      requiresAuth: true,
    },
  });
  const anthropic = await db.provider.create({
    data: {
      key: 'anthropic', name: 'Anthropic', kind: 'anthropic',
      baseUrl: 'https://api.anthropic.com', prefix: 'anthropic/', priority: 90,
      color: '#d97757', docsUrl: 'https://docs.anthropic.com', authUrl: 'https://console.anthropic.com/settings/keys',
      requiresAuth: true,
    },
  });
  const openai = await db.provider.create({
    data: {
      key: 'openai', name: 'OpenAI', kind: 'openai',
      baseUrl: 'https://api.openai.com/v1', prefix: 'openai/', priority: 90,
      color: '#0ea36e', docsUrl: 'https://platform.openai.com/docs', authUrl: 'https://platform.openai.com/api-keys',
      requiresAuth: true,
    },
  });
  const together = await db.provider.create({
    data: {
      key: 'together', name: 'Together AI', kind: 'openai',
      baseUrl: 'https://api.together.xyz/v1', prefix: 'together/', priority: 45,
      color: '#0f766e', freeTier: '$1 free credit on signup', docsUrl: 'https://docs.together.ai',
      authUrl: 'https://api.together.ai/settings/api-keys', requiresAuth: true,
    },
  });
  const ollama = await db.provider.create({
    data: {
      key: 'ollama', name: 'Ollama (local)', kind: 'openai',
      baseUrl: 'http://localhost:11434/v1', prefix: 'ollama/', priority: 80,
      color: '#94a3b8', freeTier: '100% free — runs on your machine',
      docsUrl: 'https://github.com/ollama/ollama',
    },
  });

  /* ---------------- Provider keys (demo placeholders — never real secrets) ---------------- */
  await db.providerKey.create({
    data: {
      providerId: openrouter.id, label: 'or-main', apiKey: 'sk-or-v1-SEED-DEMO-PLACEHOLDER-0000',
      weight: 1, reqCount: 1284, errCount: 9, lastUsedAt: m(4), lastError: null,
    },
  });
  await db.providerKey.create({
    data: {
      providerId: groq.id, label: 'groq-main', apiKey: 'gsk_SEED-DEMO-PLACEHOLDER-0000',
      weight: 1, reqCount: 962, errCount: 14, lastUsedAt: m(11), lastError: null,
    },
  });
  await db.providerKey.create({
    data: {
      providerId: openrouter.id, label: 'or-backup', apiKey: 'sk-or-v1-SEED-DEMO-PLACEHOLDER-1111',
      weight: 2, reqCount: 147, errCount: 31, lastUsedAt: h(5),
      cooldownUntil: new Date(now + 26 * 60_000), lastError: '429 Rate limit exceeded (free tier daily cap)',
    },
  });

  /* ---------------- Models ---------------- */
  type SeedModel = {
    providerId: number; modelId: string; exposedId: string; displayName: string;
    isFree?: boolean; status?: string; httpStatus?: number; latencyMs?: number;
    ctx: number; maxOut: number; caps: Record<string, boolean>;
    priceIn?: number; priceOut?: number; description?: string; checkedHrsAgo?: number;
  };
  const caps = (o: Record<string, boolean>) => JSON.stringify({ tools: false, vision: false, reasoning: false, ...o });

  const models: SeedModel[] = [
    // NovaFree engine (always healthy — internal)
    { providerId: novafree.id, modelId: 'nova-air', exposedId: 'nova/air', displayName: 'Nova Air', isFree: true, status: 'healthy', httpStatus: 200, latencyMs: 620, ctx: 32768, maxOut: 8192, caps: { tools: true, vision: true, reasoning: true }, description: 'Built-in free engine. Balanced speed and quality, always available.', checkedHrsAgo: 1 },
    { providerId: novafree.id, modelId: 'nova-mini', exposedId: 'nova/mini', displayName: 'Nova Mini', isFree: true, status: 'healthy', httpStatus: 200, latencyMs: 340, ctx: 16384, maxOut: 4096, caps: {}, description: 'Built-in free engine. Ultra-low latency for quick tasks.', checkedHrsAgo: 1 },
    { providerId: novafree.id, modelId: 'nova-pro', exposedId: 'nova/pro', displayName: 'Nova Pro', isFree: true, status: 'healthy', httpStatus: 200, latencyMs: 1150, ctx: 65536, maxOut: 16384, caps: { tools: true, vision: true, reasoning: true }, description: 'Built-in free engine. Deep reasoning with the largest context.', checkedHrsAgo: 1 },

    // OpenRouter free tier (real :free model IDs)
    { providerId: openrouter.id, modelId: 'meta-llama/llama-3.3-70b-instruct:free', exposedId: 'openrouter/llama-3.3-70b:free', displayName: 'Llama 3.3 70B (free)', isFree: true, status: 'healthy', httpStatus: 200, latencyMs: 890, ctx: 65536, maxOut: 8192, caps: { tools: true }, description: 'Community favorite general model on OpenRouter free tier.', checkedHrsAgo: 2 },
    { providerId: openrouter.id, modelId: 'deepseek/deepseek-r1:free', exposedId: 'openrouter/deepseek-r1:free', displayName: 'DeepSeek R1 (free)', isFree: true, status: 'healthy', httpStatus: 200, latencyMs: 1650, ctx: 65536, maxOut: 8192, caps: { reasoning: true }, description: 'Open reasoning model, free on OpenRouter.', checkedHrsAgo: 2 },
    { providerId: openrouter.id, modelId: 'google/gemini-2.0-flash-exp:free', exposedId: 'openrouter/gemini-2.0-flash:free', displayName: 'Gemini 2.0 Flash (free)', isFree: true, status: 'healthy', httpStatus: 200, latencyMs: 540, ctx: 1048576, maxOut: 8192, caps: { vision: true, tools: true }, description: 'Experimental Flash with 1M context, free via OpenRouter.', checkedHrsAgo: 3 },
    { providerId: openrouter.id, modelId: 'qwen/qwen3-235b-a22b:free', exposedId: 'openrouter/qwen3-235b:free', displayName: 'Qwen3 235B A22B (free)', isFree: true, status: 'unknown', ctx: 40960, maxOut: 8192, caps: { tools: true, reasoning: true }, description: 'MoE flagship of the Qwen3 family (free tier).', },
    { providerId: openrouter.id, modelId: 'moonshotai/kimi-k2:free', exposedId: 'openrouter/kimi-k2:free', displayName: 'Kimi K2 (free)', isFree: true, status: 'cooling', httpStatus: 429, latencyMs: 0, ctx: 131072, maxOut: 8192, caps: { tools: true }, description: 'Agentic 1T-param MoE (free tier). Cooling after 429.', },
    { providerId: openrouter.id, modelId: 'openai/gpt-oss-20b:free', exposedId: 'openrouter/gpt-oss-20b:free', displayName: 'GPT-OSS 20B (free)', isFree: true, status: 'healthy', httpStatus: 200, latencyMs: 720, ctx: 32768, maxOut: 8192, caps: { reasoning: true }, description: 'OpenAI open-weight model, free on OpenRouter.', checkedHrsAgo: 6 },

    // Groq (free tier, real IDs)
    { providerId: groq.id, modelId: 'llama-3.3-70b-versatile', exposedId: 'groq/llama-3.3-70b-versatile', displayName: 'Llama 3.3 70B Versatile', isFree: true, status: 'healthy', httpStatus: 200, latencyMs: 210, ctx: 131072, maxOut: 32768, caps: { tools: true }, description: 'Groq LPU hosted — blisteringly fast general model.', checkedHrsAgo: 1 },
    { providerId: groq.id, modelId: 'llama-3.1-8b-instant', exposedId: 'groq/llama-3.1-8b-instant', displayName: 'Llama 3.1 8B Instant', isFree: true, status: 'healthy', httpStatus: 200, latencyMs: 95, ctx: 131072, maxOut: 8192, caps: {}, description: 'The fastest healthy route in the catalogue.', checkedHrsAgo: 1 },
    { providerId: groq.id, modelId: 'moonshotai/kimi-k2-instruct', exposedId: 'groq/kimi-k2-instruct', displayName: 'Kimi K2 Instruct', isFree: true, status: 'healthy', httpStatus: 200, latencyMs: 380, ctx: 131072, maxOut: 16384, caps: { tools: true }, description: 'Kimi K2 served on Groq LPU.', checkedHrsAgo: 4 },
    { providerId: groq.id, modelId: 'qwen/qwen3-32b', exposedId: 'groq/qwen3-32b', displayName: 'Qwen3 32B', isFree: true, status: 'dead', httpStatus: 404, latencyMs: 0, ctx: 131072, maxOut: 8192, caps: { reasoning: true }, description: 'Deprecated upstream — retired from Groq catalogue.', },

    // Cerebras
    { providerId: cerebras.id, modelId: 'llama-3.3-70b', exposedId: 'cerebras/llama-3.3-70b', displayName: 'Llama 3.3 70B (Cerebras)', isFree: true, status: 'healthy', httpStatus: 200, latencyMs: 150, ctx: 128000, maxOut: 8192, caps: {}, description: 'Wafer-scale inference — 2,000+ tokens/sec.', checkedHrsAgo: 5 },
    { providerId: cerebras.id, modelId: 'qwen-3-235b-a22b-instruct-2507', exposedId: 'cerebras/qwen3-235b-instruct', displayName: 'Qwen3 235B Instruct (Cerebras)', isFree: true, status: 'unknown', ctx: 128000, maxOut: 8192, caps: { tools: true }, description: 'Qwen3 flagship on Cerebras free tier.' },

    // Google AI Studio
    { providerId: gemini.id, modelId: 'gemini-2.5-flash', exposedId: 'gemini/gemini-2.5-flash', displayName: 'Gemini 2.5 Flash', isFree: true, status: 'healthy', httpStatus: 200, latencyMs: 460, ctx: 1048576, maxOut: 65536, caps: { tools: true, vision: true, reasoning: true }, description: 'Google workhorse — free tier 15 RPM / 1,500 RPD.', checkedHrsAgo: 2 },
    { providerId: gemini.id, modelId: 'gemini-2.5-pro', exposedId: 'gemini/gemini-2.5-pro', displayName: 'Gemini 2.5 Pro', isFree: true, status: 'healthy', httpStatus: 200, latencyMs: 1300, ctx: 1048576, maxOut: 65536, caps: { tools: true, vision: true, reasoning: true }, description: 'Thinking model with 1M context, free quota available.', checkedHrsAgo: 3 },
    { providerId: gemini.id, modelId: 'gemini-2.0-flash-lite', exposedId: 'gemini/gemini-2.0-flash-lite', displayName: 'Gemini 2.0 Flash Lite', isFree: true, status: 'unknown', ctx: 1048576, maxOut: 8192, caps: { vision: true }, description: 'Cheapest Gemini, free tier 30 RPM.' },

    // GitHub Models
    { providerId: githubModels.id, modelId: 'openai/gpt-4o-mini', exposedId: 'github/gpt-4o-mini', displayName: 'GPT-4o mini (GitHub)', isFree: true, status: 'healthy', httpStatus: 200, latencyMs: 610, ctx: 128000, maxOut: 16384, caps: { tools: true, vision: true }, description: 'Free with a GitHub PAT — generous rate limits.', checkedHrsAgo: 7 },
    { providerId: githubModels.id, modelId: 'meta/Llama-4-Scout-17B-16E-Instruct', exposedId: 'github/llama-4-scout', displayName: 'Llama 4 Scout (GitHub)', isFree: true, status: 'unknown', ctx: 131072, maxOut: 8192, caps: { vision: true, tools: true }, description: 'Llama 4 Scout via GitHub Models.' },
    { providerId: githubModels.id, modelId: 'microsoft/Phi-4', exposedId: 'github/phi-4', displayName: 'Phi-4 (GitHub)', isFree: true, status: 'unknown', ctx: 16384, maxOut: 4096, caps: { reasoning: true }, description: 'Microsoft small reasoning model, free.' },

    // NVIDIA NIM
    { providerId: nvidia.id, modelId: 'deepseek-ai/deepseek-r1', exposedId: 'nvidia/deepseek-r1', displayName: 'DeepSeek R1 (NIM)', isFree: true, status: 'unknown', ctx: 163840, maxOut: 8192, caps: { reasoning: true }, description: 'Hosted NIM with free starter credits.' },
    { providerId: nvidia.id, modelId: 'meta/llama-3.3-70b-instruct', exposedId: 'nvidia/llama-3.3-70b', displayName: 'Llama 3.3 70B (NIM)', isFree: true, status: 'unknown', ctx: 131072, maxOut: 8192, caps: { tools: true }, description: 'Llama 3.3 hosted on NVIDIA infrastructure.' },

    // Mistral
    { providerId: mistral.id, modelId: 'mistral-small-latest', exposedId: 'mistral/mistral-small-latest', displayName: 'Mistral Small 3.2', isFree: true, status: 'healthy', httpStatus: 200, latencyMs: 520, ctx: 131072, maxOut: 8192, caps: { tools: true, vision: true }, description: 'Free experiment plan flagship.', checkedHrsAgo: 8 },
    { providerId: mistral.id, modelId: 'open-mistral-nemo', exposedId: 'mistral/open-mistral-nemo', displayName: 'Mistral Nemo', isFree: true, status: 'unknown', ctx: 131072, maxOut: 4096, caps: {}, description: 'Compact 12B model, free tier.' },

    // Together
    { providerId: together.id, modelId: 'meta-llama/Llama-4-Scout-17B-16E-Instruct', exposedId: 'together/llama-4-scout', displayName: 'Llama 4 Scout (Together)', isFree: false, status: 'unknown', ctx: 1048576, maxOut: 8192, caps: { vision: true, tools: true }, priceIn: 0.18, priceOut: 0.59 },
    { providerId: together.id, modelId: 'deepseek-ai/DeepSeek-V3', exposedId: 'together/deepseek-v3', displayName: 'DeepSeek V3 (Together)', isFree: false, status: 'unknown', ctx: 131072, maxOut: 8192, caps: { tools: true }, priceIn: 1.25, priceOut: 1.25 },

    // DeepSeek direct (paid)
    { providerId: deepseek.id, modelId: 'deepseek-chat', exposedId: 'deepseek/deepseek-chat', displayName: 'DeepSeek V3 Chat', isFree: false, status: 'unknown', ctx: 65536, maxOut: 8192, caps: { tools: true }, priceIn: 0.27, priceOut: 1.1 },
    { providerId: deepseek.id, modelId: 'deepseek-reasoner', exposedId: 'deepseek/deepseek-reasoner', displayName: 'DeepSeek R1 Reasoner', isFree: false, status: 'unknown', ctx: 65536, maxOut: 8192, caps: { reasoning: true }, priceIn: 0.55, priceOut: 2.19 },

    // Anthropic (paid)
    { providerId: anthropic.id, modelId: 'claude-sonnet-4-20250514', exposedId: 'anthropic/claude-sonnet-4', displayName: 'Claude Sonnet 4', isFree: false, status: 'unknown', ctx: 200000, maxOut: 64000, caps: { tools: true, vision: true, reasoning: true }, priceIn: 3, priceOut: 15 },
    { providerId: anthropic.id, modelId: 'claude-3-5-haiku-20241022', exposedId: 'anthropic/claude-3-5-haiku', displayName: 'Claude 3.5 Haiku', isFree: false, status: 'unknown', ctx: 200000, maxOut: 8192, caps: { tools: true }, priceIn: 0.8, priceOut: 4 },

    // OpenAI (paid)
    { providerId: openai.id, modelId: 'gpt-4o-mini', exposedId: 'openai/gpt-4o-mini', displayName: 'GPT-4o mini', isFree: false, status: 'unknown', ctx: 128000, maxOut: 16384, caps: { tools: true, vision: true }, priceIn: 0.15, priceOut: 0.6 },
    { providerId: openai.id, modelId: 'gpt-4o', exposedId: 'openai/gpt-4o', displayName: 'GPT-4o', isFree: false, status: 'unknown', ctx: 128000, maxOut: 16384, caps: { tools: true, vision: true }, priceIn: 2.5, priceOut: 10 },

    // Ollama (local)
    { providerId: ollama.id, modelId: 'llama3.2', exposedId: 'ollama/llama3.2', displayName: 'Llama 3.2 (local)', isFree: true, status: 'dead', httpStatus: 0, latencyMs: 0, ctx: 131072, maxOut: 4096, caps: {}, description: 'Local runtime offline — start Ollama with `ollama serve`.' },
    { providerId: ollama.id, modelId: 'qwen2.5-coder', exposedId: 'ollama/qwen2.5-coder', displayName: 'Qwen2.5 Coder (local)', isFree: true, status: 'dead', httpStatus: 0, latencyMs: 0, ctx: 32768, maxOut: 4096, caps: {}, description: 'Local runtime offline — pull with `ollama pull qwen2.5-coder`.' },
  ];

  for (const mo of models) {
    await db.model.create({
      data: {
        providerId: mo.providerId, modelId: mo.modelId, exposedId: mo.exposedId,
        displayName: mo.displayName, isFree: !!mo.isFree, status: mo.status || 'unknown',
        httpStatus: mo.httpStatus || 0, latencyMs: mo.latencyMs || 0,
        checkedAt: mo.checkedHrsAgo ? h(mo.checkedHrsAgo) : null,
        contextLength: mo.ctx, maxOutput: mo.maxOut, capabilities: caps(mo.caps),
        priceIn: mo.priceIn || 0, priceOut: mo.priceOut || 0, description: mo.description || null,
      },
    });
  }

  /* ---------------- Fallback routes ---------------- */
  await db.modelRoute.createMany({
    data: [
      { publicId: 'claude-opus-4-6', fallbacks: JSON.stringify(['anthropic/claude-sonnet-4', 'openrouter/llama-3.3-70b:free', 'nova/air']), auto: true, note: 'Opus outage workaround (Sep 2025)' },
      { publicId: 'gpt-5-codex', fallbacks: JSON.stringify(['openai/gpt-4o', 'groq/llama-3.3-70b-versatile']), auto: true, note: 'Codex CLI default chain' },
      { publicId: '*', fallbacks: JSON.stringify(['nova/air']), auto: true, note: 'Default chain — appended after every model' },
    ],
  });

  /* ---------------- Client keys ---------------- */
  await db.clientKey.createMany({
    data: [
      { name: 'Claude Desktop', token: 'nova-sk-3f9a2c7e81b64d05a2f1c8d4e6b3a9f7', rpmLimit: 60, tpdLimit: 0, tokensIn: 184320, tokensOut: 92160, reqCount: 431, lastUsedAt: m(6) },
      { name: 'Codex CLI', token: 'nova-sk-8d21e4b97c354fa0b6e8d2c1a4f79b36', rpmLimit: 120, tpdLimit: 500000, tokensIn: 402880, tokensOut: 143360, reqCount: 688, lastUsedAt: m(19) },
      { name: 'Local Dev Sandbox', token: 'nova-sk-5c7b1d9e3a264f80c1d3e5a7b9f24e18', rpmLimit: 30, tpdLimit: 100000, tokensIn: 51200, tokensOut: 20480, reqCount: 97, lastUsedAt: h(9) },
    ],
  });

  /* ---------------- Request logs (48h, realistic mix) ---------------- */
  const logSeeds: { model: string; upstream: string; provider: string; endpoint?: string; status: number; latency: number; tin: number; tout: number; via: string; spoof?: boolean; err?: string; client?: string; hrsAgo: number }[] = [];
  const hot = [
    ['nova/air', 'nova-air', 'NovaFree Engine'],
    ['openrouter/llama-3.3-70b:free', 'meta-llama/llama-3.3-70b-instruct:free', 'OpenRouter'],
    ['groq/llama-3.3-70b-versatile', 'llama-3.3-70b-versatile', 'Groq Cloud'],
    ['gemini/gemini-2.5-flash', 'gemini-2.5-flash', 'Google AI Studio'],
    ['nova/pro', 'nova-pro', 'NovaFree Engine'],
    ['openrouter/deepseek-r1:free', 'deepseek/deepseek-r1:free', 'OpenRouter'],
    ['groq/llama-3.1-8b-instant', 'llama-3.1-8b-instant', 'Groq Cloud'],
    ['github/gpt-4o-mini', 'openai/gpt-4o-mini', 'GitHub Models'],
  ];
  const clients = ['Claude Desktop', 'Codex CLI', 'Local Dev Sandbox'];
  let seed = 42;
  const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  for (let i = 0; i < 160; i++) {
    const pick = hot[Math.floor(rnd() * hot.length)];
    const r = rnd();
    let status = 200, via = 'upstream', err = '', spoof = false, latency = Math.round(300 + rnd() * 1800);
    if (pick[0].startsWith('nova/')) { via = 'nova-engine'; latency = Math.round(350 + rnd() * 900); }
    if (r > 0.93) { status = 429; via = 'upstream'; err = '429 rate_limit_exceeded — retried on fallback'; latency = Math.round(180 + rnd() * 300); }
    else if (r > 0.90) { status = 200; via = 'fallback'; spoof = rnd() > 0.4; }
    else if (r > 0.885) { status = 200; via = 'cache'; latency = Math.round(8 + rnd() * 40); }
    else if (r > 0.875) { status = 502; via = 'upstream'; err = '502 upstream_unavailable after 2 attempts'; }
    logSeeds.push({
      model: pick[0], upstream: pick[1], provider: pick[2],
      status, latency, tin: Math.round(180 + rnd() * 3200), tout: Math.round(60 + rnd() * 1400),
      via, spoof, err, client: clients[Math.floor(rnd() * clients.length)],
      hrsAgo: Math.round(rnd() * 470) / 10,
    });
  }
  for (const L of logSeeds) {
    await db.requestLog.create({
      data: {
        ts: h(L.hrsAgo), clientName: L.client!, providerName: L.provider,
        model: L.model, upstreamModel: L.upstream, endpoint: '/v1/chat/completions',
        status: L.status, latencyMs: L.latency, tokensIn: L.tin, tokensOut: L.tout,
        error: L.err || '', via: L.via, spoofed: !!L.spoof,
      },
    });
  }

  /* ---------------- Terminal history ---------------- */
  await db.terminalCommand.createMany({
    data: [
      { command: 'free -m', output: '               total        used        free      shared  buff/cache   available\nMem:           3936         2214         512          142        1210        1587\nSwap:          2048           0        2048', exitCode: 0, durationMs: 14, cwd: '/workspace', createdAt: h(26) },
      { command: 'nova status', output: '⚡ NovaRouter gateway — 12 providers · 38 models · 4 healthy routes\n   uptime: 26h 14m · avg latency: 742ms · cache hit rate: 11.4%', exitCode: 0, durationMs: 8, cwd: '/workspace', createdAt: h(26) },
    ],
  });

  /* ---------------- Storage providers ---------------- */
  await db.storageProvider.createMany({
    data: [
      { id: 'local_disk', name: 'NovaVault (Local Disk)', type: 'local', status: 'connected', isBuiltin: true, active: true, freeTier: 'Unlimited — stored on the gateway host', usageMb: 42.6, quotaMb: 0, docsUrl: null },
      { id: 'novacache', name: 'NovaCache Cloud', type: 'builtin_cloud', status: 'connected', isBuiltin: true, active: false, freeTier: 'Built-in free — 5 GB edge cache per workspace', usageMb: 318.2, quotaMb: 5120, region: 'global/edge' },
      { id: 'firebase', name: 'Firebase Storage', type: 'firebase', status: 'pending', isBuiltin: false, freeTier: 'Spark plan: 5 GB stored, 1 GB/day download', quotaMb: 5120, usageMb: 0, docsUrl: 'https://firebase.google.com/docs/storage', authUrl: 'https://console.firebase.google.com/', region: 'auto' },
      { id: 'supabase', name: 'Supabase Storage', type: 'supabase', status: 'pending', isBuiltin: false, freeTier: 'Free plan: 1 GB storage, 2 GB bandwidth', quotaMb: 1024, usageMb: 0, docsUrl: 'https://supabase.com/docs/guides/storage', authUrl: 'https://supabase.com/dashboard', region: 'auto' },
      { id: 'cloudflare_r2', name: 'Cloudflare R2', type: 's3', status: 'pending', isBuiltin: false, freeTier: '10 GB storage, zero egress fees', quotaMb: 10240, usageMb: 0, docsUrl: 'https://developers.cloudflare.com/r2/', authUrl: 'https://dash.cloudflare.com/', region: 'auto' },
      { id: 'backblaze_b2', name: 'Backblaze B2', type: 's3', status: 'pending', isBuiltin: false, freeTier: '10 GB storage, 1 GB/day download free', quotaMb: 10240, usageMb: 0, docsUrl: 'https://www.backblaze.com/b2/docs/', authUrl: 'https://secure.backblaze.com/b2_buckets.htm', region: 'auto' },
      { id: 'github', name: 'GitHub Repository', type: 'github', status: 'pending', isBuiltin: false, freeTier: 'Unlimited public repos, 1 GB file cap', quotaMb: 0, usageMb: 0, docsUrl: 'https://docs.github.com/en/repositories', authUrl: 'https://github.com/settings/tokens', region: 'global' },
      { id: 'webdav', name: 'WebDAV (any host)', type: 'webdav', status: 'disconnected', isBuiltin: false, freeTier: 'Bring your own WebDAV server', quotaMb: 0, usageMb: 0 },
    ],
  });

  await db.storageFile.createMany({
    data: [
      { name: 'gateway-config.json', path: '/', size: 4812, mime: 'application/json', providerKey: 'local_disk', data: Buffer.from(JSON.stringify({ gateway: 'novarouter', version: '2.0', spoof_model: true, auto_fallback: true }, null, 2)).toString('base64') },
      { name: 'routes-export.csv', path: '/', size: 11240, mime: 'text/csv', providerKey: 'local_disk', data: Buffer.from('public_id,fallbacks,auto\nclaude-opus-4-6,anthropic/claude-sonnet-4|nova/air,true\n* ,nova/air,true\n').toString('base64') },
      { name: 'backup-2025-09-24.json', path: '/backups', size: 65530, mime: 'application/json', providerKey: 'novacache', data: Buffer.from('{"snapshot":"2025-09-24T02:00:00Z","providers":12,"models":38,"keys":3,"client_keys":3}').toString('base64') },
      { name: 'agent-trace-demo.md', path: '/notes', size: 3021, mime: 'text/markdown', providerKey: 'local_disk', data: Buffer.from('# Agent trace\n\n1. PLAN researched task\n2. web_search found 8 sources\n3. finish summarized answer\n').toString('base64') },
    ],
  });

  /* ---------------- Provider sessions ---------------- */
  await db.providerSession.createMany({
    data: [
      { providerKey: 'novafree', username: 'nova-engine', displayName: 'NovaFree Engine', plan: 'built-in', tokenMask: 'internal', status: 'connected' },
      { providerKey: 'openrouter', username: 'artishade', displayName: 'Artishade', plan: 'free', tokenMask: 'sk-or-v1-…b77', status: 'connected' },
    ],
  });

  /* ---------------- System config ---------------- */
  const gpuProviders = [
    { id: 'colab', name: 'Google Colab', gpu: 'Tesla T4', vram_gb: 16, free_tier: 'Free tier — 12h sessions, ~2-3/day quota', status: 'available', connected: false, hours_free: '12h/session', region: 'us', note: 'Sign in with Google to attach a T4 runtime' },
    { id: 'kaggle', name: 'Kaggle Notebooks', gpu: 'T4 x2 / P100', vram_gb: 32, free_tier: '30 GPU-hours every week, free', status: 'available', connected: false, hours_free: '30h/week', region: 'us', note: 'Best sustained free GPU quota' },
    { id: 'lightning', name: 'Lightning AI', gpu: 'T4', vram_gb: 16, free_tier: '15 free monthly credits (~22h T4)', status: 'available', connected: false, hours_free: '~22h/mo', region: 'us' },
    { id: 'modal', name: 'Modal Labs', gpu: 'A10G / A100', vram_gb: 24, free_tier: '$30/month free compute credits', status: 'available', connected: false, hours_free: '$30/mo', region: 'us' },
    { id: 'hf_zerogpu', name: 'Hugging Face ZeroGPU', gpu: 'H200 (shared)', vram_gb: 80, free_tier: 'Free dynamic quota for Spaces', status: 'available', connected: false, hours_free: 'dynamic', region: 'eu' },
    { id: 'studiolab', name: 'SageMaker Studio Lab', gpu: 'T4', vram_gb: 16, free_tier: '4h GPU sessions, free AWS account', status: 'available', connected: false, hours_free: '4h/session', region: 'us' },
    { id: 'novafree_gpu', name: 'NovaFree GPU Pool', gpu: 'RTX A4000 (burst)', vram_gb: 8, free_tier: 'Built-in burst pool for gateway workloads', status: 'available', connected: true, hours_free: 'fair-use', region: 'edge', note: 'Used automatically for embedding/batch jobs' },
  ];
  await db.systemConfig.createMany({
    data: [
      { key: 'v8_heap_mb', value: '2048' },
      { key: 'swap_mb', value: '2048' },
      { key: 'boost_applied_at', value: String(now - 26 * 3600_000) },
      { key: 'admin_token', value: 'nova-admin-token' },
      { key: 'auto_fallback', value: '1' },
      { key: 'spoof_model', value: '1' },
      { key: 'hedge_delay', value: '2' },
      { key: 'cache_ttl', value: '600' },
      { key: 'gpu_enabled', value: '1' },
      { key: 'gpu_strategy', value: 'quota_aware' },
      { key: 'gpu_providers', value: JSON.stringify(gpuProviders) },
      { key: 'gateway_started_at', value: String(now - 26 * 3600_000 - 840_000) },
    ],
  });

  const counts = {
    providers: await db.provider.count(),
    models: await db.model.count(),
    logs: await db.requestLog.count(),
  };
  console.log(`✅ Seed complete — ${counts.providers} providers, ${counts.models} models, ${counts.logs} request logs`);
}

main()
  .catch((e) => { console.error(e); process.exit(1); })
  .finally(() => db.$disconnect());
