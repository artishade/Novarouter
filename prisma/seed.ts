/**
 * NovaRouter bootstrap seed — MINIMAL and 100% REAL.
 *
 * Only what the gateway genuinely needs to function is created:
 *   • the built-in NovaFree engine provider + its 3 real engine models
 *   • the gateway_started_at system config (real process start time)
 *
 * NO fake API keys, NO simulated request logs, NO fabricated sessions,
 * NO demo terminal history, NO demo storage files. Everything else in the
 * dashboard starts empty and fills up with real data as you actually use it.
 *
 * Real providers are added from Dashboard → Providers (preset catalogue) and
 * their live model catalogues are discovered from the upstream /models APIs.
 *
 * Run: bun prisma/seed.ts
 */
import { PrismaClient } from '@prisma/client';

const db = new PrismaClient();

async function main() {
  console.log('⚡ Bootstrapping NovaRouter (real data only)…');

  // Idempotent: only bootstrap when the database has no providers at all.
  const providerCount = await db.provider.count();
  if (providerCount > 0) {
    console.log(`✓ database already has ${providerCount} provider(s) — nothing to bootstrap`);
    return;
  }

  const novafree = await db.provider.create({
    data: {
      key: 'novafree',
      name: 'NovaFree Engine',
      kind: 'builtin',
      baseUrl: 'internal://nova-engine',
      prefix: 'nova/',
      priority: 1,
      color: '#10b981',
      freeTier: 'Built-in — every model here is free, no key required',
      docsUrl: 'https://github.com/artishade/Novarouter',
    },
  });

  const engineModels = [
    { modelId: 'nova-air', displayName: 'Nova Air', ctx: 32768, maxOut: 8192, caps: { tools: true, vision: true, reasoning: true }, description: 'Built-in free engine. Balanced speed and quality, always available.' },
    { modelId: 'nova-mini', displayName: 'Nova Mini', ctx: 16384, maxOut: 4096, caps: { tools: false, vision: false, reasoning: false }, description: 'Built-in free engine. Ultra-low latency for quick tasks.' },
    { modelId: 'nova-pro', displayName: 'Nova Pro', ctx: 65536, maxOut: 16384, caps: { tools: true, vision: true, reasoning: true }, description: 'Built-in free engine. Deep reasoning with the largest context.' },
  ];

  for (const m of engineModels) {
    await db.model.create({
      data: {
        providerId: novafree.id,
        modelId: m.modelId,
        exposedId: `nova/${m.modelId.replace('nova-', '')}`,
        displayName: m.displayName,
        isFree: true,
        contextLength: m.ctx,
        maxOutput: m.maxOut,
        capabilities: JSON.stringify(m.caps),
        description: m.description,
      },
    });
  }

  await db.systemConfig.create({
    data: { key: 'gateway_started_at', value: String(Date.now()) },
  });

  console.log('✓ bootstrapped: NovaFree Engine (3 models) + gateway config');
  console.log('ℹ add real providers from Dashboard → Providers — models sync live from upstream APIs');
}

main()
  .catch((err) => {
    // Bootstrapping is optional — never block the server from starting.
    console.warn('[nova] bootstrap seed failed (server will start empty):', err);
  })
  .finally(() => db.$disconnect());
