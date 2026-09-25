import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/** PATCH /api/admin/client-keys/[id] — update a client key. */
export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const keyId = Number(id);
  if (!Number.isInteger(keyId)) {
    return NextResponse.json({ error: 'Invalid client key id' }, { status: 400 });
  }

  const existing = await db.clientKey.findUnique({ where: { id: keyId } });
  if (!existing) {
    return NextResponse.json({ error: 'Client key not found' }, { status: 404 });
  }

  let body: Record<string, unknown> = {};
  try {
    const parsed: unknown = await req.json();
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) body = parsed as Record<string, unknown>;
  } catch {
    /* empty body */
  }

  const data: { enabled?: boolean; name?: string; rpmLimit?: number; tpdLimit?: number; allowedModels?: string } = {};
  if (typeof body.enabled === 'boolean') data.enabled = body.enabled;
  if (typeof body.name === 'string' && body.name.trim() !== '') data.name = body.name.trim();
  if (body.rpm_limit !== undefined) {
    const n = typeof body.rpm_limit === 'number' ? body.rpm_limit : Number(body.rpm_limit);
    if (Number.isFinite(n)) data.rpmLimit = Math.max(0, Math.round(n));
  }
  if (body.tpd_limit !== undefined) {
    const n = typeof body.tpd_limit === 'number' ? body.tpd_limit : Number(body.tpd_limit);
    if (Number.isFinite(n)) data.tpdLimit = Math.max(0, Math.round(n));
  }
  if (typeof body.allowed_models === 'string' && body.allowed_models.trim() !== '') {
    data.allowedModels = body.allowed_models.trim();
  }

  await db.clientKey.update({ where: { id: keyId }, data });

  return NextResponse.json({ ok: true });
}

/** DELETE /api/admin/client-keys/[id] — revoke a client key. */
export async function DELETE(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const keyId = Number(id);
  if (!Number.isInteger(keyId)) {
    return NextResponse.json({ error: 'Invalid client key id' }, { status: 400 });
  }

  const existing = await db.clientKey.findUnique({ where: { id: keyId } });
  if (!existing) {
    return NextResponse.json({ error: 'Client key not found' }, { status: 404 });
  }

  await db.clientKey.delete({ where: { id: keyId } });

  return NextResponse.json({ ok: true });
}
