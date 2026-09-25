import { NextRequest, NextResponse } from 'next/server';
import { getGpuProviders, setGpuProviders } from '@/lib/server/config';

/** POST /api/admin/compute/providers/[id]/toggle {enabled?} → {ok, message} */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;

  let body: { enabled?: unknown } = {};
  try {
    body = await req.json();
  } catch {
    /* empty/absent body → flip current flag */
  }

  const list = await getGpuProviders();
  const idx = list.findIndex((p) => p.id === id);
  if (idx === -1) {
    return NextResponse.json(
      { error: `Unknown compute provider: ${id}` },
      { status: 404 },
    );
  }

  const enabled =
    typeof body.enabled === 'boolean' ? body.enabled : !list[idx].enabled;
  list[idx] = { ...list[idx], enabled };
  await setGpuProviders(list);

  const message = `${list[idx].name} ${enabled ? 'enabled' : 'disabled'} — free tier: ${list[idx].free_tier}`;
  return NextResponse.json({ ok: true, message });
}
