'use client';

/**
 * Slim sticky status bar: gateway state + uptime, V8 heap usage, provider/model/latency summary, Dhaka time.
 */
import { useEffect, useState } from 'react';
import { fmtMb, fmtUptime } from '@/lib/format';
import type { GatewayStats } from '@/lib/types';

export function StatusFooter({ stats }: { stats: GatewayStats | null }) {
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const heapUsed = stats?.memory.heap_used_mb ?? 0;
  const heapTotal = stats?.memory.heap_total_mb ?? 0;
  const heapPct = heapTotal > 0 ? Math.min(100, (heapUsed / heapTotal) * 100) : 0;
  const dhakaTime =
    now === null
      ? '--:--:--'
      : new Date(now).toLocaleTimeString('en-US', { hour12: false, timeZone: 'Asia/Dhaka' });

  return (
    <footer className="sticky bottom-0 z-20 mt-auto flex h-9 shrink-0 items-center justify-between gap-4 border-t border-slate-800 bg-[#0d1322]/80 px-4 text-[11px] backdrop-blur md:px-6">
      {/* Left: status + uptime */}
      <div className="flex min-w-0 items-center gap-2">
        <span className="relative flex size-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
          <span className="relative inline-flex size-2 rounded-full bg-emerald-400" />
        </span>
        <span className="font-medium text-slate-300">Gateway online</span>
        <span className="text-slate-700">|</span>
        <span className="font-mono text-slate-500">
          up {stats ? fmtUptime(stats.uptime_s) : '—'}
        </span>
      </div>

      {/* Middle: heap usage (hidden on small screens) */}
      <div className="hidden items-center gap-2 md:flex">
        <span className="text-slate-500">V8 heap</span>
        <div className="h-1.5 w-28 overflow-hidden rounded-full bg-slate-800">
          <div
            className="h-full rounded-full bg-purple-400 transition-[width] duration-500"
            style={{ width: `${heapPct}%` }}
          />
        </div>
        <span className="font-mono text-slate-500">
          {stats ? `${fmtMb(heapUsed)} / ${fmtMb(heapTotal)}` : '—'}
        </span>
      </div>

      {/* Right: fleet summary + Dhaka time */}
      <div className="flex min-w-0 items-center gap-2 font-mono text-slate-500">
        <span className="hidden truncate sm:inline">
          {stats
            ? `${stats.providers_count} providers · ${stats.active_models} models · avg ${Math.round(stats.avg_latency_ms)}ms`
            : '— providers · — models'}
        </span>
        <span className="hidden text-slate-700 sm:inline">|</span>
        <span className="tabular-nums">Asia/Dhaka {dhakaTime}</span>
      </div>
    </footer>
  );
}
