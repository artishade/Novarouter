import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';
import type { ModelRoute } from '@/lib/types';

export const dynamic = 'force-dynamic';

function parseFallbacks(raw: string): string[] {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed.filter((v): v is string => typeof v === 'string');
    return [];
  } catch {
    return [];
  }
}

/** GET /api/admin/routes — fallback route chains. */
export async function GET() {
  const rows = await db.modelRoute.findMany({ orderBy: { createdAt: 'asc' } });

  const out: ModelRoute[] = rows.map((r) => ({
    id: r.id,
    public_id: r.publicId,
    fallbacks: parseFallbacks(r.fallbacks),
    auto: r.auto,
    enabled: r.enabled,
    note: r.note,
    created_at: r.createdAt.getTime(),
  }));

  return NextResponse.json(out);
}

/** POST /api/admin/routes — create a fallback chain. */
export async function POST(req: NextRequest) {
  let body: Record<string, unknown> = {};
  try {
    const parsed: unknown = await req.json();
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) body = parsed as Record<string, unknown>;
  } catch {
    /* empty body */
  }

  const publicId = typeof body.public_id === 'string' ? body.public_id.trim() : '';
  if (!publicId) {
    return NextResponse.json({ error: 'public_id is required' }, { status: 400 });
  }

  const fallbacks = Array.isArray(body.fallbacks)
    ? body.fallbacks.filter((v): v is string => typeof v === 'string' && v.trim() !== '').map((s) => s.trim())
    : [];

  const existing = await db.modelRoute.findUnique({ where: { publicId } });
  if (existing) {
    return NextResponse.json({ error: `A route with id '${publicId}' already exists` }, { status: 400 });
  }

  const created = await db.modelRoute.create({
    data: {
      publicId,
      fallbacks: JSON.stringify(fallbacks),
      auto: typeof body.auto === 'boolean' ? body.auto : true,
      note: typeof body.note === 'string' ? body.note : '',
    },
  });

  return NextResponse.json({ id: created.id });
}
