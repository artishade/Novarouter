import os from 'os';
import { NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { getConfig, getConfigNumber, getGpuProviders } from '@/lib/server/config';
import type { TerminalHistoryItem } from '@/lib/types';

export const dynamic = 'force-dynamic';

/** GET /api/admin/terminal/history → TerminalHistoryResponse */
export async function GET() {
  const [cwdRaw, boostRaw, heap, swap, gpuEnabledRaw, strategyRaw, gpus, rowsDesc] =
    await Promise.all([
      getConfig('terminal_cwd'),
      getConfig('boost_applied_at'),
      getConfigNumber('v8_heap_mb', 2048),
      getConfigNumber('swap_mb', 2048),
      getConfig('gpu_enabled'),
      getConfig('gpu_strategy'),
      getGpuProviders(),
      db.terminalCommand.findMany({ orderBy: { createdAt: 'desc' }, take: 50 }),
    ]);

  const history: TerminalHistoryItem[] = rowsDesc
    .slice()
    .reverse()
    .map((r) => ({
      id: r.id,
      command: r.command,
      output: r.output,
      exit_code: r.exitCode,
      duration_ms: r.durationMs,
      cwd: r.cwd,
      timestamp: r.createdAt.getTime(),
    }));

  const cpus = os.cpus().length || 1;
  const load = os.loadavg().reduce((a, b) => a + b, 0) / 3;
  const loadPct = Math.min(99, Math.max(0, Math.round((load / cpus) * 100)));

  const boostNum = Number(boostRaw);
  const boostAppliedAt =
    boostRaw !== null && Number.isFinite(boostNum) && boostNum > 0 ? boostNum : null;

  return NextResponse.json({
    cwd: cwdRaw || '/workspace',
    history,
    system: {
      platform: os.platform(),
      release: os.release(),
      arch: os.arch(),
      hostname: os.hostname(),
      uptime_s: Math.round(os.uptime()),
      totalmem_mb: Math.round(os.totalmem() / 1048576),
      freemem_mb: Math.round(os.freemem() / 1048576),
      cpus,
      load_pct: loadPct,
      node_version: process.version,
    },
    memory_config: {
      v8_heap_mb: heap,
      swap_mb: swap,
      boost_applied_at: boostAppliedAt,
    },
    // NOTE: shared type TerminalHistoryResponse.gpu accidentally declares
    // `enabled` twice (boolean flag + number count). Emit the boolean flag as
    // `enabled` and expose the count as the extra `enabled_count` key until
    // the orchestrator reconciles types.ts.
    gpu: {
      enabled: gpuEnabledRaw !== '0' && gpuEnabledRaw !== 'false',
      strategy: strategyRaw || 'quota_aware',
      total: gpus.length,
      enabled_count: gpus.filter((p) => p.enabled).length,
      connected: gpus.filter((p) => p.connected).length,
    },
  });
}
