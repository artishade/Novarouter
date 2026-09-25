'use client';

/** ProvidersTab — upstream provider management: presets, keys, sign-in, tests. */
import { useCallback, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { toast } from 'sonner';
import {
  Activity,
  ChevronDown,
  ChevronUp,
  Cpu,
  ExternalLink,
  HeartPulse,
  KeyRound,
  Link2,
  Loader2,
  LogIn,
  LogOut,
  MoreVertical,
  Plus,
  RefreshCw,
  Snowflake,
  Sparkles,
  Timer,
  Trash2,
  Zap,
} from 'lucide-react';

import { api } from '@/lib/api';
import type {
  GatewayStats,
  MetaConfig,
  Provider,
  ProviderPreset,
  UpstreamKey,
} from '@/lib/types';
import { fmtClock, fmtNum, timeAgo } from '@/lib/format';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';

type TestResult = { ok: boolean; status: string; latency_ms: number; message: string };

const CUSTOM_PRESET_KEY = '__custom__';

/* ------------------------------------------------------------------ */
/* Add provider dialog (preset grid -> config step)                    */
/* ------------------------------------------------------------------ */

function AddProviderDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: () => void;
}) {
  const [presets, setPresets] = useState<ProviderPreset[]>([]);
  const [loadingPresets, setLoadingPresets] = useState(false);
  const [step, setStep] = useState<1 | 2>(1);
  const [selected, setSelected] = useState<ProviderPreset | null>(null);
  const [name, setName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [prefix, setPrefix] = useState('');
  const [authUrl, setAuthUrl] = useState('');
  const [freeTier, setFreeTier] = useState('');
  const [priority, setPriority] = useState(50);
  const [keysText, setKeysText] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoadingPresets(true);
    api
      .getProviderPresets()
      .then((rows) => setPresets(rows))
      .catch((e: unknown) => toast.error(e instanceof Error ? e.message : 'Failed to load presets'))
      .finally(() => setLoadingPresets(false));
  }, [open]);

  const reset = useCallback(() => {
    setStep(1);
    setSelected(null);
    setName('');
    setBaseUrl('');
    setPrefix('');
    setAuthUrl('');
    setFreeTier('');
    setPriority(50);
    setKeysText('');
  }, []);

  const choosePreset = (p: ProviderPreset) => {
    setSelected(p);
    const custom = p.key === CUSTOM_PRESET_KEY;
    setName(custom ? '' : p.name);
    setBaseUrl(custom ? '' : p.base_url);
    setPrefix(custom ? '' : p.prefix);
    setAuthUrl(p.auth_url ?? '');
    setFreeTier(p.free_tier ?? '');
    setPriority(p.priority);
    setKeysText('');
    setStep(2);
  };

  const submit = async () => {
    if (!selected) return;
    if (!name.trim()) {
      toast.error('Give the provider a name');
      return;
    }
    setSubmitting(true);
    try {
      const res = (await api.createProvider({
        name: name.trim(),
        kind: selected.kind,
        base_url: baseUrl.trim() || undefined,
        prefix: prefix.trim() || undefined,
        priority,
        api_keys: keysText.trim() || undefined,
        free_tier: freeTier.trim() || undefined,
        auth_url: authUrl.trim() || undefined,
      })) as { id: number; keys_added: number; models_added?: number; sync_error?: string };
      if (res.sync_error) {
        toast.warning(`${name.trim()} added · ${res.keys_added} key(s) imported`, {
          description: `Model discovery failed: ${res.sync_error}. Use "Sync models" to retry.`,
        });
      } else if ((res.models_added ?? 0) > 0) {
        toast.success(`${name.trim()} added · ${res.keys_added} key(s) · ${res.models_added} live models discovered`);
      } else {
        toast.success(`${name.trim()} added · ${res.keys_added} key(s) imported`);
      }
      reset();
      onOpenChange(false);
      onCreated();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to create provider');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) reset();
        onOpenChange(v);
      }}
    >
      <DialogContent className="max-w-2xl border-slate-800 bg-[#0d1322]">
        <DialogHeader>
          <DialogTitle>Add provider</DialogTitle>
          <DialogDescription>
            {step === 1
              ? 'Pick a preset to prefill the endpoint, or start from a blank custom provider.'
              : 'Confirm the endpoint details, set routing priority and optionally paste API keys.'}
          </DialogDescription>
        </DialogHeader>

        {step === 1 && (
          <ScrollArea className="max-h-[380px] pr-3">
            {loadingPresets ? (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {Array.from({ length: 9 }).map((_, i) => (
                  <Skeleton key={i} className="h-[86px] rounded-lg bg-slate-800/60" />
                ))}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                <button
                  type="button"
                  onClick={() =>
                    choosePreset({
                      key: CUSTOM_PRESET_KEY,
                      name: 'Custom provider',
                      kind: 'openai',
                      base_url: '',
                      prefix: '',
                      key_hint: 'sk-...',
                      free_tier: null,
                      docs_url: null,
                      auth_url: null,
                      requires_signin: false,
                      color: '#64748b',
                      priority: 50,
                    })
                  }
                  className="rounded-lg border border-dashed border-slate-700 p-3 text-left transition hover:border-emerald-500/60 hover:bg-emerald-500/5"
                  aria-label="Add a custom provider"
                >
                  <span className="flex items-center gap-2">
                    <Plus className="size-3.5 text-emerald-400" />
                    <span className="text-sm font-medium text-slate-200">Custom</span>
                  </span>
                  <span className="mt-2 block text-[11px] leading-snug text-slate-500">
                    Any OpenAI-compatible endpoint
                  </span>
                </button>

                {presets.map((p) => (
                  <button
                    key={p.key}
                    type="button"
                    onClick={() => choosePreset(p)}
                    className="rounded-lg border border-slate-800 bg-black/20 p-3 text-left transition hover:border-emerald-500/60 hover:bg-emerald-500/5"
                    aria-label={`Select preset ${p.name}`}
                  >
                    <span className="flex items-center gap-1.5">
                      <span
                        className="size-2 shrink-0 rounded-full"
                        style={{ backgroundColor: p.color }}
                        aria-hidden
                      />
                      <span className="truncate text-sm font-medium text-slate-200">{p.name}</span>
                    </span>
                    <span className="mt-1.5 flex flex-wrap items-center gap-1">
                      <Badge variant="outline" className="border-slate-700 px-1 text-[10px] text-slate-400">
                        {p.kind}
                      </Badge>
                      {p.requires_signin && (
                        <Badge className="border-amber-900/60 bg-amber-950/40 px-1 text-[10px] text-amber-300">
                          Sign-in required
                        </Badge>
                      )}
                    </span>
                    <span className="mt-1.5 flex items-center gap-1 text-[11px] text-slate-500">
                      <Sparkles className="size-3 shrink-0 text-slate-600" />
                      <span className="truncate">{p.free_tier ?? 'No free tier listed'}</span>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </ScrollArea>
        )}

        {step === 2 && selected && (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="np-name">Name</Label>
                <Input
                  id="np-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Provider name"
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="np-prefix">Model prefix</Label>
                <Input
                  id="np-prefix"
                  value={prefix}
                  onChange={(e) => setPrefix(e.target.value)}
                  placeholder="e.g. groq/"
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="np-url">Base URL</Label>
                <Input
                  id="np-url"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://api.provider.com/v1"
                  className="font-mono text-xs"
                />
              </div>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label>Routing priority</Label>
                <span className="font-mono text-xs text-emerald-300">{priority}</span>
              </div>
              <Slider
                value={[priority]}
                min={0}
                max={100}
                step={1}
                onValueChange={(v) => setPriority(v[0] ?? 50)}
                aria-label="Routing priority"
              />
              <p className="text-[11px] text-slate-500">Lower number = tried earlier when routing.</p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="np-keys">API keys (optional)</Label>
              <Textarea
                id="np-keys"
                value={keysText}
                onChange={(e) => setKeysText(e.target.value)}
                placeholder={`One key per line\n${selected.key_hint}`}
                className="min-h-[72px] font-mono text-xs"
              />
              <p className="text-[11px] text-slate-500">
                Multiple keys enable round-robin rotation with weighted cooldowns.
              </p>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="np-auth">Auth / console URL (optional)</Label>
                <Input
                  id="np-auth"
                  value={authUrl}
                  onChange={(e) => setAuthUrl(e.target.value)}
                  placeholder="https://console.provider.com/keys"
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="np-free">Free tier note (optional)</Label>
                <Input
                  id="np-free"
                  value={freeTier}
                  onChange={(e) => setFreeTier(e.target.value)}
                  placeholder="e.g. 14k req/day free"
                  className="text-xs"
                />
              </div>
            </div>
          </div>
        )}

        <DialogFooter className="gap-2">
          {step === 2 && (
            <Button variant="outline" size="sm" onClick={() => setStep(1)} className="border-slate-700">
              Back to presets
            </Button>
          )}
          {step === 2 && (
            <Button size="sm" onClick={submit} disabled={submitting} className="bg-emerald-500 text-emerald-950 hover:bg-emerald-400">
              {submitting && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
              Create provider
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ */
/* Sign-in dialog (open console -> paste key -> session + key)         */
/* ------------------------------------------------------------------ */

function SignInDialog({
  provider,
  onClose,
  onDone,
}: {
  provider: Provider | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (provider) setKey('');
  }, [provider]);

  const submit = async () => {
    if (!provider) return;
    if (!key.trim()) {
      toast.error('Paste the API key you copied from the provider console');
      return;
    }
    setBusy(true);
    try {
      await api.signInProvider(provider.id);
      await api.createKey({ provider_id: provider.id, api_key: key.trim(), label: 'sign-in' });
      toast.success(`Signed in to ${provider.name}`);
      onClose();
      onDone();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Sign-in failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={!!provider} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md border-slate-800 bg-[#0d1322]">
        <DialogHeader>
          <DialogTitle>Sign in to {provider?.name}</DialogTitle>
          <DialogDescription>
            Two quick steps: grab an API key from the provider console, then paste it here. NovaRouter
            stores it as an upstream key and marks the provider connected.
          </DialogDescription>
        </DialogHeader>

        {provider && (
          <div className="space-y-4">
            <ol className="space-y-2 text-xs text-slate-400">
              <li className="flex items-start gap-2">
                <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border border-emerald-800 text-[9px] font-semibold text-emerald-300">
                  1
                </span>
                <span>
                  Open{' '}
                  <a
                    href={provider.auth_url ?? '#'}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 font-medium text-emerald-300 underline-offset-2 hover:underline"
                  >
                    Get your API key
                    <ExternalLink className="size-3" aria-hidden />
                  </a>{' '}
                  in a new tab and copy a key.
                </span>
              </li>
              <li className="flex items-start gap-2">
                <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border border-emerald-800 text-[9px] font-semibold text-emerald-300">
                  2
                </span>
                <span>Paste the key below — it is stored masked and never shown in full.</span>
              </li>
            </ol>

            <div className="space-y-1.5">
              <Label htmlFor="si-key">API key</Label>
              <Input
                id="si-key"
                type="password"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="Paste key here"
                className="font-mono text-xs"
                autoComplete="off"
              />
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={onClose} className="border-slate-700">
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={submit}
            disabled={busy || !provider}
            className="bg-emerald-500 text-emerald-950 hover:bg-emerald-400"
          >
            {busy && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
            Connect
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ */
/* Main tab                                                            */
/* ------------------------------------------------------------------ */

export function ProvidersTab({
  meta,
  stats,
  onRefresh,
}: {
  meta: MetaConfig | null;
  stats: GatewayStats | null;
  onRefresh: () => void;
}) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [addOpen, setAddOpen] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [keysMap, setKeysMap] = useState<Record<number, UpstreamKey[]>>({});
  const [keysLoading, setKeysLoading] = useState<Record<number, boolean>>({});
  const [keyLabel, setKeyLabel] = useState('');
  const [keyValue, setKeyValue] = useState('');
  const [addingKey, setAddingKey] = useState(false);
  const [busyTest, setBusyTest] = useState<Record<number, boolean>>({});
  const [testResults, setTestResults] = useState<Record<number, TestResult>>({});
  const [testingAll, setTestingAll] = useState(false);
  const [signInFor, setSignInFor] = useState<Provider | null>(null);
  const [deleteFor, setDeleteFor] = useState<Provider | null>(null);

  const load = useCallback(async () => {
    try {
      const rows = await api.getProviders();
      setProviders(rows);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load providers');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const refreshAll = useCallback(() => {
    void load();
    onRefresh();
  }, [load, onRefresh]);

  const loadKeys = useCallback(async (providerId: number) => {
    setKeysLoading((m) => ({ ...m, [providerId]: true }));
    try {
      const keys = await api.getKeys(providerId);
      setKeysMap((m) => ({ ...m, [providerId]: keys }));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load keys');
    } finally {
      setKeysLoading((m) => ({ ...m, [providerId]: false }));
    }
  }, []);

  const toggleExpanded = (p: Provider) => {
    if (expandedId === p.id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(p.id);
    setKeyLabel('');
    setKeyValue('');
    if (!keysMap[p.id]) void loadKeys(p.id);
  };

  const runTest = useCallback(async (p: Provider) => {
    setBusyTest((b) => ({ ...b, [p.id]: true }));
    try {
      const r = (await api.testProvider(p.id)) as TestResult;
      setTestResults((m) => ({ ...m, [p.id]: r }));
      if (r.ok) {
        toast.success(`${p.name}: ${r.status} · ${r.latency_ms}ms`, { description: r.message });
      } else {
        toast.error(`${p.name}: ${r.status}`, { description: r.message });
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : `Test failed for ${p.name}`);
    } finally {
      setBusyTest((b) => ({ ...b, [p.id]: false }));
    }
  }, []);

  const runTestAll = async () => {
    const targets = providers.filter((p) => p.enabled && (p.key_count > 0 || p.kind === 'builtin'));
    if (targets.length === 0) {
      toast.info('No providers with keys to test yet');
      return;
    }
    setTestingAll(true);
    let okCount = 0;
    for (const p of targets) {
      setBusyTest((b) => ({ ...b, [p.id]: true }));
      try {
        const r = (await api.testProvider(p.id)) as TestResult;
        setTestResults((m) => ({ ...m, [p.id]: r }));
        if (r.ok) okCount += 1;
      } catch {
        /* keep sweeping */
      } finally {
        setBusyTest((b) => ({ ...b, [p.id]: false }));
      }
    }
    toast.success(`Test sweep complete — ${okCount}/${targets.length} providers reachable`);
    setTestingAll(false);
    refreshAll();
  };

  const toggleProvider = async (p: Provider, checked: boolean) => {
    setProviders((rows) => rows.map((r) => (r.id === p.id ? { ...r, enabled: checked } : r)));
    try {
      await api.updateProvider(p.id, { enabled: checked });
      toast.success(`${p.name} ${checked ? 'enabled' : 'disabled'}`);
      onRefresh();
    } catch (e) {
      setProviders((rows) => rows.map((r) => (r.id === p.id ? { ...r, enabled: !checked } : r)));
      toast.error(e instanceof Error ? e.message : 'Failed to update provider');
    }
  };

  const submitKey = async (p: Provider) => {
    const raw = keyValue.trim();
    if (!raw) {
      toast.error('Paste at least one API key');
      return;
    }
    setAddingKey(true);
    try {
      const isBulk = /[\n,]/.test(raw);
      if (isBulk) {
        const res = (await api.createKeysBulk({
          provider_id: p.id,
          keys: raw,
          label_prefix: keyLabel.trim() || 'imported',
        })) as { added: number };
        toast.success(`${res.added} key(s) added to ${p.name}`);
      } else {
        await api.createKey({
          provider_id: p.id,
          api_key: raw,
          label: keyLabel.trim() || 'manual',
        });
        toast.success(`Key added to ${p.name}`);
      }
      setKeyLabel('');
      setKeyValue('');
      await loadKeys(p.id);
      onRefresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to add key');
    } finally {
      setAddingKey(false);
    }
  };

  const removeKey = async (p: Provider, keyId: number) => {
    try {
      await api.deleteKey(keyId);
      toast.success('Key deleted');
      await loadKeys(p.id);
      onRefresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to delete key');
    }
  };

  const signOut = async (p: Provider) => {
    try {
      await api.signOutProvider(p.id);
      toast.success(`Signed out of ${p.name}`);
      refreshAll();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Sign-out failed');
    }
  };

  const removeProvider = async () => {
    if (!deleteFor) return;
    const target = deleteFor;
    setDeleteFor(null);
    try {
      await api.deleteProvider(target.id);
      toast.success(`${target.name} removed`);
      refreshAll();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to delete provider');
    }
  };

  const q = search.trim().toLowerCase();
  const visible = providers.filter(
    (p) =>
      !q ||
      p.name.toLowerCase().includes(q) ||
      p.key.toLowerCase().includes(q) ||
      p.prefix.toLowerCase().includes(q) ||
      p.base_url.toLowerCase().includes(q)
  );

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold text-slate-100">Providers</h2>
        <Badge variant="outline" className="border-slate-700 text-slate-400">
          {providers.length} configured
        </Badge>
        {stats && (
          <Badge variant="outline" className="border-emerald-900/70 bg-emerald-950/30 text-emerald-300">
            {stats.active_keys} upstream keys · {stats.connected_providers} signed in
          </Badge>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search providers..."
            aria-label="Search providers"
            className="h-9 w-48 border-slate-800 bg-black/30 text-xs"
          />
          <Button
            variant="outline"
            size="sm"
            onClick={runTestAll}
            disabled={testingAll}
            className="border-slate-700 text-slate-300 hover:bg-slate-800"
          >
            {testingAll ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <Zap className="size-3.5" aria-hidden />}
            Test All
          </Button>
          <Button
            size="sm"
            onClick={() => setAddOpen(true)}
            className="bg-emerald-500 text-emerald-950 hover:bg-emerald-400"
          >
            <Plus className="size-3.5" aria-hidden />
            Add Provider
          </Button>
        </div>
      </div>

      {/* Cards */}
      {loading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-52 rounded-xl bg-slate-800/50" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-800 bg-[#0d1322]/60 p-10 text-center text-sm text-slate-500">
          {providers.length === 0 ? 'No providers yet — add your first upstream.' : 'No providers match your search.'}
        </div>
      ) : (
        <div className="max-h-[calc(100vh-330px)] space-y-4 overflow-y-auto pr-1">
          {visible.map((p, i) => {
            const tr = testResults[p.id];
            const keys = keysMap[p.id] ?? [];
            const now = Date.now();
            return (
              <motion.div
                key={p.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.25, delay: Math.min(i * 0.04, 0.3) }}
              >
                <div
                  className="overflow-hidden rounded-xl border border-slate-800 bg-[#0d1322]/80"
                  style={{ borderLeft: `3px solid ${p.color || '#10b981'}` }}
                >
                  <div className="p-4">
                    {/* Card header */}
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-slate-100">{p.name}</span>
                      <Badge variant="outline" className="border-slate-700 px-1.5 text-[10px] text-slate-400">
                        {p.kind}
                      </Badge>
                      <Badge
                        variant="outline"
                        className="border-slate-700 px-1.5 font-mono text-[10px] text-slate-500"
                        title="Routing priority (lower = tried earlier)"
                      >
                        P{p.priority}
                      </Badge>
                      <div className="ml-auto flex items-center gap-2">
                        <Switch
                          checked={p.enabled}
                          onCheckedChange={(c) => void toggleProvider(p, c)}
                          aria-label={`Toggle provider ${p.name}`}
                        />
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="size-7 text-slate-500 hover:text-slate-200"
                              aria-label={`Actions for ${p.name}`}
                            >
                              <MoreVertical className="size-4" aria-hidden />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent
                            align="end"
                            className="border-slate-800 bg-[#0d1322] text-slate-200"
                          >
                            <DropdownMenuLabel className="text-[11px] text-slate-500">
                              {p.name}
                            </DropdownMenuLabel>
                            <DropdownMenuItem onClick={() => void runTest(p)} className="gap-2 text-xs">
                              <Zap className="size-3.5 text-amber-300" aria-hidden /> Test connection
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => toggleExpanded(p)} className="gap-2 text-xs">
                              <KeyRound className="size-3.5 text-emerald-300" aria-hidden /> Keys
                            </DropdownMenuItem>
                            {p.auth_url && !p.session && (
                              <DropdownMenuItem onClick={() => setSignInFor(p)} className="gap-2 text-xs">
                                <LogIn className="size-3.5 text-emerald-300" aria-hidden /> Sign in
                              </DropdownMenuItem>
                            )}
                            {p.session && (
                              <DropdownMenuItem onClick={() => void signOut(p)} className="gap-2 text-xs">
                                <LogOut className="size-3.5 text-rose-300" aria-hidden /> Sign out
                              </DropdownMenuItem>
                            )}
                            {p.key !== 'novafree' && (
                              <>
                                <DropdownMenuSeparator className="bg-slate-800" />
                                <DropdownMenuItem
                                  onClick={() => setDeleteFor(p)}
                                  className="gap-2 text-xs text-rose-300 focus:text-rose-300"
                                >
                                  <Trash2 className="size-3.5" aria-hidden /> Delete provider
                                </DropdownMenuItem>
                              </>
                            )}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </div>

                    {/* Endpoint lines */}
                    <div className="mt-2 space-y-1">
                      <p className="truncate font-mono text-[11px] text-slate-500" title={p.base_url}>
                        <span className="text-slate-600">prefix </span>
                        {p.prefix || '—'}
                      </p>
                      <p className="truncate font-mono text-[11px] text-slate-500" title={p.base_url}>
                        <span className="text-slate-600">url&nbsp;&nbsp;&nbsp;&nbsp; </span>
                        {p.base_url}
                      </p>
                    </div>

                    {/* Stats row */}
                    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-400">
                      <span className="flex items-center gap-1" title="Upstream keys">
                        <KeyRound className="size-3.5 text-slate-500" aria-hidden /> {p.key_count}
                      </span>
                      <span className="flex items-center gap-1" title="Models in catalogue">
                        <Cpu className="size-3.5 text-slate-500" aria-hidden /> {p.model_count}
                      </span>
                      <span className="flex items-center gap-1 text-emerald-300/90" title="Healthy models">
                        <HeartPulse className="size-3.5" aria-hidden /> {p.ok_count}
                      </span>
                      <span className="flex items-center gap-1 text-amber-300/90" title="Cooling models">
                        <Snowflake className="size-3.5" aria-hidden /> {p.cooling_count}
                      </span>
                      {tr && (
                        <span
                          className={`flex items-center gap-1 font-mono ${
                            tr.ok ? 'text-emerald-300' : 'text-rose-300'
                          }`}
                          title={tr.message}
                        >
                          <Activity className="size-3.5" aria-hidden /> {tr.status}
                          {tr.ok ? ` · ${tr.latency_ms}ms` : ''}
                        </span>
                      )}
                    </div>

                    {/* Free tier note */}
                    {p.free_tier && (
                      <p className="mt-2 flex items-center gap-1.5 text-[11px] text-slate-500">
                        <Sparkles className="size-3 shrink-0 text-slate-600" aria-hidden />
                        <span className="truncate">{p.free_tier}</span>
                      </p>
                    )}

                    {/* Session chip */}
                    {p.session && (
                      <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-emerald-900/60 bg-emerald-950/30 px-2.5 py-1.5">
                        <span className="flex size-6 items-center justify-center rounded-full bg-emerald-500/20 text-[10px] font-semibold uppercase text-emerald-300">
                          {(p.session.display_name || p.session.username).slice(0, 1)}
                        </span>
                        <span className="text-[11px] text-slate-300">
                          Signed in as{' '}
                          <span className="font-medium text-emerald-300">{p.session.username}</span> (
                          {p.session.plan})
                        </span>
                        <span className="ml-auto flex items-center gap-1 text-[10px] uppercase tracking-wide text-emerald-400">
                          <span className="size-1.5 animate-pulse rounded-full bg-emerald-400" aria-hidden />
                          connected
                        </span>
                      </div>
                    )}

                    {/* Expand keys */}
                    <button
                      type="button"
                      onClick={() => toggleExpanded(p)}
                      className="mt-3 flex w-full items-center gap-1.5 text-[11px] text-slate-500 transition hover:text-emerald-300"
                      aria-expanded={expandedId === p.id}
                      aria-label={`${expandedId === p.id ? 'Hide' : 'Show'} keys for ${p.name}`}
                    >
                      {expandedId === p.id ? (
                        <ChevronUp className="size-3.5" aria-hidden />
                      ) : (
                        <ChevronDown className="size-3.5" aria-hidden />
                      )}
                      Keys ({p.key_count})
                    </button>
                  </div>

                  {/* Inline keys panel */}
                  {expandedId === p.id && (
                    <div className="border-t border-slate-800 bg-black/20 p-4">
                      <div className="mb-2 flex items-center justify-between">
                        <span className="text-[10px] font-medium uppercase tracking-wider text-slate-500">
                          Upstream keys
                        </span>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-6 text-slate-500 hover:text-emerald-300"
                          onClick={() => void loadKeys(p.id)}
                          aria-label={`Reload keys for ${p.name}`}
                        >
                          <RefreshCw className="size-3" aria-hidden />
                        </Button>
                      </div>

                      {keysLoading[p.id] ? (
                        <div className="space-y-2">
                          {Array.from({ length: 2 }).map((_, k) => (
                            <Skeleton key={k} className="h-9 rounded-lg bg-slate-800/50" />
                          ))}
                        </div>
                      ) : keys.length === 0 ? (
                        <p className="rounded-lg border border-dashed border-slate-800 px-3 py-3 text-[11px] text-slate-500">
                          No keys yet — add one below or use Sign In{p.auth_url ? '' : ' via the menu'}.
                        </p>
                      ) : (
                        <ScrollArea className="max-h-56 pr-2">
                          <div className="space-y-2">
                            {keys.map((k) => {
                              const cooling = !!k.cooldown_until && k.cooldown_until > now;
                              return (
                                <div
                                  key={k.id}
                                  className="flex flex-wrap items-center gap-x-2.5 gap-y-1 rounded-lg border border-slate-800/80 bg-[#0d1322]/70 px-2.5 py-2"
                                >
                                  <KeyRound className="size-3.5 shrink-0 text-slate-600" aria-hidden />
                                  <span className="font-mono text-[11px] text-slate-300">{k.api_key_preview}</span>
                                  {k.label && (
                                    <span className="max-w-[140px] truncate text-[11px] text-slate-500" title={k.label}>
                                      {k.label}
                                    </span>
                                  )}
                                  <Badge
                                    variant="outline"
                                    className="border-slate-700 px-1 font-mono text-[10px] text-slate-500"
                                    title="Rotation weight"
                                  >
                                    w{k.weight}
                                  </Badge>
                                  <span className="text-[10px] text-slate-500" title="Requests / errors">
                                    {fmtNum(k.req_count)} req · {fmtNum(k.err_count)} err
                                  </span>
                                  {cooling ? (
                                    <Badge className="border-amber-900/60 bg-amber-950/40 px-1.5 text-[10px] text-amber-300">
                                      <Timer className="size-3" aria-hidden />
                                      cooldown until {fmtClock(k.cooldown_until)}
                                    </Badge>
                                  ) : (
                                    <span className="text-[10px] text-slate-600">last used {timeAgo(k.last_used_at)}</span>
                                  )}
                                  <button
                                    type="button"
                                    onClick={() => void removeKey(p, k.id)}
                                    className="ml-auto text-slate-600 transition hover:text-rose-400"
                                    aria-label={`Delete key ${k.api_key_preview}`}
                                  >
                                    <Trash2 className="size-3.5" aria-hidden />
                                  </button>
                                </div>
                              );
                            })}
                          </div>
                        </ScrollArea>
                      )}

                      <Separator className="my-3 bg-slate-800/70" />

                      <div className="rounded-lg border border-dashed border-slate-700/80 p-3">
                        <div className="flex flex-col gap-2 sm:flex-row">
                          <Input
                            value={keyLabel}
                            onChange={(e) => setKeyLabel(e.target.value)}
                            placeholder="Label (optional)"
                            aria-label={`Label for new key on ${p.name}`}
                            className="h-8 border-slate-800 bg-black/30 text-xs sm:w-40"
                          />
                          <Input
                            value={keyValue}
                            onChange={(e) => setKeyValue(e.target.value)}
                            placeholder="Paste API key"
                            aria-label={`API key value for ${p.name}`}
                            autoComplete="off"
                            className="h-8 border-slate-800 bg-black/30 font-mono text-xs"
                          />
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={addingKey}
                            onClick={() => void submitKey(p)}
                            className="h-8 shrink-0 border-emerald-800/70 text-emerald-300 hover:bg-emerald-950/50 hover:text-emerald-200"
                          >
                            {addingKey ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <Plus className="size-3.5" aria-hidden />}
                            Add key
                          </Button>
                        </div>
                        <p className="mt-1.5 flex items-center gap-1 text-[11px] text-slate-600">
                          <Link2 className="size-3" aria-hidden />
                          Bulk paste: separate multiple keys with newlines or commas to import them at once.
                        </p>
                      </div>
                    </div>
                  )}
                </div>
              </motion.div>
            );
          })}
        </div>
      )}

      {/* footer meta hint */}
      {meta && (
        <p className="text-[11px] text-slate-600">
          Cooldowns: 429 → {meta.cooldowns['429']}s · 402 → {meta.cooldowns['402']}s · 5xx →{' '}
          {meta.cooldowns['5xx']}s · key rotation is weighted by key priority.
        </p>
      )}

      <AddProviderDialog open={addOpen} onOpenChange={setAddOpen} onCreated={refreshAll} />

      <SignInDialog provider={signInFor} onClose={() => setSignInFor(null)} onDone={refreshAll} />

      <AlertDialog open={!!deleteFor} onOpenChange={(v) => !v && setDeleteFor(null)}>
        <AlertDialogContent className="border-slate-800 bg-[#0d1322]">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {deleteFor?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes the provider along with its {deleteFor?.model_count ?? 0} models and{' '}
              {deleteFor?.key_count ?? 0} keys. Gateway routing for its models falls back to the
              default chain.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-slate-700 bg-transparent text-slate-300 hover:bg-slate-800 hover:text-slate-100">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void removeProvider()}
              className="bg-rose-600 text-white hover:bg-rose-500"
            >
              Delete provider
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
