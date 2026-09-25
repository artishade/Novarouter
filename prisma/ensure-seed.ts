/**
 * NovaRouter database bootstrap (runs at container start via docker-entrypoint.sh).
 *
 * 1. EMPTY database  → bootstrap the real minimum: built-in NovaFree engine
 *    (provider + its 3 engine models) + gateway_started_at. Nothing fake.
 * 2. SEEDED database (an older deployment with demo data) → one-time surgical
 *    purge of every seeded artifact: placeholder API keys, simulated request
 *    logs, fake sessions/terminal/storage/client keys, demo agent tasks and
 *    seeded preset providers that never received a real key. User-created
 *    providers and their real keys are preserved.
 * 3. Otherwise → untouched.
 *
 * Works for both SQLite and Postgres (uses whichever client was generated).
 */
import { PrismaClient } from '@prisma/client';

const db = new PrismaClient();

/** Exact provider keys used by the old demo seed — never matches user-created duplicates (which get -2, -3 suffixes). */
const SEEDED_PRESET_KEYS = [
  'openrouter', 'groq', 'gemini', 'cerebras', 'github-models', 'mistral',
  'nvidia', 'deepseek', 'together', 'xai', 'fireworks', 'ollama', 'openai', 'anthropic',
];

/** One-time cleanup of the old "realistic demo" seed artifacts. */
async function purgeDemoDataOnce(): Promise<void> {
  const flag = await db.systemConfig.findUnique({ where: { key: 'demo_data_purged' } });
  if (flag) return;

  const demoKeyCount = await db.providerKey.count({
    where: { apiKey: { contains: 'SEED-DEMO-PLACEHOLDER' } },
  });

  if (demoKeyCount > 0) {
    console.log('[nova] seeded demo data detected — purging every mock artifact…');

    // 1. Placeholder upstream keys.
    await db.providerKey.deleteMany({
      where: { apiKey: { contains: 'SEED-DEMO-PLACEHOLDER' } },
    });

    // 2. Seeded preset providers that never received a real key (cascade deletes
    //    their statically-catalogued models). Providers the user actually set up
    //    — with real keys — are preserved.
    const seeded = await db.provider.findMany({
      where: { kind: { not: 'builtin' }, key: { in: SEEDED_PRESET_KEYS } },
      include: { keys: { select: { id: true } } },
    });
    for (const p of seeded) {
      if (p.keys.length === 0) {
        await db.provider.delete({ where: { id: p.id } });
        console.log(`[nova] removed seeded provider "${p.key}" (no real key was ever added)`);
      }
    }

    // 3. Fabricated telemetry, sessions and demo activity.
    await db.requestLog.deleteMany();
    await db.providerSession.deleteMany();
    await db.terminalCommand.deleteMany();
    await db.storageFile.deleteMany();
    await db.storageProvider.deleteMany();
    await db.clientKey.deleteMany();
    await db.agentStep.deleteMany();
    await db.agentTask.deleteMany();
    await db.modelRoute.deleteMany();

    console.log('[nova] demo data purged — the dashboard now shows only real data');
  }

  await db.systemConfig.upsert({
    where: { key: 'demo_data_purged' },
    update: { value: String(Date.now()) },
    create: { key: 'demo_data_purged', value: String(Date.now()) },
  });
}

const providerCount = await db.provider.count();

if (providerCount === 0) {
  console.log('[nova] empty database — bootstrapping the real minimum…');
  try {
    await import('./seed');
  } catch (err) {
    // Bootstrapping is optional — never block the server from starting.
    console.warn('[nova] bootstrap failed (server will start with an empty database):', err);
  }
} else {
  console.log(`[nova] database already has ${providerCount} provider(s) — checking for legacy demo data`);
}

try {
  await purgeDemoDataOnce();
} catch (err) {
  console.warn('[nova] demo-data purge skipped:', err);
}

await db.$disconnect();
