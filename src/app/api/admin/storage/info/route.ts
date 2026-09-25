import { NextResponse } from 'next/server';
import { db } from '@/lib/db';
import type { StorageFileInfo, StorageProviderInfo } from '@/lib/types';

export const dynamic = 'force-dynamic';

function mapStorageProvider(r: {
  id: string;
  name: string;
  type: string;
  status: string;
  isBuiltin: boolean;
  active: boolean;
  freeTier: string | null;
  usageMb: number;
  quotaMb: number;
  region: string | null;
  docsUrl: string | null;
  authUrl: string | null;
  lastTestAt: Date | null;
  lastError: string | null;
}): StorageProviderInfo {
  return {
    id: r.id,
    name: r.name,
    type: r.type,
    status: r.status,
    is_builtin: r.isBuiltin,
    active: r.active,
    free_tier: r.freeTier,
    usage_mb: r.usageMb,
    quota_mb: r.quotaMb,
    region: r.region,
    docs_url: r.docsUrl,
    auth_url: r.authUrl,
    last_test_at: r.lastTestAt ? r.lastTestAt.getTime() : null,
    last_error: r.lastError,
  };
}

function mapStorageFile(r: {
  id: number;
  name: string;
  path: string;
  size: number;
  mime: string;
  providerKey: string;
  createdAt: Date;
}): StorageFileInfo {
  return {
    id: r.id,
    name: r.name,
    path: r.path,
    size: r.size,
    mime: r.mime,
    provider_key: r.providerKey,
    created_at: r.createdAt.getTime(),
  };
}

/** GET /api/admin/storage/info → StorageInfo */
export async function GET() {
  const [providers, files] = await Promise.all([
    db.storageProvider.findMany({ orderBy: { createdAt: 'asc' } }),
    db.storageFile.findMany({ orderBy: { createdAt: 'desc' } }),
  ]);

  const active =
    providers.find((p) => p.active) ??
    providers.find((p) => p.id === 'local_disk');
  const activeKey = active?.id ?? 'local_disk';

  const activeFiles = files.filter((f) => f.providerKey === activeKey);
  const totalMb =
    Math.round((activeFiles.reduce((a, f) => a + f.size, 0) / 1048576) * 100) / 100;
  const quotaMb = active?.quotaMb ?? 0;
  const pct =
    quotaMb > 0 ? Math.min(100, Math.round((totalMb / quotaMb) * 10000) / 100) : 0;

  return NextResponse.json({
    active_provider: activeKey,
    providers: providers.map(mapStorageProvider),
    files: files.map(mapStorageFile),
    usage: {
      files: activeFiles.length,
      total_mb: totalMb,
      quota_mb: quotaMb,
      pct,
    },
  });
}
