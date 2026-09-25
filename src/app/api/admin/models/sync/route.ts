import { NextRequest, NextResponse } from 'next/server';
import { syncProviders } from '@/lib/server/model-sync';

export const dynamic = 'force-dynamic';
export const maxDuration = 120;

/**
 * POST /api/admin/models/sync?provider_id= — REAL live catalogue sync.
 *
 * Queries every (or one) enabled provider's actual /models endpoint — OpenAI-
 * compatible, Gemini, Anthropic and Ollama wire formats — and upserts what the
 * provider REALLY exposes right now: real model ids, context windows, pricing,
 * free flags. Nothing is inserted from a static list; providers that fail
 * (bad key, network down) are reported per-provider and remain untouched.
 */
export async function POST(req: NextRequest) {
  const sp = new URL(req.url).searchParams;
  const providerIdRaw = Number(sp.get('provider_id'));
  const providerId = sp.get('provider_id') !== null && Number.isInteger(providerIdRaw) ? providerIdRaw : undefined;

  const reports = await syncProviders(providerId !== undefined ? { id: providerId } : {});

  const synced = reports.reduce((a, r) => a + r.discovered, 0);
  const newAdded = reports.reduce((a, r) => a + r.created, 0);
  const updated = reports.reduce((a, r) => a + r.updated, 0);
  const failed = reports.filter((r) => !r.ok);

  return NextResponse.json({
    ok: failed.length === 0 || newAdded + updated > 0,
    synced,
    new_added: newAdded,
    updated,
    providers: reports,
    ...(failed.length > 0
      ? { errors: failed.map((r) => `${r.provider}: ${r.error ?? 'failed'}`) }
      : {}),
  });
}
