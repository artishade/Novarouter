'use client';

/** RoutesTab — fallback route manager with identity-spoofing preview. */
import { Fragment, useCallback, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { toast } from 'sonner';
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  Fingerprint,
  Info,
  Loader2,
  Pencil,
  Play,
  Plus,
  Route as RouteIcon,
  Trash2,
  X,
} from 'lucide-react';

import { api } from '@/lib/api';
import type { GatewayStats, MetaConfig, Model, ModelRoute, RoutePreview } from '@/lib/types';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
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
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';

/* ------------------------------------------------------------------ */
/* Create / edit route dialog                                          */
/* ------------------------------------------------------------------ */

function RouteFormDialog({
  open,
  onOpenChange,
  models,
  initial,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  models: Model[];
  initial: ModelRoute | null;
  onSaved: () => void;
}) {
  const [publicId, setPublicId] = useState('');
  const [fallbacks, setFallbacks] = useState<string[]>([]);
  const [auto, setAuto] = useState(true);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setPublicId(initial?.public_id ?? '');
    setFallbacks(initial?.fallbacks ?? []);
    setAuto(initial?.auto ?? true);
    setNote(initial?.note ?? '');
  }, [open, initial]);

  const toggleModel = (exposedId: string) => {
    setFallbacks((prev) =>
      prev.includes(exposedId) ? prev.filter((f) => f !== exposedId) : [...prev, exposedId]
    );
  };

  const move = (index: number, dir: -1 | 1) => {
    setFallbacks((prev) => {
      const next = [...prev];
      const target = index + dir;
      if (target < 0 || target >= next.length) return prev;
      const tmp = next[index];
      next[index] = next[target];
      next[target] = tmp;
      return next;
    });
  };

  const submit = async () => {
    if (!publicId.trim()) {
      toast.error('Public model id is required (use * for the default chain)');
      return;
    }
    if (fallbacks.length === 0 && !auto) {
      toast.error('Pick at least one fallback model, or enable auto stand-ins');
      return;
    }
    setSaving(true);
    try {
      if (initial) {
        await api.updateRoute(initial.id, {
          public_id: publicId.trim(),
          fallbacks,
          auto,
          note: note.trim(),
        });
        toast.success(`Route ${publicId.trim()} updated`);
      } else {
        await api.createRoute({
          public_id: publicId.trim(),
          fallbacks,
          auto,
          note: note.trim(),
        });
        toast.success(`Route ${publicId.trim()} created`);
      }
      onOpenChange(false);
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to save route');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl border-slate-800 bg-[#0d1322]">
        <DialogHeader>
          <DialogTitle>{initial ? 'Edit route' : 'New fallback route'}</DialogTitle>
          <DialogDescription>
            Map a public model id to an ordered chain of upstream models. Clients keep asking for the
            public id — NovaRouter walks the chain until one answers.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="rt-public">Public model id</Label>
              <Input
                id="rt-public"
                value={publicId}
                onChange={(e) => setPublicId(e.target.value)}
                placeholder="my-model or * for default chain"
                className="font-mono text-xs"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="rt-note">Note (optional)</Label>
              <Input
                id="rt-note"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Why this chain exists"
                className="text-xs"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>Pick fallback models (in order)</Label>
            <ScrollArea className="max-h-40 rounded-lg border border-slate-800 bg-black/20 p-2.5">
              <div className="flex flex-wrap gap-1.5">
                {models.length === 0 && (
                  <span className="text-[11px] text-slate-600">No models in the catalogue yet.</span>
                )}
                {models.map((m) => {
                  const selected = fallbacks.includes(m.exposed_id);
                  return (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => toggleModel(m.exposed_id)}
                      aria-pressed={selected}
                      aria-label={`${selected ? 'Remove' : 'Add'} ${m.exposed_id} to chain`}
                      className={`rounded-md border px-2 py-1 font-mono text-[11px] transition ${
                        selected
                          ? 'border-emerald-700/70 bg-emerald-950/50 text-emerald-300'
                          : 'border-slate-800 bg-black/30 text-slate-400 hover:border-slate-600 hover:text-slate-200'
                      }`}
                    >
                      {m.exposed_id}
                    </button>
                  );
                })}
              </div>
            </ScrollArea>
          </div>

          <div className="space-y-1.5">
            <Label>Chain order ({fallbacks.length})</Label>
            {fallbacks.length === 0 ? (
              <p className="rounded-lg border border-dashed border-slate-800 px-3 py-2 text-[11px] text-slate-600">
                Nothing selected yet — chips above append here in click order.
              </p>
            ) : (
              <div className="flex flex-wrap items-center gap-1.5">
                {fallbacks.map((f, idx) => (
                  <span
                    key={f}
                    className="flex items-center gap-1 rounded-md border border-emerald-800/60 bg-emerald-950/40 px-1.5 py-1 font-mono text-[11px] text-emerald-300"
                  >
                    <span className="text-[9px] text-emerald-500/80">F{idx + 1}</span>
                    {f}
                    <button
                      type="button"
                      onClick={() => move(idx, -1)}
                      disabled={idx === 0}
                      className="text-emerald-500/70 transition hover:text-emerald-300 disabled:opacity-30"
                      aria-label={`Move ${f} up`}
                    >
                      <ArrowUp className="size-3" aria-hidden />
                    </button>
                    <button
                      type="button"
                      onClick={() => move(idx, 1)}
                      disabled={idx === fallbacks.length - 1}
                      className="text-emerald-500/70 transition hover:text-emerald-300 disabled:opacity-30"
                      aria-label={`Move ${f} down`}
                    >
                      <ArrowDown className="size-3" aria-hidden />
                    </button>
                    <button
                      type="button"
                      onClick={() => toggleModel(f)}
                      className="text-emerald-500/70 transition hover:text-rose-400"
                      aria-label={`Remove ${f} from chain`}
                    >
                      <X className="size-3" aria-hidden />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="flex items-center gap-2">
            <Switch checked={auto} onCheckedChange={setAuto} aria-label="Allow automatic stand-ins" id="rt-auto" />
            <Label htmlFor="rt-auto" className="text-xs text-slate-300">
              Auto stand-ins
            </Label>
            <span className="text-[11px] text-slate-500">
              When the chain is exhausted, healthy free models step in automatically.
            </span>
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
            className="border-slate-700"
          >
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={() => void submit()}
            disabled={saving}
            className="bg-emerald-500 text-emerald-950 hover:bg-emerald-400"
          >
            {saving && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
            {initial ? 'Save changes' : 'Create route'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ */
/* Main tab                                                            */
/* ------------------------------------------------------------------ */

export function RoutesTab({
  meta,
  stats,
  onRefresh,
}: {
  meta: MetaConfig | null;
  stats: GatewayStats | null;
  onRefresh: () => void;
}) {
  const [routes, setRoutes] = useState<ModelRoute[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ModelRoute | null>(null);
  const [deleteFor, setDeleteFor] = useState<ModelRoute | null>(null);
  const [previewModel, setPreviewModel] = useState('');
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [preview, setPreview] = useState<RoutePreview | null>(null);

  const load = useCallback(async () => {
    try {
      const [routeRows, modelRes] = await Promise.all([api.getRoutes(), api.getModels()]);
      setRoutes(routeRows);
      setModels(modelRes.rows);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load routes');
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

  const toggleRoute = async (r: ModelRoute, checked: boolean) => {
    setRoutes((rows) => rows.map((row) => (row.id === r.id ? { ...row, enabled: checked } : row)));
    try {
      await api.updateRoute(r.id, { enabled: checked });
      toast.success(`Route ${r.public_id} ${checked ? 'enabled' : 'disabled'}`);
      onRefresh();
    } catch (e) {
      setRoutes((rows) => rows.map((row) => (row.id === r.id ? { ...row, enabled: !checked } : row)));
      toast.error(e instanceof Error ? e.message : 'Failed to update route');
    }
  };

  const removeRoute = async () => {
    if (!deleteFor) return;
    const target = deleteFor;
    setDeleteFor(null);
    try {
      await api.deleteRoute(target.id);
      toast.success(`Route ${target.public_id} deleted`);
      refreshAll();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to delete route');
    }
  };

  const runPreview = async (model: string) => {
    if (!model.trim()) {
      toast.error('Enter a model id to preview its fallback resolution');
      return;
    }
    setPreviewBusy(true);
    try {
      const p = await api.previewRoute(model.trim());
      setPreview(p);
      setPreviewOpen(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Preview failed');
    } finally {
      setPreviewBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold text-slate-100">Fallback routes</h2>
        <Badge variant="outline" className="border-slate-700 text-slate-400">
          {routes.length} routes
        </Badge>
        {stats && (
          <Badge variant="outline" className="border-slate-700 text-slate-400">
            {stats.healthy_models} healthy models available as stand-ins
          </Badge>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Input
            value={previewModel}
            onChange={(e) => setPreviewModel(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void runPreview(previewModel);
            }}
            placeholder="Preview by model id..."
            aria-label="Model id to preview fallback chain"
            className="h-9 w-52 border-slate-800 bg-black/30 font-mono text-xs"
          />
          <Button
            variant="outline"
            size="sm"
            onClick={() => void runPreview(previewModel)}
            disabled={previewBusy}
            className="border-slate-700 text-slate-300 hover:bg-slate-800"
          >
            {previewBusy ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <Play className="size-3.5" aria-hidden />}
            Preview
          </Button>
          <Button
            size="sm"
            onClick={() => {
              setEditing(null);
              setDialogOpen(true);
            }}
            className="bg-emerald-500 text-emerald-950 hover:bg-emerald-400"
          >
            <Plus className="size-3.5" aria-hidden />
            New Route
          </Button>
        </div>
      </div>

      {/* Intro alert */}
      <Alert className="border-emerald-900/70 bg-emerald-950/20">
        <Info className="size-4 text-emerald-400" aria-hidden />
        <AlertTitle className="text-emerald-300">Identity spoofing keeps clients happy</AlertTitle>
        <AlertDescription className="text-slate-400">
          When a model is down, NovaRouter retries the chain and still answers with the model id the
          client asked for. Stage 1 tries the model directly, stage 2 walks your explicit chain, and
          stage 3 picks healthy free stand-ins when auto is enabled.{' '}
          {meta?.fallback.spoof_model ? 'Spoofing is currently ON.' : 'Spoofing is currently OFF.'}
        </AlertDescription>
      </Alert>

      {/* Route cards */}
      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-32 rounded-xl bg-slate-800/50" />
          ))}
        </div>
      ) : routes.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-slate-800 bg-[#0d1322]/60 p-10 text-center">
          <RouteIcon className="size-6 text-slate-600" aria-hidden />
          <p className="text-sm text-slate-500">
            No fallback routes yet. Create one, or add a <span className="font-mono text-slate-400">*</span>{' '}
            route to define the default chain.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {routes.map((r, i) => (
            <motion.div
              key={r.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.25, delay: Math.min(i * 0.05, 0.3) }}
            >
              <div
                className={`rounded-xl border bg-[#0d1322]/80 p-4 ${
                  r.public_id === '*' ? 'border-purple-900/50' : 'border-slate-800'
                }`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm text-emerald-300">{r.public_id}</span>
                  {r.public_id === '*' && (
                    <Badge className="border-purple-800/60 bg-purple-950/50 text-[10px] uppercase tracking-wide text-purple-300">
                      Default chain
                    </Badge>
                  )}
                  {r.auto && (
                    <Badge variant="outline" className="border-slate-700 text-[10px] text-slate-400">
                      auto
                    </Badge>
                  )}
                  {!r.enabled && (
                    <Badge variant="outline" className="border-amber-900/60 bg-amber-950/30 text-[10px] text-amber-300">
                      disabled
                    </Badge>
                  )}
                  <div className="ml-auto flex items-center gap-2">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-7 text-slate-500 hover:text-emerald-300"
                      onClick={() => void runPreview(r.public_id)}
                      aria-label={`Preview route ${r.public_id}`}
                      title="Preview fallback resolution"
                    >
                      <Play className="size-3.5" aria-hidden />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-7 text-slate-500 hover:text-slate-200"
                      onClick={() => {
                        setEditing(r);
                        setDialogOpen(true);
                      }}
                      aria-label={`Edit route ${r.public_id}`}
                    >
                      <Pencil className="size-3.5" aria-hidden />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-7 text-slate-500 hover:text-rose-400"
                      onClick={() => setDeleteFor(r)}
                      aria-label={`Delete route ${r.public_id}`}
                    >
                      <Trash2 className="size-3.5" aria-hidden />
                    </Button>
                    <Switch
                      checked={r.enabled}
                      onCheckedChange={(c) => void toggleRoute(r, c)}
                      aria-label={`Toggle route ${r.public_id}`}
                    />
                  </div>
                </div>

                {r.note && <p className="mt-1 text-[11px] text-slate-500">{r.note}</p>}

                {/* Chain visualization */}
                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                  <span className="flex items-center gap-1.5 rounded-md border border-slate-700 bg-black/40 px-2 py-1 font-mono text-[11px] text-slate-200">
                    <span className="text-[9px] uppercase text-slate-500">req</span>
                    {r.public_id}
                  </span>
                  {r.fallbacks.map((f, idx) => (
                    <Fragment key={`${r.id}-${f}-${idx}`}>
                      <ArrowRight className="size-3 shrink-0 text-slate-600" aria-hidden />
                      <span className="flex items-center gap-1.5 rounded-md border border-slate-800 bg-black/30 px-2 py-1 font-mono text-[11px] text-slate-300">
                        <span className="text-[9px] text-slate-500">F{idx + 1}</span>
                        {f}
                      </span>
                    </Fragment>
                  ))}
                  {r.auto && (
                    <>
                      <ArrowRight className="size-3 shrink-0 text-slate-600" aria-hidden />
                      <span className="flex items-center gap-1.5 rounded-md border border-emerald-900/60 bg-emerald-950/40 px-2 py-1 font-mono text-[11px] text-emerald-300/90">
                        <span className="text-[9px] uppercase text-emerald-600">auto</span>
                        free stand-ins
                      </span>
                    </>
                  )}
                  {r.fallbacks.length === 0 && !r.auto && (
                    <span className="text-[11px] text-slate-600">empty chain — requests fail through</span>
                  )}
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      )}

      <RouteFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        models={models}
        initial={editing}
        onSaved={refreshAll}
      />

      <AlertDialog open={!!deleteFor} onOpenChange={(v) => !v && setDeleteFor(null)}>
        <AlertDialogContent className="border-slate-800 bg-[#0d1322]">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete route {deleteFor?.public_id}?</AlertDialogTitle>
            <AlertDialogDescription>
              Requests for this model will lose their explicit chain and fall through to the default
              chain (if any) and auto stand-ins.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-slate-700 bg-transparent text-slate-300 hover:bg-slate-800 hover:text-slate-100">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void removeRoute()}
              className="bg-rose-600 text-white hover:bg-rose-500"
            >
              Delete route
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Preview dialog */}
      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="max-w-lg border-slate-800 bg-[#0d1322]">
          <DialogHeader>
            <DialogTitle className="font-mono text-sm text-emerald-300">{preview?.model}</DialogTitle>
            <DialogDescription>
              Fallback resolution — the exact order the gateway will try right now.
            </DialogDescription>
          </DialogHeader>

          {preview && (
            <div className="space-y-4">
              {preview.spoof_model && (
                <div className="flex items-start gap-2.5 rounded-lg border border-purple-900/60 bg-purple-950/30 p-3">
                  <Fingerprint className="mt-0.5 size-4 shrink-0 text-purple-300" aria-hidden />
                  <div>
                    <Badge className="border-purple-800/60 bg-purple-950/60 text-[10px] uppercase tracking-wide text-purple-300">
                      Spoof identity on
                    </Badge>
                    <p className="mt-1 text-[11px] leading-relaxed text-slate-400">
                      Whichever upstream answers, the response is re-labelled as{' '}
                      <span className="font-mono text-purple-300">{preview.model}</span> — the client never
                      sees the swap.
                    </p>
                  </div>
                </div>
              )}

              <ol className="space-y-0">
                {preview.stages.map((s, i) => (
                  <li key={s.label} className="relative border-l border-slate-800 pb-4 pl-5 last:pb-0">
                    <span className="absolute -left-[9px] top-0 flex size-4 items-center justify-center rounded-full border border-emerald-800 bg-[#0d1322] text-[9px] font-semibold text-emerald-300">
                      {i + 1}
                    </span>
                    <p className="text-[11px] font-medium uppercase tracking-wider text-slate-400">{s.label}</p>
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {s.models.length === 0 && (
                        <span className="text-[11px] text-slate-600">no candidates in this stage</span>
                      )}
                      {s.models.map((m) => (
                        <span
                          key={m}
                          className="rounded-md border border-slate-700 bg-black/40 px-2 py-0.5 font-mono text-[11px] text-slate-300"
                        >
                          {m}
                        </span>
                      ))}
                    </div>
                  </li>
                ))}
              </ol>

              <div className="flex flex-wrap gap-2 text-[11px] text-slate-500">
                <span>
                  direct hit: <span className="font-mono text-slate-300">{preview.direct ? 'yes' : 'no'}</span>
                </span>
                <span>·</span>
                <span>
                  auto allowed: <span className="font-mono text-slate-300">{preview.auto_allowed ? 'yes' : 'no'}</span>
                </span>
                <span>·</span>
                <span>
                  explicit chain: <span className="font-mono text-slate-300">{preview.explicit_chain.length} models</span>
                </span>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
