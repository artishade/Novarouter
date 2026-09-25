import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';
import type { RequestLog } from '@/lib/types';

export const dynamic = 'force-dynamic';

/** GET /api/admin/logs?limit=100 — most recent request logs, descending by ts. */
export async function GET(req: NextRequest) {
  const sp = new URL(req.url).searchParams;
  const limitRaw = Number(sp.get('limit'));
  const limit = Number.isFinite(limitRaw) && limitRaw >= 1 ? Math.min(Math.floor(limitRaw), 1000) : 100;

  const rows = await db.requestLog.findMany({
    orderBy: { ts: 'desc' },
    take: limit,
  });

  const logs: RequestLog[] = rows.map((l) => ({
    id: l.id,
    ts: l.ts.getTime(),
    client_name: l.clientName,
    provider_name: l.providerName,
    model: l.model,
    upstream_model: l.upstreamModel,
    endpoint: l.endpoint,
    status: l.status,
    latency_ms: l.latencyMs,
    tokens_in: l.tokensIn,
    tokens_out: l.tokensOut,
    error: l.error,
    via: l.via,
    spoofed: l.spoofed,
  }));

  return NextResponse.json(logs);
}
