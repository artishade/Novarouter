'use client';

/** ModelsTab — model catalogue with filters, sync, health checks and detail view. */
import { useCallback, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { toast } from 'sonner';
import {
  Brain,
  Check,
  Copy,
  Cpu,
  Eye,
  Gauge,
  HeartPulse,
  Loader2,
  RefreshCcw,
  Search,
  Wrench,
  X,
  Zap,
} from 'lucide-react';

import { api } from '@/lib/api';
import type { GatewayStats, MetaConfig, Model, Provider } from '@/lib/types';
import { fmtNum, timeAgo } from '@/lib/format';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';

type TestResult = { ok: boolean; status: string; latency_ms: number; message: string };

const STATUS_STYLES: Record<string, { dot: string; chip: string }> = {
  healthy: { dot: 'bg-emerald-400', chip: 'border-emerald-900/70 bg-emerald-950/40 text-emerald-300' },
  cooling: { dot: 'bg-amber-400', chip: 'border-amber-900/70 bg-amber-950/40 text-amber-300' },
  dead: { dot: 'bg-rose-400', chip: 'border-rose-900/70 bg-rose-950/40 text-rose-300' },
  unknown: { dot: 'bg-slate-500', chip: 'border-slate-700 bg-slate-900/60 text-slate-400' },
};

function copyText(text: string, message: string) {
  if (!navigator.clipboard?.writeText) {
    toast.error('Clipboard unavailable in this browser');
    return;
  }
  navigator.clipboard
    .writeText(text)
    .then(() => toast.success(message))
    .catch(() => toast.error('Clipboard unavailable in this browser'));
}

export function ModelsTab({
  meta,
  stats,
  onRefresh,
}: {
  meta: MetaConfig | null;
  stats: GatewayStats | null;
  onRefresh: () => void;
}) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [rows, setRows] = useState<Model[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [debounced, setDebounced] = useState('');
  const [providerId, setProviderId] = useState('all');
  const [status, setStatus] = useState('all');
  const [capability, setCapability] = useState('all');
  const [freeOnly, setFreeOnly] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [checking, setChecking] = useState(false);
  const [pinging, setPinging] = useState<Record<number, boolean>>({});
  const [detail, setDetail] = useState<Model | null>(null);

  useEffect(() => {
    api
      .getProviders()
      .then((rowsP) => setProviders(rowsP))
      .catch(() => toast.error('Failed to load providers for the filter'));
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(search), 350);
    return () => clearTimeout(t);
  }, [search]);

  const load = useCallback(async () => {
    try {
      const res = await api.getModels({
        search: debounced.trim() || undefined,
        provider_id: providerId !== 'all' ? Number(providerId) : undefined,
        status: status !== 'all' ? status : undefined,
        capability: capability !== 'all' ? capability : undefined,
        free: freeOnly || undefined,
      });
      setRows(res.rows);
      setTotal(res.total);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load models');
    } finally {
      setLoading(false);
    }
  }, [debounced, providerId, status, capability, freeOnly]);

  useEffect(() => {
    void load();
  }, [load]);

  const reload = useCallback(() => {
    void load();
  }, [load]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      const r = (await api.syncModels()) as { ok: boolean; synced: number; new_added: number };
      toast.success(`Catalogue synced — ${r.synced} examined, ${r.new_added} new added`);
      reload();
      onRefresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Catalogue sync failed');
    } finally {
      setSyncing(false);
    }
  };

  const pingModel = async (m: Model) => {
    setPinging((p) => ({ ...p, [m.id]: true }));
    try {
      const r = (await api.pingModel(m.id)) as TestResult & { http_status: number; detail: string };
      setRows((prev) =>
        prev.map((row) =>
          row.id === m.id
            ? {
                ...row,
                status: r.status as Model['status'],
                latency_ms: r.latency_ms,
                http_status: r.http_status,
                detail: r.detail,
                checked_at: Date.now(),
              }
            : row
        )
      );
      if (r.ok) {
        toast.success(`${m.exposed_id}: ${r.status} · ${r.latency_ms}ms`);
      } else {
        toast.error(`${m.exposed_id}: ${r.status}`, { description: r.detail || 'Unreachable' });
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : `Ping failed for ${m.exposed_id}`);
    } finally {
      setPinging((p) => ({ ...p, [m.id]: false }));
    }
  };

  const runHealthCheck = async () => {
    const candidates = rows.filter((m) => m.enabled && m.status !== 'dead');
    if (candidates.length === 0) {
      toast.info('No enabled models to check right now');
      return;
    }
    setChecking(true);
    const toastId = 'model-health-check';
    let okCount = 0;
    for (let i = 0; i < candidates.length; i++) {
      const m = candidates[i];
      toast.loading(`Health check ${i + 1}/${candidates.length} — ${m.exposed_id}`, { id: toastId });
      try {
        const r = (await api.pingModel(m.id)) as TestResult & { http_status: number; detail: string };
        setRows((prev) =>
          prev.map((row) =>
            row.id === m.id
              ? {
                  ...row,
                  status: r.status as Model['status'],
                  latency_ms: r.latency_ms,
                  http_status: r.http_status,
                  detail: r.detail,
                  checked_at: Date.now(),
                }
              : row
          )
        );
        if (r.ok) okCount += 1;
      } catch {
        /* continue sweep */
      }
    }
    toast.success(`Health check done — ${okCount}/${candidates.length} reachable`, { id: toastId });
    setChecking(false);
    onRefresh();
  };

  const toggleModel = async (m: Model, checked: boolean) => {
    setRows((prev) => prev.map((row) => (row.id === m.id ? { ...row, enabled: checked } : row)));
    try {
      await api.toggleModel(m.id, checked);
      toast.success(`${m.exposed_id} ${checked ? 'enabled' : 'disabled'}`);
      onRefresh();
    } catch (e) {
      setRows((prev) => prev.map((row) => (row.id === m.id ? { ...row, enabled: !checked } : row)));
      toast.error(e instanceof Error ? e.message : 'Failed to update model');
    }
  };

  const curlFor = (m: Model) =>
    `curl ${meta?.base_url ?? '/v1'}/chat/completions \\\n  -H "Authorization: Bearer $NOVA_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{\n    "model": "${m.exposed_id}",\n    "messages": [{ "role": "user", "content": "Hello, NovaRouter" }]\n  }'`;

  const activeFilters =
    (providerId !== 'all' ? 1 : 0) +
    (status !== 'all' ? 1 : 0) +
    (capability !== 'all' ? 1 : 0) +
    (freeOnly ? 1 : 0);

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold text-slate-100">Models</h2>
        <Badge variant="outline" className="border-slate-700 text-slate-400">
          {total} shown
        </Badge>
        {stats && (
          <Badge variant="outline" className="border-emerald-900/70 bg-emerald-950/30 text-emerald-300">
            {stats.healthy_models} healthy · {stats.dead_models} dead
          </Badge>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-slate-600" aria-hidden />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search models..."
              aria-label="Search models"
              className="h-9 w-48 border-slate-800 bg-black/30 pl-8 text-xs"
            />
          </div>
          <Select value={providerId} onValueChange={setProviderId}>
            <SelectTrigger className="h-9 w-[150px] border-slate-800 bg-black/30 text-xs" aria-label="Filter by provider">
              <SelectValue placeholder="Provider" />
            </SelectTrigger>
            <SelectContent className="border-slate-800 bg-[#0d1322]">
              <SelectItem value="all" className="text-xs">All providers</SelectItem>
              {providers.map((p) => (
                <SelectItem key={p.id} value={String(p.id)} className="text-xs">
                  {p.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger className="h-9 w-[120px] border-slate-800 bg-black/30 text-xs" aria-label="Filter by status">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent className="border-slate-800 bg-[#0d1322]">
              <SelectItem value="all" className="text-xs">Any status</SelectItem>
              <SelectItem value="healthy" className="text-xs">Healthy</SelectItem>
              <SelectItem value="cooling" className="text-xs">Cooling</SelectItem>
              <SelectItem value="dead" className="text-xs">Dead</SelectItem>
              <SelectItem value="unknown" className="text-xs">Unknown</SelectItem>
            </SelectContent>
          </Select>
          <Select value={capability} onValueChange={setCapability}>
            <SelectTrigger className="h-9 w-[130px] border-slate-800 bg-black/30 text-xs" aria-label="Filter by capability">
              <SelectValue placeholder="Capability" />
            </SelectTrigger>
            <SelectContent className="border-slate-800 bg-[#0d1322]">
              <SelectItem value="all" className="text-xs">Any capability</SelectItem>
              <SelectItem value="tools" className="text-xs">Tools</SelectItem>
              <SelectItem value="vision" className="text-xs">Vision</SelectItem>
              <SelectItem value="reasoning" className="text-xs">Reasoning</SelectItem>
            </SelectContent>
          </Select>
          <label className="flex h-9 items-center gap-2 rounded-md border border-slate-800 bg-black/30 px-2.5 text-xs text-slate-300">
            <Switch
              checked={freeOnly}
              onCheckedChange={setFreeOnly}
              aria-label="Show free models only"
            />
            Free only
          </label>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void handleSync()}
            disabled={syncing}
            className="border-slate-700 text-slate-300 hover:bg-slate-800"
          >
            {syncing ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <RefreshCcw className="size-3.5" aria-hidden />}
            Sync Catalogue
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void runHealthCheck()}
            disabled={checking}
            className="border-emerald-800/70 text-emerald-300 hover:bg-emerald-950/40 hover:text-emerald-200"
          >
            {checking ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <HeartPulse className="size-3.5" aria-hidden />}
            Health check
          </Button>
        </div>
      </div>

      {/* Grid */}
      {loading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 9 }).map((_, i) => (
            <Skeleton key={i} className="h-48 rounded-xl bg-slate-800/50" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-800 bg-[#0d1322]/60 p-10 text-center text-sm text-slate-500">
          {activeFilters > 0 || debounced
            ? 'No models match the current filters — try clearing them.'
            : 'No models in the catalogue yet — run Sync Catalogue.'}
        </div>
      ) : (
        <div className="max-h-[calc(100vh-330px)] overflow-y-auto pr-1">
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {rows.map((m, i) => {
              const st = STATUS_STYLES[m.status] ?? STATUS_STYLES.unknown;
              return (
                <motion.div
                  key={m.id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.22, delay: Math.min(i * 0.03, 0.3) }}
                >
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => setDetail(m)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') setDetail(m);
                    }}
                    aria-label={`Open details for ${m.display_name}`}
                    className="cursor-pointer rounded-xl border border-slate-800 bg-[#0d1322]/80 p-4 transition hover:border-slate-600 hover:bg-[#0d1322]"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-slate-100">{m.display_name}</p>
                        <p className="truncate font-mono text-[11px] text-slate-500">{m.exposed_id}</p>
                      </div>
                      <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                        <Switch
                          checked={m.enabled}
                          onCheckedChange={(c) => void toggleModel(m, c)}
                          aria-label={`Toggle model ${m.exposed_id}`}
                        />
                      </div>
                    </div>

                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <span className="flex items-center gap-1.5 rounded-md border border-slate-800 bg-black/30 px-1.5 py-0.5 text-[10px] text-slate-400">
                        <span
                          className="size-1.5 rounded-full"
                          style={{ backgroundColor: m.provider_color || '#64748b' }}
                          aria-hidden
                        />
                        {m.provider_name ?? 'unknown'}
                      </span>
                      <Badge variant="outline" className={`px-1.5 text-[10px] ${st.chip}`}>
                        <span className={`size-1.5 rounded-full ${st.dot}`} aria-hidden />
                        {m.status}
                      </Badge>
                      {m.is_free && (
                        <Badge className="border-emerald-800/60 bg-emerald-950/60 px-1.5 text-[10px] text-emerald-300">
                          FREE
                        </Badge>
                      )}
                    </div>

                    <div className="mt-2.5 flex flex-wrap items-center gap-1.5 font-mono text-[10px] text-slate-500">
                      <span className="rounded border border-slate-800 bg-black/30 px-1.5 py-0.5">
                        {fmtNum(m.context_length)} ctx
                      </span>
                      <span className="rounded border border-slate-800 bg-black/30 px-1.5 py-0.5">
                        {fmtNum(m.max_output)} out
                      </span>
                      {m.latency_ms > 0 && (
                        <span className="flex items-center gap-0.5 rounded border border-slate-800 bg-black/30 px-1.5 py-0.5 text-emerald-300/90">
                          <Zap className="size-2.5" aria-hidden />
                          {m.latency_ms}ms
                        </span>
                      )}
                    </div>

                    {m.price_in > 0 && (
                      <p className="mt-1.5 text-[11px] text-slate-500">
                        ${m.price_in}/M in · ${m.price_out}/M out
                      </p>
                    )}

                    <div className="mt-2.5 flex items-center gap-2.5">
                      <span
                        className={m.capabilities?.tools ? 'text-emerald-300/90' : 'text-slate-700'}
                        title={m.capabilities?.tools ? 'Tool calling supported' : 'No tool calling'}
                      >
                        <Wrench className="size-3.5" aria-hidden />
                        <span className="sr-only">tools</span>
                      </span>
                      <span
                        className={m.capabilities?.vision ? 'text-emerald-300/90' : 'text-slate-700'}
                        title={m.capabilities?.vision ? 'Vision supported' : 'No vision'}
                      >
                        <Eye className="size-3.5" aria-hidden />
                        <span className="sr-only">vision</span>
                      </span>
                      <span
                        className={m.capabilities?.reasoning ? 'text-emerald-300/90' : 'text-slate-700'}
                        title={m.capabilities?.reasoning ? 'Reasoning supported' : 'No reasoning'}
                      >
                        <Brain className="size-3.5" aria-hidden />
                        <span className="sr-only">reasoning</span>
                      </span>
                      <div className="ml-auto" onClick={(e) => e.stopPropagation()}>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={pinging[m.id]}
                          onClick={() => void pingModel(m)}
                          className="h-7 border-slate-700 px-2 text-[11px] text-slate-300 hover:bg-slate-800"
                          aria-label={`Ping ${m.exposed_id}`}
                        >
                          {pinging[m.id] ? (
                            <Loader2 className="size-3 animate-spin" aria-hidden />
                          ) : (
                            <Gauge className="size-3" aria-hidden />
                          )}
                          Ping
                        </Button>
                      </div>
                    </div>

                    {m.description && (
                      <p className="mt-2 line-clamp-2 text-[11px] leading-snug text-slate-500">{m.description}</p>
                    )}
                  </div>
                </motion.div>
              );
            })}
          </div>
        </div>
      )}

      {/* Detail dialog */}
      <Dialog open={!!detail} onOpenChange={(v) => !v && setDetail(null)}>
        <DialogContent className="max-w-lg border-slate-800 bg-[#0d1322]">
          {detail && (
            <>
              <DialogHeader>
                <DialogTitle className="flex flex-wrap items-center gap-2">
                  <span>{detail.display_name}</span>
                  {detail.is_free && (
                    <Badge className="border-emerald-800/60 bg-emerald-950/60 text-[10px] text-emerald-300">
                      FREE
                    </Badge>
                  )}
                  <Badge
                    variant="outline"
                    className={`text-[10px] ${(STATUS_STYLES[detail.status] ?? STATUS_STYLES.unknown).chip}`}
                  >
                    <span
                      className={`size-1.5 rounded-full ${(STATUS_STYLES[detail.status] ?? STATUS_STYLES.unknown).dot}`}
                      aria-hidden
                    />
                    {detail.status}
                  </Badge>
                </DialogTitle>
                <DialogDescription className="font-mono text-[11px] text-slate-500">
                  {detail.exposed_id} · via {detail.provider_name ?? 'unknown'}
                </DialogDescription>
              </DialogHeader>

              <div className="space-y-3">
                {detail.description && (
                  <p className="text-xs leading-relaxed text-slate-400">{detail.description}</p>
                )}

                <div className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-lg border border-slate-800 bg-black/20 p-3 text-[11px]">
                  <span className="text-slate-500">Context</span>
                  <span className="text-right font-mono text-slate-300">{fmtNum(detail.context_length)}</span>
                  <span className="text-slate-500">Max output</span>
                  <span className="text-right font-mono text-slate-300">{fmtNum(detail.max_output)}</span>
                  <span className="text-slate-500">Latency</span>
                  <span className="text-right font-mono text-slate-300">
                    {detail.latency_ms > 0 ? `${detail.latency_ms}ms` : '—'}
                  </span>
                  <span className="text-slate-500">Last HTTP</span>
                  <span className="text-right font-mono text-slate-300">
                    {detail.http_status > 0 ? detail.http_status : '—'}
                  </span>
                  <span className="text-slate-500">Pricing</span>
                  <span className="text-right font-mono text-slate-300">
                    {detail.price_in > 0
                      ? `$${detail.price_in}/M in · $${detail.price_out}/M out`
                      : 'free'}
                  </span>
                  <span className="text-slate-500">Checked</span>
                  <span className="text-right font-mono text-slate-300">{timeAgo(detail.checked_at)}</span>
                  {detail.detail && (
                    <>
                      <span className="text-slate-500">Detail</span>
                      <span className="truncate text-right text-slate-400" title={detail.detail}>
                        {detail.detail}
                      </span>
                    </>
                  )}
                </div>

                <div className="flex items-center gap-3 text-[11px] text-slate-400">
                  <span className={detail.capabilities?.tools ? 'text-emerald-300' : 'text-slate-600'}>
                    <Wrench className="mr-1 inline size-3.5" aria-hidden />
                    tools
                  </span>
                  <span className={detail.capabilities?.vision ? 'text-emerald-300' : 'text-slate-600'}>
                    <Eye className="mr-1 inline size-3.5" aria-hidden />
                    vision
                  </span>
                  <span className={detail.capabilities?.reasoning ? 'text-emerald-300' : 'text-slate-600'}>
                    <Brain className="mr-1 inline size-3.5" aria-hidden />
                    reasoning
                  </span>
                  <span className="ml-auto flex items-center gap-1.5">
                    <Cpu className="size-3.5 text-slate-600" aria-hidden />
                    <Switch
                      checked={detail.enabled}
                      onCheckedChange={(c) => {
                        void toggleModel(detail, c);
                        setDetail({ ...detail, enabled: c });
                      }}
                      aria-label={`Toggle model ${detail.exposed_id}`}
                    />
                  </span>
                </div>

                <Separator className="bg-slate-800" />

                <div>
                  <div className="mb-1.5 flex items-center justify-between">
                    <Label className="text-[10px] uppercase tracking-wider text-slate-500">
                      cURL example
                    </Label>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6 text-slate-500 hover:text-emerald-300"
                      onClick={() => copyText(curlFor(detail), 'cURL command copied')}
                      aria-label="Copy cURL example"
                    >
                      <Copy className="size-3.5" aria-hidden />
                    </Button>
                  </div>
                  <pre className="max-h-44 overflow-auto rounded-lg border border-slate-800 bg-black/40 p-3 font-mono text-[11px] leading-relaxed text-emerald-200/90">
                    {curlFor(detail)}
                  </pre>
                  <p className="mt-1.5 flex items-center gap-1 text-[11px] text-slate-600">
                    <Check className="size-3 text-emerald-500" aria-hidden />
                    Works with any OpenAI SDK — point base_url at {meta?.base_url ?? '/v1'} and use a NovaRouter
                    client key.
                  </p>
                </div>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* reset hint */}
      {(activeFilters > 0 || debounced) && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setSearch('');
            setProviderId('all');
            setStatus('all');
            setCapability('all');
            setFreeOnly(false);
          }}
          className="text-[11px] text-slate-500 hover:text-slate-200"
        >
          <X className="size-3" aria-hidden />
          Clear filters
        </Button>
      )}
    </div>
  );
}
