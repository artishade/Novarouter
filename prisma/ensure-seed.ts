/**
 * Seeds demo data only when the database is empty.
 * Works for both SQLite and Postgres (uses whichever client was generated).
 * Called by docker-entrypoint.sh unless NOVA_SEED=0.
 */
import { PrismaClient } from '@prisma/client';

const db = new PrismaClient();

const count = await db.provider.count();

if (count === 0) {
  console.log('[nova] empty database — seeding realistic demo data...');
  try {
    await import('./seed');
  } catch (err) {
    // Seeding is optional — never block the server from starting.
    console.warn('[nova] seed failed (server will start with an empty database):', err);
  }
} else {
  console.log(`[nova] database already has ${count} provider(s) — skipping seed`);
}

await db.$disconnect();
