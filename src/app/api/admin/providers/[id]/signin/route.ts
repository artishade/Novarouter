import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';
import type { ProviderSessionInfo } from '@/lib/types';

export const dynamic = 'force-dynamic';

/** first 8 + '…' + last 4 (contract's key mask format). */
function maskKey(key: string): string {
  if (key.length <= 12) return `${key.slice(0, 4)}…`;
  return `${key.slice(0, 8)}…${key.slice(-4)}`;
}

/** POST /api/admin/providers/[id]/signin — create/refresh the provider dashboard session. */
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const providerId = Number(id);
  if (!Number.isInteger(providerId)) {
    return NextResponse.json({ error: 'Invalid provider id' }, { status: 400 });
  }

  const provider = await db.provider.findUnique({ where: { id: providerId } });
  if (!provider) {
    return NextResponse.json({ error: 'Provider not found' }, { status: 404 });
  }
  if (provider.authUrl == null) {
    return NextResponse.json({ error: 'This provider does not require sign-in' }, { status: 400 });
  }

  let username = '';
  try {
    const parsed: unknown = await req.json();
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const u = (parsed as Record<string, unknown>).username;
      if (typeof u === 'string' && u.trim() !== '') username = u.trim();
    }
  } catch {
    /* empty body — use default username */
  }
  if (!username) username = `${provider.key}-operator`;

  const newestKey = await db.providerKey.findFirst({
    where: { providerId: provider.id },
    orderBy: [{ createdAt: 'desc' }, { id: 'desc' }],
  });
  const tokenMask = newestKey ? maskKey(newestKey.apiKey) : 'pat-••••';

  const [, session] = await db.$transaction([
    db.providerSession.deleteMany({ where: { providerKey: provider.key } }),
    db.providerSession.create({
      data: {
        providerKey: provider.key,
        username,
        displayName: provider.name,
        plan: 'free',
        status: 'connected',
        tokenMask,
        connectedAt: new Date(),
      },
    }),
  ]);

  const out: ProviderSessionInfo = {
    username: session.username,
    display_name: session.displayName,
    plan: session.plan,
    status: session.status,
    token_mask: session.tokenMask,
    connected_at: session.connectedAt.getTime(),
  };

  return NextResponse.json({ ok: true, session: out });
}
