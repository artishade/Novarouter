'use client';

/**
 * Expandable icon + label navigation rail.
 * Groups: OPERATE / GATEWAY / INFRA / INSIGHT. Auto-collapses to an icon rail on <lg screens.
 */
import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import {
  BarChart3,
  Boxes,
  ChevronsLeft,
  ChevronsRight,
  GitBranch,
  HardDrive,
  KeyRound,
  LayoutDashboard,
  ScrollText,
  Server,
  Sparkles,
  type LucideIcon,
} from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cx } from '@/lib/format';
import type { GatewayStats } from '@/lib/types';
import type { TabKey } from './Dashboard';

interface NavItem {
  key: TabKey;
  label: string;
  icon: LucideIcon;
}

const GROUPS: Array<{ label: string; items: NavItem[] }> = [
  {
    label: 'Operate',
    items: [
      { key: 'overview', label: 'Overview', icon: LayoutDashboard },
      { key: 'console', label: 'Nova Console', icon: Sparkles },
    ],
  },
  {
    label: 'Gateway',
    items: [
      { key: 'providers', label: 'Providers', icon: Server },
      { key: 'models', label: 'Models', icon: Boxes },
      { key: 'routes', label: 'Routes', icon: GitBranch },
      { key: 'keys', label: 'Client Keys', icon: KeyRound },
    ],
  },
  {
    label: 'Infra',
    items: [{ key: 'storage', label: 'Storage', icon: HardDrive }],
  },
  {
    label: 'Insight',
    items: [
      { key: 'analytics', label: 'Analytics', icon: BarChart3 },
      { key: 'logs', label: 'Request Logs', icon: ScrollText },
    ],
  },
];

const DESKTOP_MQ = '(min-width: 1024px)';

export function Sidebar({
  activeTab,
  onNavigate,
  expanded,
  onToggle,
  stats,
}: {
  activeTab: TabKey;
  onNavigate: (tab: TabKey) => void;
  expanded: boolean;
  onToggle: () => void;
  stats: GatewayStats | null;
}) {
  const [isDesktop, setIsDesktop] = useState(false);

  useEffect(() => {
    const mql = window.matchMedia(DESKTOP_MQ);
    const onChange = () => setIsDesktop(mql.matches);
    onChange();
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, []);

  const expandedNow = expanded && isDesktop;
  const healthy = !!stats;

  return (
    <aside
      className={cx(
        'sticky top-0 z-30 flex h-screen shrink-0 flex-col border-r border-slate-800 bg-[#0a0f1c]/90 transition-[width] duration-200 ease-out',
        expandedNow ? 'w-60' : 'w-14'
      )}
    >
      {/* Brand + collapse toggle */}
      <div
        className={cx(
          'flex h-14 shrink-0 items-center border-b border-slate-800',
          expandedNow ? 'justify-between px-3' : 'justify-center px-0'
        )}
      >
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-emerald-500/30 bg-emerald-500/15 font-mono text-lg font-bold text-emerald-400">
            N
          </div>
          {expandedNow && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.2 }}
              className="min-w-0"
            >
              <div className="truncate text-sm font-semibold tracking-tight text-slate-100">
                NovaRouter
              </div>
              <div className="text-[10px] uppercase tracking-widest text-slate-500">
                Gateway Console
              </div>
            </motion.div>
          )}
        </div>
        {expandedNow && (
          <button
            type="button"
            onClick={onToggle}
            title="Collapse sidebar"
            aria-label="Collapse sidebar"
            className="inline-flex size-7 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-800/70 hover:text-slate-300"
          >
            <ChevronsLeft className="size-4" />
          </button>
        )}
      </div>

      {/* Nav groups */}
      <nav className="flex-1 overflow-y-auto overflow-x-hidden px-2 py-3">
        {GROUPS.map((group, gi) => (
          <div key={group.label} className={gi > 0 ? 'mt-5' : ''}>
            {expandedNow ? (
              <div className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-widest text-slate-600">
                {group.label}
              </div>
            ) : (
              gi > 0 && <div className="mx-3 mb-2 border-t border-slate-800/80" />
            )}
            <div className="flex flex-col gap-0.5">
              {group.items.map((item) => {
                const active = item.key === activeTab;
                const button = (
                  <button
                    type="button"
                    onClick={() => onNavigate(item.key)}
                    title={expandedNow ? undefined : item.label}
                    aria-label={item.label}
                    aria-current={active ? 'page' : undefined}
                    className={cx(
                      'group relative flex w-full items-center rounded-lg text-sm transition-colors',
                      expandedNow ? 'gap-3 px-3 py-2' : 'h-10 justify-center px-0 py-0',
                      active
                        ? 'bg-emerald-500/10 text-emerald-300'
                        : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
                    )}
                  >
                    {active && (
                      <span className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-full bg-emerald-400" />
                    )}
                    <item.icon
                      className={cx(
                        'size-4 shrink-0',
                        active ? 'text-emerald-400' : 'text-slate-500 group-hover:text-slate-300'
                      )}
                    />
                    {expandedNow && <span className="truncate">{item.label}</span>}
                  </button>
                );
                return expandedNow ? (
                  <div key={item.key}>{button}</div>
                ) : (
                  <Tooltip key={item.key}>
                    <TooltipTrigger asChild>{button}</TooltipTrigger>
                    <TooltipContent side="right" className="border border-slate-700 bg-[#0d1322] text-slate-200">
                      {item.label}
                    </TooltipContent>
                  </Tooltip>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Gateway health mini-card */}
      <div className="shrink-0 p-2 pb-3">
        {expandedNow ? (
          <div className="rounded-xl border border-slate-800 bg-[#0d1322] p-3">
            <div className="flex items-center gap-2">
              <span className="relative flex size-2">
                {healthy && (
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
                )}
                <span
                  className={cx(
                    'relative inline-flex size-2 rounded-full',
                    healthy ? 'bg-emerald-400' : 'bg-slate-600'
                  )}
                />
              </span>
              <span className="text-xs font-medium text-slate-300">
                {healthy ? 'Gateway online' : 'Connecting'}
              </span>
            </div>
            <div className="mt-1.5 font-mono text-[10px] text-slate-500">
              NovaRouter v2.0
            </div>
          </div>
        ) : (
          <div className="flex justify-center py-1.5">
            <span className="relative flex size-2">
              {healthy && (
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
              )}
              <span
                className={cx(
                  'relative inline-flex size-2 rounded-full',
                  healthy ? 'bg-emerald-400' : 'bg-slate-600'
                )}
              />
            </span>
          </div>
        )}
      </div>

      {/* Expand affordance when collapsed on desktop */}
      {!expandedNow && isDesktop && (
        <button
          type="button"
          onClick={onToggle}
          title="Expand sidebar"
          aria-label="Expand sidebar"
          className="flex h-9 items-center justify-center border-t border-slate-800/70 text-slate-500 transition-colors hover:text-emerald-400"
        >
          <ChevronsRight className="size-4" />
        </button>
      )}
    </aside>
  );
}
