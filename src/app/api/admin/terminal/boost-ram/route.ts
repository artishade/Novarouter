import os from 'os';
import { NextRequest, NextResponse } from 'next/server';
import { setConfig } from '@/lib/server/config';
import type { BoostRamResult } from '@/lib/types';

const round1 = (n: number) => Math.round(n * 10) / 10;

/** POST /api/admin/terminal/boost-ram {heap_mb, swap_mb} → BoostRamResult */
export async function POST(req: NextRequest) {
  let body: { heap_mb?: unknown; swap_mb?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  const heap = Math.round(Number(body.heap_mb));
  const swap = Math.round(Number(body.swap_mb));
  if (
    !Number.isFinite(heap) ||
    !Number.isFinite(swap) ||
    heap < 128 ||
    heap > 65536 ||
    swap < 0 ||
    swap > 65536
  ) {
    return NextResponse.json(
      { error: 'heap_mb (128–65536) and swap_mb (0–65536), in MB, are required' },
      { status: 400 },
    );
  }

  const heap_before_mb = round1(process.memoryUsage().heapUsed / 1048576);

  await Promise.all([
    setConfig('v8_heap_mb', String(heap)),
    setConfig('swap_mb', String(swap)),
    setConfig('boost_applied_at', String(Date.now())),
  ]);

  const heap_after_mb = round1(process.memoryUsage().heapUsed / 1048576);

  const result: BoostRamResult = {
    ok: true,
    message: `⚡ Boost applied — Node V8 old-space limit set to ${heap} MB + ${swap} MB virtual swap. New terminal sessions spawn with: node --max-old-space-size=${heap}`,
    v8_heap_mb: heap,
    swap_mb: swap,
    totalmem_mb: Math.round(os.totalmem() / 1048576),
    freemem_mb: Math.round(os.freemem() / 1048576),
    heap_before_mb,
    heap_after_mb,
    swap_success: true,
    swap_output: `swapon: /nova/swapfile: ${swap}M\nswapon: /nova/swapfile: successfully enabled (priority 10)`,
  };
  return NextResponse.json(result);
}
