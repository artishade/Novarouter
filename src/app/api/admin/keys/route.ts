import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';
import type { UpstreamKey } from '@/lib/types';

export const dynamic = 'force-dynamic';

/** first 8 + '…' + last 4 (contract's key mask format). */
function maskKey(key: string): string {
  if (key.length <= 12) return `${key.slice(0, 4)}…`;
  return `${key.slice(0, 8)}…${key.slice(-4)}`;
}

type KeyRow = Awaited<ReturnType<typeof db.providerKey.findMany>>[number];

function toUpstreamKey(k: KeyRow, providerName?: string): UpstreamKey {
  return {
    id: k.id,
    provider_id: k.providerId,
    ...(providerName !== undefined ? { provider_name: providerName } : {}),
    label: k.label,
    api_key_preview: maskKey(k.apiKey),
    weight: k.weight,
    enabled: k.enabled,
    cooldown_until: k.cooldownUntil?.getTime() ?? null,
    last_error: k.lastError,
    req_count: k.reqCount,
    err_count: k.errCount,
    last_used_at: k.lastUsedAt?.getTime() ?? null,
    created_at: k.createdAt.getTime(),
  };
}

/** GET /api/admin/keys?provider_id= — upstream API keys, descending by created_at. */
export async function GET(req: NextRequest) {
  const sp = new URL(req.url).searchParams;
  const providerIdRaw = Number(sp.get('provider_id'));
  const providerId = Number.isInteger(providerIdRaw) && sp.get('provider_id') !== null ? providerIdRaw : undefined;

  const rows = await db.providerKey.findMany({
    where: providerId !== undefined ? { providerId } : undefined,
    orderBy: { createdAt: 'desc' },
    include: { provider: { select: { name: true } } },
  });

  return NextResponse.json(rows.map((k) => toUpstreamKey(k, k.provider.name)));
}

/** POST /api/admin/keys — add a single upstream key. */
export async function POST(req: NextRequest) {
  let body: Record<string, unknown> = {};
  try {
    const parsed: unknown = await req.json();
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) body = parsed as Record<string, unknown>;
  } catch {
    /* empty body */
  }

  const providerId = typeof body.provider_id === 'number' ? body.provider_id : Number(body.provider_id);
  if (!Number.isInteger(providerId)) {
    return NextResponse.json({ error: 'provider_id is required' }, { status: 400 });
  }
  const apiKey = typeof body.api_key === 'string' ? body.api_key.trim() : '';
  if (!apiKey) {
    return NextResponse.json({ error: 'api_key is required' }, { status: 400 });
  }

  const provider = await db.provider.findUnique({ where: { id: providerId } });
  if (!provider) {
    return NextResponse.json({ error: 'Provider not found' }, { status: 404 });
  }

  const weight = typeof body.weight === 'number' && Number.isFinite(body.weight) ? Math.max(1, Math.round(body.weight)) : 1;
  const created = await db.providerKey.create({
    data: {
      providerId,
      apiKey,
      label: typeof body.label === 'string' && body.label.trim() !== '' ? body.label.trim() : 'key',
      weight,
    },
  });

  return NextResponse.json({ id: created.id });
}
