import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

/** POST /api/admin/storage/disconnect {provider_key} → {ok} */
export async function POST(req: NextRequest) {
  let body: { provider_key?: unknown };
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

  // local_disk is always mounted — it keeps its active flag.
  await db.storageProvider.update({
    where: { id: key },
    data: {
      status: 'disconnected',
      ...(row.id === 'local_disk' ? {} : { active: false }),
    },
  });

  return NextResponse.json({ ok: true });
}
