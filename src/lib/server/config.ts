/**
 * SystemConfig KV helpers — shared module (owned by Agent 2-b).
 * Other agents may import these once this file exists.
 */
import { db } from '@/lib/db';
import type { ComputeProvider } from '@/lib/types';

/** Memory-related SystemConfig keys used by the RAM booster + terminal. */
export const MEMORY_KEYS = ['v8_heap_mb', 'swap_mb', 'boost_applied_at'] as const;

/** Read a raw string value from the SystemConfig KV table. */
export async function getConfig(key: string): Promise<string | null> {
  const row = await db.systemConfig.findUnique({ where: { key } });
  return row ? row.value : null;
}

/** Upsert a raw string value into the SystemConfig KV table. */
export async function setConfig(key: string, value: string): Promise<void> {
  await db.systemConfig.upsert({
    where: { key },
    update: { value },
    create: { key, value },
  });
}

/** Read a JSON-encoded value; returns `fallback` when missing or malformed. */
export async function getConfigJson<T>(key: string, fallback: T): Promise<T> {
  const raw = await getConfig(key);
  if (raw === null || raw === '') return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

/** JSON-encode and upsert a value. */
export async function setConfigJson(key: string, value: unknown): Promise<void> {
  await setConfig(key, JSON.stringify(value));
}

/** Read a numeric value; returns `fallback` when missing or NaN. */
export async function getConfigNumber(key: string, fallback: number): Promise<number> {
  const raw = await getConfig(key);
  if (raw === null) return fallback;
  const n = Number(raw);
  return Number.isFinite(n) ? n : fallback;
}

/**
 * Compute providers live in SystemConfig 'gpu_providers' as a JSON array.
 * Seeded entries carry no `enabled` flag: default is disabled for everyone
 * except the built-in NovaFree GPU pool.
 */
const DEFAULT_ENABLED_IDS = new Set(['novafree_gpu']);

export async function getGpuProviders(): Promise<ComputeProvider[]> {
  const raw = await getConfigJson<unknown[]>('gpu_providers', []);
  if (!Array.isArray(raw)) return [];
  return raw.map((entry) => {
    const p = (entry ?? {}) as Record<string, unknown>;
    const id = String(p.id ?? 'provider');
    return {
      id,
      name: String(p.name ?? id),
      gpu: String(p.gpu ?? '—'),
      vram_gb: Number(p.vram_gb ?? 0) || 0,
      free_tier: String(p.free_tier ?? ''),
      status: String(p.status ?? 'available'),
      connected: Boolean(p.connected),
      hours_free: String(p.hours_free ?? ''),
      region: String(p.region ?? 'global'),
      note: p.note === undefined ? undefined : String(p.note),
      enabled:
        typeof p.enabled === 'boolean' ? p.enabled : DEFAULT_ENABLED_IDS.has(id),
    } satisfies ComputeProvider;
  });
}

export async function setGpuProviders(list: ComputeProvider[]): Promise<void> {
  await setConfigJson('gpu_providers', list);
}
