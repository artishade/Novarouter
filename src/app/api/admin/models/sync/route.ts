import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

type CatalogEntry = {
  modelId: string;
  exposedId: string;
  displayName: string;
  isFree?: boolean;
  ctx: number;
  maxOut: number;
  caps?: Record<string, boolean>;
  priceIn?: number;
  priceOut?: number;
  description?: string;
};

/**
 * Curated per-provider catalogue (mirrors the seeded rows + a few fresh additions
 * so a sync actually discovers new models). Keyed by Provider.key.
 */
const CATALOG: Record<string, CatalogEntry[]> = {
  novafree: [
    { modelId: 'nova-air', exposedId: 'nova/air', displayName: 'Nova Air', isFree: true, ctx: 32768, maxOut: 8192, caps: { tools: true, vision: true, reasoning: true }, description: 'Built-in free engine. Balanced speed and quality, always available.' },
    { modelId: 'nova-mini', exposedId: 'nova/mini', displayName: 'Nova Mini', isFree: true, ctx: 16384, maxOut: 4096, description: 'Built-in free engine. Ultra-low latency for quick tasks.' },
    { modelId: 'nova-pro', exposedId: 'nova/pro', displayName: 'Nova Pro', isFree: true, ctx: 65536, maxOut: 16384, caps: { tools: true, vision: true, reasoning: true }, description: 'Built-in free engine. Deep reasoning with the largest context.' },
  ],
  openrouter: [
    { modelId: 'meta-llama/llama-3.3-70b-instruct:free', exposedId: 'openrouter/llama-3.3-70b:free', displayName: 'Llama 3.3 70B (free)', isFree: true, ctx: 65536, maxOut: 8192, caps: { tools: true }, description: 'Community favorite general model on OpenRouter free tier.' },
    { modelId: 'deepseek/deepseek-r1:free', exposedId: 'openrouter/deepseek-r1:free', displayName: 'DeepSeek R1 (free)', isFree: true, ctx: 65536, maxOut: 8192, caps: { reasoning: true }, description: 'Open reasoning model, free on OpenRouter.' },
    { modelId: 'google/gemini-2.0-flash-exp:free', exposedId: 'openrouter/gemini-2.0-flash:free', displayName: 'Gemini 2.0 Flash (free)', isFree: true, ctx: 1048576, maxOut: 8192, caps: { vision: true, tools: true }, description: 'Experimental Flash with 1M context, free via OpenRouter.' },
    { modelId: 'qwen/qwen3-235b-a22b:free', exposedId: 'openrouter/qwen3-235b:free', displayName: 'Qwen3 235B A22B (free)', isFree: true, ctx: 40960, maxOut: 8192, caps: { tools: true, reasoning: true }, description: 'MoE flagship of the Qwen3 family (free tier).' },
    { modelId: 'moonshotai/kimi-k2:free', exposedId: 'openrouter/kimi-k2:free', displayName: 'Kimi K2 (free)', isFree: true, ctx: 131072, maxOut: 8192, caps: { tools: true }, description: 'Agentic 1T-param MoE (free tier).' },
    { modelId: 'openai/gpt-oss-20b:free', exposedId: 'openrouter/gpt-oss-20b:free', displayName: 'GPT-OSS 20B (free)', isFree: true, ctx: 32768, maxOut: 8192, caps: { reasoning: true }, description: 'OpenAI open-weight model, free on OpenRouter.' },
    { modelId: 'deepseek/deepseek-chat-v3.1:free', exposedId: 'openrouter/deepseek-chat-v3.1:free', displayName: 'DeepSeek V3.1 Chat (free)', isFree: true, ctx: 163840, maxOut: 8192, caps: { tools: true }, description: 'DeepSeek V3.1 hybrid chat model on OpenRouter free tier.' },
    { modelId: 'mistralai/mistral-small-3.2-24b-instruct:free', exposedId: 'openrouter/mistral-small-3.2:free', displayName: 'Mistral Small 3.2 (free)', isFree: true, ctx: 131072, maxOut: 8192, caps: { tools: true, vision: true }, description: 'Mistral Small 3.2 24B, free via OpenRouter.' },
    { modelId: 'z-ai/glm-4.5-air:free', exposedId: 'openrouter/glm-4.5-air:free', displayName: 'GLM 4.5 Air (free)', isFree: true, ctx: 131072, maxOut: 8192, caps: { tools: true, reasoning: true }, description: 'Z.ai GLM-4.5-Air agentic MoE (free tier).' },
  ],
  groq: [
    { modelId: 'llama-3.3-70b-versatile', exposedId: 'groq/llama-3.3-70b-versatile', displayName: 'Llama 3.3 70B Versatile', isFree: true, ctx: 131072, maxOut: 32768, caps: { tools: true }, description: 'Groq LPU hosted — blisteringly fast general model.' },
    { modelId: 'llama-3.1-8b-instant', exposedId: 'groq/llama-3.1-8b-instant', displayName: 'Llama 3.1 8B Instant', isFree: true, ctx: 131072, maxOut: 8192, description: 'The fastest healthy route in the catalogue.' },
    { modelId: 'moonshotai/kimi-k2-instruct', exposedId: 'groq/kimi-k2-instruct', displayName: 'Kimi K2 Instruct', isFree: true, ctx: 131072, maxOut: 16384, caps: { tools: true }, description: 'Kimi K2 served on Groq LPU.' },
    { modelId: 'qwen/qwen3-32b', exposedId: 'groq/qwen3-32b', displayName: 'Qwen3 32B', isFree: true, ctx: 131072, maxOut: 8192, caps: { reasoning: true }, description: 'Qwen3 32B on the Groq free tier.' },
    { modelId: 'openai/gpt-oss-120b', exposedId: 'groq/gpt-oss-120b', displayName: 'GPT-OSS 120B', isFree: true, ctx: 131072, maxOut: 32768, caps: { reasoning: true, tools: true }, description: 'OpenAI open-weight 120B on Groq LPU.' },
  ],
  cerebras: [
    { modelId: 'llama-3.3-70b', exposedId: 'cerebras/llama-3.3-70b', displayName: 'Llama 3.3 70B (Cerebras)', isFree: true, ctx: 128000, maxOut: 8192, description: 'Wafer-scale inference — 2,000+ tokens/sec.' },
    { modelId: 'qwen-3-235b-a22b-instruct-2507', exposedId: 'cerebras/qwen3-235b-instruct', displayName: 'Qwen3 235B Instruct (Cerebras)', isFree: true, ctx: 128000, maxOut: 8192, caps: { tools: true }, description: 'Qwen3 flagship on Cerebras free tier.' },
    { modelId: 'llama-4-scout-17b-16e-instruct', exposedId: 'cerebras/llama-4-scout', displayName: 'Llama 4 Scout (Cerebras)', isFree: true, ctx: 131072, maxOut: 8192, caps: { vision: true, tools: true }, description: 'Llama 4 Scout served on Cerebras wafer-scale cluster.' },
  ],
  gemini: [
    { modelId: 'gemini-2.5-flash', exposedId: 'gemini/gemini-2.5-flash', displayName: 'Gemini 2.5 Flash', isFree: true, ctx: 1048576, maxOut: 65536, caps: { tools: true, vision: true, reasoning: true }, description: 'Google workhorse — free tier 15 RPM / 1,500 RPD.' },
    { modelId: 'gemini-2.5-pro', exposedId: 'gemini/gemini-2.5-pro', displayName: 'Gemini 2.5 Pro', isFree: true, ctx: 1048576, maxOut: 65536, caps: { tools: true, vision: true, reasoning: true }, description: 'Thinking model with 1M context, free quota available.' },
    { modelId: 'gemini-2.0-flash-lite', exposedId: 'gemini/gemini-2.0-flash-lite', displayName: 'Gemini 2.0 Flash Lite', isFree: true, ctx: 1048576, maxOut: 8192, caps: { vision: true }, description: 'Cheapest Gemini, free tier 30 RPM.' },
    { modelId: 'gemini-2.5-flash-lite', exposedId: 'gemini/gemini-2.5-flash-lite', displayName: 'Gemini 2.5 Flash Lite', isFree: true, ctx: 1048576, maxOut: 65536, caps: { vision: true }, description: 'Fastest Gemini 2.5 variant, free tier 30 RPM.' },
  ],
  'github-models': [
    { modelId: 'openai/gpt-4o-mini', exposedId: 'github/gpt-4o-mini', displayName: 'GPT-4o mini (GitHub)', isFree: true, ctx: 128000, maxOut: 16384, caps: { tools: true, vision: true }, description: 'Free with a GitHub PAT — generous rate limits.' },
    { modelId: 'meta/Llama-4-Scout-17B-16E-Instruct', exposedId: 'github/llama-4-scout', displayName: 'Llama 4 Scout (GitHub)', isFree: true, ctx: 131072, maxOut: 8192, caps: { vision: true, tools: true }, description: 'Llama 4 Scout via GitHub Models.' },
    { modelId: 'microsoft/Phi-4', exposedId: 'github/phi-4', displayName: 'Phi-4 (GitHub)', isFree: true, ctx: 16384, maxOut: 4096, caps: { reasoning: true }, description: 'Microsoft small reasoning model, free.' },
    { modelId: 'openai/gpt-4.1-mini', exposedId: 'github/gpt-4.1-mini', displayName: 'GPT-4.1 mini (GitHub)', isFree: true, ctx: 128000, maxOut: 16384, caps: { tools: true, vision: true }, description: 'GPT-4.1 mini with a free GitHub PAT.' },
  ],
  nvidia: [
    { modelId: 'deepseek-ai/deepseek-r1', exposedId: 'nvidia/deepseek-r1', displayName: 'DeepSeek R1 (NIM)', isFree: true, ctx: 163840, maxOut: 8192, caps: { reasoning: true }, description: 'Hosted NIM with free starter credits.' },
    { modelId: 'meta/llama-3.3-70b-instruct', exposedId: 'nvidia/llama-3.3-70b', displayName: 'Llama 3.3 70B (NIM)', isFree: true, ctx: 131072, maxOut: 8192, caps: { tools: true }, description: 'Llama 3.3 hosted on NVIDIA infrastructure.' },
    { modelId: 'qwen/qwen2.5-coder-32b-instruct', exposedId: 'nvidia/qwen2.5-coder-32b', displayName: 'Qwen2.5 Coder 32B (NIM)', isFree: true, ctx: 32768, maxOut: 8192, caps: { tools: true }, description: 'Code-specialized Qwen hosted on NVIDIA NIM.' },
  ],
  mistral: [
    { modelId: 'mistral-small-latest', exposedId: 'mistral/mistral-small-latest', displayName: 'Mistral Small 3.2', isFree: true, ctx: 131072, maxOut: 8192, caps: { tools: true, vision: true }, description: 'Free experiment plan flagship.' },
    { modelId: 'open-mistral-nemo', exposedId: 'mistral/open-mistral-nemo', displayName: 'Mistral Nemo', isFree: true, ctx: 131072, maxOut: 4096, description: 'Compact 12B model, free tier.' },
    { modelId: 'magistral-small-latest', exposedId: 'mistral/magistral-small-latest', displayName: 'Magistral Small', isFree: true, ctx: 40000, maxOut: 8192, caps: { reasoning: true }, description: 'Mistral reasoning model on the free experiment plan.' },
  ],
  together: [
    { modelId: 'meta-llama/Llama-4-Scout-17B-16E-Instruct', exposedId: 'together/llama-4-scout', displayName: 'Llama 4 Scout (Together)', ctx: 1048576, maxOut: 8192, caps: { vision: true, tools: true }, priceIn: 0.18, priceOut: 0.59 },
    { modelId: 'deepseek-ai/DeepSeek-V3', exposedId: 'together/deepseek-v3', displayName: 'DeepSeek V3 (Together)', ctx: 131072, maxOut: 8192, caps: { tools: true }, priceIn: 1.25, priceOut: 1.25 },
  ],
  deepseek: [
    { modelId: 'deepseek-chat', exposedId: 'deepseek/deepseek-chat', displayName: 'DeepSeek V3 Chat', ctx: 65536, maxOut: 8192, caps: { tools: true }, priceIn: 0.27, priceOut: 1.1 },
    { modelId: 'deepseek-reasoner', exposedId: 'deepseek/deepseek-reasoner', displayName: 'DeepSeek R1 Reasoner', ctx: 65536, maxOut: 8192, caps: { reasoning: true }, priceIn: 0.55, priceOut: 2.19 },
  ],
  anthropic: [
    { modelId: 'claude-sonnet-4-20250514', exposedId: 'anthropic/claude-sonnet-4', displayName: 'Claude Sonnet 4', ctx: 200000, maxOut: 64000, caps: { tools: true, vision: true, reasoning: true }, priceIn: 3, priceOut: 15 },
    { modelId: 'claude-3-5-haiku-20241022', exposedId: 'anthropic/claude-3-5-haiku', displayName: 'Claude 3.5 Haiku', ctx: 200000, maxOut: 8192, caps: { tools: true }, priceIn: 0.8, priceOut: 4 },
  ],
  openai: [
    { modelId: 'gpt-4o-mini', exposedId: 'openai/gpt-4o-mini', displayName: 'GPT-4o mini', ctx: 128000, maxOut: 16384, caps: { tools: true, vision: true }, priceIn: 0.15, priceOut: 0.6 },
    { modelId: 'gpt-4o', exposedId: 'openai/gpt-4o', displayName: 'GPT-4o', ctx: 128000, maxOut: 16384, caps: { tools: true, vision: true }, priceIn: 2.5, priceOut: 10 },
  ],
  ollama: [
    { modelId: 'llama3.2', exposedId: 'ollama/llama3.2', displayName: 'Llama 3.2 (local)', isFree: true, ctx: 131072, maxOut: 4096, description: 'Local runtime — start Ollama with `ollama serve`.' },
    { modelId: 'qwen2.5-coder', exposedId: 'ollama/qwen2.5-coder', displayName: 'Qwen2.5 Coder (local)', isFree: true, ctx: 32768, maxOut: 4096, description: 'Local runtime — pull with `ollama pull qwen2.5-coder`.' },
    { modelId: 'llama3.1', exposedId: 'ollama/llama3.1', displayName: 'Llama 3.1 (local)', isFree: true, ctx: 131072, maxOut: 4096, description: 'Local runtime — pull with `ollama pull llama3.1`.' },
  ],
  xai: [
    { modelId: 'grok-4-fast', exposedId: 'xai/grok-4-fast', displayName: 'Grok 4 Fast', ctx: 2000000, maxOut: 32768, caps: { tools: true }, description: 'xAI Grok 4 Fast — generous free credits on the data-sharing plan.' },
    { modelId: 'grok-3-mini', exposedId: 'xai/grok-3-mini', displayName: 'Grok 3 Mini', ctx: 131072, maxOut: 16384, caps: { reasoning: true }, description: 'Lightweight Grok 3 with reasoning on xAI free credits.' },
  ],
  fireworks: [
    { modelId: 'accounts/fireworks/models/llama4-scout-instruct', exposedId: 'fireworks/llama-4-scout', displayName: 'Llama 4 Scout (Fireworks)', ctx: 131072, maxOut: 8192, caps: { vision: true, tools: true }, description: 'Serverless Llama 4 Scout on Fireworks free tier.' },
    { modelId: 'accounts/fireworks/models/deepseek-v3', exposedId: 'fireworks/deepseek-v3', displayName: 'DeepSeek V3 (Fireworks)', ctx: 131072, maxOut: 8192, caps: { tools: true }, description: 'DeepSeek V3 served serverlessly by Fireworks.' },
  ],
};

/**
 * POST /api/admin/models/sync?provider_id= — catalogue refresh.
 * Ensures the curated models exist for every provider with ≥1 key (or builtin).
 * No network calls; new_added counts freshly created rows, synced counts examined entries.
 */
export async function POST(req: NextRequest) {
  const sp = new URL(req.url).searchParams;
  const providerIdRaw = Number(sp.get('provider_id'));
  const providerId = sp.get('provider_id') !== null && Number.isInteger(providerIdRaw) ? providerIdRaw : undefined;

  const providers = await db.provider.findMany({
    where: providerId !== undefined ? { id: providerId } : undefined,
    include: { keys: { select: { id: true } } },
  });

  let synced = 0;
  let newAdded = 0;

  for (const provider of providers) {
    const entries = CATALOG[provider.key];
    if (!entries) continue;
    if (provider.kind !== 'builtin' && provider.keys.length === 0) continue;

    const existing = await db.model.findMany({
      where: { providerId: provider.id },
      select: { modelId: true },
    });
    const have = new Set(existing.map((m) => m.modelId));

    for (const entry of entries) {
      synced += 1;
      if (have.has(entry.modelId)) continue;
      await db.model.create({
        data: {
          providerId: provider.id,
          modelId: entry.modelId,
          exposedId: entry.exposedId,
          displayName: entry.displayName,
          isFree: entry.isFree ?? false,
          status: 'unknown',
          contextLength: entry.ctx,
          maxOutput: entry.maxOut,
          capabilities: JSON.stringify({ tools: false, vision: false, reasoning: false, ...entry.caps }),
          priceIn: entry.priceIn ?? 0,
          priceOut: entry.priceOut ?? 0,
          description: entry.description ?? null,
        },
      });
      have.add(entry.modelId);
      newAdded += 1;
    }
  }

  return NextResponse.json({ ok: true, synced, new_added: newAdded });
}
