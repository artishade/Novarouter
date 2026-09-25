/** NovaRouter shared types — single source of truth for API shapes (snake_case over the wire). */

export interface Provider {
  id: number;
  key: string;
  name: string;
  kind: 'openai' | 'anthropic' | 'gemini' | 'builtin' | string;
  base_url: string;
  prefix: string;
  enabled: boolean;
  priority: number;
  color: string;
  docs_url: string | null;
  auth_url: string | null;
  requires_auth: boolean;
  free_tier: string | null;
  created_at: number;
  key_count: number;
  model_count: number;
  ok_count: number;
  cooling_count: number;
  session: ProviderSessionInfo | null;
}

export interface ProviderSessionInfo {
  username: string;
  display_name: string | null;
  plan: string;
  status: string;
  token_mask: string | null;
  connected_at: number;
}

export interface UpstreamKey {
  id: number;
  provider_id: number;
  provider_name?: string;
  label: string;
  api_key_preview: string;
  weight: number;
  enabled: boolean;
  cooldown_until: number | null;
  last_error: string | null;
  req_count: number;
  err_count: number;
  last_used_at: number | null;
  created_at: number;
}

export interface ModelCapabilities {
  tools?: boolean;
  vision?: boolean;
  reasoning?: boolean;
  audio_in?: boolean;
  image_out?: boolean;
}

export interface Model {
  id: number;
  provider_id: number;
  provider_name?: string;
  provider_color?: string;
  model_id: string;
  exposed_id: string;
  display_name: string;
  is_free: boolean;
  status: 'healthy' | 'cooling' | 'dead' | 'unknown';
  http_status: number;
  detail: string;
  latency_ms: number;
  checked_at: number | null;
  enabled: boolean;
  context_length: number;
  max_output: number;
  capabilities: ModelCapabilities;
  price_in: number;
  price_out: number;
  description: string | null;
}

export interface ModelRoute {
  id: number;
  public_id: string;
  fallbacks: string[];
  auto: boolean;
  enabled: boolean;
  note: string;
  created_at: number;
}

export interface RoutePreview {
  model: string;
  direct: boolean;
  explicit_chain: string[];
  auto_allowed: boolean;
  auto_targets: string[];
  stages: Array<{ label: string; models: string[] }>;
  spoof_model: boolean;
}

export interface ClientKey {
  id: number;
  name: string;
  token: string;
  enabled: boolean;
  allowed_models: string;
  rpm_limit: number;
  tpd_limit: number;
  tokens_in: number;
  tokens_out: number;
  req_count: number;
  last_used_at: number | null;
  created_at: number;
}

export interface RequestLog {
  id: number;
  ts: number;
  client_name: string;
  provider_name: string;
  model: string;
  upstream_model: string;
  endpoint: string;
  status: number;
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  error: string;
  via: string;
  spoofed: boolean;
}

export interface GatewayStats {
  total_requests: number;
  total_tokens: number;
  total_tokens_in: number;
  total_tokens_out: number;
  active_keys: number;
  active_models: number;
  dead_models: number;
  healthy_models: number;
  providers_count: number;
  connected_providers: number;
  cache_hit_rate: number;
  avg_latency_ms: number;
  error_rate: number;
  uptime_s: number;
  memory: {
    heap_used_mb: number;
    heap_total_mb: number;
    rss_mb: number;
    pct: number;
  };
  v8: {
    heap_mb: number;
    swap_mb: number;
  };
}

export interface TimeseriesBucket {
  bucket: string;
  reqs: number;
  tokens: number;
  errors: number;
}

export interface AnalyticsSummary {
  total_requests: number;
  total_tokens: number;
  tokens_in: number;
  tokens_out: number;
  avg_latency_ms: number;
  p50_latency_ms: number;
  p90_latency_ms: number;
  p99_latency_ms: number;
  error_count: number;
  error_rate: number;
  spoofed_fallbacks_count: number;
  cache_hits: number;
}

export interface AnalyticsResponse {
  hours: number;
  group_by: string;
  timeseries: TimeseriesBucket[];
  top_models: Array<{ model: string; reqs: number; tokens: number }>;
  top_providers: Array<{ provider: string; reqs: number }>;
  top_clients: Array<{ client: string; reqs: number }>;
  summary: AnalyticsSummary;
}

export interface ProviderPreset {
  key: string;
  name: string;
  kind: string;
  base_url: string;
  prefix: string;
  key_hint: string;
  free_tier: string | null;
  docs_url: string | null;
  auth_url: string | null;
  requires_signin: boolean;
  color: string;
  priority: number;
}

export interface MetaConfig {
  version: string;
  base_url: string;
  storage: string;
  fallback: { auto: boolean; spoof_model: boolean; max: number };
  cooldowns: Record<string, number>;
  scheduler: { check_interval: number };
  hedging: { delay: number };
  cache: { ttl: number; max_entries: number };
  limits: { file_max_mb: number; batch_max_items: number };
  presets: ProviderPreset[];
  kinds: string[];
  statuses: string[];
  admin_token: string;
}

export interface CheckJob {
  id: string;
  status: string;
  scope: string;
  total: number;
  done: number;
  ok: number;
  fail: number;
  created_at: number;
}

/* ---------------- Terminal / Compute ---------------- */

export interface TerminalSystemInfo {
  platform: string;
  release: string;
  arch: string;
  hostname: string;
  uptime_s: number;
  totalmem_mb: number;
  freemem_mb: number;
  cpus: number;
  load_pct: number;
  node_version: string;
}

export interface TerminalHistoryItem {
  id: number;
  command: string;
  output: string;
  exit_code: number;
  duration_ms: number;
  cwd: string;
  timestamp: number;
}

export interface TerminalHistoryResponse {
  cwd: string;
  history: TerminalHistoryItem[];
  system: TerminalSystemInfo;
  memory_config: { v8_heap_mb: number; swap_mb: number; boost_applied_at: number | null };
  gpu: { enabled: boolean; strategy: string; total: number; enabled_count: number; connected: number };
}

export interface ExecResult {
  ok: boolean;
  output: string;
  stdout: string;
  stderr: string;
  code: number;
  duration_ms: number;
  cwd: string;
}

export interface BoostRamResult {
  ok: boolean;
  message: string;
  v8_heap_mb: number;
  swap_mb: number;
  totalmem_mb: number;
  freemem_mb: number;
  heap_before_mb: number;
  heap_after_mb: number;
  swap_success: boolean;
  swap_output: string;
}

export interface ComputeProvider {
  id: string;
  name: string;
  gpu: string;
  vram_gb: number;
  free_tier: string;
  status: string;
  connected: boolean;
  hours_free: string;
  region: string;
  note?: string;
  enabled: boolean;
}

export interface GpuConfig {
  enabled: boolean;
  strategy: string;
  providers: ComputeProvider[];
  total_vram_gb: number;
  enabled_vram_gb: number;
}

/* ---------------- Storage ---------------- */

export interface StorageProviderInfo {
  id: string;
  name: string;
  type: string;
  status: string;
  is_builtin: boolean;
  active: boolean;
  free_tier: string | null;
  usage_mb: number;
  quota_mb: number;
  region: string | null;
  docs_url: string | null;
  auth_url: string | null;
  last_test_at: number | null;
  last_error: string | null;
}

export interface StorageFileInfo {
  id: number;
  name: string;
  path: string;
  size: number;
  mime: string;
  provider_key: string;
  created_at: number;
}

export interface StorageInfo {
  active_provider: string;
  providers: StorageProviderInfo[];
  files: StorageFileInfo[];
  usage: { files: number; total_mb: number; quota_mb: number; pct: number };
}

/* ---------------- Agent ---------------- */

export interface AgentStep {
  id: number;
  step_number: number;
  action: string;
  title: string;
  description: string;
  detail: string;
  status: string;
  latency_ms: number;
  created_at: number;
}

export interface AgentTask {
  id: string;
  goal: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | string;
  model: string;
  max_steps: number;
  summary: string;
  error: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  steps: AgentStep[];
}

/* ---------------- Gateway ---------------- */

export interface GatewayModelEntry {
  id: string;
  object: string;
  owned_by: string;
  context_length: number;
  capabilities: ModelCapabilities;
  is_free: boolean;
  status: string;
}

export interface NovaMeta {
  upstream_model: string;
  provider: string;
  fallback: boolean;
  hedged: boolean;
  cached: boolean;
  spoofed: boolean;
  stage: number;
}
