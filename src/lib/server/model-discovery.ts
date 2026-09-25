/**
 * Live model discovery — queries each enabled provider's public /models endpoint
 * so the gateway, dashboard and Nova Agent can see what is ACTUALLY available
 * upstream right now (not just the seeded catalogue).
 *
 * Provider wire formats handled:
 *   • OpenAI-compatible (openrouter, groq, cerebras, github-models, nvidia,
 *     mistral, together, deepseek, openai, xai, fireworks) → GET {base}/models
 *   • Gemini (generativelanguage.googleapis.com) → GET {base}/models?key=…
 *   • Anthropic → GET {base}/v1/models with x-api-key + anthropic-version
 *   • Ollama (local) → GET {base-no-/v1}/api/tags
 *   • novafree (builtin) → served from the local catalogue, no network
 *
 * Results are cached in-memory for DISCOVERY_TTL_MS. Network calls are bounded
 * by FETCH_TIMEOUT_MS and every failure is reported per-provider (never thrown).
 */
import { db } from '@/lib/db';

export interface DiscoveredModel {
  id: string; // provider-native model id (what you pass upstream)
  object: 'model';
  owned_by: string; // provider key (e.g. "groq")
  provider_id: number;
  provider_name: string;
  display_name?: string;
  context_length?: number;
  max_output?: number;
  /** USD per 1M tokens (0 = free / unknown) */
  pricing_prompt?: number;
  pricing_completion?: number;
  is_free: boolean;
}

export interface ProviderDiscoveryResult {
  provider: string;
  provider_id: number;
  ok: boolean;
  count: number;
  models: DiscoveredModel[];
  error?: string;
  duration_ms: number;
  cached: boolean;
}

const DISCOVERY_TTL_MS = 5 * 60 * 1000;
const FETCH_TIMEOUT_MS = 8000;

/** Providers whose /models catalogue is public (no auth header needed). */
const KEYLESS_PROVIDERS = new Set(['openrouter']);

/** Providers that are free to use (built-in or free-tier by design). */
const FREE_PROVIDERS = new Set(['novafree', 'ollama']);

interface CacheEntry {
  at: number;
  results: ProviderDiscoveryResult[];
}
const cache = new Map<string, CacheEntry>();

function toNumber(v: unknown): number | undefined {
  const n = typeof v === 'string' ? Number(v) : typeof v === 'number' ? v : NaN;
  return Number.isFinite(n) ? n : undefined;
}

function markFree(providerKey: string, id: string, prompt?: number, completion?: number): boolean {
  if (FREE_PROVIDERS.has(providerKey)) return true;
  if (id.endsWith(':free')) return true;
  const p = prompt ?? 0;
  const c = completion ?? 0;
  return p === 0 && c === 0;
}

/** Robust array extraction — providers return {data:[]}, {models:[]}, {body:[]} or a bare array. */
function extractList(json: unknown): Record<string, unknown>[] {
  if (Array.isArray(json)) return json as Record<string, unknown>[];
  if (json && typeof json === 'object') {
    const o = json as Record<string, unknown>;
    for (const key of ['data', 'models', 'body']) {
      if (Array.isArray(o[key])) return o[key] as Record<string, unknown>[];
    }
  }
  return [];
}

function joinUrl(base: string, path: string): string {
  return `${base.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`;
}

async function fetchJson(url: string, headers: Record<string, string> = {}): Promise<unknown> {
  const res = await fetch(url, {
    headers: { Accept: 'application/json', ...headers },
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    cache: 'no-store',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`.trim());
  return res.json();
}

/** Normalize one OpenAI-compatible entry. */
function fromOpenAICompatible(raw: Record<string, unknown>, providerKey: string, providerId: number, providerName: string): DiscoveredModel {
  const id = String(raw.id ?? raw.name ?? raw.model ?? '').trim();
  const pricing = (raw.pricing ?? {}) as Record<string, unknown>;
  const prompt = toNumber(pricing.prompt) ?? toNumber((raw.pricing as Record<string, unknown> | undefined)?.input);
  const completion = toNumber(pricing.completion) ?? toNumber((raw.pricing as Record<string, unknown> | undefined)?.output);
  return {
    id,
    object: 'model',
    owned_by: providerKey,
    provider_id: providerId,
    provider_name: providerName,
    display_name: typeof raw.name === 'string' && raw.name !== id ? raw.name : undefined,
    context_length: toNumber(raw.context_length) ?? toNumber(raw.context_window) ?? toNumber(raw.max_model_len),
    max_output: toNumber(raw.max_completion_tokens) ?? toNumber(raw.max_output_tokens),
    // OpenRouter reports $/token — normalize to $/1M tokens.
    pricing_prompt: prompt !== undefined && prompt > 0 && prompt < 0.01 ? Math.round(prompt * 1_000_000 * 1000) / 1000 : prompt,
    pricing_completion: completion !== undefined && completion > 0 && completion < 0.01 ? Math.round(completion * 1_000_000 * 1000) / 1000 : completion,
    is_free: markFree(providerKey, id, prompt, completion),
  };
}

async function discoverOpenAICompatible(
  base: string,
  key: string | null,
  providerKey: string,
  providerId: number,
  providerName: string,
  extraHeaders: Record<string, string> = {},
): Promise<DiscoveredModel[]> {
  const headers: Record<string, string> = { ...extraHeaders };
  if (key && !KEYLESS_PROVIDERS.has(providerKey)) headers.Authorization = `Bearer ${key}`;
  const json = await fetchJson(joinUrl(base, 'models'), headers);
  return extractList(json)
    .map((raw) => fromOpenAICompatible(raw, providerKey, providerId, providerName))
    .filter((m) => m.id);
}

async function discoverGemini(
  base: string,
  key: string | null,
  providerId: number,
  providerName: string,
): Promise<DiscoveredModel[]> {
  if (!key) throw new Error('no API key configured');
  const json = await fetchJson(joinUrl(base, `models?key=${encodeURIComponent(key)}&pageSize=200`));
  return extractList(json)
    .map((raw) => {
      const fullName = String(raw.name ?? ''); // "models/gemini-2.0-flash"
      const id = fullName.replace(/^models\//, '');
      const methods = Array.isArray(raw.supportedGenerationMethods) ? (raw.supportedGenerationMethods as string[]) : [];
      return {
        id,
        object: 'model' as const,
        owned_by: 'gemini',
        provider_id: providerId,
        provider_name: providerName,
        display_name: typeof raw.displayName === 'string' ? raw.displayName : undefined,
        context_length: toNumber(raw.inputTokenLimit),
        max_output: toNumber(raw.outputTokenLimit),
        is_free: methods.length === 0 || methods.includes('generateContent'),
      };
    })
    .filter((m) => m.id);
}

async function discoverAnthropic(
  base: string,
  key: string | null,
  providerId: number,
  providerName: string,
): Promise<DiscoveredModel[]> {
  if (!key) throw new Error('no API key configured');
  const json = await fetchJson(joinUrl(base, 'v1/models?limit=100'), {
    'x-api-key': key,
    'anthropic-version': '2023-06-01',
  });
  return extractList(json)
    .map((raw) => {
      const id = String(raw.id ?? '').trim();
      const ctx = toNumber(raw.context_window);
      return {
        id,
        object: 'model' as const,
        owned_by: 'anthropic',
        provider_id: providerId,
        provider_name: providerName,
        display_name: typeof raw.display_name === 'string' ? raw.display_name : undefined,
        context_length: ctx,
        is_free: markFree('anthropic', id),
      };
    })
    .filter((m) => m.id);
}

async function discoverOllama(
  base: string,
  providerId: number,
  providerName: string,
): Promise<DiscoveredModel[]> {
  // Ollama's OpenAI-compatible base is http://host:11434/v1 — tags live on the root.
  const root = base.replace(/\/v1\/?$/, '');
  const json = await fetchJson(joinUrl(root, 'api/tags'));
  return extractList(json)
    .map((raw) => {
      const id = String(raw.name ?? raw.model ?? '').trim();
      return {
        id,
        object: 'model' as const,
        owned_by: 'ollama',
        provider_id: providerId,
        provider_name: providerName,
        display_name: id,
        is_free: true,
      };
    })
    .filter((m) => m.id);
}

async function discoverNovaFree(providerId: number, providerName: string): Promise<DiscoveredModel[]> {
  const models = await db.model.findMany({
    where: { providerId, enabled: true },
    orderBy: { exposedId: 'asc' },
  });
  return models.map((m) => ({
    id: m.modelId,
    object: 'model' as const,
    owned_by: 'novafree',
    provider_id: providerId,
    provider_name: providerName,
    display_name: m.displayName || m.exposedId,
    context_length: m.contextLength,
    max_output: m.maxOutput,
    is_free: true,
  }));
}

async function discoverOne(
  provider: { id: number; key: string; name: string; kind: string; baseUrl: string },
  apiKey: string | null,
): Promise<ProviderDiscoveryResult> {
  const start = Date.now();
  try {
    let models: DiscoveredModel[];
    switch (provider.key) {
      case 'novafree':
        models = await discoverNovaFree(provider.id, provider.name);
        break;
      case 'gemini':
        models = await discoverGemini(provider.baseUrl, apiKey, provider.id, provider.name);
        break;
      case 'anthropic':
        models = await discoverAnthropic(provider.baseUrl, apiKey, provider.id, provider.name);
        break;
      case 'ollama':
        models = await discoverOllama(provider.baseUrl, provider.id, provider.name);
        break;
      default:
        models = await discoverOpenAICompatible(
          provider.baseUrl,
          apiKey,
          provider.key,
          provider.id,
          provider.name,
        );
    }
    return {
      provider: provider.key,
      provider_id: provider.id,
      ok: true,
      count: models.length,
      models,
      duration_ms: Date.now() - start,
      cached: false,
    };
  } catch (err) {
    return {
      provider: provider.key,
      provider_id: provider.id,
      ok: false,
      count: 0,
      models: [],
      error: err instanceof Error ? err.message : String(err),
      duration_ms: Date.now() - start,
      cached: false,
    };
  }
}

/**
 * Discover live models. `providerKey` filters to a single provider; results are
 * cached per filter signature for DISCOVERY_TTL_MS.
 */
export async function discoverProviderModels(providerKey?: string): Promise<ProviderDiscoveryResult[]> {
  const cacheKey = providerKey ?? 'all';
  const hit = cache.get(cacheKey);
  if (hit && Date.now() - hit.at < DISCOVERY_TTL_MS) {
    return hit.results.map((r) => ({ ...r, cached: true }));
  }

  const providers = await db.provider.findMany({
    where: { enabled: true, ...(providerKey ? { key: providerKey } : {}) },
    include: { keys: { where: { enabled: true }, select: { apiKey: true }, take: 1 } },
    orderBy: { priority: 'asc' },
  });

  const results = await Promise.all(
    providers.map((p) => {
      const needsKey = p.kind !== 'builtin' && !KEYLESS_PROVIDERS.has(p.key);
      const apiKey = p.keys[0]?.apiKey ?? null;
      if (needsKey && !apiKey) {
        return Promise.resolve({
          provider: p.key,
          provider_id: p.id,
          ok: false,
          count: 0,
          models: [],
          error: 'no enabled API key — add one in Dashboard → Providers',
          duration_ms: 0,
          cached: false,
        } satisfies ProviderDiscoveryResult);
      }
      return discoverOne(
        { id: p.id, key: p.key, name: p.name, kind: p.kind, baseUrl: p.baseUrl },
        apiKey,
      );
    }),
  );

  cache.set(cacheKey, { at: Date.now(), results });
  return results;
}

export interface DiscoverySummary {
  object: 'list';
  discover: true;
  cached: boolean;
  providers_queried: number;
  providers_ok: number;
  total_models: number;
  free_models: number;
  data: DiscoveredModel[];
  meta: Array<{
    provider: string;
    ok: boolean;
    count: number;
    error?: string;
    duration_ms: number;
    cached: boolean;
  }>;
}

/** Aggregate discovery results into the OpenAI-compatible response shape. */
export function aggregateDiscovery(results: ProviderDiscoveryResult[]): DiscoverySummary {
  const data = results.flatMap((r) => r.models);
  return {
    object: 'list',
    discover: true,
    cached: results.some((r) => r.cached),
    providers_queried: results.length,
    providers_ok: results.filter((r) => r.ok).length,
    total_models: data.length,
    free_models: data.filter((m) => m.is_free).length,
    data,
    meta: results.map(({ provider, ok, count, error, duration_ms, cached }) => ({
      provider,
      ok,
      count,
      ...(error ? { error } : {}),
      duration_ms,
      cached,
    })),
  };
}

/** Compact text report for the Nova Agent's discover_models tool (≤ ~1800 chars). */
export function summarizeDiscoveryForAgent(
  results: ProviderDiscoveryResult[],
  filterNote?: string,
): string {
  const okResults = results.filter((r) => r.ok);
  const total = okResults.reduce((a, r) => a + r.count, 0);
  const free = okResults.reduce((a, r) => a + r.models.filter((m) => m.is_free).length, 0);
  const lines: string[] = [
    `Live model discovery (/v1/models?discover=1)${filterNote ? ` [filter: ${filterNote}]` : ''}:`,
    `Providers reachable: ${okResults.length}/${results.length} · models: ${total} total · ${free} free`,
  ];
  for (const r of results) {
    if (!r.ok) {
      lines.push(`• ${r.provider}: unavailable — ${r.error ?? 'error'}`);
      continue;
    }
    const freeCount = r.models.filter((m) => m.is_free).length;
    const sample = r.models
      .slice(0, 6)
      .map((m) => m.id + (m.is_free ? ' (free)' : ''))
      .join(', ');
    const more = r.count > 6 ? ` …+${r.count - 6} more` : '';
    lines.push(`• ${r.provider}: ${r.count} models (${freeCount} free) — ${sample}${more}`);
  }
  return lines.join('\n').slice(0, 1800);
}
