import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';
import type { Model as ModelType, ModelCapabilities } from '@/lib/types';

export const dynamic = 'force-dynamic';

function parseCaps(raw: string): ModelCapabilities {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as ModelCapabilities;
    }
    return {};
  } catch {
    return {};
  }
}

/** Human-readable check detail synthesized from status + last HTTP code. */
function statusDetail(status: string, httpStatus: number): string {
  if (status === 'healthy') return httpStatus > 0 ? `HTTP ${httpStatus} — upstream OK` : 'Healthy';
  if (status === 'cooling') return httpStatus > 0 ? `HTTP ${httpStatus} — cooling down` : 'Cooling down';
  if (status === 'dead') return httpStatus > 0 ? `HTTP ${httpStatus} — upstream rejected` : 'Unreachable / offline';
  return httpStatus > 0 ? `Last check: HTTP ${httpStatus}` : 'Not checked yet';
}

type ModelRow = Awaited<ReturnType<typeof db.model.findMany>>[number];

function toModel(m: ModelRow, providerName: string, providerColor: string): ModelType {
  return {
    id: m.id,
    provider_id: m.providerId,
    provider_name: providerName,
    provider_color: providerColor,
    model_id: m.modelId,
    exposed_id: m.exposedId,
    display_name: m.displayName,
    is_free: m.isFree,
    status: m.status as ModelType['status'],
    http_status: m.httpStatus,
    detail: statusDetail(m.status, m.httpStatus),
    latency_ms: m.latencyMs,
    checked_at: m.checkedAt?.getTime() ?? null,
    enabled: m.enabled,
    context_length: m.contextLength,
    max_output: m.maxOutput,
    capabilities: parseCaps(m.capabilities),
    price_in: m.priceIn,
    price_out: m.priceOut,
    description: m.description,
  };
}

/**
 * GET /api/admin/models?provider_id&status&search&capability&free
 * Ordered by provider priority asc, then exposed_id.
 */
export async function GET(req: NextRequest) {
  const sp = new URL(req.url).searchParams;

  const providerIdRaw = Number(sp.get('provider_id'));
  const providerId = sp.get('provider_id') !== null && Number.isInteger(providerIdRaw) ? providerIdRaw : undefined;

  const statusRaw = sp.get('status');
  const status = statusRaw && ['healthy', 'cooling', 'dead', 'unknown'].includes(statusRaw) ? statusRaw : undefined;

  const freeRaw = sp.get('free');
  const free = freeRaw === 'true' || freeRaw === '1' ? true : freeRaw === 'false' || freeRaw === '0' ? false : undefined;

  const search = (sp.get('search') ?? '').trim().toLowerCase();
  const capability = sp.get('capability');
  const capabilityFilter =
    capability === 'tools' || capability === 'vision' || capability === 'reasoning' ? capability : undefined;

  const rows = await db.model.findMany({
    where: {
      ...(providerId !== undefined ? { providerId } : {}),
      ...(status !== undefined ? { status } : {}),
      ...(free !== undefined ? { isFree: free } : {}),
    },
    include: { provider: { select: { name: true, color: true, priority: true } } },
  });

  const filtered = rows
    .filter((m) => {
      if (search) {
        const hay = `${m.exposedId}\n${m.displayName}\n${m.modelId}`.toLowerCase();
        if (!hay.includes(search)) return false;
      }
      if (capabilityFilter) {
        const caps = parseCaps(m.capabilities);
        if (caps[capabilityFilter] !== true) return false;
      }
      return true;
    })
    .sort((a, b) => a.provider.priority - b.provider.priority || a.exposedId.localeCompare(b.exposedId));

  const out = filtered.map((m) => toModel(m, m.provider.name, m.provider.color));

  return NextResponse.json({ total: out.length, rows: out });
}
