/**
 * Nova Agent — autonomous task runner (backend only).
 *
 * Loop: plan (LLM, strict JSON) → execute tool → persist AgentStep → repeat
 * until `finish` or the step limit; then a final LLM call writes the summary.
 * All tool failures are recorded as error steps and never abort the task;
 * a cancelled status in the DB stops the loop on the next iteration.
 *
 * z-ai-web-dev-sdk is used here (server-side only — never imported by client code).
 */
import ZAI from 'z-ai-web-dev-sdk';
import os from 'os';
import type { AgentStep, AgentTask } from '@prisma/client';
import { db } from '@/lib/db';

const MAX_DETAIL_CHARS = 2000;
const ALLOWED_ACTIONS = new Set([
  'web_search',
  'read_url',
  'terminal',
  'gateway_stats',
  'storage_scan',
  'finish',
]);

const AGENT_SYSTEM_PROMPT = `You are Nova Agent, an autonomous research & operations agent inside the NovaRouter AI gateway.

You work toward the user's goal one step at a time using these tools:
- web_search: search the live web for up-to-date information ("query_or_url_or_command" = search query)
- read_url: read and extract any web page ("query_or_url_or_command" = full URL, must start with http)
- terminal: run a safe diagnostic command in the gateway sandbox ("query_or_url_or_command" = command, e.g. "free -m", "df -h", "nova status", "nova gpu")
- gateway_stats: inspect live gateway stats, models and routes (no input needed)
- storage_scan: scan configured storage providers and files (no input needed)
- finish: the goal is achieved ("query_or_url_or_command" = final key takeaway, optional)

STRICT OUTPUT RULE — respond with JSON only, no prose, no markdown fences, exactly this shape:
{"action": "web_search|read_url|terminal|gateway_stats|storage_scan|finish", "title": "short title", "query_or_url_or_command": "...", "reason": "why this step"}

Rules:
1. Exactly one action per response.
2. Titles are short labels (max 60 chars); "reason" explains why this step helps the goal.
3. Prefer web_search then read_url to gather evidence; use terminal/gateway_stats/storage_scan for system questions.
4. Never repeat a step that already succeeded with the same input — read the step log first.
5. As soon as the goal is achieved (or no further step adds value), respond with action "finish".`;

const FINAL_SYSTEM_PROMPT =
  'Write the final answer to the goal in concise markdown (≤200 words), using the evidence gathered.';

/* ------------------------------------------------------------------ */
/* Serialization (snake_case wire format per src/lib/types.ts)         */
/* ------------------------------------------------------------------ */

type TaskWithSteps = AgentTask & { steps: AgentStep[] };

export function serializeAgentTask(task: TaskWithSteps) {
  return {
    id: task.id,
    goal: task.goal,
    status: task.status,
    model: task.model,
    max_steps: task.maxSteps,
    summary: task.summary,
    error: task.error ?? null,
    created_at: task.createdAt.getTime(),
    started_at: task.startedAt ? task.startedAt.getTime() : null,
    finished_at: task.finishedAt ? task.finishedAt.getTime() : null,
    steps: task.steps.map((s) => ({
      id: s.id,
      step_number: s.stepNumber,
      action: s.action,
      title: s.title,
      description: s.description,
      detail: s.detail,
      status: s.status,
      latency_ms: s.latencyMs,
      created_at: s.createdAt.getTime(),
    })),
  };
}

/* ------------------------------------------------------------------ */
/* Planning (LLM)                                                      */
/* ------------------------------------------------------------------ */

interface AgentPlan {
  action: string;
  title: string;
  query: string;
  reason: string;
}

/** Defensively parse the planner output: strip code fences, find the first {...} block. */
function parsePlan(raw: string): AgentPlan | null {
  if (!raw) return null;
  let text = raw.trim();
  text = text
    .replace(/^```(?:json)?\s*\n?/i, '')
    .replace(/\n?```\s*$/i, '')
    .trim();
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start === -1 || end === -1 || end <= start) return null;
  let obj: unknown;
  try {
    obj = JSON.parse(text.slice(start, end + 1));
  } catch {
    return null;
  }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return null;
  const o = obj as Record<string, unknown>;
  const action = String(o.action ?? '').trim();
  if (!ALLOWED_ACTIONS.has(action)) return null;
  return {
    action,
    title: String(o.title ?? action).trim().slice(0, 120) || action,
    query: String(o.query_or_url_or_command ?? '').trim(),
    reason: String(o.reason ?? '').trim(),
  };
}

function buildPlannerUserPrompt(goal: string, steps: AgentStep[]): string {
  const log =
    steps.length === 0
      ? '(no steps yet — this is the beginning of the task)'
      : steps
          .map((s) => {
            const detail = s.detail.replace(/\s+/g, ' ').trim().slice(0, 200) || '(no detail)';
            const flag = s.status === 'error' ? ' [FAILED]' : '';
            return `${s.stepNumber}. [${s.action}${flag}] ${s.title} → ${detail}`;
          })
          .join('\n');
  return `# Goal\n${goal}\n\n# Step log so far\n${log}\n\nDecide the next single action. Respond with STRICT JSON per the system rules. Use action "finish" when the goal is achieved.`;
}

/* ------------------------------------------------------------------ */
/* Tool implementations                                                */
/* ------------------------------------------------------------------ */

function stripHtml(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

async function toolWebSearch(zai: ZAI, query: string): Promise<string> {
  const q = query || 'NovaRouter AI gateway';
  const results = await zai.functions.invoke('web_search', { query: q, num: 5 });
  if (!Array.isArray(results) || results.length === 0) return `No results for "${q}".`;
  return results
    .map((r, i) => `${i + 1}. ${r.name}\n   ${r.url}\n   ${String(r.snippet ?? '').trim().slice(0, 200)}`)
    .join('\n');
}

async function toolReadUrl(zai: ZAI, url: string): Promise<string> {
  const page = await zai.functions.invoke('page_reader', { url });
  const title = page?.data?.title || url;
  const text = stripHtml(String(page?.data?.html ?? '')).slice(0, 1500);
  return `${title}\n\n${text || '(no extractable text)'}`;
}

function fallbackSystemStats(command: string): string {
  const totalMb = Math.round(os.totalmem() / (1024 * 1024));
  const freeMb = Math.round(os.freemem() / (1024 * 1024));
  const load = os.loadavg()[0];
  const loadPct = Math.min(99, Math.round((load / Math.max(1, os.cpus().length)) * 100));
  return [
    `$ ${command}`,
    '[sandbox fallback] Safe executor module unavailable — basic system diagnostics only:',
    `Host: ${os.hostname()} · ${os.platform()} ${os.arch()} · Node ${process.version}`,
    `CPUs: ${os.cpus().length} · Load: ${loadPct}%`,
    `Memory: ${freeMb} MB free / ${totalMb} MB total (${Math.round((freeMb / Math.max(1, totalMb)) * 100)}% free)`,
    `Uptime: ${Math.round(os.uptime())}s`,
  ].join('\n');
}

/** Dynamic-import agent 2-b's safe executor; fall back to OS stats if it is missing. */
async function toolTerminal(command: string): Promise<string> {
  const cmd = command || 'nova status';
  try {
    const mod = (await import('@/lib/server/terminal-exec')) as {
      executeCommand?: (command: string) => Promise<{
        ok?: boolean;
        output?: string;
        stdout?: string;
        stderr?: string;
        code?: number;
      }>;
    };
    if (mod && typeof mod.executeCommand === 'function') {
      const res = await mod.executeCommand(cmd);
      const out = String(
        res.output ?? [res.stdout, res.stderr].filter((x) => x && String(x).trim()).join('\n')
      ).trim();
      return `$ ${cmd}\n${out || '(no output)'}`;
    }
    return fallbackSystemStats(cmd);
  } catch {
    return fallbackSystemStats(cmd);
  }
}

async function toolGatewayStats(): Promise<string> {
  const since = new Date(Date.now() - 24 * 3600 * 1000);
  const [providers, enabledProviders, models, healthy, clientKeys, logs] = await Promise.all([
    db.provider.count(),
    db.provider.count({ where: { enabled: true } }),
    db.model.count(),
    db.model.count({ where: { status: 'healthy', enabled: true } }),
    db.clientKey.count(),
    db.requestLog.findMany({ where: { ts: { gte: since } }, select: { status: true, latencyMs: true } }),
  ]);
  const errors = logs.filter((l) => l.status >= 400).length;
  const avgLatency = logs.length
    ? Math.round(logs.reduce((a, l) => a + l.latencyMs, 0) / logs.length)
    : 0;
  const errPct = logs.length ? ((errors / logs.length) * 100).toFixed(1) : '0.0';
  return [
    'Gateway telemetry (live):',
    `• Providers: ${providers} registered · ${enabledProviders} enabled`,
    `• Models: ${models} in catalogue · ${healthy} healthy`,
    `• Client keys: ${clientKeys}`,
    `• Requests (24h): ${logs.length} · Errors: ${errors} (${errPct}%) · Avg latency: ${avgLatency} ms`,
  ].join('\n');
}

async function toolStorageScan(): Promise<string> {
  const [providers, files] = await Promise.all([
    db.storageProvider.findMany({ orderBy: [{ active: 'desc' }, { id: 'asc' }] }),
    db.storageFile.findMany({ select: { name: true, size: true } }),
  ]);
  const lines = providers.map((p) => {
    const flag = p.active ? 'ACTIVE' : p.status;
    const usage =
      p.quotaMb > 0
        ? `— ${p.usageMb.toFixed(1)}/${p.quotaMb.toFixed(0)} MB`
        : `— ${p.usageMb.toFixed(1)} MB used`;
    const tier = p.freeTier ? ` · free tier: ${p.freeTier}` : '';
    return `• ${p.id} (${p.type}): ${flag} ${usage}${tier}`;
  });
  const totalBytes = files.reduce((a, f) => a + f.size, 0);
  return [
    'Storage scan:',
    ...(lines.length ? lines : ['• (no storage providers configured)']),
    `Files stored: ${files.length} (total ${(totalBytes / (1024 * 1024)).toFixed(2)} MB)`,
  ].join('\n');
}

async function executeTool(zai: ZAI, plan: AgentPlan): Promise<string> {
  switch (plan.action) {
    case 'web_search':
      return toolWebSearch(zai, plan.query);
    case 'read_url':
      return toolReadUrl(zai, plan.query);
    case 'terminal':
      return toolTerminal(plan.query);
    case 'gateway_stats':
      return toolGatewayStats();
    case 'storage_scan':
      return toolStorageScan();
    default:
      throw new Error(`Unknown action: ${plan.action}`);
  }
}

/* ------------------------------------------------------------------ */
/* Runner                                                              */
/* ------------------------------------------------------------------ */

export async function runAgentTask(taskId: string): Promise<void> {
  let zai: ZAI | null = null;
  try {
    const task = await db.agentTask.findUnique({ where: { id: taskId } });
    if (!task || task.status === 'cancelled' || task.status === 'completed' || task.status === 'failed') {
      return;
    }

    await db.agentTask.update({
      where: { id: taskId },
      data: { status: 'running', startedAt: task.startedAt ?? new Date(), error: null },
    });

    zai = await ZAI.create();

    const maxSteps = Math.max(1, Math.min(task.maxSteps || 8, 24));
    let parseRetried = false; // one retry after an unparseable planner response
    let finished = false;

    for (let i = 0; i < maxSteps; i++) {
      // Honor cancellation between steps.
      const current = await db.agentTask.findUnique({ where: { id: taskId }, select: { status: true } });
      if (!current || current.status === 'cancelled') return;

      const steps = await db.agentStep.findMany({
        where: { taskId },
        orderBy: { stepNumber: 'asc' },
      });
      const nextStepNumber = (steps[steps.length - 1]?.stepNumber ?? 0) + 1;

      // ── Plan via LLM ──
      const planStart = Date.now();
      let raw = '';
      let plan: AgentPlan | null = null;
      try {
        const completion = await zai.chat.completions.create({
          messages: [
            { role: 'system', content: AGENT_SYSTEM_PROMPT },
            { role: 'user', content: buildPlannerUserPrompt(task.goal, steps) },
          ],
          thinking: { type: 'disabled' },
        });
        raw = String(completion?.choices?.[0]?.message?.content ?? '');
        plan = parsePlan(raw);
      } catch (err) {
        raw = `LLM call failed: ${err instanceof Error ? err.message : String(err)}`;
      }
      const planLatency = Date.now() - planStart;

      if (!plan) {
        // Unparseable / failed planner output → record a 'think' step, retry once, then fail.
        if (parseRetried) {
          await db.agentTask.update({
            where: { id: taskId },
            data: {
              status: 'failed',
              error: 'Agent planner returned unparseable output twice',
              finishedAt: new Date(),
            },
          });
          return;
        }
        parseRetried = true;
        await db.agentStep.create({
          data: {
            taskId,
            stepNumber: nextStepNumber,
            action: 'think',
            title: 'Planning (unparsed response)',
            description: 'Planner output was not valid JSON — retrying once.',
            detail: raw.slice(0, MAX_DETAIL_CHARS),
            status: 'error',
            latencyMs: planLatency,
          },
        });
        continue;
      }
      parseRetried = false;

      // ── Finish action ──
      if (plan.action === 'finish') {
        await db.agentStep.create({
          data: {
            taskId,
            stepNumber: nextStepNumber,
            action: 'finish',
            title: plan.title,
            description: plan.reason,
            detail: plan.query || plan.reason || 'Goal achieved.',
            status: 'info',
            latencyMs: planLatency,
          },
        });
        finished = true;
        break;
      }

      // ── Execute tool with timing ──
      const toolStart = Date.now();
      let detail = '';
      let status: 'ok' | 'error' = 'ok';
      try {
        detail = await executeTool(zai, plan);
      } catch (err) {
        detail = `Tool error: ${err instanceof Error ? err.message : String(err)}`;
        status = 'error';
      }
      const toolLatency = Date.now() - toolStart;

      await db.agentStep.create({
        data: {
          taskId,
          stepNumber: nextStepNumber,
          action: plan.action,
          title: plan.title,
          description: plan.reason,
          detail: detail.slice(0, MAX_DETAIL_CHARS),
          status,
          latencyMs: toolLatency,
        },
      });
    }

    // Honor cancellation after the loop as well.
    const afterLoop = await db.agentTask.findUnique({ where: { id: taskId }, select: { status: true } });
    if (!afterLoop || afterLoop.status === 'cancelled') return;

    // ── Final summary via LLM ──
    const allSteps = await db.agentStep.findMany({ where: { taskId }, orderBy: { stepNumber: 'asc' } });
    let summary = '';
    try {
      const evidence =
        allSteps.map((s) => `${s.stepNumber}. [${s.action}] ${s.title}\n${s.detail}`).join('\n\n') ||
        '(no steps recorded)';
      const completion = await zai.chat.completions.create({
        messages: [
          { role: 'system', content: FINAL_SYSTEM_PROMPT },
          { role: 'user', content: `# Goal\n${task.goal}\n\n# Evidence gathered\n${evidence}` },
        ],
        thinking: { type: 'disabled' },
      });
      summary = String(completion?.choices?.[0]?.message?.content ?? '').trim();
    } catch {
      summary = '';
    }

    if (!summary) {
      // Graceful fallback if the final LLM call fails.
      summary =
        allSteps
          .map((s) => `- **${s.title}** (${s.action})${s.detail ? `: ${s.detail.replace(/\s+/g, ' ').trim().slice(0, 160)}` : ''}`)
          .join('\n') || 'Task ended without recorded steps.';
    }

    await db.agentTask.update({
      where: { id: taskId },
      data: {
        status: 'completed',
        summary,
        finishedAt: new Date(),
      },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    await db.agentTask
      .update({
        where: { id: taskId },
        data: { status: 'failed', error: message, finishedAt: new Date() },
      })
      .catch(() => undefined);
  }
}
