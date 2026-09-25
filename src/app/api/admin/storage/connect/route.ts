import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

/** POST /api/admin/storage/connect {provider_key, config} → status connected */
export async function POST(req: NextRequest) {
  let body: { provider_key?: unknown; config?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  const key = typeof body.provider_key === 'string' ? body.provider_key : '';
  if (!key) {
    return NextResponse.json({ error: 'provider_key is required' }, { status: 400 });
  }

  const row = await db.storageProvider.findUnique({ where: { id: key } });
  if (!row) {
    return NextResponse.json(
      { error: `Unknown storage provider: ${key}` },
      { status: 404 },
    );
  }

  let existing: Record<string, unknown> = {};
  try {
    existing = JSON.parse(row.config) as Record<string, unknown>;
  } catch {
    existing = {};
  }
  const incoming =
    body.config && typeof body.config === 'object'
      ? (body.config as Record<string, unknown>)
      : {};
  const merged = { ...existing, ...incoming };

  await db.storageProvider.update({
    where: { id: key },
    data: {
      config: JSON.stringify(merged),
      status: 'connected',
      lastError: null,
    },
  });

  const fieldCount = Object.keys(incoming).filter(
    (k) => typeof merged[k] === 'string' || typeof merged[k] === 'number',
  ).length;

  return NextResponse.json({
    ok: true,
    status: 'connected',
    message: `${row.name} connected — ${fieldCount} config field${fieldCount === 1 ? '' : 's'} saved`,
  });
}
