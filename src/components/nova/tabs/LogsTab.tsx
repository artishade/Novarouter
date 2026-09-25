'use client';

/**
 * Request logs tab: searchable, filterable log table with expandable error rows.
 * Data is polled by the Dashboard shell; this tab also exposes a manual refresh.
 */
import { Fragment, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, RefreshCw, Search } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cx, fmtClock, fmtDate, fmtNum, timeAgo } from '@/lib/format';
import type { RequestLog } from '@/lib/types';

const STATUS_CLASS: (status: number) => string = (status) => {
  if (status >= 200 && status < 300) return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300';
  if (status < 400) return 'border-teal-500/30 bg-teal-500/10 text-teal-300';
  if (status < 500) return 'border-amber-500/30 bg-amber-500/10 text-amber-300';
  return 'border-rose-500/30 bg-rose-500/10 text-rose-300';
};

const VIA_CLASS: Record<string, string> = {
  upstream: 'border-sky-500/30 bg-sky-500/10 text-sky-300',
  'nova-engine': 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  fallback: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  cache: 'border-teal-500/30 bg-teal-500/10 text-teal-300',
};

const METHOD_RE = /^(GET|POST|PUT|PATCH|DELETE)\s+/;

const SCROLL_CSS = `
.nova-log-scroll::-webkit-scrollbar { width: 8px; height: 8px; }
.nova-log-scroll::-webkit-scrollbar-track { background: transparent; }
.nova-log-scroll::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 8px; }
.nova-log-scroll::-webkit-scrollbar-thumb:hover { background: #334155; }
.nova-log-scroll { scrollbar-width: thin; scrollbar-color: #1e293b transparent; }
`;

function latencyClass(ms: number): string {
  if (ms >= 2000) return 'text-rose-400';
  if (ms >= 800) return 'text-amber-300';
  return 'text-slate-300';
}

export function LogsTab({ logs, onRefresh }: { logs: RequestLog[]; onRefresh: () => void }) {
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [viaFilter, setViaFilter] = useState('all');
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return logs.filter((l) => {
      if (statusFilter === '2xx' && !(l.status >= 200 && l.status < 300)) return false;
      if (statusFilter === '4xx' && !(l.status >= 400 && l.status < 500)) return false;
      if (statusFilter === '5xx' && l.status < 500) return false;
      if (viaFilter !== 'all' && l.via !== viaFilter) return false;
      if (q) {
        const hay = `${l.model} ${l.provider_name} ${l.error} ${l.upstream_model}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [logs, search, statusFilter, viaFilter]);

  const refresh = () => {
    setBusy(true);
    onRefresh();
    window.setTimeout(() => setBusy(false), 600);
  };

  const toggleRow = (id: number) => setExpandedId((cur) => (cur === id ? null : id));

  return (
    <div className="flex flex-col gap-3">
      <style>{SCROLL_CSS}</style>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1 sm:max-w-xs">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-slate-500" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter by model, provider, error"
            aria-label="Search request logs"
            className="h-8 border-slate-800 bg-[#080c14] pl-8 font-mono text-xs placeholder:text-slate-600"
          />
        </div>

        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger
            size="sm"
            aria-label="Filter by HTTP status class"
            className="w-[110px] border-slate-800 bg-[#080c14] font-mono text-xs"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="border-slate-800 bg-[#0d1322] text-slate-200">
            <SelectItem value="all" className="font-mono text-xs">all status</SelectItem>
            <SelectItem value="2xx" className="font-mono text-xs">2xx success</SelectItem>
            <SelectItem value="4xx" className="font-mono text-xs">4xx client</SelectItem>
            <SelectItem value="5xx" className="font-mono text-xs">5xx server</SelectItem>
          </SelectContent>
        </Select>

        <Select value={viaFilter} onValueChange={setViaFilter}>
          <SelectTrigger
            size="sm"
            aria-label="Filter by routing path"
            className="w-[150px] border-slate-800 bg-[#080c14] font-mono text-xs"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="border-slate-800 bg-[#0d1322] text-slate-200">
            <SelectItem value="all" className="font-mono text-xs">all routes</SelectItem>
            <SelectItem value="upstream" className="font-mono text-xs">upstream</SelectItem>
            <SelectItem value="nova-engine" className="font-mono text-xs">nova-engine</SelectItem>
            <SelectItem value="fallback" className="font-mono text-xs">fallback</SelectItem>
            <SelectItem value="cache" className="font-mono text-xs">cache</SelectItem>
          </SelectContent>
        </Select>

        <span className="font-mono text-[11px] text-slate-500">
          {filtered.length} of {logs.length} requests
        </span>

        <Button
          variant="outline"
          size="sm"
          onClick={refresh}
          title="Reload the request log"
          aria-label="Reload the request log"
          className="ml-auto border-slate-800 bg-[#0d1322]/80 font-mono text-xs hover:border-emerald-500/40 hover:text-emerald-300"
        >
          <RefreshCw className={cx('size-3.5', busy && 'animate-spin')} />
          Refresh
        </Button>
      </div>

      {/* Log table */}
      <div className="nova-log-scroll max-h-[70vh] overflow-y-auto rounded-xl border border-slate-800 bg-[#0d1322]/80">
        <Table>
          <TableHeader className="sticky top-0 z-10 border-slate-800 bg-[#0d1322]/95 backdrop-blur">
            <TableRow className="border-slate-800 hover:bg-transparent">
              <TableHead className="text-[10px] uppercase tracking-widest text-slate-500">Time</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-slate-500">Endpoint</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-slate-500">Model</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-slate-500">Provider</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-slate-500">Via</TableHead>
              <TableHead className="text-[10px] uppercase tracking-widest text-slate-500">Status</TableHead>
              <TableHead className="text-right text-[10px] uppercase tracking-widest text-slate-500">Latency</TableHead>
              <TableHead className="text-right text-[10px] uppercase tracking-widest text-slate-500">Tokens</TableHead>
              <TableHead className="w-8" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow className="border-slate-800/60 hover:bg-transparent">
                <TableCell colSpan={9} className="py-10 text-center text-xs text-slate-500">
                  No requests match the current filters.
                </TableCell>
              </TableRow>
            ) : (
              filtered.map((log) => {
                const match = log.endpoint.match(METHOD_RE);
                const method = match ? match[1] : null;
                const path = match ? log.endpoint.slice(match[0].length) : log.endpoint;
                const expanded = expandedId === log.id;
                return (
                  <Fragment key={log.id}>
                    <TableRow
                      className={cx(
                        'group border-slate-800/60',
                        expanded ? 'bg-[#080c14]/60' : undefined
                      )}
                    >
                    <TableCell className="font-mono text-[11px] text-slate-400">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span>{fmtClock(log.ts)}</span>
                        </TooltipTrigger>
                        <TooltipContent className="border border-slate-700 bg-[#0d1322] font-mono text-slate-200">
                          {timeAgo(log.ts)}
                        </TooltipContent>
                      </Tooltip>
                    </TableCell>
                    <TableCell className="max-w-[220px] font-mono text-[11px] text-slate-300">
                      <div className="flex items-center gap-1.5">
                        {method && (
                          <span
                            className={cx(
                              'rounded px-1 py-0.5 text-[9px] font-semibold',
                              'bg-slate-800/80 text-slate-400'
                            )}
                          >
                            {method}
                          </span>
                        )}
                        <span className="truncate">{path}</span>
                      </div>
                    </TableCell>
                    <TableCell className="max-w-[200px] font-mono text-[11px] text-slate-200">
                      <span className="block truncate" title={log.model}>
                        {log.model}
                      </span>
                    </TableCell>
                    <TableCell className="max-w-[130px] truncate text-[11px] text-slate-400">
                      {log.provider_name || '—'}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap items-center gap-1">
                        <Badge
                          variant="outline"
                          className={cx('font-mono text-[10px]', VIA_CLASS[log.via] ?? 'border-slate-700 bg-slate-800/40 text-slate-400')}
                        >
                          {log.via}
                        </Badge>
                        {log.spoofed && (
                          <Badge
                            variant="outline"
                            className="border-amber-500/30 bg-amber-500/10 font-mono text-[10px] text-amber-300"
                            title="Response model identity was spoofed to the requested model"
                          >
                            identity spoofed
                          </Badge>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={cx('font-mono', STATUS_CLASS(log.status))}
                      >
                        {log.status}
                      </Badge>
                    </TableCell>
                    <TableCell className={cx('text-right font-mono text-[11px] tabular-nums', latencyClass(log.latency_ms))}>
                      {log.latency_ms}ms
                    </TableCell>
                    <TableCell className="text-right font-mono text-[11px] tabular-nums text-slate-400">
                      {fmtNum(log.tokens_in)} / {fmtNum(log.tokens_out)}
                    </TableCell>
                    <TableCell>
                      <button
                        type="button"
                        onClick={() => toggleRow(log.id)}
                        title={expanded ? 'Hide details' : 'Show request details'}
                        aria-label={expanded ? 'Hide details' : 'Show request details'}
                        aria-expanded={expanded}
                        className="inline-flex size-6 items-center justify-center rounded text-slate-500 transition-colors hover:bg-slate-800 hover:text-emerald-400"
                      >
                        {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
                      </button>
                    </TableCell>
                  </TableRow>
                    <TableRow className="border-slate-800/60 hover:bg-transparent">
                      <TableCell colSpan={9} className="bg-[#080c14]/50 px-4 py-3">
                        <div className="flex flex-col gap-1.5">
                          {log.error ? (
                            <div className="break-all rounded-md border border-rose-500/20 bg-rose-500/5 px-2.5 py-1.5 font-mono text-[11px] text-rose-300">
                              {log.error}
                            </div>
                          ) : (
                            <div className="text-[11px] text-slate-500">No error recorded for this request.</div>
                          )}
                          <div className="flex flex-wrap gap-x-5 gap-y-1 font-mono text-[10px] text-slate-500">
                            <span>upstream: {log.upstream_model || '—'}</span>
                            <span>client: {log.client_name || '—'}</span>
                            <span>ts: {fmtDate(log.ts)}</span>
                            <span>log id: #{log.id}</span>
                            <span>stage via: {log.via}</span>
                          </div>
                        </div>
                      </TableCell>
                    </TableRow>
                  </Fragment>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
