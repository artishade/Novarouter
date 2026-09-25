/** GET /api/agent/tasks/[id] — single AgentTask with steps (404 if missing). */
import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { serializeAgentTask } from '@/lib/server/agent';

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const task = await db.agentTask.findUnique({
      where: { id },
      include: { steps: { orderBy: { stepNumber: 'asc' } } },
    });
    if (!task) {
      return NextResponse.json({ error: 'Agent task not found' }, { status: 404 });
    }
    return NextResponse.json(serializeAgentTask(task));
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to load agent task';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
