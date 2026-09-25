import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/** PATCH /api/admin/models/[id] — toggle a model on/off. */
export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const modelId = Number(id);
  if (!Number.isInteger(modelId)) {
    return NextResponse.json({ error: 'Invalid model id' }, { status: 400 });
  }

  const model = await db.model.findUnique({ where: { id: modelId } });
  if (!model) {
    return NextResponse.json({ error: 'Model not found' }, { status: 404 });
  }

  let body: Record<string, unknown> = {};
  try {
    const parsed: unknown = await req.json();
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) body = parsed as Record<string, unknown>;
  } catch {
    /* empty body */
  }

  if (typeof body.enabled !== 'boolean') {
    return NextResponse.json({ error: 'enabled (boolean) is required' }, { status: 400 });
  }

  await db.model.update({ where: { id: modelId }, data: { enabled: body.enabled } });

  return NextResponse.json({ ok: true });
}
