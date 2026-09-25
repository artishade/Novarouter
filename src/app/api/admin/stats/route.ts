import { db } from '@/lib/db';
import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const r1 = (n: number): number => Math.round(n * 10) / 10;

/** GET /api/admin/stats — live gateway telemetry for the dashboard. */
export async function GET() {
  const now = Date.now();
  const dayAgo = new Date(now - 24 * 3600_000);

  const [totals, recent, activeKeys, activeModels, healthyModels, deadModels, providersCount, sessions] =
    await Promise.all([
      db.requestLog.aggregate({ _count: { _all: true }, _sum: { tokensIn: true, tokensOut: true } }),
      db.requestLog.findMany({
        where: { ts: { gte: dayAgo } },
        select: { status: true, latencyMs: true, via: true },
      }),
      db.providerKey.count({ where: { enabled: true } }),
      db.model.count({ where: { enabled: true } }),
      db.model.count({ where: { enabled: true, status: 'healthy' } }),
      db.model.count({ where: { status: 'dead' } }),
      db.provider.count(),
      db.providerSession.findMany({ select: { providerKey: true } }),
    ]);

  const sessionKeys = [...new Set(sessions.map((s) => s.providerKey))];
  const connectedProviders = sessionKeys.length
    ? await db.provider.count({ where: { key: { in: sessionKeys } } })
    : 0;

  const n24 = recent.length;
  const cacheHits = recent.filter((l) => l.via === 'cache').length;
  const errors = recent.filter((l) => l.status >= 400).length;
  const avgLatency = n24 ? recent.reduce((acc, l) => acc + l.latencyMs, 0) / n24 : 0;

  const cfgRows = await db.systemConfig.findMany({
    where: { key: { in: ['gateway_started_at', 'v8_heap_mb', 'swap_mb'] } },
  });
  const cfgNum = (k: string, dflt: number): number => {
    const raw = cfgRows.find((c) => c.key === k)?.value;
    if (raw === undefined) return dflt;
    const n = Number(raw);
    return Number.isFinite(n) ? n : dflt;
  };
  const startedAt = cfgNum('gateway_started_at', 0);
  const uptimeS = startedAt > 0 ? Math.max(0, Math.floor((now - startedAt) / 1000)) : 0;

  const mu = process.memoryUsage();
  const heapUsedMb = mu.heapUsed / 1048576;
  const heapTotalMb = mu.heapTotal / 1048576;

  return NextResponse.json({
    total_requests: totals._count._all,
    total_tokens: (totals._sum.tokensIn ?? 0) + (totals._sum.tokensOut ?? 0),
    total_tokens_in: totals._sum.tokensIn ?? 0,
    total_tokens_out: totals._sum.tokensOut ?? 0,
    active_keys: activeKeys,
    active_models: activeModels,
    dead_models: deadModels,
    healthy_models: healthyModels,
    providers_count: providersCount,
    connected_providers: connectedProviders,
    cache_hit_rate: n24 ? r1((cacheHits / n24) * 100) : 0,
    avg_latency_ms: r1(avgLatency),
    error_rate: n24 ? r1((errors / n24) * 100) : 0,
    uptime_s: uptimeS,
    memory: {
      heap_used_mb: r1(heapUsedMb),
      heap_total_mb: r1(heapTotalMb),
      rss_mb: r1(mu.rss / 1048576),
      pct: heapTotalMb > 0 ? r1((heapUsedMb / heapTotalMb) * 100) : 0,
    },
    v8: {
      heap_mb: cfgNum('v8_heap_mb', 2048),
      swap_mb: cfgNum('swap_mb', 2048),
    },
  });
}
