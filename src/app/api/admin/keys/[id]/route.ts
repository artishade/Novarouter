import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/** DELETE /api/admin/keys/[id] — remove an upstream API key. */
export async function DELETE(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const keyId = Number(id);
  if (!Number.isInteger(keyId)) {
    return NextResponse.json({ error: 'Invalid key id' }, { status: 400 });
  }

  const existing = await db.providerKey.findUnique({ where: { id: keyId } });
  if (!existing) {
    return NextResponse.json({ error: 'Key not found' }, { status: 404 });
  }

  await db.providerKey.delete({ where: { id: keyId } });

  return NextResponse.json({ ok: true });
}
