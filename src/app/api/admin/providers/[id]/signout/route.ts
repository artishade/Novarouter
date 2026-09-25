import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/** POST /api/admin/providers/[id]/signout — drop the provider dashboard session. */
export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const providerId = Number(id);
  if (!Number.isInteger(providerId)) {
    return NextResponse.json({ error: 'Invalid provider id' }, { status: 400 });
  }

  const provider = await db.provider.findUnique({ where: { id: providerId } });
  if (!provider) {
    return NextResponse.json({ error: 'Provider not found' }, { status: 404 });
  }

  await db.providerSession.deleteMany({ where: { providerKey: provider.key } });

  return NextResponse.json({ ok: true });
}
