/** NovaRouter client API wrapper — all admin/gateway calls in one typed place. */
import type {
  AgentTask,
  AnalyticsResponse,
  BoostRamResult,
  ClientKey,
  ComputeProvider,
  ExecResult,
  GatewayModelEntry,
  GatewayStats,
  GpuConfig,
  MetaConfig,
  Model,
  ModelRoute,
  Provider,
  ProviderPreset,
  RequestLog,
  RoutePreview,
  StorageInfo,
  TerminalHistoryResponse,
  UpstreamKey,
} from './types';

async function jfetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const ej = await res.json();
      if (ej.error) msg = ej.error;
    } catch { /* not json */ }
    throw new Error(msg);
  }
  return (await res.json()) as T;
}

const post = <T,>(path: string, body?: unknown) =>
  jfetch<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
const patch = <T,>(path: string, body?: unknown) =>
  jfetch<T>(path, { method: 'PATCH', body: body === undefined ? undefined : JSON.stringify(body) });
const del = <T,>(path: string) => jfetch<T>(path, { method: 'DELETE' });

export const api = {
  /* Meta / stats / analytics */
  getMeta: () => jfetch<MetaConfig>('/api/admin/meta'),
  getStats: () => jfetch<GatewayStats>('/api/admin/stats'),
  getAnalytics: (hours = 24, groupBy = 'hour') =>
    jfetch<AnalyticsResponse>(`/api/admin/analytics?hours=${hours}&group_by=${groupBy}`),

  /* Providers */
  getProviders: () => jfetch<Provider[]>('/api/admin/providers'),
  getProviderPresets: () => jfetch<ProviderPreset[]>('/api/admin/providers/presets'),
  createProvider: (data: {
    name: string; kind?: string; base_url?: string; prefix?: string;
    priority?: number; api_keys?: string; free_tier?: string; docs_url?: string; auth_url?: string;
  }) => post<{ id: number; keys_added: number }>('/api/admin/providers', data),
  updateProvider: (id: number, data: Partial<Provider>) => patch<{ ok: boolean }>(`/api/admin/providers/${id}`, data),
  deleteProvider: (id: number) => del<{ ok: boolean }>(`/api/admin/providers/${id}`),
  testProvider: (id: number) =>
    post<{ ok: boolean; provider: string; key_count: number; latency_ms: number; status: string; message: string }>(
      `/api/admin/providers/${id}/test`
    ),
  signInProvider: (id: number, data?: { username?: string }) =>
    post<{ ok: boolean; session: { username: string; plan: string; display_name: string | null; token_mask: string | null; connected_at: number } }>(
      `/api/admin/providers/${id}/signin`, data
    ),
  signOutProvider: (id: number) => post<{ ok: boolean }>(`/api/admin/providers/${id}/signout`),

  /* Upstream keys */
  getKeys: (providerId?: number) =>
    jfetch<UpstreamKey[]>(`/api/admin/keys${providerId ? `?provider_id=${providerId}` : ''}`),
  createKey: (data: { provider_id: number; api_key: string; label?: string; weight?: number }) =>
    post<{ id: number }>('/api/admin/keys', data),
  createKeysBulk: (data: { provider_id: number; keys: string; label_prefix?: string }) =>
    post<{ added: number; ids: number[] }>('/api/admin/keys/bulk', data),
  deleteKey: (id: number) => del<{ ok: boolean }>(`/api/admin/keys/${id}`),
  clearCooldowns: () => post<{ ok: boolean; cleared: number }>('/api/admin/keys/clear-cooldowns'),

  /* Client keys */
  getClientKeys: () => jfetch<ClientKey[]>('/api/admin/client-keys'),
  createClientKey: (data: { name: string; allowed_models?: string; rpm_limit?: number; tpd_limit?: number }) =>
    post<ClientKey>('/api/admin/client-keys', data),
  updateClientKey: (id: number, data: Partial<ClientKey>) => patch<{ ok: boolean }>(`/api/admin/client-keys/${id}`, data),
  deleteClientKey: (id: number) => del<{ ok: boolean }>(`/api/admin/client-keys/${id}`),

  /* Models */
  getModels: (params?: { provider_id?: number; status?: string; search?: string; capability?: string; free?: boolean }) => {
    const q = new URLSearchParams();
    if (params?.provider_id) q.set('provider_id', String(params.provider_id));
    if (params?.status) q.set('status', params.status);
    if (params?.search) q.set('search', params.search);
    if (params?.capability) q.set('capability', params.capability);
    if (params?.free !== undefined) q.set('free', String(params.free));
    return jfetch<{ total: number; rows: Model[] }>(`/api/admin/models?${q.toString()}`);
  },
  toggleModel: (id: number, enabled: boolean) => patch<{ ok: boolean }>(`/api/admin/models/${id}`, { enabled }),
  pingModel: (id: number) =>
    post<{ ok: boolean; status: string; latency_ms: number; http_status: number; detail: string }>(`/api/admin/models/${id}/ping`),
  syncModels: (providerId?: number) =>
    post<{ ok: boolean; synced: number; new_added: number }>(
      `/api/admin/models/sync${providerId ? `?provider_id=${providerId}` : ''}`
    ),

  /* Routes */
  getRoutes: () => jfetch<ModelRoute[]>('/api/admin/routes'),
  createRoute: (data: { public_id: string; fallbacks: string[]; auto?: boolean; note?: string }) =>
    post<{ id: number }>('/api/admin/routes', data),
  updateRoute: (id: number, data: Partial<ModelRoute>) => patch<{ ok: boolean }>(`/api/admin/routes/${id}`, data),
  deleteRoute: (id: number) => del<{ ok: boolean }>(`/api/admin/routes/${id}`),
  previewRoute: (model: string) =>
    jfetch<RoutePreview>(`/api/admin/routes/preview?model=${encodeURIComponent(model)}`),

  /* Logs */
  getLogs: (limit = 100) => jfetch<RequestLog[]>(`/api/admin/logs?limit=${limit}`),

  /* Terminal */
  terminalHistory: () => jfetch<TerminalHistoryResponse>('/api/admin/terminal/history'),
  terminalExec: (command: string, cwd?: string) => post<ExecResult>('/api/admin/terminal/exec', { command, cwd }),
  terminalClear: () => post<{ ok: boolean }>('/api/admin/terminal/clear'),
  boostRam: (heap_mb: number, swap_mb: number) =>
    post<BoostRamResult>('/api/admin/terminal/boost-ram', { heap_mb, swap_mb }),

  /* Compute / GPU */
  getComputeProviders: () => jfetch<ComputeProvider[]>('/api/admin/compute/providers'),
  toggleComputeProvider: (id: string, enabled: boolean) =>
    post<{ ok: boolean; message: string }>(`/api/admin/compute/providers/${id}/toggle`, { enabled }),
  getGpuConfig: () => jfetch<GpuConfig>('/api/admin/compute/config'),
  setGpuConfig: (data: { enabled?: boolean; strategy?: string }) =>
    post<{ ok: boolean; config: GpuConfig }>('/api/admin/compute/config', data),

  /* Storage */
  getStorageInfo: () => jfetch<StorageInfo>('/api/admin/storage/info'),
  setStorageProvider: (data: { provider_key: string; config?: Record<string, unknown> }) =>
    post<{ ok: boolean; active_provider: string }>('/api/admin/storage/config', data),
  connectStorageProvider: (data: { provider_key: string; config: Record<string, unknown> }) =>
    post<{ ok: boolean; status: string; message: string }>('/api/admin/storage/connect', data),
  disconnectStorageProvider: (providerKey: string) =>
    post<{ ok: boolean }>('/api/admin/storage/disconnect', { provider_key: providerKey }),
  addCustomStorageProvider: (data: {
    id?: string; name: string; type: string; endpoint?: string; bucket_name?: string;
    access_key?: string; secret_key?: string; region?: string; free_tier?: string;
  }) => post<{ ok: boolean; provider_key: string }>('/api/admin/storage/providers', data),
  testStorageConnection: (providerKey: string) =>
    post<{ ok: boolean; status: string; latency_ms: number; message: string }>(
      '/api/admin/storage/test-connection', { provider_key: providerKey }
    ),
  uploadStorageFile: (file: File, providerKey?: string) => {
    const fd = new FormData();
    fd.append('file', file);
    if (providerKey) fd.append('provider_key', providerKey);
    return jfetch<{ ok: boolean; file: { id: number; name: string; size: number; mime: string } }>(
      '/api/admin/storage/files', { method: 'POST', body: fd }
    );
  },
  downloadStorageFile: (id: number) =>
    jfetch<{ ok: boolean; file: { name: string; mime: string; data_base64: string } }>(`/api/admin/storage/files/${id}`),
  deleteStorageFile: (id: number) => del<{ ok: boolean }>(`/api/admin/storage/files/${id}`),
  backupStorage: () =>
    post<{ ok: boolean; file_name: string; size_bytes: number; provider: string; message: string }>('/api/admin/storage/backup'),

  /* Agent */
  getAgentTasks: () => jfetch<AgentTask[]>('/api/agent/tasks'),
  getAgentTask: (id: string) => jfetch<AgentTask>(`/api/agent/tasks/${id}`),
  createAgentTask: (data: { goal: string; max_steps?: number; model?: string }) =>
    post<{ id: string; status: string }>('/api/agent/tasks', data),
  cancelAgentTask: (id: string) => post<{ ok: boolean }>(`/api/agent/tasks/${id}/cancel`),
  getAgentTools: () =>
    jfetch<Array<{ id: string; name: string; description: string; icon: string }>>('/api/agent/tools'),

  /* Gateway (playground + public API surface) */
  gatewayModels: () => jfetch<{ data: GatewayModelEntry[] }>('/api/v1/models'),
  chatCompletion: (data: {
    model: string;
    messages: Array<{ role: string; content: string }>;
    temperature?: number;
    max_tokens?: number;
  }) =>
    post<{
      id: string;
      object: string;
      model: string;
      choices: Array<{ index: number; message: { role: string; content: string }; finish_reason: string }>;
      usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
      _nova: { upstream_model: string; provider: string; fallback: boolean; cached: boolean; spoofed: boolean; stage: number };
    }>('/api/v1/chat/completions', data),
};

