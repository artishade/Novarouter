import { NextResponse } from 'next/server';
import { db } from '@/lib/db';

/** POST /api/admin/terminal/clear → wipe all terminal history */
export async function POST() {
  await db.terminalCommand.deleteMany();
  return NextResponse.json({ ok: true });
}
