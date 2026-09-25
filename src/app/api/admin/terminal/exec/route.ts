import { NextRequest, NextResponse } from 'next/server';
import { setConfig } from '@/lib/server/config';
import { executeCommand } from '@/lib/server/terminal-exec';

/** POST /api/admin/terminal/exec {command, cwd?} → ExecResult */
export async function POST(req: NextRequest) {
  let body: { command?: unknown; cwd?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  if (typeof body.command !== 'string' || !body.command.trim()) {
    return NextResponse.json({ error: 'command is required' }, { status: 400 });
  }

  const cwdInput = typeof body.cwd === 'string' && body.cwd.trim() ? body.cwd : undefined;
  const result = await executeCommand(body.command, cwdInput);

  // Keep the persisted cwd in sync so terminal/history reflects the session.
  if (cwdInput) {
    try {
      await setConfig('terminal_cwd', result.cwd);
    } catch {
      /* best-effort */
    }
  }

  return NextResponse.json(result);
}
