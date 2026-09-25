import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';
import type { RoutePreview } from '@/lib/types';

export const dynamic = 'force-dynamic';

function parseFallbacks(raw: string): string[] {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed.filter((v): v is string => typeof v === 'string');
    return [];
  } catch {
    return [];
  }
}

/**
 * GET /api/admin/routes/preview?model= — resolve the 3-stage fallback pipeline
 * for a requested model id without sending a request.
 */
export async function GET(req: NextRequest) {
  const sp = new URL(req.url).searchParams;
  const model = (sp.get('model') ?? '*').trim() || '*';

  const [models, route, starRoute, cfgSpoof] = await Promise.all([
    db.model.findMany({
      where: { enabled: true },
      include: { provider: { select: { key: true } } },
    }),
    db.modelRoute.findUnique({ where: { publicId: model } }),
    db.modelRoute.findUnique({ where: { publicId: '*' } }),
    db.systemConfig.findUnique({ where: { key: 'spoof_model' } }),
  ]);

  /* Stage 1 — direct providers that serve this model (by exposed or upstream id). */
  const directMatches = models.filter((m) => m.exposedId === model || m.modelId === model);
  const stage1 = [...new Set(directMatches.map((m) => m.exposedId))];

  /* Stage 2 — explicit fallback chain (matched route, else the '*' default chain). */
  const effectiveRoute = route ?? starRoute;
  const explicitChain = effectiveRoute ? parseFallbacks(effectiveRoute.fallbacks) : [];
  const autoAllowed = effectiveRoute ? effectiveRoute.auto : true;

  /* Stage 3 — auto stand-ins: healthy, enabled, free models not already listed. */
  const listed = new Set<string>([model, ...stage1, ...explicitChain]);
  const autoTargets = autoAllowed
    ? models
        .filter((m) => m.status === 'healthy' && m.enabled && m.isFree && !listed.has(m.exposedId))
        .sort((a, b) => a.provider.key.localeCompare(b.provider.key) || a.exposedId.localeCompare(b.exposedId))
        .slice(0, 3)
        .map((m) => m.exposedId)
    : [];

  const preview: RoutePreview = {
    model,
    direct: stage1.length > 0,
    explicit_chain: explicitChain,
    auto_allowed: autoAllowed,
    auto_targets: autoTargets,
    stages: [
      { label: 'Stage 1 — Direct providers', models: stage1 },
      { label: 'Stage 2 — Fallback chain', models: explicitChain },
      { label: 'Stage 3 — Auto stand-ins', models: autoTargets },
    ],
    spoof_model: cfgSpoof ? cfgSpoof.value === '1' || cfgSpoof.value === 'true' : true,
  };

  return NextResponse.json(preview);
}
