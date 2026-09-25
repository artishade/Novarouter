/**
 * GET /api/v1/models — OpenAI-compatible model listing.
 * Returns every enabled Model joined with its Provider (exposed_id → id, owned_by → provider.key).
 */
import { NextResponse } from 'next/server';
import { db } from '@/lib/db';

export async function GET() {
  try {
    const models = await db.model.findMany({
      where: { enabled: true },
      include: { provider: { select: { key: true, enabled: true } } },
      orderBy: [{ provider: { priority: 'asc' } }, { exposedId: 'asc' }],
    });

    const data = models
      .filter((m) => m.provider.enabled)
      .map((m) => {
        let capabilities: Record<string, boolean> = {};
        try {
          const parsed = JSON.parse(m.capabilities);
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            capabilities = parsed as Record<string, boolean>;
          }
        } catch {
          capabilities = {};
        }

        return {
          id: m.exposedId,
          object: 'model',
          owned_by: m.provider.key,
          context_length: m.contextLength,
          capabilities,
          is_free: m.isFree,
          status: m.status,
        };
      });

    return NextResponse.json({ object: 'list', data });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to list models';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
