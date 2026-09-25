import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';
import { randomUUID } from 'crypto';
import type { ClientKey } from '@/lib/types';

export const dynamic = 'force-dynamic';

type ClientKeyRow = Awaited<ReturnType<typeof db.clientKey.findMany>>[number];

function toClientKey(k: ClientKeyRow): ClientKey {
  return {
    id: k.id,
    name: k.name,
    token: k.token,
    enabled: k.enabled,
    allowed_models: k.allowedModels,
    rpm_limit: k.rpmLimit,
    tpd_limit: k.tpdLimit,
    tokens_in: k.tokensIn,
    tokens_out: k.tokensOut,
    req_count: k.reqCount,
    last_used_at: k.lastUsedAt?.getTime() ?? null,
    created_at: k.createdAt.getTime(),
  };
}

/** GET /api/admin/client-keys — dashboard client keys, descending by created_at. */
export async function GET() {
  const rows = await db.clientKey.findMany({ orderBy: { createdAt: 'desc' } });
  return NextResponse.json(rows.map(toClientKey));
}

/** POST /api/admin/client-keys — mint a new client key. */
export async function POST(req: NextRequest) {
  let body: Record<string, unknown> = {};
  try {
    const parsed: unknown = await req.json();
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) body = parsed as Record<string, unknown>;
  } catch {
    /* empty body */
  }

  const name = typeof body.name === 'string' ? body.name.trim() : '';
  if (!name) {
    return NextResponse.json({ error: 'Client key name is required' }, { status: 400 });
  }

  const num = (v: unknown, dflt: number): number => {
    const n = typeof v === 'number' ? v : typeof v === 'string' ? Number(v) : NaN;
    return Number.isFinite(n) ? Math.max(0, Math.round(n)) : dflt;
  };

  const token = `nova-sk-${(randomUUID() + randomUUID()).replaceAll('-', '').slice(0, 32)}`;

  const created = await db.clientKey.create({
    data: {
      name,
      token,
      allowedModels: typeof body.allowed_models === 'string' && body.allowed_models.trim() !== '' ? body.allowed_models.trim() : '*',
      rpmLimit: num(body.rpm_limit, 60),
      tpdLimit: num(body.tpd_limit, 0),
    },
  });

  return NextResponse.json(toClientKey(created));
}
