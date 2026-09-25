'use client';

/**
 * StorageTab — NovaRouter storage manager.
 * Free cloud providers (Firebase, Supabase, R2, B2, GitHub, WebDAV, S3, local),
 * one-click backups, uploads and a file browser over /api/admin/storage/**.
 */

import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { toast } from 'sonner';
import {
  Archive,
  Braces,
  CheckCircle2,
  Circle,
  CircleDot,
  Cloud,
  CloudCog,
  Database,
  Download,
  ExternalLink,
  File,
  FileText,
  Flame,
  FolderSync,
  Github,
  HardDrive,
  Loader2,
  PlugZap,
  Plus,
  Sparkles,
  Table as TableIcon,
  Trash2,
  Unplug,
  Upload,
  Zap,
} from 'lucide-react';
import { api } from '@/lib/api';
import type {
  GatewayStats,
  MetaConfig,
  StorageFileInfo,
  StorageInfo,
  StorageProviderInfo,
} from '@/lib/types';
import { cx, fmtBytes, fmtDate, fmtMb, timeAgo } from '@/lib/format';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

const MAX_UPLOAD_BYTES = 5 * 1024 * 1024; // storage endpoint hard cap

interface ConnectField {
  key: string;
  label: string;
  password?: boolean;
  placeholder?: string;
}

interface ConnectDef {
  fields: ConnectField[];
  linkLabel: string;
  note: string;
}

interface BackupReceipt {
  ok: boolean;
  file_name: string;
  size_bytes: number;
  provider: string;
  message: string;
}

const CONNECT_DEFS: Record<string, ConnectDef> = {
  firebase: {
    fields: [
      { key: 'project_id', label: 'Project ID', placeholder: 'nova-router' },
      { key: 'api_key', label: 'Web API Key', password: true, placeholder: 'AIza…' },
      { key: 'bucket_name', label: 'Storage Bucket', placeholder: 'nova-router.appspot.com' },
    ],
    linkLabel: 'Open Firebase Console',
    note: 'Sign in to the Firebase console with the Google account that owns the project, then copy the Web API key from Project Settings.',
  },
  supabase: {
    fields: [
      { key: 'project_url', label: 'Project URL', placeholder: 'https://xyzcompany.supabase.co' },
      { key: 'anon_key', label: 'Anon Key', password: true, placeholder: 'eyJhbGciOi…' },
      { key: 'bucket_name', label: 'Bucket Name', placeholder: 'nova-storage' },
    ],
    linkLabel: 'Open Supabase Dashboard',
    note: 'Find the project URL and anon key under Project Settings → API in the Supabase dashboard.',
  },
  cloudflare_r2: {
    fields: [
      { key: 'account_id', label: 'Account ID', placeholder: '32-char hex id' },
      { key: 'access_key_id', label: 'Access Key ID' },
      { key: 'secret_access_key', label: 'Secret Access Key', password: true },
      { key: 'bucket_name', label: 'Bucket Name', placeholder: 'nova' },
    ],
    linkLabel: 'Open Cloudflare R2 Dashboard',
    note: 'Create an R2 API token with Object Read & Write permission and paste the access key pair here.',
  },
  backblaze_b2: {
    fields: [
      { key: 'key_id', label: 'Key ID' },
      { key: 'application_key', label: 'Application Key', password: true },
      { key: 'bucket_name', label: 'Bucket Name', placeholder: 'nova-backups' },
    ],
    linkLabel: 'Open Backblaze B2 Console',
    note: 'Create a Master or limited Application Key in the B2 console — application keys are shown only once.',
  },
  github: {
    fields: [
      { key: 'username', label: 'Username', placeholder: 'octocat' },
      { key: 'personal_access_token', label: 'Personal Access Token', password: true, placeholder: 'ghp_…' },
      { key: 'repo', label: 'Repository (owner/name)', placeholder: 'octocat/nova-storage' },
    ],
    linkLabel: 'Create token on GitHub',
    note: 'Generate a fine-grained personal access token with Contents read & write permission for the storage repository.',
  },
};

const CUSTOM_TYPES = ['s3', 'supabase', 'firebase', 'github', 'webdav', 'local'] as const;

const EMPTY_CUSTOM = {
  name: '',
  type: 's3',
  endpoint: '',
  bucket: '',
  access_key: '',
  secret_key: '',
  region: '',
  free_tier: '',
};

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : 'Request failed';
}

function providerIcon(type: string) {
  switch (type) {
    case 'local':
    case 'local_disk':
      return HardDrive;
    case 'builtin_cloud':
      return CloudCog;
    case 'firebase':
      return Flame;
    case 'supabase':
      return Database;
    case 'github':
      return Github;
    case 'webdav':
      return FolderSync;
    case 'backblaze_b2':
      return Database;
    case 'cloudflare_r2':
    case 's3':
    default:
      return Cloud;
  }
}

function statusBadgeCls(status: string): { cls: string; label: string } {
  switch (status) {
    case 'connected':
      return { cls: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300', label: 'connected' };
    case 'pending':
      return { cls: 'border-amber-500/40 bg-amber-500/10 text-amber-300', label: 'pending' };
    case 'error':
      return { cls: 'border-rose-500/40 bg-rose-500/10 text-rose-300', label: 'error' };
    default:
      return { cls: 'border-slate-600/50 bg-slate-500/10 text-slate-400', label: status || 'disconnected' };
  }
}

function fileIconFor(f: StorageFileInfo) {
  const mime = (f.mime || '').toLowerCase();
  const name = f.name.toLowerCase();
  if (mime.includes('json') || name.endsWith('.json')) return Braces;
  if (mime.includes('csv') || name.endsWith('.csv')) return TableIcon;
  if (mime.includes('markdown') || name.endsWith('.md')) return FileText;
  return File;
}

function connectDefFor(p: StorageProviderInfo): ConnectDef | null {
  return CONNECT_DEFS[p.type] ?? CONNECT_DEFS[p.id] ?? null;
}

function providerName(key: string, providers: StorageProviderInfo[]): string {
  return providers.find((p) => p.id === key)?.name ?? key;
}

export function StorageTab({
  meta,
  stats,
  onRefresh,
}: {
  meta: MetaConfig | null;
  stats: GatewayStats | null;
  onRefresh: () => void;
}) {
  const [info, setInfo] = useState<StorageInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const [connectTarget, setConnectTarget] = useState<StorageProviderInfo | null>(null);
  const [connectValues, setConnectValues] = useState<Record<string, string>>({});
  const [customOpen, setCustomOpen] = useState(false);
  const [custom, setCustom] = useState({ ...EMPTY_CUSTOM });
  const [backupDone, setBackupDone] = useState<BackupReceipt | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<StorageFileInfo | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await api.getStorageInfo();
        if (alive) setInfo(res);
      } catch (e) {
        toast.error(errMsg(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  async function reload() {
    try {
      const res = await api.getStorageInfo();
      setInfo(res);
    } catch (e) {
      toast.error(errMsg(e));
    }
  }

  function onUploadPicked(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (file.size > MAX_UPLOAD_BYTES) {
      toast.error(`File exceeds the 5 MB storage upload limit (${fmtBytes(file.size)})`);
      return;
    }
    void handleUpload(file);
  }

  async function handleUpload(file: File) {
    try {
      setBusy('upload');
      const res = await api.uploadStorageFile(file);
      toast.success(`Uploaded ${res.file.name} (${fmtBytes(res.file.size)})`);
      await reload();
      onRefresh();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleBackup() {
    try {
      setBusy('backup');
      const res = (await api.backupStorage()) as BackupReceipt;
      setBackupDone(res);
      toast.success(`Backup created: ${res.file_name} (${fmtBytes(res.size_bytes)})`);
      await reload();
      onRefresh();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleSetDefault(p: StorageProviderInfo) {
    try {
      setBusy(`default:${p.id}`);
      await api.setStorageProvider({ provider_key: p.id });
      toast.success(`${p.name} is now the active storage provider`);
      await reload();
      onRefresh();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleTest(p: StorageProviderInfo) {
    try {
      setBusy(`test:${p.id}`);
      const res = (await api.testStorageConnection(p.id)) as {
        ok: boolean;
        status: string;
        latency_ms: number;
        message: string;
      };
      if (res.ok) toast.success(`${p.name}: ${res.status} in ${res.latency_ms} ms`);
      else toast.error(`${p.name}: ${res.message}`);
      await reload();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  function openConnect(p: StorageProviderInfo) {
    setConnectTarget(p);
    setConnectValues({});
  }

  async function handleConnect() {
    if (!connectTarget) return;
    const def = connectDefFor(connectTarget);
    if (!def) return;
    const config: Record<string, string> = {};
    for (const f of def.fields) {
      const v = (connectValues[f.key] ?? '').trim();
      if (v) config[f.key] = v;
    }
    if (Object.keys(config).length === 0) {
      toast.error('Fill in at least one credential field before connecting');
      return;
    }
    try {
      setBusy('connect');
      await api.connectStorageProvider({ provider_key: connectTarget.id, config });
      toast.success(`${connectTarget.name} connected`);
      setConnectTarget(null);
      setConnectValues({});
      await reload();
      onRefresh();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleDisconnect(p: StorageProviderInfo) {
    try {
      setBusy(`disc:${p.id}`);
      await api.disconnectStorageProvider(p.id);
      toast.success(`${p.name} disconnected`);
      await reload();
      onRefresh();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleCreateCustom() {
    if (!custom.name.trim()) {
      toast.error('Provider name is required');
      return;
    }
    try {
      setBusy('custom');
      const res = (await api.addCustomStorageProvider({
        name: custom.name.trim(),
        type: custom.type,
        endpoint: custom.endpoint.trim() || undefined,
        bucket_name: custom.bucket.trim() || undefined,
        access_key: custom.access_key.trim() || undefined,
        secret_key: custom.secret_key.trim() || undefined,
        region: custom.region.trim() || undefined,
        free_tier: custom.free_tier.trim() || undefined,
      })) as { ok: boolean; provider_key: string };
      toast.success(`Provider ${res.provider_key} created (pending — connect it to activate)`);
      setCustomOpen(false);
      setCustom({ ...EMPTY_CUSTOM });
      await reload();
      onRefresh();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleDownload(f: StorageFileInfo) {
    try {
      setBusy(`dl:${f.id}`);
      const res = await api.downloadStorageFile(f.id);
      const bin = atob(res.file.data_base64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const blob = new Blob([bytes], { type: res.file.mime || 'application/octet-stream' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = res.file.name || f.name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success(`Downloaded ${f.name}`);
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleDelete(f: StorageFileInfo) {
    setDeleteTarget(null);
    try {
      setBusy(`del:${f.id}`);
      await api.deleteStorageFile(f.id);
      toast.success(`Deleted ${f.name}`);
      await reload();
      onRefresh();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  if (loading && !info) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-28 w-full rounded-xl bg-slate-800/50" />
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <Skeleton key={i} className="h-44 w-full rounded-xl bg-slate-800/50" />
          ))}
        </div>
        <Skeleton className="h-64 w-full rounded-xl bg-slate-800/50" />
      </div>
    );
  }

  if (!info) {
    return (
      <div className="flex flex-col items-start gap-3 rounded-xl border border-rose-500/30 bg-rose-500/5 p-6">
        <p className="text-sm text-rose-300">Storage telemetry unavailable — the gateway did not respond.</p>
        <Button size="sm" onClick={() => void reload()} className="bg-emerald-500 text-slate-950 hover:bg-emerald-400">
          Retry
        </Button>
      </div>
    );
  }

  const active = info.providers.find((p) => p.active) ?? null;
  const hasQuota = !!active && active.quota_mb > 0;
  const usagePct = hasQuota ? Math.min(100, Math.max(0, Math.round(info.usage.pct))) : 0;
  const connectDef = connectTarget ? connectDefFor(connectTarget) : null;
  const connectLinkUrl = connectTarget ? connectTarget.auth_url ?? connectTarget.docs_url : null;

  return (
    <div className="space-y-5">
      {/* ---- Active provider banner ---- */}
      <Card className="rounded-xl border-slate-800 bg-[#0d1322]/80">
        <CardContent className="flex flex-col gap-4 p-4 md:p-5 lg:flex-row lg:items-center">
          <div className="flex min-w-0 flex-1 items-center gap-4">
            <span className="flex size-11 shrink-0 items-center justify-center rounded-xl border border-emerald-500/40 bg-emerald-500/10 text-emerald-300">
              <HardDrive className="size-5" />
            </span>
            <div className="min-w-0 flex-1 space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-[10px] uppercase tracking-widest text-emerald-400/80">active provider</span>
                <span className="truncate text-sm font-semibold text-slate-100">
                  {active ? active.name : info.active_provider}
                </span>
                <Badge variant="outline" className="border-slate-700 font-mono text-[10px] text-slate-400">
                  {meta?.storage ?? 'hybrid (local + cloud)'}
                </Badge>
              </div>
              {hasQuota && active ? (
                <div className="space-y-1">
                  <Progress
                    value={usagePct}
                    className="h-2 bg-slate-800 [&_[data-slot=progress-indicator]]:bg-emerald-500"
                    aria-label="Active provider storage usage"
                  />
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
                    <span className="font-mono">
                      {fmtMb(active.usage_mb)} of {fmtMb(active.quota_mb)} used
                    </span>
                    <span className="font-mono text-emerald-300">{usagePct}%</span>
                    <span className="flex items-center gap-1">
                      <File className="size-3" /> {info.usage.files} files
                    </span>
                    {stats && <span className="text-slate-600">gateway heap {Math.round(stats.memory.pct)}%</span>}
                  </div>
                </div>
              ) : (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
                  <span className="font-mono">{fmtMb(active?.usage_mb ?? info.usage.total_mb)} stored</span>
                  <span className="font-mono text-emerald-300">unlimited quota</span>
                  <span className="flex items-center gap-1">
                    <File className="size-3" /> {info.usage.files} files
                  </span>
                  {stats && <span className="text-slate-600">gateway heap {Math.round(stats.memory.pct)}%</span>}
                </div>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              onChange={onUploadPicked}
              aria-hidden="true"
              tabIndex={-1}
            />
            <Button
              size="sm"
              variant="outline"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy !== null}
              aria-label="Upload file to the active storage provider"
              className="border-slate-700 bg-transparent text-slate-300 hover:border-emerald-500/50 hover:text-emerald-300"
            >
              {busy === 'upload' ? <Loader2 className="size-4 animate-spin" /> : <Upload className="size-4" />}
              Upload
            </Button>
            <Button
              size="sm"
              onClick={() => void handleBackup()}
              disabled={busy !== null}
              aria-label="Create a storage backup now"
              className="bg-emerald-500 text-slate-950 hover:bg-emerald-400"
            >
              {busy === 'backup' ? <Loader2 className="size-4 animate-spin" /> : <Archive className="size-4" />}
              Backup Now
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* ---- Providers grid ---- */}
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <HardDrive className="size-4 text-emerald-400" />
          Storage Providers
          <Badge variant="outline" className="border-slate-700 font-mono text-[10px] text-slate-500">
            {info.providers.length}
          </Badge>
        </h2>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setCustomOpen(true)}
          className="border-slate-700 bg-transparent text-slate-300 hover:border-emerald-500/50 hover:text-emerald-300"
        >
          <Plus className="size-4" />
          Add Custom Provider
        </Button>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {info.providers.map((p) => {
          const Icon = providerIcon(p.type);
          const sb = statusBadgeCls(p.status);
          const def = connectDefFor(p);
          const cardPct = p.quota_mb > 0 ? Math.min(100, Math.max(0, Math.round((p.usage_mb / p.quota_mb) * 100))) : 0;
          return (
            <Card
              key={p.id}
              className={cx(
                'rounded-xl border bg-[#0d1322]/80 transition-colors',
                p.active ? 'border-emerald-500/40' : 'border-slate-800'
              )}
            >
              <CardContent className="space-y-3 p-4">
                <div className="flex items-start gap-2.5">
                  <span
                    className={cx(
                      'flex size-9 shrink-0 items-center justify-center rounded-lg border',
                      p.active
                        ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                        : 'border-slate-800 bg-slate-900/60 text-slate-400'
                    )}
                  >
                    <Icon className="size-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate text-sm font-semibold text-slate-100">{p.name}</span>
                      {p.active && (
                        <Badge className="border-transparent bg-emerald-500/15 px-1.5 py-0 text-[10px] text-emerald-300">
                          active
                        </Badge>
                      )}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      <Badge variant="outline" className="border-slate-700 px-1.5 py-0 font-mono text-[10px] text-slate-400">
                        {p.type}
                      </Badge>
                      <Badge variant="outline" className={cx('px-1.5 py-0 text-[10px]', sb.cls)}>
                        {sb.label}
                      </Badge>
                    </div>
                  </div>
                </div>

                {p.free_tier && (
                  <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-slate-500">
                    <Sparkles className="mt-0.5 size-3 shrink-0 text-amber-300/80" />
                    <span>{p.free_tier}</span>
                  </p>
                )}

                {p.quota_mb > 0 && (
                  <div className="space-y-1">
                    <Progress
                      value={cardPct}
                      className="h-1.5 bg-slate-800 [&_[data-slot=progress-indicator]]:bg-emerald-500"
                      aria-label={`${p.name} usage`}
                    />
                    <div className="flex justify-between font-mono text-[10px] text-slate-500">
                      <span>{fmtMb(p.usage_mb)} used</span>
                      <span>
                        {cardPct}% of {fmtMb(p.quota_mb)}
                      </span>
                    </div>
                  </div>
                )}

                {p.last_error && (
                  <p className="truncate text-[11px] text-rose-300/90" title={p.last_error}>
                    Error: {p.last_error}
                  </p>
                )}
                {p.last_test_at && (
                  <p className="text-[10px] text-slate-600">Last tested {timeAgo(p.last_test_at)}</p>
                )}

                <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
                  <Button
                    size="sm"
                    variant={p.active ? 'default' : 'outline'}
                    disabled={p.active || busy !== null}
                    onClick={() => void handleSetDefault(p)}
                    aria-label={`Use ${p.name} as default storage provider`}
                    className={cx(
                      p.active
                        ? 'bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/15'
                        : 'border-slate-700 bg-transparent text-slate-300 hover:border-emerald-500/50 hover:text-emerald-300'
                    )}
                  >
                    {p.active ? <CircleDot className="size-3.5" /> : <Circle className="size-3.5" />}
                    {p.active ? 'Default' : 'Use as default'}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy !== null}
                    onClick={() => void handleTest(p)}
                    aria-label={`Test ${p.name} connection`}
                    className="border-slate-700 bg-transparent text-slate-300 hover:border-emerald-500/50 hover:text-emerald-300"
                  >
                    {busy === `test:${p.id}` ? <Loader2 className="size-3.5 animate-spin" /> : <Zap className="size-3.5" />}
                    Test
                  </Button>
                  {def && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy !== null}
                      onClick={() => openConnect(p)}
                      aria-label={`Connect ${p.name}`}
                      className="border-purple-500/40 bg-transparent text-purple-300 hover:bg-purple-500/10 hover:text-purple-200"
                    >
                      <PlugZap className="size-3.5" />
                      Connect
                    </Button>
                  )}
                  {p.status === 'connected' && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy !== null}
                      onClick={() => void handleDisconnect(p)}
                      aria-label={`Disconnect ${p.name}`}
                      className="text-slate-400 hover:bg-rose-500/10 hover:text-rose-300"
                    >
                      {busy === `disc:${p.id}` ? (
                        <Loader2 className="size-3.5 animate-spin" />
                      ) : (
                        <Unplug className="size-3.5" />
                      )}
                      Disconnect
                    </Button>
                  )}
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* ---- Files table ---- */}
      <Card className="rounded-xl border-slate-800 bg-[#0d1322]/80">
        <CardContent className="p-0">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 px-4 py-3">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
              <File className="size-4 text-emerald-400" />
              Files
              <Badge variant="outline" className="border-slate-700 font-mono text-[10px] text-slate-500">
                {info.files.length}
              </Badge>
            </h2>
            <span className="font-mono text-xs text-slate-500">
              {fmtMb(info.usage.total_mb)} stored on {active ? active.name : info.active_provider}
            </span>
          </div>
          <div className="max-h-[40vh] overflow-y-auto">
            <Table>
              <TableHeader>
                <TableRow className="border-slate-800 hover:bg-transparent">
                  <TableHead className="pl-4 text-slate-500">Name</TableHead>
                  <TableHead className="text-slate-500">Size</TableHead>
                  <TableHead className="text-slate-500">Provider</TableHead>
                  <TableHead className="text-slate-500">Created</TableHead>
                  <TableHead className="pr-4 text-right text-slate-500">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {info.files.map((f) => {
                  const FIcon = fileIconFor(f);
                  return (
                    <TableRow key={f.id} className="border-slate-800/70">
                      <TableCell className="pl-4">
                        <div className="flex min-w-0 items-center gap-2">
                          <FIcon className="size-4 shrink-0 text-emerald-400" />
                          <div className="min-w-0">
                            <div className="truncate font-mono text-sm text-slate-200">{f.name}</div>
                            {f.path && f.path !== '/' && (
                              <span className="mt-0.5 inline-block rounded bg-slate-800/60 px-1.5 py-0.5 font-mono text-[10px] text-slate-500">
                                {f.path}
                              </span>
                            )}
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="font-mono text-xs text-slate-400">{fmtBytes(f.size)}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className="border-slate-700 px-1.5 py-0 font-mono text-[10px] text-slate-400">
                          {providerName(f.provider_key, info.providers)}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-slate-400">{fmtDate(f.created_at)}</TableCell>
                      <TableCell className="pr-4 text-right">
                        <div className="flex items-center justify-end gap-1">
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="size-8 text-slate-400 hover:text-emerald-300"
                                disabled={busy !== null}
                                onClick={() => void handleDownload(f)}
                                aria-label={`Download ${f.name}`}
                              >
                                {busy === `dl:${f.id}` ? (
                                  <Loader2 className="size-4 animate-spin" />
                                ) : (
                                  <Download className="size-4" />
                                )}
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Download</TooltipContent>
                          </Tooltip>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="size-8 text-slate-400 hover:bg-rose-500/10 hover:text-rose-300"
                                disabled={busy !== null}
                                onClick={() => setDeleteTarget(f)}
                                aria-label={`Delete ${f.name}`}
                              >
                                <Trash2 className="size-4" />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Delete</TooltipContent>
                          </Tooltip>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
                {info.files.length === 0 && (
                  <TableRow className="border-slate-800/70 hover:bg-transparent">
                    <TableCell colSpan={5} className="py-8 text-center text-sm text-slate-500">
                      No files yet — upload one or create a backup to get started.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* ---- Connect dialog ---- */}
      <Dialog
        open={!!connectTarget}
        onOpenChange={(o) => {
          if (!o) {
            setConnectTarget(null);
            setConnectValues({});
          }
        }}
      >
        <DialogContent className="max-w-md border-slate-800 bg-[#0d1322]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-slate-100">
              <PlugZap className="size-4 text-purple-300" />
              Connect {connectTarget?.name}
            </DialogTitle>
            <DialogDescription className="leading-relaxed text-slate-500">{connectDef?.note}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            {connectDef?.fields.map((f) => (
              <div key={f.key} className="space-y-1.5">
                <Label htmlFor={`cf-${f.key}`} className="text-xs text-slate-400">
                  {f.label}
                </Label>
                <Input
                  id={`cf-${f.key}`}
                  type={f.password ? 'password' : 'text'}
                  placeholder={f.placeholder}
                  autoComplete="off"
                  spellCheck={false}
                  value={connectValues[f.key] ?? ''}
                  onChange={(e) => setConnectValues((v) => ({ ...v, [f.key]: e.target.value }))}
                  className="border-slate-800 bg-[#070b13] font-mono text-sm"
                />
              </div>
            ))}
            {connectLinkUrl && connectDef && (
              <a
                href={connectLinkUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 text-xs text-emerald-400 transition hover:text-emerald-300"
              >
                <ExternalLink className="size-3.5" />
                {connectDef.linkLabel}
              </a>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setConnectTarget(null);
                setConnectValues({});
              }}
              className="border-slate-700 bg-transparent text-slate-300"
            >
              Cancel
            </Button>
            <Button
              onClick={() => void handleConnect()}
              disabled={busy !== null}
              className="bg-emerald-500 text-slate-950 hover:bg-emerald-400"
            >
              {busy === 'connect' && <Loader2 className="size-4 animate-spin" />}
              Connect
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ---- Backup receipt dialog ---- */}
      <Dialog open={!!backupDone} onOpenChange={(o) => !o && setBackupDone(null)}>
        <DialogContent className="max-w-sm border-slate-800 bg-[#0d1322]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-slate-100">
              <CheckCircle2 className="size-5 text-emerald-400" />
              Backup created
            </DialogTitle>
            <DialogDescription className="leading-relaxed text-slate-500">{backupDone?.message}</DialogDescription>
          </DialogHeader>
          <div className="space-y-2 rounded-lg border border-slate-800 bg-[#070b13] p-3 font-mono text-xs">
            <div className="flex items-center justify-between gap-4">
              <span className="text-slate-500">file</span>
              <span className="truncate text-emerald-300">{backupDone?.file_name}</span>
            </div>
            <div className="flex items-center justify-between gap-4">
              <span className="text-slate-500">size</span>
              <span className="text-slate-300">{backupDone ? fmtBytes(backupDone.size_bytes) : '—'}</span>
            </div>
            <div className="flex items-center justify-between gap-4">
              <span className="text-slate-500">provider</span>
              <span className="text-slate-300">{backupDone?.provider}</span>
            </div>
          </div>
          <DialogFooter>
            <Button onClick={() => setBackupDone(null)} className="bg-emerald-500 text-slate-950 hover:bg-emerald-400">
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ---- Custom provider dialog ---- */}
      <Dialog
        open={customOpen}
        onOpenChange={(o) => {
          setCustomOpen(o);
          if (!o) setCustom({ ...EMPTY_CUSTOM });
        }}
      >
        <DialogContent className="max-w-lg border-slate-800 bg-[#0d1322]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-slate-100">
              <Plus className="size-4 text-emerald-400" />
              Add Custom Storage Provider
            </DialogTitle>
            <DialogDescription className="leading-relaxed text-slate-500">
              Register any S3-compatible bucket, Supabase project, Firebase bucket, GitHub repo, WebDAV share or local
              path as a storage target.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="cp-name" className="text-xs text-slate-400">
                Name
              </Label>
              <Input
                id="cp-name"
                value={custom.name}
                onChange={(e) => setCustom({ ...custom, name: e.target.value })}
                placeholder="My S3 Bucket"
                className="border-slate-800 bg-[#070b13] font-mono text-sm"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-slate-400">Type</Label>
              <Select value={custom.type} onValueChange={(v) => setCustom({ ...custom, type: v })}>
                <SelectTrigger className="w-full border-slate-800 bg-[#070b13] text-sm" aria-label="Provider type">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="border-slate-800 bg-[#0d1322]">
                  {CUSTOM_TYPES.map((t) => (
                    <SelectItem key={t} value={t} className="font-mono text-sm">
                      {t}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="cp-endpoint" className="text-xs text-slate-400">
                Endpoint
              </Label>
              <Input
                id="cp-endpoint"
                value={custom.endpoint}
                onChange={(e) => setCustom({ ...custom, endpoint: e.target.value })}
                placeholder="https://s3.us-east-1.amazonaws.com"
                className="border-slate-800 bg-[#070b13] font-mono text-sm"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="cp-bucket" className="text-xs text-slate-400">
                Bucket
              </Label>
              <Input
                id="cp-bucket"
                value={custom.bucket}
                onChange={(e) => setCustom({ ...custom, bucket: e.target.value })}
                placeholder="nova-storage"
                className="border-slate-800 bg-[#070b13] font-mono text-sm"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="cp-ak" className="text-xs text-slate-400">
                Access Key
              </Label>
              <Input
                id="cp-ak"
                value={custom.access_key}
                onChange={(e) => setCustom({ ...custom, access_key: e.target.value })}
                autoComplete="off"
                className="border-slate-800 bg-[#070b13] font-mono text-sm"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="cp-sk" className="text-xs text-slate-400">
                Secret
              </Label>
              <Input
                id="cp-sk"
                type="password"
                value={custom.secret_key}
                onChange={(e) => setCustom({ ...custom, secret_key: e.target.value })}
                autoComplete="off"
                className="border-slate-800 bg-[#070b13] font-mono text-sm"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="cp-region" className="text-xs text-slate-400">
                Region
              </Label>
              <Input
                id="cp-region"
                value={custom.region}
                onChange={(e) => setCustom({ ...custom, region: e.target.value })}
                placeholder="us-east-1"
                className="border-slate-800 bg-[#070b13] font-mono text-sm"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="cp-ft" className="text-xs text-slate-400">
                Free Tier Note
              </Label>
              <Input
                id="cp-ft"
                value={custom.free_tier}
                onChange={(e) => setCustom({ ...custom, free_tier: e.target.value })}
                placeholder="5 GB free, no card required"
                className="border-slate-800 bg-[#070b13] font-mono text-sm"
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setCustomOpen(false);
                setCustom({ ...EMPTY_CUSTOM });
              }}
              className="border-slate-700 bg-transparent text-slate-300"
            >
              Cancel
            </Button>
            <Button
              onClick={() => void handleCreateCustom()}
              disabled={busy !== null || !custom.name.trim()}
              className="bg-emerald-500 text-slate-950 hover:bg-emerald-400"
            >
              {busy === 'custom' && <Loader2 className="size-4 animate-spin" />}
              Create Provider
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ---- Delete confirmation ---- */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(o) => !o && setDeleteTarget(null)}>
        <AlertDialogContent className="border-slate-800 bg-[#0d1322]">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-slate-100">Delete file?</AlertDialogTitle>
            <AlertDialogDescription className="leading-relaxed text-slate-400">
              {deleteTarget ? (
                <>
                  <span className="font-mono text-rose-300">{deleteTarget.name}</span> will be permanently removed from{' '}
                  {deleteTarget ? providerName(deleteTarget.provider_key, info.providers) : 'storage'}. This action
                  cannot be undone.
                </>
              ) : (
                'This file will be permanently removed.'
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-slate-700 bg-transparent text-slate-300">Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deleteTarget && void handleDelete(deleteTarget)}
              className="bg-rose-600 text-white hover:bg-rose-500"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
