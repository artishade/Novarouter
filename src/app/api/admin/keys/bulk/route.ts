import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/** POST /api/admin/keys/bulk — add many keys (newline/comma separated) at once. */
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
  const provider = await db.provider.findUnique({ where: { id: providerId } });
  if (!provider) {
    return NextResponse.json({ error: 'Provider not found' }, { status: 404 });
  }

  const rawKeys = typeof body.keys === 'string' ? body.keys : '';
  const keys = rawKeys
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);

  if (keys.length === 0) {
    return NextResponse.json({ error: 'No keys provided' }, { status: 400 });
  }

  const labelPrefix = typeof body.label_prefix === 'string' && body.label_prefix.trim() !== '' ? body.label_prefix.trim() : 'key';

  const created = await db.providerKey.createMany({
    data: keys.map((apiKey, i) => ({
      providerId,
      apiKey,
      label: keys.length === 1 ? labelPrefix : `${labelPrefix}-${i + 1}`,
    })),
  });

  // createMany doesn't return rows on SQLite — fetch the ids we just inserted.
  const rows = await db.providerKey.findMany({
    where: { providerId },
    orderBy: { id: 'desc' },
    take: created.count,
    select: { id: true },
  });
  const ids = rows.map((r) => r.id).sort((a, b) => a - b);

  return NextResponse.json({ added: created.count, ids });
}
