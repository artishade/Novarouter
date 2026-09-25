import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/**
 * POST /api/admin/models/[id]/ping — health-check a single model.
 * builtin → simulated healthy; provider with keys → real GET {base_url}/models (6s timeout);
 * no keys → status stays 'unknown'.
 * Persists status / latencyMs / checkedAt / httpStatus on this model row only.
 */
export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const modelId = Number(id);
  if (!Number.isInteger(modelId)) {
    return NextResponse.json({ error: 'Invalid model id' }, { status: 400 });
  }

  const model = await db.model.findUnique({
    where: { id: modelId },
    include: { provider: { include: { keys: { select: { apiKey: true, enabled: true } } } } },
  });
  if (!model) {
    return NextResponse.json({ error: 'Model not found' }, { status: 404 });
  }

  const provider = model.provider;
  let status: string;
  let latencyMs: number;
  let httpStatus: number;
  let detail: string;
  let ok: boolean;

  if (provider.kind === 'builtin') {
    latencyMs = 300 + Math.floor(Math.random() * 401); // 300-700
    status = 'healthy';
    httpStatus = 200;
    detail = `Built-in engine responded in ${latencyMs}ms`;
    ok = true;
  } else if (provider.keys.length === 0 || !provider.baseUrl) {
    status = 'unknown';
    latencyMs = 0;
    httpStatus = 0;
    detail = provider.keys.length === 0 ? 'No upstream key configured' : 'Provider has no base URL configured';
    ok = false;
  } else {
    const key = (provider.keys.find((k) => k.enabled) ?? provider.keys[0]).apiKey;
    const url = `${provider.baseUrl.replace(/\/+$/, '')}/models`;
    const started = Date.now();
    try {
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${key}` },
        signal: AbortSignal.timeout(6000),
      });
      latencyMs = Date.now() - started;
      httpStatus = res.status;
      if (res.ok) {
        status = 'healthy';
        detail = `HTTP ${res.status} — upstream OK in ${latencyMs}ms`;
        ok = true;
      } else {
        status = 'dead';
        detail = `Upstream returned HTTP ${res.status}`;
        ok = false;
      }
    } catch (err) {
      latencyMs = Date.now() - started;
      status = 'dead';
      httpStatus = 0;
      detail = (err instanceof Error ? err.message : 'Network error').slice(0, 120);
      ok = false;
    }
  }

  await db.model.update({
    where: { id: model.id },
    data: {
      status,
      latencyMs,
      httpStatus,
      checkedAt: new Date(),
    },
  });

  return NextResponse.json({ ok, status, latency_ms: latencyMs, http_status: httpStatus, detail });
}
