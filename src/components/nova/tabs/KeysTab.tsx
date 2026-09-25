'use client';

/** KeysTab — client access keys: issue, reveal, limits, usage. */
import { useCallback, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { toast } from 'sonner';
import {
  Activity,
  CalendarDays,
  Check,
  Clock,
  Coins,
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Pencil,
  Plus,
  ShieldAlert,
  Trash2,
} from 'lucide-react';

import { api } from '@/lib/api';
import type { ClientKey, GatewayStats, MetaConfig } from '@/lib/types';
import { fmtDate, fmtNum, timeAgo } from '@/lib/format';

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
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';

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

/* ------------------------------------------------------------------ */
/* Issue new key dialog                                                */
/* ------------------------------------------------------------------ */

function IssueKeyDialog({
  open,
  onOpenChange,
  onIssued,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onIssued: (k: ClientKey) => void;
}) {
  const [name, setName] = useState('');
  const [allowedModels, setAllowedModels] = useState('*');
  const [rpm, setRpm] = useState('60');
  const [tpd, setTpd] = useState('0');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setName('');
      setAllowedModels('*');
      setRpm('60');
      setTpd('0');
    }
  }, [open]);

  const submit = async () => {
    if (!name.trim()) {
      toast.error('Give the key a name so you can recognise it later');
      return;
    }
    const rpmN = Number(rpm);
    const tpdN = Number(tpd);
    if (!Number.isFinite(rpmN) || rpmN < 0) {
      toast.error('RPM limit must be a non-negative number');
      return;
    }
    if (!Number.isFinite(tpdN) || tpdN < 0) {
      toast.error('TPD limit must be a non-negative number (0 = unlimited)');
      return;
    }
    setBusy(true);
    try {
      const created = (await api.createClientKey({
        name: name.trim(),
        allowed_models: allowedModels.trim() || '*',
        rpm_limit: Math.round(rpmN),
        tpd_limit: Math.round(tpdN),
      })) as ClientKey;
      toast.success(`Key "${created.name}" issued`);
      onOpenChange(false);
      onIssued(created);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to issue key');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md border-slate-800 bg-[#0d1322]">
        <DialogHeader>
          <DialogTitle>Issue new client key</DialogTitle>
          <DialogDescription>
            Keys authenticate requests to the gateway. The full token is shown once after issuing.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="ck-name">Name</Label>
            <Input
              id="ck-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. my-app-production"
              className="text-xs"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ck-models">Allowed models</Label>
            <Input
              id="ck-models"
              value={allowedModels}
              onChange={(e) => setAllowedModels(e.target.value)}
              placeholder="* or comma-separated exposed ids"
              className="font-mono text-xs"
            />
            <p className="text-[11px] text-slate-500">Use * to allow every enabled model.</p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="ck-rpm">RPM limit</Label>
              <Input
                id="ck-rpm"
                type="number"
                min={0}
                value={rpm}
                onChange={(e) => setRpm(e.target.value)}
                className="font-mono text-xs"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ck-tpd">TPD limit (0 = unlimited)</Label>
              <Input
                id="ck-tpd"
                type="number"
                min={0}
                value={tpd}
                onChange={(e) => setTpd(e.target.value)}
                className="font-mono text-xs"
              />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)} className="border-slate-700">
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={() => void submit()}
            disabled={busy}
            className="bg-emerald-500 text-emerald-950 hover:bg-emerald-400"
          >
            {busy && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
            Issue key
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ */
/* Edit key dialog (limits + name)                                     */
/* ------------------------------------------------------------------ */

function EditKeyDialog({
  target,
  onClose,
  onSaved,
}: {
  target: ClientKey | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState('');
  const [allowedModels, setAllowedModels] = useState('*');
  const [rpm, setRpm] = useState('0');
  const [tpd, setTpd] = useState('0');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (target) {
      setName(target.name);
      setAllowedModels(target.allowed_models || '*');
      setRpm(String(target.rpm_limit));
      setTpd(String(target.tpd_limit));
    }
  }, [target]);

  const submit = async () => {
    if (!target) return;
    const rpmN = Number(rpm);
    const tpdN = Number(tpd);
    if (!Number.isFinite(rpmN) || rpmN < 0 || !Number.isFinite(tpdN) || tpdN < 0) {
      toast.error('Limits must be non-negative numbers');
      return;
    }
    setBusy(true);
    try {
      await api.updateClientKey(target.id, {
        name: name.trim() || target.name,
        allowed_models: allowedModels.trim() || '*',
        rpm_limit: Math.round(rpmN),
        tpd_limit: Math.round(tpdN),
      });
      toast.success(`Key "${target.name}" updated`);
      onClose();
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to update key');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={!!target} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md border-slate-800 bg-[#0d1322]">
        <DialogHeader>
          <DialogTitle>Edit key limits</DialogTitle>
          <DialogDescription>
            Tighten or relax rate and daily token limits for {target?.name}.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="ek-name">Name</Label>
            <Input
              id="ek-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="text-xs"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ek-models">Allowed models</Label>
            <Input
              id="ek-models"
              value={allowedModels}
              onChange={(e) => setAllowedModels(e.target.value)}
              className="font-mono text-xs"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="ek-rpm">RPM limit</Label>
              <Input
                id="ek-rpm"
                type="number"
                min={0}
                value={rpm}
                onChange={(e) => setRpm(e.target.value)}
                className="font-mono text-xs"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ek-tpd">TPD limit (0 = unlimited)</Label>
              <Input
                id="ek-tpd"
                type="number"
                min={0}
                value={tpd}
                onChange={(e) => setTpd(e.target.value)}
                className="font-mono text-xs"
              />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={onClose} className="border-slate-700">
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={() => void submit()}
            disabled={busy}
            className="bg-emerald-500 text-emerald-950 hover:bg-emerald-400"
          >
            {busy && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
            Save limits
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ */
/* Main tab                                                            */
/* ------------------------------------------------------------------ */

export function KeysTab({
  meta,
  stats,
  onRefresh,
}: {
  meta: MetaConfig | null;
  stats: GatewayStats | null;
  onRefresh: () => void;
}) {
  const [keys, setKeys] = useState<ClientKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [issueOpen, setIssueOpen] = useState(false);
  const [issued, setIssued] = useState<ClientKey | null>(null);
  const [revealed, setRevealed] = useState<Record<number, boolean>>({});
  const [deleteFor, setDeleteFor] = useState<ClientKey | null>(null);
  const [editFor, setEditFor] = useState<ClientKey | null>(null);

  const load = useCallback(async () => {
    try {
      const rows = await api.getClientKeys();
      setKeys(rows);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load client keys');
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

  const toggleKey = async (k: ClientKey, checked: boolean) => {
    setKeys((rows) => rows.map((row) => (row.id === k.id ? { ...row, enabled: checked } : row)));
    try {
      await api.updateClientKey(k.id, { enabled: checked });
      toast.success(`Key "${k.name}" ${checked ? 'enabled' : 'disabled'}`);
      onRefresh();
    } catch (e) {
      setKeys((rows) => rows.map((row) => (row.id === k.id ? { ...row, enabled: !checked } : row)));
      toast.error(e instanceof Error ? e.message : 'Failed to update key');
    }
  };

  const removeKey = async () => {
    if (!deleteFor) return;
    const target = deleteFor;
    setDeleteFor(null);
    try {
      await api.deleteClientKey(target.id);
      toast.success(`Key "${target.name}" revoked`);
      refreshAll();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to delete key');
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold text-slate-100">Client keys</h2>
        <Badge variant="outline" className="border-slate-700 text-slate-400">
          {keys.length} issued
        </Badge>
        {stats && (
          <Badge variant="outline" className="border-emerald-900/70 bg-emerald-950/30 text-emerald-300">
            {fmtNum(stats.total_requests)} gateway requests · {fmtNum(stats.total_tokens)} tokens
          </Badge>
        )}
        <Button
          size="sm"
          onClick={() => setIssueOpen(true)}
          className="ml-auto bg-emerald-500 text-emerald-950 hover:bg-emerald-400"
        >
          <Plus className="size-3.5" aria-hidden />
          Issue New Key
        </Button>
      </div>

      {/* Key cards */}
      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-40 rounded-xl bg-slate-800/50" />
          ))}
        </div>
      ) : keys.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-800 bg-[#0d1322]/60 p-10 text-center text-sm text-slate-500">
          No client keys yet — issue one to start talking to the gateway.
        </div>
      ) : (
        <div className="space-y-3">
          {keys.map((k, i) => {
            const isRevealed = !!revealed[k.id];
            const usagePct = k.tpd_limit > 0 ? Math.min(100, (k.tokens_out / k.tpd_limit) * 100) : 0;
            const barColor =
              usagePct >= 90 ? '[&>div]:bg-rose-500' : usagePct >= 75 ? '[&>div]:bg-amber-500' : '[&>div]:bg-emerald-500';
            return (
              <motion.div
                key={k.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.25, delay: Math.min(i * 0.05, 0.3) }}
              >
                <div className="rounded-xl border border-slate-800 bg-[#0d1322]/80 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <KeyRound className="size-4 text-emerald-400" aria-hidden />
                    <span className="text-sm font-semibold text-slate-100">{k.name}</span>
                    {!k.enabled && (
                      <Badge variant="outline" className="border-amber-900/60 bg-amber-950/30 text-[10px] text-amber-300">
                        disabled
                      </Badge>
                    )}
                    <Badge variant="outline" className="border-slate-800 px-1.5 font-mono text-[10px] text-slate-500">
                      {k.allowed_models === '*' ? 'all models' : k.allowed_models}
                    </Badge>
                    <div className="ml-auto flex items-center gap-2">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7 text-slate-500 hover:text-slate-200"
                        onClick={() => setEditFor(k)}
                        aria-label={`Edit limits for key ${k.name}`}
                        title="Edit limits"
                      >
                        <Pencil className="size-3.5" aria-hidden />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7 text-slate-500 hover:text-rose-400"
                        onClick={() => setDeleteFor(k)}
                        aria-label={`Delete key ${k.name}`}
                      >
                        <Trash2 className="size-3.5" aria-hidden />
                      </Button>
                      <Switch
                        checked={k.enabled}
                        onCheckedChange={(c) => void toggleKey(k, c)}
                        aria-label={`Toggle key ${k.name}`}
                      />
                    </div>
                  </div>

                  {/* Token row */}
                  <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 bg-black/30 px-2.5 py-2">
                    <code
                      className="min-w-0 flex-1 truncate font-mono text-xs text-slate-300"
                      title={isRevealed ? k.token : 'Reveal to copy the full token'}
                    >
                      {isRevealed ? k.token : `${k.token.slice(0, 12)}${'•'.repeat(18)}${k.token.slice(-4)}`}
                    </code>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-7 text-slate-500 hover:text-emerald-300"
                      onClick={() => setRevealed((m) => ({ ...m, [k.id]: !m[k.id] }))}
                      aria-label={isRevealed ? `Hide token for ${k.name}` : `Reveal token for ${k.name}`}
                    >
                      {isRevealed ? <EyeOff className="size-3.5" aria-hidden /> : <Eye className="size-3.5" aria-hidden />}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-7 text-slate-500 hover:text-emerald-300"
                      onClick={() => copyText(k.token, 'Token copied to clipboard')}
                      aria-label={`Copy token for ${k.name}`}
                    >
                      <Copy className="size-3.5" aria-hidden />
                    </Button>
                  </div>

                  {/* Usage stats */}
                  <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-400">
                    <span className="flex items-center gap-1" title="Total requests">
                      <Activity className="size-3.5 text-slate-500" aria-hidden /> {fmtNum(k.req_count)} req
                    </span>
                    <span className="flex items-center gap-1" title="Tokens in / out">
                      <Coins className="size-3.5 text-slate-500" aria-hidden /> {fmtNum(k.tokens_in)} in ·{' '}
                      {fmtNum(k.tokens_out)} out
                    </span>
                    <span className="flex items-center gap-1" title="Last used">
                      <Clock className="size-3.5 text-slate-500" aria-hidden /> {timeAgo(k.last_used_at)}
                    </span>
                    <span className="flex items-center gap-1" title="Created">
                      <CalendarDays className="size-3.5 text-slate-500" aria-hidden /> {fmtDate(k.created_at)}
                    </span>
                    <span className="ml-auto flex items-center gap-1.5">
                      <Badge variant="outline" className="border-slate-700 px-1.5 font-mono text-[10px] text-slate-400">
                        RPM {k.rpm_limit > 0 ? k.rpm_limit : 'unlimited'}
                      </Badge>
                      <Badge variant="outline" className="border-slate-700 px-1.5 font-mono text-[10px] text-slate-400">
                        TPD {k.tpd_limit > 0 ? fmtNum(k.tpd_limit) : 'unlimited'}
                      </Badge>
                    </span>
                  </div>

                  {/* Usage bar */}
                  {k.tpd_limit > 0 && (
                    <div className="mt-3">
                      <div className="mb-1 flex items-center justify-between text-[10px] text-slate-500">
                        <span>Daily token budget (out)</span>
                        <span className="font-mono">
                          {fmtNum(k.tokens_out)} / {fmtNum(k.tpd_limit)} ({usagePct.toFixed(0)}%)
                        </span>
                      </div>
                      <Progress value={usagePct} className={`h-1.5 bg-slate-800 ${barColor}`} aria-label={`Usage for ${k.name}`} />
                    </div>
                  )}
                </div>
              </motion.div>
            );
          })}
        </div>
      )}

      {/* Gateway usage hint */}
      {meta && (
        <>
          <Separator className="bg-slate-800/70" />
          <p className="flex items-center gap-1.5 text-[11px] text-slate-600">
            <Check className="size-3 text-emerald-600" aria-hidden />
            Send keys as <span className="font-mono text-slate-400">Authorization: Bearer nova-sk-...</span> against{' '}
            <span className="font-mono text-slate-400">{meta.base_url}</span>/chat/completions
          </p>
        </>
      )}

      <IssueKeyDialog
        open={issueOpen}
        onOpenChange={setIssueOpen}
        onIssued={(created) => {
          setIssued(created);
          refreshAll();
        }}
      />

      {/* Show-once dialog */}
      <Dialog open={!!issued} onOpenChange={(v) => !v && setIssued(null)}>
        <DialogContent className="max-w-md border-slate-800 bg-[#0d1322]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ShieldAlert className="size-4 text-emerald-400" aria-hidden />
              Save your token now
            </DialogTitle>
            <DialogDescription>
              This is the only time the full token is shown. Copy it into your app before closing.
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-lg border border-emerald-800/70 bg-emerald-950/30 p-3">
            <code className="block break-all font-mono text-sm leading-relaxed text-emerald-200">
              {issued?.token}
            </code>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              size="sm"
              onClick={() => issued && copyText(issued.token, 'Token copied to clipboard')}
              className="border-emerald-800/70 text-emerald-300 hover:bg-emerald-950/50 hover:text-emerald-200"
            >
              <Copy className="size-3.5" aria-hidden />
              Copy token
            </Button>
            <Button
              size="sm"
              onClick={() => setIssued(null)}
              className="bg-emerald-500 text-emerald-950 hover:bg-emerald-400"
            >
              <Check className="size-3.5" aria-hidden />
              I saved it
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <EditKeyDialog target={editFor} onClose={() => setEditFor(null)} onSaved={refreshAll} />

      <AlertDialog open={!!deleteFor} onOpenChange={(v) => !v && setDeleteFor(null)}>
        <AlertDialogContent className="border-slate-800 bg-[#0d1322]">
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke key &quot;{deleteFor?.name}&quot;?</AlertDialogTitle>
            <AlertDialogDescription>
              Clients using this token will immediately receive 401 responses. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-slate-700 bg-transparent text-slate-300 hover:bg-slate-800 hover:text-slate-100">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void removeKey()}
              className="bg-rose-600 text-white hover:bg-rose-500"
            >
              Revoke key
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
