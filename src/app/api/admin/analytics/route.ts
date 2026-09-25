import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/** Asia/Dhaka is a fixed UTC+6 offset (no DST) — safe to apply manually. */
const DHAKA_OFFSET_MS = 6 * 3600_000;

const pad2 = (n: number): string => String(n).padStart(2, '0');

/** Floor a timestamp to the start of its Asia/Dhaka hour (returned as UTC epoch ms). */
function hourKey(ts: number): number {
  return Math.floor((ts + DHAKA_OFFSET_MS) / 3600_000) * 3600_000 - DHAKA_OFFSET_MS;
}

/** Floor a timestamp to the start of its Asia/Dhaka day (returned as UTC epoch ms). */
function dayKey(ts: number): number {
  return Math.floor((ts + DHAKA_OFFSET_MS) / 86400_000) * 86400_000 - DHAKA_OFFSET_MS;
}

function hourLabel(keyTs: number): string {
  const d = new Date(keyTs + DHAKA_OFFSET_MS);
  return `${pad2(d.getUTCHours())}:00`;
}

function dayLabel(keyTs: number): string {
  const d = new Date(keyTs + DHAKA_OFFSET_MS);
  return `${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`;
}

/** Nearest-rank percentile on an ascending-sorted array. */
function nearestRank(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  const idx = Math.max(0, Math.ceil((p / 100) * sorted.length) - 1);
  return sorted[Math.min(idx, sorted.length - 1)];
}

const r1 = (n: number): number => Math.round(n * 10) / 10;

/** GET /api/admin/analytics?hours=24|168|720&group_by=hour|day */
export async function GET(req: NextRequest) {
  const sp = new URL(req.url).searchParams;

  const hoursRaw = Number(sp.get('hours'));
  const hours = Number.isFinite(hoursRaw) && hoursRaw >= 1 ? Math.min(Math.floor(hoursRaw), 8760) : 24;

  const gbRaw = sp.get('group_by');
  const groupBy = gbRaw === 'hour' || gbRaw === 'day' ? gbRaw : hours > 48 ? 'day' : 'hour';

  const now = Date.now();
  const from = now - hours * 3600_000;

  const logs = await db.requestLog.findMany({
    where: { ts: { gte: new Date(from) } },
    select: { ts: true, model: true, providerName: true, clientName: true, status: true, latencyMs: true, tokensIn: true, tokensOut: true, via: true, spoofed: true },
  });

  /* ---- Zero-filled timeseries buckets (ascending) ---- */
  const step = groupBy === 'hour' ? 3600_000 : 86400_000;
  const keyOf = groupBy === 'hour' ? hourKey : dayKey;
  const labelOf = groupBy === 'hour' ? hourLabel : dayLabel;

  const buckets = new Map<number, { reqs: number; tokens: number; errors: number }>();
  for (let k = keyOf(from); k <= keyOf(now); k += step) {
    buckets.set(k, { reqs: 0, tokens: 0, errors: 0 });
  }
  for (const log of logs) {
    const k = keyOf(log.ts.getTime());
    const b = buckets.get(k);
    if (!b) continue;
    b.reqs += 1;
    b.tokens += log.tokensIn + log.tokensOut;
    if (log.status >= 400) b.errors += 1;
  }
  const timeseries = [...buckets.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([k, b]) => ({ bucket: labelOf(k), reqs: b.reqs, tokens: b.tokens, errors: b.errors }));

  /* ---- Top groupings ---- */
  const modelAgg = new Map<string, { reqs: number; tokens: number }>();
  const providerAgg = new Map<string, number>();
  const clientAgg = new Map<string, number>();
  for (const log of logs) {
    const m = modelAgg.get(log.model) ?? { reqs: 0, tokens: 0 };
    m.reqs += 1;
    m.tokens += log.tokensIn + log.tokensOut;
    modelAgg.set(log.model, m);
    providerAgg.set(log.providerName, (providerAgg.get(log.providerName) ?? 0) + 1);
    clientAgg.set(log.clientName, (clientAgg.get(log.clientName) ?? 0) + 1);
  }
  const topModels = [...modelAgg.entries()]
    .sort((a, b) => b[1].reqs - a[1].reqs || b[1].tokens - a[1].tokens)
    .slice(0, 8)
    .map(([model, v]) => ({ model, reqs: v.reqs, tokens: v.tokens }));
  const topProviders = [...providerAgg.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([provider, reqs]) => ({ provider, reqs }));
  const topClients = [...clientAgg.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([client, reqs]) => ({ client, reqs }));

  /* ---- Summary ---- */
  const totalTokens = logs.reduce((acc, l) => acc + l.tokensIn + l.tokensOut, 0);
  const tokensIn = logs.reduce((acc, l) => acc + l.tokensIn, 0);
  const tokensOut = logs.reduce((acc, l) => acc + l.tokensOut, 0);
  const errorCount = logs.filter((l) => l.status >= 400).length;
  const latencies = logs.map((l) => l.latencyMs).sort((a, b) => a - b);
  const avgLatency = latencies.length ? latencies.reduce((a, b) => a + b, 0) / latencies.length : 0;

  return NextResponse.json({
    hours,
    group_by: groupBy,
    timeseries,
    top_models: topModels,
    top_providers: topProviders,
    top_clients: topClients,
    summary: {
      total_requests: logs.length,
      total_tokens: totalTokens,
      tokens_in: tokensIn,
      tokens_out: tokensOut,
      avg_latency_ms: r1(avgLatency),
      p50_latency_ms: nearestRank(latencies, 50),
      p90_latency_ms: nearestRank(latencies, 90),
      p99_latency_ms: nearestRank(latencies, 99),
      error_count: errorCount,
      error_rate: logs.length ? r1((errorCount / logs.length) * 100) : 0,
      spoofed_fallbacks_count: logs.filter((l) => l.spoofed).length,
      cache_hits: logs.filter((l) => l.via === 'cache').length,
    },
  });
}
