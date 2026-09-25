import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/** PATCH /api/admin/routes/[id] — update a fallback route. */
export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const routeId = Number(id);
  if (!Number.isInteger(routeId)) {
    return NextResponse.json({ error: 'Invalid route id' }, { status: 400 });
  }

  const existing = await db.modelRoute.findUnique({ where: { id: routeId } });
  if (!existing) {
    return NextResponse.json({ error: 'Route not found' }, { status: 404 });
  }

  let body: Record<string, unknown> = {};
  try {
    const parsed: unknown = await req.json();
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) body = parsed as Record<string, unknown>;
  } catch {
    /* empty body */
  }

  const data: { publicId?: string; fallbacks?: string; auto?: boolean; enabled?: boolean; note?: string } = {};

  if (typeof body.public_id === 'string' && body.public_id.trim() !== '') {
    const publicId = body.public_id.trim();
    if (publicId !== existing.publicId) {
      const clash = await db.modelRoute.findUnique({ where: { publicId } });
      if (clash) {
        return NextResponse.json({ error: `A route with id '${publicId}' already exists` }, { status: 400 });
      }
      data.publicId = publicId;
    }
  }
  if (Array.isArray(body.fallbacks)) {
    const fallbacks = body.fallbacks
      .filter((v): v is string => typeof v === 'string' && v.trim() !== '')
      .map((s) => s.trim());
    data.fallbacks = JSON.stringify(fallbacks);
  }
  if (typeof body.auto === 'boolean') data.auto = body.auto;
  if (typeof body.enabled === 'boolean') data.enabled = body.enabled;
  if (typeof body.note === 'string') data.note = body.note;

  await db.modelRoute.update({ where: { id: routeId }, data });

  return NextResponse.json({ ok: true });
}

/** DELETE /api/admin/routes/[id] — remove a fallback route. */
export async function DELETE(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const routeId = Number(id);
  if (!Number.isInteger(routeId)) {
    return NextResponse.json({ error: 'Invalid route id' }, { status: 400 });
  }

  const existing = await db.modelRoute.findUnique({ where: { id: routeId } });
  if (!existing) {
    return NextResponse.json({ error: 'Route not found' }, { status: 404 });
  }

  await db.modelRoute.delete({ where: { id: routeId } });

  return NextResponse.json({ ok: true });
}
