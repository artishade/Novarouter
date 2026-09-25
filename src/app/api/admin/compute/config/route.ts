import { NextRequest, NextResponse } from 'next/server';
import {
  getConfig,
  setConfig,
  getGpuProviders,
} from '@/lib/server/config';
import type { GpuConfig } from '@/lib/types';

export const dynamic = 'force-dynamic';

async function buildGpuConfig(): Promise<GpuConfig> {
  const [providers, gpuEnabledRaw, strategyRaw] = await Promise.all([
    getGpuProviders(),
    getConfig('gpu_enabled'),
    getConfig('gpu_strategy'),
  ]);
  return {
    enabled: gpuEnabledRaw !== '0' && gpuEnabledRaw !== 'false',
    strategy: strategyRaw || 'quota_aware',
    providers,
    total_vram_gb: providers.reduce((a, p) => a + (p.vram_gb || 0), 0),
    enabled_vram_gb: providers
      .filter((p) => p.enabled)
      .reduce((a, p) => a + (p.vram_gb || 0), 0),
  };
}

/** GET /api/admin/compute/config → GpuConfig */
export async function GET() {
  return NextResponse.json(await buildGpuConfig());
}

/** POST /api/admin/compute/config {enabled?, strategy?} → {ok, config} */
export async function POST(req: NextRequest) {
  let body: { enabled?: unknown; strategy?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  if (typeof body.enabled === 'boolean') {
    await setConfig('gpu_enabled', body.enabled ? '1' : '0');
  }
  if (typeof body.strategy === 'string' && body.strategy.trim()) {
    await setConfig('gpu_strategy', body.strategy.trim());
  }

  const config = await buildGpuConfig();
  return NextResponse.json({ ok: true, config });
}
