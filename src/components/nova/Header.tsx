'use client';

/**
 * Sticky top bar: tab title/subtitle, base URL chip with copy, auto-refresh toggle, manual refresh, clock.
 */
import { useEffect, useState } from 'react';
import { Check, Copy, RefreshCw } from 'lucide-react';
import { Switch } from '@/components/ui/switch';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { toast } from 'sonner';
import { cx } from '@/lib/format';
import type { TabKey } from './Dashboard';

const TAB_META: Record<TabKey, { title: string; subtitle: string }> = {
  overview: { title: 'Overview', subtitle: 'Gateway health at a glance' },
  console: { title: 'Nova Console', subtitle: 'Chat, commands and autonomous agent in one box' },
  providers: { title: 'Providers', subtitle: 'Upstream accounts, keys and sign-in' },
  models: { title: 'Models', subtitle: 'Catalogue, health and exposure' },
  routes: { title: 'Routes', subtitle: 'Fallback chains and resolution preview' },
  keys: { title: 'Client Keys', subtitle: 'Access tokens for API consumers' },
  storage: { title: 'Storage', subtitle: 'Buckets, files and backups' },
  analytics: { title: 'Analytics', subtitle: 'Traffic, latency and cache insights' },
  logs: { title: 'Request Logs', subtitle: 'Live gateway request stream' },
};

export function Header({
  activeTab,
  baseUrl,
  autoRefresh,
  onAutoRefreshChange,
  onRefresh,
  refreshing,
}: {
  activeTab: TabKey;
  baseUrl: string | null;
  autoRefresh: boolean;
  onAutoRefreshChange: (on: boolean) => void;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const copyBaseUrl = async () => {
    if (!baseUrl) return;
    try {
      await navigator.clipboard.writeText(baseUrl);
      setCopied(true);
      toast.success('Base URL copied to clipboard');
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error('Clipboard is not available');
    }
  };

  const meta = TAB_META[activeTab];

  return (
    <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center justify-between gap-3 border-b border-slate-800 bg-[#0d1322]/80 px-4 backdrop-blur md:px-6">
      {/* Left: current tab */}
      <div className="min-w-0">
        <h1 className="truncate text-sm font-semibold leading-tight text-slate-100">
          {meta.title}
        </h1>
        <p className="hidden truncate text-[11px] leading-tight text-slate-500 sm:block">
          {meta.subtitle}
        </p>
      </div>

      {/* Right: controls */}
      <div className="flex shrink-0 items-center gap-2 md:gap-3">
        {baseUrl && (
          <div className="hidden items-center gap-1.5 rounded-md border border-slate-800 bg-[#080c14] py-1 pl-2.5 pr-1 font-mono text-[11px] text-slate-400 md:flex">
            <span className="max-w-[220px] truncate lg:max-w-none">{baseUrl}</span>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={copyBaseUrl}
                  title="Copy base URL"
                  aria-label="Copy base URL"
                  className="inline-flex size-6 items-center justify-center rounded text-slate-500 transition-colors hover:bg-slate-800 hover:text-emerald-400"
                >
                  {copied ? (
                    <Check className="size-3.5 text-emerald-400" />
                  ) : (
                    <Copy className="size-3.5" />
                  )}
                </button>
              </TooltipTrigger>
              <TooltipContent className="border border-slate-700 bg-[#0d1322] text-slate-200">
                Copy OpenAI-compatible base URL
              </TooltipContent>
            </Tooltip>
          </div>
        )}

        <div className="flex items-center gap-2 rounded-md border border-slate-800 bg-[#080c14] px-2.5 py-1">
          <span className="relative flex size-2">
            {autoRefresh && (
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
            )}
            <span
              className={cx(
                'relative inline-flex size-2 rounded-full transition-colors',
                autoRefresh ? 'bg-emerald-400' : 'bg-slate-600'
              )}
            />
          </span>
          <span className="hidden text-[11px] font-medium text-slate-400 sm:inline">
            Auto-refresh
          </span>
          <Switch
            checked={autoRefresh}
            onCheckedChange={onAutoRefreshChange}
            aria-label="Toggle auto-refresh"
            className="data-[state=checked]:bg-emerald-500/80"
          />
        </div>

        <button
          type="button"
          onClick={onRefresh}
          title="Refresh data now"
          aria-label="Refresh data now"
          className="inline-flex size-8 items-center justify-center rounded-md border border-slate-800 bg-[#080c14] text-slate-400 transition-colors hover:border-emerald-500/40 hover:text-emerald-400"
        >
          <RefreshCw className={cx('size-4', refreshing && 'animate-spin')} />
        </button>

        <div className="hidden w-[70px] text-right font-mono text-xs tabular-nums text-slate-400 lg:block">
          {now === null ? '--:--:--' : new Date(now).toLocaleTimeString('en-US', { hour12: false })}
        </div>
      </div>
    </header>
  );
}
