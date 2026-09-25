/**
 * GET /api/v1/models — OpenAI-compatible model listing.
 *
 * Default mode:
 *   Returns every enabled Model joined with its Provider (exposed_id → id, owned_by → provider.key).
 *
 * Live discovery mode (used by Nova Agent and external clients):
 *   GET /api/v1/models?discover=1                  → live models from ALL enabled providers
 *   GET /api/v1/models?discover=1&provider=groq    → live models from one provider
 *   GET /api/v1/models?discover=1&free=1           → only models that are free upstream
 *
 * Discovery queries each provider's real /models endpoint (OpenAI-compatible,
 * Gemini, Anthropic and Ollama wire formats are handled), normalizes the result
 * and caches it in-memory for 5 minutes. Results are OpenAI-compatible:
 * { object: "list", data: [{ id, object: "model", owned_by, ... }] }.
 */
import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { aggregateDiscovery, discoverProviderModels } from '@/lib/server/model-discovery';

export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  try {
    const sp = new URL(req.url).searchParams;
    const discover = sp.get('discover') === '1' || sp.get('discover') === 'true';

    if (discover) {
      const providerParam = sp.get('provider')?.trim() || undefined;
      const onlyFree = sp.get('free') === '1' || sp.get('free') === 'true';
      const results = await discoverProviderModels(providerParam || undefined);
      const summary = aggregateDiscovery(results);
      if (onlyFree) {
        summary.data = summary.data.filter((m) => m.is_free);
        summary.total_models = summary.data.length;
      }
      return NextResponse.json(summary);
    }

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
