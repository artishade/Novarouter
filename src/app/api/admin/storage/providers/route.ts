import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

function slugify(name: string): string {
  return (
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'provider'
  );
}

/** POST /api/admin/storage/providers — add a custom storage provider */
export async function POST(req: NextRequest) {
  let body: {
    name?: unknown;
    type?: unknown;
    endpoint?: unknown;
    bucket_name?: unknown;
    access_key?: unknown;
    secret_key?: unknown;
    region?: unknown;
    free_tier?: unknown;
  };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  const name = typeof body.name === 'string' ? body.name.trim() : '';
  if (!name) {
    return NextResponse.json({ error: 'name is required' }, { status: 400 });
  }
  const type = typeof body.type === 'string' && body.type.trim() ? body.type.trim() : 'webdav';

  // Unique provider key: slug + numeric suffix when needed.
  const slug = slugify(name);
  let id = slug;
  let i = 2;
  while ((await db.storageProvider.findUnique({ where: { id } }))) {
    id = `${slug}-${i}`;
    i += 1;
  }

  const str = (v: unknown) => (typeof v === 'string' && v.trim() ? v.trim() : undefined);
  const config: Record<string, string> = {};
  const endpoint = str(body.endpoint);
  const bucket = str(body.bucket_name);
  const accessKey = str(body.access_key);
  const secretKey = str(body.secret_key);
  const region = str(body.region);
  if (endpoint) config.endpoint = endpoint;
  if (bucket) config.bucket_name = bucket;
  if (accessKey) config.access_key = accessKey;
  if (secretKey) config.secret_key = secretKey;
  if (region) config.region = region;

  await db.storageProvider.create({
    data: {
      id,
      name,
      type,
      config: JSON.stringify(config),
      status: 'pending',
      isBuiltin: false,
      active: false,
      freeTier: str(body.free_tier) ?? null,
      region: region ?? null,
    },
  });

  return NextResponse.json({ ok: true, provider_key: id });
}
