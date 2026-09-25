import { db } from '@/lib/db';
import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/** POST /api/admin/keys/clear-cooldowns — lift every cooldown on upstream keys. */
export async function POST() {
  const res = await db.providerKey.updateMany({ data: { cooldownUntil: null } });
  return NextResponse.json({ ok: true, cleared: res.count });
}
