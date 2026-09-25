'use client';

/**
 * NovaRouter dashboard shell — tab state, shared data polling, layout.
 * Owns: Sidebar | Header + active tab + StatusFooter.
 */
import { useCallback, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { api } from '@/lib/api';
import type { GatewayStats, MetaConfig, Model, Provider, RequestLog } from '@/lib/types';
import { Sidebar } from './Sidebar';
import { Header } from './Header';
import { StatusFooter } from './StatusFooter';
import { OverviewTab } from './tabs/OverviewTab';
import { AnalyticsTab } from './tabs/AnalyticsTab';
import { LogsTab } from './tabs/LogsTab';
import { ConsoleTab } from './tabs/ConsoleTab';
import { ProvidersTab } from './tabs/ProvidersTab';
import { ModelsTab } from './tabs/ModelsTab';
import { RoutesTab } from './tabs/RoutesTab';
import { KeysTab } from './tabs/KeysTab';
import { StorageTab } from './tabs/StorageTab';

export type TabKey =
  | 'overview'
  | 'console'
  | 'providers'
  | 'models'
  | 'routes'
  | 'keys'
  | 'storage'
  | 'analytics'
  | 'logs';

const SIDEBAR_KEY = 'nova.sidebar.expanded';
const POLL_MS = 5000;

export function Dashboard() {
  const [activeTab, setActiveTab] = useState<TabKey>('overview');
  const [sidebarExpanded, setSidebarExpanded] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const [meta, setMeta] = useState<MetaConfig | null>(null);
  const [stats, setStats] = useState<GatewayStats | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [logs, setLogs] = useState<RequestLog[]>([]);

  const loadData = useCallback(async () => {
    setRefreshing(true);
    const [metaR, statsR, providersR, modelsR, logsR] = await Promise.allSettled([
      api.getMeta(),
      api.getStats(),
      api.getProviders(),
      api.getModels(),
      api.getLogs(100),
    ]);
    if (metaR.status === 'fulfilled') setMeta(metaR.value);
    if (statsR.status === 'fulfilled') setStats(statsR.value);
    if (providersR.status === 'fulfilled') setProviders(providersR.value);
    if (modelsR.status === 'fulfilled') setModels(modelsR.value.rows ?? []);
    if (logsR.status === 'fulfilled') setLogs(logsR.value);
    setRefreshing(false);
  }, []);

  /* Initial load (deferred a tick so state updates stay out of the effect pass) */
  useEffect(() => {
    const t = window.setTimeout(() => void loadData(), 0);
    return () => window.clearTimeout(t);
  }, [loadData]);

  /* Poll every 5s while auto-refresh is on */
  useEffect(() => {
    if (!autoRefresh) return;
    const id = window.setInterval(() => {
      void loadData();
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [autoRefresh, loadData]);

  /* Restore sidebar preference after mount (SSR-safe, deferred) */
  useEffect(() => {
    const t = window.setTimeout(() => {
      try {
        const v = window.localStorage.getItem(SIDEBAR_KEY);
        if (v !== null) setSidebarExpanded(v === '1');
      } catch {
        /* storage unavailable */
      }
    }, 0);
    return () => window.clearTimeout(t);
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_KEY, sidebarExpanded ? '1' : '0');
    } catch {
      /* storage unavailable */
    }
  }, [sidebarExpanded]);

  const renderTab = () => {
    switch (activeTab) {
      case 'overview':
        return (
          <OverviewTab
            meta={meta}
            stats={stats}
            onRefresh={loadData}
            onNavigate={setActiveTab}
          />
        );
      case 'console':
        return <ConsoleTab meta={meta} stats={stats} models={models} onRefresh={loadData} />;
      case 'providers':
        return <ProvidersTab meta={meta} stats={stats} onRefresh={loadData} />;
      case 'models':
        return <ModelsTab meta={meta} stats={stats} onRefresh={loadData} />;
      case 'routes':
        return <RoutesTab meta={meta} stats={stats} onRefresh={loadData} />;
      case 'keys':
        return <KeysTab meta={meta} stats={stats} onRefresh={loadData} />;
      case 'storage':
        return <StorageTab meta={meta} stats={stats} onRefresh={loadData} />;
      case 'analytics':
        return <AnalyticsTab meta={meta} stats={stats} onRefresh={loadData} />;
      case 'logs':
        return <LogsTab logs={logs} onRefresh={loadData} />;
      default:
        return null;
    }
  };

  return (
    <div className="flex min-h-screen bg-[#080c14] text-slate-200">
      <Sidebar
        activeTab={activeTab}
        onNavigate={setActiveTab}
        expanded={sidebarExpanded}
        onToggle={() => setSidebarExpanded((v) => !v)}
        stats={stats}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <Header
          activeTab={activeTab}
          baseUrl={meta?.base_url ?? null}
          autoRefresh={autoRefresh}
          onAutoRefreshChange={setAutoRefresh}
          onRefresh={loadData}
          refreshing={refreshing}
        />

        <main className="flex-1 px-4 py-5 md:px-6 lg:px-8">
          <motion.div
            key={activeTab}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25, ease: 'easeOut' }}
          >
            {renderTab()}
          </motion.div>
        </main>

        <StatusFooter stats={stats} />
      </div>
    </div>
  );
}
