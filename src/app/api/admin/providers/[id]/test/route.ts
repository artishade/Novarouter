import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';
export const maxDuration = 60;

const FETCH_TIMEOUT_MS = 8000;
const ENGINE_PING_TIMEOUT_MS = 20_000;

interface ProbeResult {
  ok: boolean;
  http_status: number;
  latency_ms: number;
  models_found: number;
  detail: string;
}

function joinUrl(base: string, path: string): string {
  return `${base.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`;
}

function extractList(json: unknown): unknown[] {
  if (Array.isArray(json)) return json;
  if (json && typeof json === 'object') {
    const o = json as Record<string, unknown>;
    for (const key of ['data', 'models', 'body']) {
      if (Array.isArray(o[key])) return o[key];
    }
  }
  return [];
}

/** Real GET against one upstream key, using the provider's wire format. */
async function probeUpstream(
  providerKey: string,
  kind: string,
  baseUrl: string,
  apiKey: string | null,
): Promise<ProbeResult> {
  const started = Date.now();
  let url = joinUrl(baseUrl, 'models');
  const headers: Record<string, string> = { Accept: 'application/json' };

  if (kind === 'gemini') {
    if (!apiKey) throw new Error('missing key');
    url = joinUrl(baseUrl, `models?key=${encodeURIComponent(apiKey)}&pageSize=50`);
  } else if (kind === 'anthropic') {
    if (!apiKey) throw new Error('missing key');
    url = joinUrl(baseUrl, 'v1/models?limit=50');
    headers['x-api-key'] = apiKey;
    headers['anthropic-version'] = '2023-06-01';
  } else if (providerKey === 'openrouter' && apiKey) {
    // OpenRouter's /models catalogue is public and accepts any Bearer — /auth/key
    // actually validates the key against the account.
    url = joinUrl(baseUrl, 'auth/key');
    headers.Authorization = `Bearer ${apiKey}`;
  } else if (apiKey) {
    headers.Authorization = `Bearer ${apiKey}`;
  }

  try {
    const res = await fetch(url, {
      headers,
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      cache: 'no-store',
    });
    const latency = Date.now() - started;
    if (res.ok) {
      let count = 0;
      let detail = `Upstream catalogue reachable — HTTP ${res.status} in ${latency}ms`;
      try {
        const json = await res.json();
        count = extractList(json).length;
        if (url.endsWith('/auth/key')) {
          detail = `Key authenticated with OpenRouter — HTTP ${res.status} in ${latency}ms`;
        }
      } catch {
        count = 0;
      }
      return {
        ok: true,
        http_status: res.status,
        latency_ms: latency,
        models_found: count,
        detail,
      };
    }
    const detail =
      res.status === 401 || res.status === 403
        ? `auth rejected (HTTP ${res.status})`
        : `upstream returned HTTP ${res.status}`;
    return {
      ok: false,
      http_status: res.status,
      latency_ms: latency,
      models_found: 0,
      detail,
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return {
      ok: false,
      http_status: 0,
      latency_ms: Date.now() - started,
      models_found: 0,
      detail: /abort|timeout/i.test(msg) ? `timed out after ${FETCH_TIMEOUT_MS}ms` : msg.slice(0, 160),
    };
  }
}

/** Real end-to-end ping of the built-in NovaFree engine (z-ai-web-dev-sdk). */
async function probeNovaEngine(): Promise<ProbeResult> {
  const started = Date.now();
  try {
    const ZAI = (await import('z-ai-web-dev-sdk')).default;
    const zai = await ZAI.create();
    const completionPromise = zai.chat.completions.create({
      messages: [{ role: 'user', content: 'Reply with the single word: pong' }],
      thinking: { type: 'disabled' },
    }) as Promise<{ choices?: Array<{ message?: { content?: unknown } }> }>;
    const timeout = new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error(`engine ping timed out after ${ENGINE_PING_TIMEOUT_MS}ms`)), ENGINE_PING_TIMEOUT_MS),
    );
    const completion = await Promise.race([completionPromise, timeout]);
    const content = completion?.choices?.[0]?.message?.content;
    const latency = Date.now() - started;
    return {
      ok: typeof content === 'string' && content.length > 0,
      http_status: 200,
      latency_ms: latency,
      models_found: 0,
      detail:
        typeof content === 'string' && content.length > 0
          ? `Engine answered a live test completion in ${latency}ms`
          : 'Engine returned an empty completion',
    };
  } catch (err) {
    const msg = (err instanceof Error ? err.message : String(err)).slice(0, 200);
    return {
      ok: false,
      http_status: 0,
      latency_ms: Date.now() - started,
      models_found: 0,
      detail: `Built-in engine unavailable: ${msg}`,
    };
  }
}

/**
 * POST /api/admin/providers/[id]/test — REAL provider connectivity test.
 *
 * • builtin   → live test completion through the NovaFree engine (z-ai-web-dev-sdk)
 * • external  → live GET {base_url}/models per provider wire format, probing up to
 *               3 enabled keys; ok when at least one key authenticates upstream.
 * No simulated latencies, no fabricated statuses — every number comes from the wire.
 */
export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const providerId = Number(id);
  if (!Number.isInteger(providerId)) {
    return NextResponse.json({ error: 'Invalid provider id' }, { status: 400 });
  }

  const provider = await db.provider.findUnique({
    where: { id: providerId },
    include: { keys: { where: { enabled: true }, orderBy: { weight: 'desc' } } },
  });
  if (!provider) {
    return NextResponse.json({ error: 'Provider not found' }, { status: 404 });
  }

  if (provider.kind === 'builtin') {
    const engine = await probeNovaEngine();
    const models = await db.model.count({ where: { providerId: provider.id, enabled: true } });
    return NextResponse.json({
      ok: engine.ok,
      provider: provider.key,
      key_count: 0,
      latency_ms: engine.latency_ms,
      status: engine.ok ? 'ok' : 'engine_error',
      models_found: models,
      message: engine.ok
        ? `${engine.detail} · ${models} engine model(s) enabled`
        : engine.detail,
    });
  }

  if (!provider.baseUrl || provider.baseUrl.startsWith('internal://')) {
    return NextResponse.json({
      ok: false,
      provider: provider.key,
      key_count: provider.keys.length,
      latency_ms: 0,
      status: 'misconfigured',
      models_found: 0,
      message: 'Provider has no upstream base URL configured — edit it before testing',
    });
  }

  if (provider.keys.length === 0 && provider.key !== 'openrouter' && provider.key !== 'ollama') {
    return NextResponse.json({
      ok: false,
      provider: provider.key,
      key_count: 0,
      latency_ms: 0,
      status: 'no_key',
      models_found: 0,
      message: 'No enabled API key — add at least one key, then test again',
    });
  }

  // Probe up to 3 enabled keys (sequential, bounded) — any success means the provider works.
  // Keyless providers (openrouter public catalogue, local ollama) are probed without a key.
  let candidates: Array<{ apiKey: string } | null> = provider.keys.slice(0, 3);
  if (candidates.length === 0) candidates = [null];
  const probes: ProbeResult[] = [];
  for (const k of candidates) {
    probes.push(await probeUpstream(provider.key, provider.kind, provider.baseUrl, k?.apiKey ?? null));
    if (probes[probes.length - 1].ok) break; // first working key is enough
  }
  const best = probes.find((p) => p.ok) ?? probes[probes.length - 1] ?? {
    ok: false, http_status: 0, latency_ms: 0, models_found: 0,
    detail: 'no enabled key to probe',
  };

  const status = best.ok
    ? 'ok'
    : best.http_status === 401 || best.http_status === 403
      ? 'auth_error'
      : best.http_status > 0
        ? 'http_error'
        : 'network_error';

  const failedKeys = probes.filter((p) => !p.ok).length;
  const message = best.ok
    ? provider.keys.length === 0
      ? `${best.detail} · ${best.models_found} models visible upstream (public catalogue — no key required)`
      : `${best.detail} · ${best.models_found} models visible upstream`
    : `${best.detail}${failedKeys > 1 ? ` (${failedKeys} key(s) probed)` : ''}`;

  return NextResponse.json({
    ok: best.ok,
    provider: provider.key,
    key_count: provider.keys.length,
    latency_ms: best.latency_ms,
    status,
    models_found: best.models_found,
    message,
  });
}
