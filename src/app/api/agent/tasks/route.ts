/**
 * /api/agent/tasks
 *   GET  — list tasks (desc by createdAt, steps included asc by stepNumber)
 *   POST — create a task {goal, max_steps?, model?} and kick off the runner fire-and-forget
 */
import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { runAgentTask, serializeAgentTask } from '@/lib/server/agent';

export async function GET() {
  try {
    const tasks = await db.agentTask.findMany({
      orderBy: { createdAt: 'desc' },
      include: { steps: { orderBy: { stepNumber: 'asc' } } },
    });
    return NextResponse.json(tasks.map(serializeAgentTask));
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to list agent tasks';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json().catch(() => ({}))) as {
      goal?: unknown;
      max_steps?: unknown;
      model?: unknown;
    };

    const goal = typeof body.goal === 'string' ? body.goal.trim() : '';
    if (!goal) {
      return NextResponse.json({ error: "Field 'goal' is required and must be non-empty" }, { status: 400 });
    }

    const maxSteps =
      typeof body.max_steps === 'number' && Number.isFinite(body.max_steps)
        ? Math.max(1, Math.min(Math.floor(body.max_steps), 24))
        : undefined;
    const model = typeof body.model === 'string' && body.model.trim() ? body.model.trim() : 'auto';

    const task = await db.agentTask.create({
      data: {
        goal,
        model,
        ...(maxSteps !== undefined ? { maxSteps } : {}),
      },
    });

    // Fire-and-forget: respond immediately, the runner updates the DB as it progresses.
    runAgentTask(task.id).catch(() => {});

    return NextResponse.json({ id: task.id, status: task.status });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to create agent task';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
