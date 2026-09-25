'use client';

/**
 * Overview tab: KPI stat cards, NovaFree engine banner, provider health,
 * recent activity feed, quick actions.
 */
import { useEffect, useState, type ReactNode } from 'react';
import { motion, type Variants } from 'framer-motion';
import {
  Activity,
  ArrowRight,
  Coins,
  HeartPulse,
  KeyRound,
  ShieldAlert,
  Sparkles,
  TerminalSquare,
  Timer,
  TrendingDown,
  TrendingUp,
  type LucideIcon,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import { cx, fmtNum, timeAgo } from '@/lib/format';
import type { GatewayStats, MetaConfig, Provider, RequestLog } from '@/lib/types';
import type { TabKey } from '../Dashboard';

const container: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06 } },
};

const item: Variants = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: { duration: 0.3, ease: 'easeOut' } },
};

function statusBadgeClass(status: number): string {
  if (status >= 200 && status < 300) return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300';
  if (status < 500) return 'border-amber-500/30 bg-amber-500/10 text-amber-300';
  return 'border-rose-500/30 bg-rose-500/10 text-rose-300';
}

function DeltaChip({
  now,
  before,
  lowerIsBetter = false,
  unit = '',
}: {
  now: number | undefined;
  before: number | null | undefined;
  lowerIsBetter?: boolean;
  unit?: string;
}) {
  if (before === null || before === undefined || now === undefined) return null;
  const d = now - before;
  if (d === 0) return null;
  const good = lowerIsBetter ? d < 0 : d > 0;
  return (
    <span
      className={cx(
        'inline-flex items-center gap-0.5 text-[11px] font-medium',
        good ? 'text-emerald-400' : 'text-rose-400'
      )}
    >
      {d > 0 ? <TrendingUp className="size-3" /> : <TrendingDown className="size-3" />}
      {d > 0 ? '+' : '-'}
      {fmtNum(Math.abs(d))}
      {unit}
    </span>
  );
}

function KpiCard({
  icon: Icon,
  label,
  value,
  sub,
  delta,
}: {
  icon: LucideIcon;
  label: string;
  value: ReactNode;
  sub: string;
  delta: ReactNode;
}) {
  return (
    <motion.div
      variants={item}
      className="rounded-xl border border-slate-800 bg-[#0d1322]/80 p-4 transition-colors hover:border-slate-700"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex size-9 items-center justify-center rounded-lg border border-emerald-500/20 bg-emerald-500/10 text-emerald-400">
          <Icon className="size-4" />
        </div>
        {delta}
      </div>
      <div className="mt-3 font-mono text-2xl font-semibold tabular-nums text-slate-100">
        {value}
      </div>
      <div className="mt-0.5 text-xs text-slate-500">{label}</div>
      <div className="mt-1 font-mono text-[10px] text-slate-600">{sub}</div>
    </motion.div>
  );
}

function PanelCard({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <motion.div
      variants={item}
      className="flex flex-col rounded-xl border border-slate-800 bg-[#0d1322]/80 p-4"
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-500">{title}</h2>
        {action}
      </div>
      <div className="min-h-0 flex-1">{children}</div>
    </motion.div>
  );
}

export function OverviewTab({
  meta,
  stats,
  onRefresh,
  onNavigate,
}: {
  meta: MetaConfig | null;
  stats: GatewayStats | null;
  onRefresh: () => void;
  onNavigate?: (tab: TabKey) => void;
}) {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [recent, setRecent] = useState<RequestLog[] | null>(null);
  const [kpi, setKpi] = useState<{ prev: GatewayStats | null; cur: GatewayStats | null }>({
    prev: null,
    cur: null,
  });

  /* KPI delta tracker — keeps the previous poll alongside the current one */
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const s = await api.getStats();
        if (!alive) return;
        setKpi((h) => ({ prev: h.cur ?? s, cur: s }));
      } catch {
        /* shell also polls stats */
      }
    };
    void load();
    const id = window.setInterval(load, 5000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  /* Providers + recent logs for this tab (fresh feed independent of shell poll) */
  useEffect(() => {
    let alive = true;
    const load = async () => {
      const [p, l] = await Promise.allSettled([api.getProviders(), api.getLogs(50)]);
      if (!alive) return;
      if (p.status === 'fulfilled') setProviders(p.value);
      if (l.status === 'fulfilled') setRecent(l.value.slice(0, 8));
    };
    void load();
    const id = window.setInterval(load, 15000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const clearCooldowns = async () => {
    try {
      const r = await api.clearCooldowns();
      toast.success(`Cooldowns cleared on ${r.cleared} key${r.cleared === 1 ? '' : 's'}`);
      onRefresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to clear cooldowns');
    }
  };

  const prev = kpi.prev;
  const topProviders = (providers ?? []).slice(0, 6);

  return (
    <motion.div variants={container} initial="hidden" animate="show" className="flex flex-col gap-4">
      {/* (a) KPI grid */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          icon={Activity}
          label="Total Requests 24h"
          value={stats ? fmtNum(stats.total_requests) : <Skeleton className="h-8 w-20" />}
          sub={`${fmtNum(stats?.total_tokens_in)} in / ${fmtNum(stats?.total_tokens_out)} out`}
          delta={<DeltaChip now={stats?.total_requests} before={prev?.total_requests} />}
        />
        <KpiCard
          icon={Coins}
          label="Tokens Processed"
          value={stats ? fmtNum(stats.total_tokens) : <Skeleton className="h-8 w-20" />}
          sub="prompt + completion tokens"
          delta={<DeltaChip now={stats?.total_tokens} before={prev?.total_tokens} />}
        />
        <KpiCard
          icon={Timer}
          label="Avg Latency"
          value={stats ? `${Math.round(stats.avg_latency_ms)}ms` : <Skeleton className="h-8 w-20" />}
          sub="end-to-end, last 24h"
          delta={
            <DeltaChip
              now={stats?.avg_latency_ms}
              before={prev?.avg_latency_ms}
              lowerIsBetter
              unit="ms"
            />
          }
        />
        <KpiCard
          icon={ShieldAlert}
          label="Error Rate"
          value={stats ? `${stats.error_rate.toFixed(2)}%` : <Skeleton className="h-8 w-20" />}
          sub={`${fmtNum(stats?.dead_models)} dead models`}
          delta={
            <DeltaChip now={stats?.error_rate} before={prev?.error_rate} lowerIsBetter unit="%" />
          }
        />
      </div>

      {/* (b) NovaFree engine banner */}
      <motion.div
        variants={item}
        className="rounded-xl bg-gradient-to-r from-emerald-500/60 via-emerald-400/20 to-emerald-500/60 p-[1px]"
      >
        <div className="flex flex-col gap-4 rounded-[11px] bg-[#0d1322] p-5 sm:flex-row sm:items-center">
          <div className="flex size-11 shrink-0 items-center justify-center rounded-lg border border-emerald-500/30 bg-emerald-500/15 text-emerald-400">
            <Sparkles className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold text-slate-100">NovaFree Engine</h2>
              <Badge
                variant="outline"
                className="border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
              >
                zero API keys
              </Badge>
              <Badge
                variant="outline"
                className="border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
              >
                automatic fallback
              </Badge>
              {meta?.fallback.spoof_model && (
                <Badge
                  variant="outline"
                  className="border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                >
                  identity spoofing
                </Badge>
              )}
              <Badge
                variant="outline"
                className="border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
              >
                always free
              </Badge>
            </div>
            <p className="mt-1.5 text-xs leading-relaxed text-slate-400">
              NovaRouter ships with built-in NovaFree models that work with no upstream account.
              When a provider fails, cools down, or has no key left, requests fall back to the
              NovaFree engine automatically — with identity spoofing so clients keep seeing the
              model they asked for.
            </p>
          </div>
          <Button
            onClick={() => onNavigate?.('console')}
            className="shrink-0 bg-emerald-500/90 text-emerald-950 hover:bg-emerald-400"
          >
            Try in Console
            <ArrowRight className="size-4" />
          </Button>
        </div>
      </motion.div>

      {/* (c) + (d) provider health + recent activity */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <PanelCard
          title="Provider Health"
          action={
            <button
              type="button"
              onClick={() => onNavigate?.('providers')}
              className="text-[11px] text-emerald-400 transition-colors hover:text-emerald-300"
            >
              Manage
            </button>
          }
        >
          {providers === null ? (
            <div className="flex flex-col gap-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-9 w-full" />
              ))}
            </div>
          ) : topProviders.length === 0 ? (
            <div className="py-6 text-center text-xs text-slate-500">No providers configured yet.</div>
          ) : (
            <div className="flex flex-col divide-y divide-slate-800/70">
              {topProviders.map((p) => (
                <div key={p.id} className="flex items-center gap-3 py-2 first:pt-0 last:pb-0">
                  <span
                    className="size-2 shrink-0 rounded-full"
                    style={{ backgroundColor: p.color || '#34d399' }}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium text-slate-200">{p.name}</div>
                    <div className="font-mono text-[10px] text-slate-500">
                      {p.ok_count}/{p.model_count} models ok · {p.key_count} keys
                    </div>
                  </div>
                  {p.cooling_count > 0 && (
                    <Badge
                      variant="outline"
                      className="border-amber-500/30 bg-amber-500/10 text-amber-300"
                    >
                      {p.cooling_count} cooling
                    </Badge>
                  )}
                  {p.session && (
                    <Badge
                      variant="outline"
                      className="border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                    >
                      signed in
                    </Badge>
                  )}
                  <Switch
                    checked={p.enabled}
                    disabled
                    aria-label={`${p.name} enabled`}
                    title="Enabled state is managed in the Providers tab"
                    className="data-[state=checked]:bg-emerald-500/70 data-[state=disabled]:opacity-60"
                  />
                </div>
              ))}
            </div>
          )}
        </PanelCard>

        <PanelCard
          title="Recent Activity"
          action={
            <button
              type="button"
              onClick={() => onNavigate?.('logs')}
              className="text-[11px] text-emerald-400 transition-colors hover:text-emerald-300"
            >
              View all
            </button>
          }
        >
          {recent === null ? (
            <div className="flex flex-col gap-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-9 w-full" />
              ))}
            </div>
          ) : recent.length === 0 ? (
            <div className="py-6 text-center text-xs text-slate-500">
              No requests logged yet — send one from the Playground.
            </div>
          ) : (
            <div className="flex flex-col divide-y divide-slate-800/70">
              {recent.map((log) => (
                <div key={log.id} className="flex items-center gap-2.5 py-2 first:pt-0 last:pb-0">
                  <Badge
                    variant="outline"
                    className={cx('shrink-0 font-mono', statusBadgeClass(log.status))}
                  >
                    {log.status}
                  </Badge>
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-300">
                    {log.model}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-slate-500">
                    {log.latency_ms}ms · {log.via}
                  </span>
                  <span className="w-14 shrink-0 text-right text-[10px] text-slate-600">
                    {timeAgo(log.ts)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </PanelCard>
      </div>

      {/* (e) Quick actions */}
      <motion.div variants={item} className="flex flex-wrap gap-3">
        <Button
          variant="outline"
          onClick={clearCooldowns}
          title="Clear cooldown state on all upstream keys"
          className="border-slate-800 bg-[#0d1322]/80 hover:border-emerald-500/40 hover:bg-emerald-500/10 hover:text-emerald-300"
        >
          <KeyRound className="size-4" />
          Clear Cooldowns
        </Button>
        <Button
          variant="outline"
          onClick={() => {
            toast.info('Health check requested — live provider and model status refresh automatically.');
            onRefresh();
          }}
          title="Run a gateway-wide health check"
          className="border-slate-800 bg-[#0d1322]/80 hover:border-emerald-500/40 hover:bg-emerald-500/10 hover:text-emerald-300"
        >
          <HeartPulse className="size-4" />
          Run Health Check
        </Button>
        <Button
          variant="outline"
          onClick={() => onNavigate?.('console')}
          title="Open the unified console — commands, chat and agent"
          className="border-slate-800 bg-[#0d1322]/80 hover:border-emerald-500/40 hover:bg-emerald-500/10 hover:text-emerald-300"
        >
          <TerminalSquare className="size-4" />
          Open Console
        </Button>
      </motion.div>
    </motion.div>
  );
}
