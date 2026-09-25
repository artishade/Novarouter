/**
 * Live model catalogue sync — shared by:
 *   • POST /api/admin/models/sync            (manual "Sync models" button)
 *   • POST /api/admin/providers              (auto-sync right after a provider is added)
 *
 * Queries each provider's REAL upstream /models endpoint (no static catalogue)
 * and upserts what is actually available: real ids, context windows, pricing,
 * free flags. Failures are reported per-provider and never fabricated.
 */
import { db } from '@/lib/db';
import { discoverProviderFresh } from '@/lib/server/model-discovery';

/** Hard cap per provider — protects the DB from pathological upstream catalogues. */
const MAX_MODELS_PER_PROVIDER = 500;

/**
 * Built-in NovaFree engine models. This is the REAL engine surface (served by
 * z-ai-web-dev-sdk inside this process) — not a placeholder catalogue.
 */
const NOVA_ENGINE_MODELS = [
  { modelId: 'nova-air', exposedId: 'nova/air', displayName: 'Nova Air', ctx: 32768, maxOut: 8192, caps: { tools: true, vision: true, reasoning: true }, description: 'Built-in free engine. Balanced speed and quality, always available.' },
  { modelId: 'nova-mini', exposedId: 'nova/mini', displayName: 'Nova Mini', ctx: 16384, maxOut: 4096, caps: { tools: false, vision: false, reasoning: false }, description: 'Built-in free engine. Ultra-low latency for quick tasks.' },
  { modelId: 'nova-pro', exposedId: 'nova/pro', displayName: 'Nova Pro', ctx: 65536, maxOut: 16384, caps: { tools: true, vision: true, reasoning: true }, description: 'Built-in free engine. Deep reasoning with the largest context.' },
];

export interface ProviderSyncReport {
  provider: string;
  provider_id: number;
  ok: boolean;
  discovered: number;
  created: number;
  updated: number;
  error?: string;
  duration_ms: number;
}

function defaultCapabilities(caps?: Record<string, boolean>): string {
  return JSON.stringify({ tools: false, vision: false, reasoning: false, ...caps });
}

/** Sync ONE provider from its live upstream catalogue (real network calls). */
export async function syncProvider(
  provider: { id: number; key: string; name: string; kind: string; baseUrl: string; prefix: string },
): Promise<ProviderSyncReport> {
  const started = Date.now();
  const base = { provider: provider.key, provider_id: provider.id };

  // ── Built-in engine: catalogue is the engine's real surface, no network ──
  if (provider.kind === 'builtin') {
    let created = 0;
    let updated = 0;
    for (const entry of NOVA_ENGINE_MODELS) {
      const existing = await db.model.findUnique({
        where: { providerId_modelId: { providerId: provider.id, modelId: entry.modelId } },
        select: { id: true },
      });
      if (existing) {
        await db.model.update({
          where: { id: existing.id },
          data: {
            displayName: entry.displayName,
            contextLength: entry.ctx,
            maxOutput: entry.maxOut,
            capabilities: defaultCapabilities(entry.caps),
            description: entry.description,
          },
        });
        updated += 1;
      } else {
        await db.model.create({
          data: {
            providerId: provider.id,
            modelId: entry.modelId,
            exposedId: entry.exposedId,
            displayName: entry.displayName,
            isFree: true,
            contextLength: entry.ctx,
            maxOutput: entry.maxOut,
            capabilities: defaultCapabilities(entry.caps),
            description: entry.description,
          },
        });
        created += 1;
      }
    }
    return { ...base, ok: true, discovered: NOVA_ENGINE_MODELS.length, created, updated, duration_ms: Date.now() - started };
  }

  // ── Everything else: live discovery against the real upstream /models endpoint ──
  const result = await discoverProviderFresh(provider.id);
  if (!result.ok) {
    return {
      ...base,
      ok: false,
      discovered: 0,
      created: 0,
      updated: 0,
      error: result.error ?? 'discovery failed',
      duration_ms: Date.now() - started,
    };
  }

  const discovered = result.models.slice(0, MAX_MODELS_PER_PROVIDER);
  const existing = await db.model.findMany({
    where: { providerId: provider.id },
    select: { modelId: true },
  });
  const have = new Set(existing.map((m) => m.modelId));

  let created = 0;
  let updated = 0;

  for (const m of discovered) {
    const isKnown = have.has(m.id);
    const data = {
      displayName: m.display_name || m.id,
      isFree: m.is_free,
      contextLength: m.context_length ?? 0,
      maxOutput: m.max_output ?? 0,
      priceIn: m.pricing_prompt ?? 0,
      priceOut: m.pricing_completion ?? 0,
    };
    if (isKnown) {
      await db.model.update({
        where: { providerId_modelId: { providerId: provider.id, modelId: m.id } },
        data,
      });
      updated += 1;
    } else {
      await db.model.create({
        data: {
          providerId: provider.id,
          modelId: m.id,
          exposedId: `${provider.prefix}${m.id}`,
          status: 'unknown',
          capabilities: defaultCapabilities(),
          description: null,
          ...data,
        },
      });
      have.add(m.id);
      created += 1;
    }
  }

  return {
    ...base,
    ok: true,
    discovered: result.count,
    created,
    updated,
    duration_ms: Date.now() - started,
  };
}

/** Sync many providers sequentially; returns per-provider reports. */
export async function syncProviders(
  where: { id?: number },
): Promise<ProviderSyncReport[]> {
  const providers = await db.provider.findMany({
    where: { enabled: true, ...where },
    select: { id: true, key: true, name: true, kind: true, baseUrl: true, prefix: true },
    orderBy: [{ priority: 'asc' }, { id: 'asc' }],
  });

  const reports: ProviderSyncReport[] = [];
  for (const provider of providers) {
    reports.push(await syncProvider(provider));
  }
  return reports;
}
