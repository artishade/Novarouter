import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

/** POST /api/admin/storage/config {provider_key, config?} → set active provider */
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

  const data: { active: boolean; config?: string } = { active: true };
  if (body.config && typeof body.config === 'object') {
    let existing: Record<string, unknown> = {};
    try {
      existing = JSON.parse(row.config) as Record<string, unknown>;
    } catch {
      existing = {};
    }
    data.config = JSON.stringify({
      ...existing,
      ...(body.config as Record<string, unknown>),
    });
  }

  await db.storageProvider.update({ where: { id: key }, data });
  await db.storageProvider.updateMany({
    where: { id: { not: key } },
    data: { active: false },
  });

  return NextResponse.json({ ok: true, active_provider: key });
}
