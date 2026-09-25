import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

/** GET /api/admin/storage/files/[id] → {ok, file:{name, mime, data_base64}} */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const fileId = Number(id);
  if (!Number.isInteger(fileId)) {
    return NextResponse.json({ error: 'Invalid file id' }, { status: 400 });
  }

  const row = await db.storageFile.findUnique({ where: { id: fileId } });
  if (!row) {
    return NextResponse.json({ error: 'File not found' }, { status: 404 });
  }

  return NextResponse.json({
    ok: true,
    file: { name: row.name, mime: row.mime, data_base64: row.data ?? '' },
  });
}

/** DELETE /api/admin/storage/files/[id] → {ok} */
export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const fileId = Number(id);
  if (!Number.isInteger(fileId)) {
    return NextResponse.json({ error: 'Invalid file id' }, { status: 400 });
  }

  const row = await db.storageFile.findUnique({ where: { id: fileId } });
  if (!row) {
    return NextResponse.json({ error: 'File not found' }, { status: 404 });
  }

  await db.storageFile.delete({ where: { id: fileId } });
  return NextResponse.json({ ok: true });
}
