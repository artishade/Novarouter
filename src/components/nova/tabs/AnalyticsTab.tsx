'use client';

/**
 * Analytics tab: time range selector, requests/errors area chart, top models bar,
 * top providers horizontal bar, latency percentiles, status donut, cache hit rate.
 */
import { useEffect, useState, type ReactNode } from 'react';
import { motion, type Variants } from 'framer-motion';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  type TooltipProps,
  XAxis,
  YAxis,
} from 'recharts';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { api } from '@/lib/api';
import { cx, fmtNum } from '@/lib/format';
import type { AnalyticsResponse, GatewayStats, MetaConfig } from '@/lib/types';

const EMERALD = '#34d399';
const ROSE = '#fb7185';
const GRID = '#1e293b';
const AXIS = '#475569';

const RANGES: Array<{ label: string; hours: number }> = [
  { label: 'Last 24 hours', hours: 24 },
  { label: 'Last 7 days', hours: 168 },
  { label: 'Last 30 days', hours: 720 },
];

const container: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.07 } },
};

const item: Variants = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: { duration: 0.3, ease: 'easeOut' } },
};

function shortModel(m: string): string {
  const tail = m.includes('/') ? m.split('/').slice(1).join('/') : m;
  return tail.length > 16 ? tail.slice(0, 15) + '…' : tail;
}

function ChartTip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="rounded-md border border-slate-700 bg-[#0d1322] px-3 py-2 text-xs shadow-lg">
      {label !== undefined && label !== '' && (
        <div className="mb-1 font-mono text-[10px] text-slate-500">{String(label)}</div>
      )}
      {payload.map((entry, i) => (
        <div key={i} className="flex items-center gap-2">
          <span className="size-2 rounded-full" style={{ backgroundColor: entry.color }} />
          <span className="text-slate-400">{entry.name}</span>
          <span className="ml-auto font-mono tabular-nums text-slate-200">
            {fmtNum(Number(entry.value ?? 0))}
          </span>
        </div>
      ))}
    </div>
  );
}

function Panel({
  title,
  subtitle,
  right,
  children,
  className,
}: {
  title: string;
  subtitle?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <motion.div
      variants={item}
      className={cx('flex flex-col rounded-xl border border-slate-800 bg-[#0d1322]/80 p-4', className)}
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-500">{title}</h2>
          {subtitle && <p className="mt-0.5 text-[11px] text-slate-600">{subtitle}</p>}
        </div>
        {right}
      </div>
      <div className="min-h-0 flex-1">{children}</div>
    </motion.div>
  );
}

function ChartSkeleton({ height = 'h-64' }: { height?: string }) {
  return <Skeleton className={cx('w-full rounded-xl', height)} />;
}

export function AnalyticsTab({
  meta,
  stats,
  onRefresh,
}: {
  meta: MetaConfig | null;
  stats: GatewayStats | null;
  onRefresh: () => void;
}) {
  const [hours, setHours] = useState(24);
  const [data, setData] = useState<AnalyticsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const groupBy = hours >= 720 ? 'day' : 'hour';

  useEffect(() => {
    let alive = true;
    const t = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      api
        .getAnalytics(hours, groupBy)
        .then((res) => {
          if (alive) setData(res);
        })
        .catch((e: unknown) => {
          if (alive) setError(e instanceof Error ? e.message : 'Failed to load analytics');
        })
        .finally(() => {
          if (alive) setLoading(false);
        });
    }, 0);
    return () => {
      alive = false;
      window.clearTimeout(t);
    };
  }, [hours, groupBy]);

  const summary = data?.summary;
  const total = summary?.total_requests ?? 0;
  const errorCount = summary?.error_count ?? 0;
  const successCount = Math.max(0, total - errorCount);
  const cacheRate = total > 0 && summary ? (summary.cache_hits / total) * 100 : 0;

  const statusData = [
    { name: '2xx success', value: successCount, fill: EMERALD },
    { name: 'errors', value: errorCount, fill: ROSE },
  ];

  const percentiles = [
    { label: 'p50 latency', value: summary?.p50_latency_ms, dot: 'bg-emerald-400' },
    { label: 'p90 latency', value: summary?.p90_latency_ms, dot: 'bg-amber-400' },
    { label: 'p99 latency', value: summary?.p99_latency_ms, dot: 'bg-rose-400' },
  ];

  return (
    <motion.div variants={container} initial="hidden" animate="show" className="flex flex-col gap-4">
      {/* Header: range selector + summary chips */}
      <motion.div
        variants={item}
        className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 bg-[#0d1322]/80 p-4"
      >
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant="outline"
            className="border-emerald-500/30 bg-emerald-500/10 font-mono text-emerald-300"
          >
            {fmtNum(total)} requests
          </Badge>
          <Badge
            variant="outline"
            className="border-emerald-500/30 bg-emerald-500/10 font-mono text-emerald-300"
          >
            {fmtNum(summary?.total_tokens ?? 0)} tokens
          </Badge>
          <Badge
            variant="outline"
            className={cx(
              'font-mono',
              (summary?.error_rate ?? 0) > 5
                ? 'border-rose-500/30 bg-rose-500/10 text-rose-300'
                : 'border-amber-500/30 bg-amber-500/10 text-amber-300'
            )}
          >
            {(summary?.error_rate ?? 0).toFixed(2)}% errors
          </Badge>
          <Badge
            variant="outline"
            className="border-slate-700 bg-slate-800/40 font-mono text-slate-300"
          >
            {fmtNum(summary?.spoofed_fallbacks_count ?? 0)} spoofed fallbacks
          </Badge>
          {stats && (
            <Badge
              variant="outline"
              className="border-teal-500/30 bg-teal-500/10 font-mono text-teal-300"
            >
              live avg {Math.round(stats.avg_latency_ms)}ms
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          {meta && (
            <span className="hidden font-mono text-[10px] text-slate-600 sm:inline">
              gateway v{meta.version}
            </span>
          )}
          <button
            type="button"
            onClick={onRefresh}
            title="Refresh shared gateway data"
            aria-label="Refresh shared gateway data"
            className="rounded-md border border-slate-800 bg-[#080c14] px-2.5 py-1 font-mono text-[11px] text-slate-400 transition-colors hover:border-emerald-500/40 hover:text-emerald-400"
          >
            sync
          </button>
          <Select
            value={String(hours)}
            onValueChange={(v) => setHours(Number(v))}
          >
            <SelectTrigger size="sm" aria-label="Analytics time range" className="w-[150px] border-slate-800 bg-[#080c14] font-mono text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="border-slate-800 bg-[#0d1322] text-slate-200">
              {RANGES.map((r) => (
                <SelectItem key={r.hours} value={String(r.hours)} className="font-mono text-xs">
                  {r.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </motion.div>

      {error && (
        <motion.div
          variants={item}
          className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-xs text-rose-300"
        >
          Failed to load analytics: {error}
        </motion.div>
      )}

      {/* Requests + errors over time */}
      <Panel title="Requests & Errors" subtitle={`Per ${groupBy} bucket, emerald = requests, rose = errors`}>
        {loading && !data ? (
          <ChartSkeleton />
        ) : (
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data?.timeseries ?? []} margin={{ top: 5, right: 8, left: -12, bottom: 0 }}>
                <defs>
                  <linearGradient id="gradReqs" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={EMERALD} stopOpacity={0.35} />
                    <stop offset="100%" stopColor={EMERALD} stopOpacity={0.02} />
                  </linearGradient>
                  <linearGradient id="gradErr" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={ROSE} stopOpacity={0.3} />
                    <stop offset="100%" stopColor={ROSE} stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="bucket"
                  tick={{ fill: AXIS, fontSize: 10 }}
                  tickLine={false}
                  axisLine={{ stroke: GRID }}
                  interval="preserveStartEnd"
                  minTickGap={24}
                />
                <YAxis tick={{ fill: AXIS, fontSize: 10 }} tickLine={false} axisLine={false} tickFormatter={(v: number) => fmtNum(v)} />
                <Tooltip content={<ChartTip />} cursor={{ stroke: GRID }} />
                <Area type="monotone" dataKey="reqs" name="requests" stroke={EMERALD} strokeWidth={2} fill="url(#gradReqs)" />
                <Area type="monotone" dataKey="errors" name="errors" stroke={ROSE} strokeWidth={2} fill="url(#gradErr)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </Panel>

      {/* Top models + top providers */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel title="Top Models" subtitle="Tokens processed (in + out)">
          {loading && !data ? (
            <ChartSkeleton />
          ) : (
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={(data?.top_models ?? []).slice(0, 6)} margin={{ top: 5, right: 8, left: -12, bottom: 0 }}>
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
                  <XAxis
                    dataKey="model"
                    tickFormatter={(m: string) => shortModel(m)}
                    tick={{ fill: AXIS, fontSize: 10 }}
                    tickLine={false}
                    axisLine={{ stroke: GRID }}
                    interval={0}
                  />
                  <YAxis tick={{ fill: AXIS, fontSize: 10 }} tickLine={false} axisLine={false} tickFormatter={(v: number) => fmtNum(v)} />
                  <Tooltip content={<ChartTip />} cursor={{ fill: 'rgba(52,211,153,0.06)' }} />
                  <Bar dataKey="tokens" name="tokens" fill={EMERALD} radius={[4, 4, 0, 0]} maxBarSize={42} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Panel>

        <Panel title="Top Providers" subtitle="Requests routed per provider">
          {loading && !data ? (
            <ChartSkeleton />
          ) : (
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={(data?.top_providers ?? []).slice(0, 7)} layout="vertical" margin={{ top: 5, right: 16, left: 8, bottom: 0 }}>
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" horizontal={false} />
                  <XAxis type="number" tick={{ fill: AXIS, fontSize: 10 }} tickLine={false} axisLine={{ stroke: GRID }} tickFormatter={(v: number) => fmtNum(v)} />
                  <YAxis
                    type="category"
                    dataKey="provider"
                    width={104}
                    tick={{ fill: AXIS, fontSize: 10 }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip content={<ChartTip />} cursor={{ fill: 'rgba(52,211,153,0.06)' }} />
                  <Bar dataKey="reqs" name="requests" fill={EMERALD} radius={[0, 4, 4, 0]} maxBarSize={16} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Panel>
      </div>

      {/* Percentiles + donut + cache */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Panel title="Latency Percentiles" subtitle="Nearest-rank over the selected range">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3 lg:grid-cols-1">
            {percentiles.map((p) => (
              <div
                key={p.label}
                className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#080c14] px-3 py-2.5"
              >
                <div className="flex items-center gap-2">
                  <span className={cx('size-2 rounded-full', p.dot)} />
                  <span className="text-xs text-slate-400">{p.label}</span>
                </div>
                <span className="font-mono text-lg font-semibold tabular-nums text-slate-100">
                  {loading && !data ? (
                    <Skeleton className="h-6 w-16" />
                  ) : (
                    `${Math.round(p.value ?? 0)}ms`
                  )}
                </span>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Status Distribution" subtitle="2xx success vs errors">
          {loading && !data ? (
            <ChartSkeleton />
          ) : (
            <div className="relative h-64">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Tooltip content={<ChartTip />} />
                  <Pie
                    data={statusData}
                    dataKey="value"
                    nameKey="name"
                    innerRadius={62}
                    outerRadius={88}
                    paddingAngle={3}
                    strokeWidth={0}
                  >
                    {statusData.map((entry) => (
                      <Cell key={entry.name} fill={entry.fill} />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                <span className="font-mono text-2xl font-semibold tabular-nums text-slate-100">
                  {fmtNum(total)}
                </span>
                <span className="text-[10px] uppercase tracking-widest text-slate-500">requests</span>
              </div>
              <div className="absolute inset-x-0 bottom-0 flex justify-center gap-4">
                {statusData.map((entry) => (
                  <div key={entry.name} className="flex items-center gap-1.5 text-[11px] text-slate-400">
                    <span className="size-2 rounded-full" style={{ backgroundColor: entry.fill }} />
                    {entry.name} · {fmtNum(entry.value)}
                  </div>
                ))}
              </div>
            </div>
          )}
        </Panel>

        <Panel title="Cache Hit Rate" subtitle="Requests served by the response cache">
          <div className="flex h-64 flex-col items-center justify-center gap-4">
            {loading && !data ? (
              <Skeleton className="h-16 w-28" />
            ) : (
              <>
                <div className="font-mono text-4xl font-semibold tabular-nums text-emerald-400">
                  {cacheRate.toFixed(1)}%
                </div>
                <div className="h-2 w-40 overflow-hidden rounded-full bg-slate-800">
                  <div
                    className="h-full rounded-full bg-emerald-400 transition-[width] duration-500"
                    style={{ width: `${Math.min(100, cacheRate)}%` }}
                  />
                </div>
                <div className="text-center font-mono text-[11px] text-slate-500">
                  {fmtNum(summary?.cache_hits ?? 0)} of {fmtNum(total)} requests
                  <br />
                  served straight from the response cache
                </div>
              </>
            )}
          </div>
        </Panel>
      </div>
    </motion.div>
  );
}
