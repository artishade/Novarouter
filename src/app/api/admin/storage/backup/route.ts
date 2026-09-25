import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

const pad2 = (n: number) => String(n).padStart(2, '0');
const maskSecret = (s: string) =>
  s.length > 12 ? `${s.slice(0, 6)}…${s.slice(-4)}` : '••••••••';

/**
 * POST /api/admin/storage/backup
 * Snapshot {providers, models, routes, client_keys, config, exported_at} as a
 * JSON StorageFile in the active provider → {ok, file_name, size_bytes, provider, message}
 */
export async function POST(_req: NextRequest) {
  const [providers, models, routes, clientKeys, configRows, activeRow, activeFallback] =
    await Promise.all([
      db.provider.findMany({ include: { keys: true }, orderBy: { priority: 'asc' } }),
      db.model.findMany({ include: { provider: { select: { key: true } } } }),
      db.modelRoute.findMany({ orderBy: { id: 'asc' } }),
      db.clientKey.findMany({ orderBy: { createdAt: 'asc' } }),
      db.systemConfig.findMany({ orderBy: { key: 'asc' } }),
      db.storageProvider.findFirst({ where: { active: true } }),
      db.storageProvider.findUnique({ where: { id: 'local_disk' } }),
    ]);

  const activeProvider = activeRow ?? activeFallback;
  const providerKey = activeProvider?.id ?? 'local_disk';
  const providerName = activeProvider?.name ?? 'NovaVault (Local Disk)';

  const snapshot = {
    providers: providers.map((p) => ({
      key: p.key,
      name: p.name,
      kind: p.kind,
      base_url: p.baseUrl,
      prefix: p.prefix,
      priority: p.priority,
      enabled: p.enabled,
      color: p.color,
      free_tier: p.freeTier,
      keys: p.keys.map((k) => ({
        label: k.label,
        api_key: maskSecret(k.apiKey),
        weight: k.weight,
        enabled: k.enabled,
      })),
    })),
    models: models.map((m) => ({
      exposed_id: m.exposedId,
      model_id: m.modelId,
      provider: m.provider.key,
      status: m.status,
      enabled: m.enabled,
      is_free: m.isFree,
    })),
    routes: routes.map((r) => ({
      public_id: r.publicId,
      fallbacks: (() => {
        try {
          return JSON.parse(r.fallbacks) as string[];
        } catch {
          return [];
        }
      })(),
      auto: r.auto,
      enabled: r.enabled,
      note: r.note,
    })),
    client_keys: clientKeys.map((k) => ({
      name: k.name,
      token: maskSecret(k.token),
      enabled: k.enabled,
      rpm_limit: k.rpmLimit,
      tpd_limit: k.tpdLimit,
    })),
    config: Object.fromEntries(configRows.map((c) => [c.key, c.value])),
    exported_at: new Date().toISOString(),
  };

  const json = JSON.stringify(snapshot, null, 2);
  const size = Buffer.byteLength(json, 'utf8');
  const now = new Date();
  const fileName = `backup-${now.getUTCFullYear()}-${pad2(now.getUTCMonth() + 1)}-${pad2(now.getUTCDate())}-${pad2(now.getUTCHours())}${pad2(now.getUTCMinutes())}.json`;

  await db.storageFile.create({
    data: {
      name: fileName,
      path: '/backups',
      size,
      mime: 'application/json',
      providerKey,
      data: Buffer.from(json, 'utf8').toString('base64'),
    },
  });

  return NextResponse.json({
    ok: true,
    file_name: fileName,
    size_bytes: size,
    provider: providerKey,
    message: `Backup snapshot saved to ${providerName} — ${(size / 1024).toFixed(1)} KB · ${models.length} models · ${routes.length} routes`,
  });
}
