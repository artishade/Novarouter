/**
 * POST /api/agent/tasks/[id]/cancel — mark a task cancelled.
 * A running loop stops on its next DB status check; queued tasks never start.
 */
import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const task = await db.agentTask.findUnique({ where: { id }, select: { id: true, status: true, finishedAt: true } });
    if (!task) {
      return NextResponse.json({ error: 'Agent task not found' }, { status: 404 });
    }
    await db.agentTask.update({
      where: { id },
      data: {
        status: 'cancelled',
        ...(task.finishedAt ? {} : { finishedAt: new Date() }),
      },
    });
    return NextResponse.json({ ok: true });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to cancel agent task';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
