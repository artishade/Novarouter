import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/** PATCH /api/admin/providers/[id] — update editable provider fields. */
export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const providerId = Number(id);
  if (!Number.isInteger(providerId)) {
    return NextResponse.json({ error: 'Invalid provider id' }, { status: 400 });
  }

  const provider = await db.provider.findUnique({ where: { id: providerId } });
  if (!provider) {
    return NextResponse.json({ error: 'Provider not found' }, { status: 404 });
  }

  let body: Record<string, unknown> = {};
  try {
    const parsed: unknown = await req.json();
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) body = parsed as Record<string, unknown>;
  } catch {
    /* empty body */
  }

  const data: { enabled?: boolean; priority?: number; baseUrl?: string; prefix?: string; name?: string } = {};
  if (typeof body.enabled === 'boolean') data.enabled = body.enabled;
  if (body.priority !== undefined) {
    const n = typeof body.priority === 'number' ? body.priority : Number(body.priority);
    if (Number.isFinite(n)) data.priority = Math.round(n);
  }
  if (typeof body.base_url === 'string') data.baseUrl = body.base_url;
  if (typeof body.prefix === 'string') data.prefix = body.prefix;
  if (typeof body.name === 'string' && body.name.trim() !== '') data.name = body.name.trim();

  await db.provider.update({ where: { id: providerId }, data });

  return NextResponse.json({ ok: true });
}

/** DELETE /api/admin/providers/[id] — removes provider + cascades keys/models. */
export async function DELETE(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const providerId = Number(id);
  if (!Number.isInteger(providerId)) {
    return NextResponse.json({ error: 'Invalid provider id' }, { status: 400 });
  }

  const provider = await db.provider.findUnique({ where: { id: providerId } });
  if (!provider) {
    return NextResponse.json({ error: 'Provider not found' }, { status: 404 });
  }
  if (provider.key === 'novafree') {
    return NextResponse.json(
      { error: 'The built-in NovaFree engine cannot be deleted' },
      { status: 400 },
    );
  }

  await db.provider.delete({ where: { id: providerId } });
  await db.providerSession.deleteMany({ where: { providerKey: provider.key } });

  return NextResponse.json({ ok: true });
}
