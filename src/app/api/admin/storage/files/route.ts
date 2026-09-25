import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

const MAX_BYTES = 5 * 1024 * 1024; // 5 MB sandbox limit

/**
 * POST /api/admin/storage/files
 * Accepts multipart FormData (file[, provider_key]) OR JSON {name, mime, data_base64, provider_key?}.
 * Stores base64 payload in StorageFile.data → {ok, file:{id,name,size,mime}}.
 */
export async function POST(req: NextRequest) {
  const contentType = req.headers.get('content-type') || '';

  let name: string;
  let mime: string;
  let base64: string;
  let size: number;
  let providerKeyInput: string | undefined;

  if (contentType.includes('multipart/form-data')) {
    let form: FormData;
    try {
      form = await req.formData();
    } catch {
      return NextResponse.json({ error: 'Invalid multipart body' }, { status: 400 });
    }
    const file = form.get('file');
    if (!file || typeof file === 'string') {
      return NextResponse.json(
        { error: 'No file provided — attach a "file" field' },
        { status: 400 },
      );
    }
    const pk = form.get('provider_key');
    providerKeyInput = typeof pk === 'string' && pk.trim() ? pk.trim() : undefined;

    const buf = Buffer.from(await file.arrayBuffer());
    size = buf.length;
    base64 = buf.toString('base64');
    name = file.name || 'upload.bin';
    mime = file.type || 'application/octet-stream';
  } else {
    let body: { name?: unknown; mime?: unknown; data_base64?: unknown; provider_key?: unknown };
    try {
      body = await req.json();
    } catch {
      return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
    }
    if (typeof body.name !== 'string' || !body.name.trim()) {
      return NextResponse.json({ error: 'name is required' }, { status: 400 });
    }
    if (typeof body.data_base64 !== 'string' || body.data_base64.length === 0) {
      return NextResponse.json({ error: 'data_base64 is required' }, { status: 400 });
    }
    name = body.name.trim();
    mime = typeof body.mime === 'string' && body.mime ? body.mime : 'application/octet-stream';
    providerKeyInput =
      typeof body.provider_key === 'string' && body.provider_key.trim()
        ? body.provider_key.trim()
        : undefined;
    const buf = Buffer.from(body.data_base64, 'base64');
    size = buf.length;
    base64 = buf.toString('base64');
  }

  if (size > MAX_BYTES) {
    return NextResponse.json(
      { error: 'File too large — sandbox limit is 5 MB' },
      { status: 413 },
    );
  }

  let providerKey = providerKeyInput;
  if (!providerKey) {
    const active = await db.storageProvider.findFirst({ where: { active: true } });
    providerKey = active?.id ?? 'local_disk';
  } else {
    const exists = await db.storageProvider.findUnique({ where: { id: providerKey } });
    if (!exists) {
      return NextResponse.json(
        { error: `Unknown storage provider: ${providerKey}` },
        { status: 404 },
      );
    }
  }

  const created = await db.storageFile.create({
    data: { name, path: '/', size, mime, providerKey, data: base64 },
  });

  return NextResponse.json({
    ok: true,
    file: { id: created.id, name: created.name, size: created.size, mime: created.mime },
  });
}
