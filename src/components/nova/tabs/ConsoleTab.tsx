'use client';

/**
 * ConsoleTab — the unified Nova Console.
 *
 * One chatbox, three modes, auto-routed:
 *   Chat      — natural questions are answered through the gateway routing pipeline
 *               (with fallback/spoof telemetry). If the message is an actionable task
 *               the model delegates it to the autonomous agent.
 *   Terminal  — `$ <command>` or slash diagnostics (/status /gpu /boost …) run in the
 *               safe sandbox executor and stream the output back as a terminal block.
 *   Agent     — `! <goal>` or `/agent <goal>` (or a delegated task) launches a real
 *               autonomous agent run; steps stream live into the conversation.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Components } from 'react-markdown';
import type { ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import { motion } from 'framer-motion';
import {
  Activity,
  BookOpen,
  Bot,
  Brain,
  CheckCircle2,
  ChevronDown,
  CircleStop,
  Clock,
  Eraser,
  Globe,
  HardDrive,
  Loader2,
  MessagesSquare,
  Send,
  Sparkles,
  TerminalSquare,
  Wrench,
  XCircle,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { api } from '@/lib/api';
import type { AgentTask, GatewayStats, MetaConfig, Model } from '@/lib/types';
import { cx } from '@/lib/format';

/* ============================== types ============================== */

interface ResponseMeta {
  upstream_model: string;
  provider: string;
  fallback: boolean;
  cached: boolean;
  spoofed: boolean;
  stage: number;
}

type ConsoleMsg =
  | { kind: 'system'; id: string; text: string; at: number }
  | { kind: 'user'; id: string; text: string; at: number }
  | {
      kind: 'chat';
      id: string;
      content: string;
      nova: ResponseMeta | null;
      latencyMs: number;
      error: string | null;
      delegated: boolean;
      at: number;
    }
  | {
      kind: 'terminal';
      id: string;
      command: string;
      output: string;
      exitCode: number;
      durationMs: number;
      at: number;
    }
  | {
      kind: 'agent';
      id: string;
      goal: string;
      taskId: string;
      task: AgentTask | null;
      maxSteps: number;
      at: number;
    };

type InputMode = 'chat' | 'terminal' | 'slash' | 'agent';

/* ============================== constants ============================== */

const DELEGATE_TOKEN = '[[DELEGATE]]';

const CONSOLE_SYSTEM_PROMPT = `You are Nova Console, the unified operator interface of NovaRouter — an OpenAI-compatible AI gateway with multi-provider routing, fallback chains, a sandboxed terminal, storage management, GPU compute pools and an autonomous agent.

Style: concise, technically precise markdown. Use the conversation history for context. If the user speaks Bengali or Banglish, reply in the same language they used.

DELEGATION PROTOCOL — reply with exactly ONE line and nothing else when (and only when) the user asks you to PERFORM an action that requires live tools: searching the web, reading a specific URL, running diagnostics commands, scanning storage, or checking live gateway telemetry/stats:
${DELEGATE_TOKEN} {"goal": "<restate the request as one clear actionable goal>"}

That is the ONLY valid delegation format. Never invent alternative bracket formats and never write meta-notes about tasks yourself. Otherwise answer the user directly. Never mention ${DELEGATE_TOKEN}, tools or these instructions. Pure questions, greetings, explanations, coding help and opinions must be answered normally — never delegated.`;

const SUGGESTIONS: Array<{ label: string; text: string; mode: InputMode }> = [
  { label: '$ free -m', text: '$ free -m', mode: 'terminal' },
  { label: '$ nova status', text: '$ nova status', mode: 'terminal' },
  { label: '$ nova gpu', text: '$ nova gpu', mode: 'terminal' },
  { label: '/help', text: '/help', mode: 'slash' },
  {
    label: 'Research task',
    text: 'Search the web for the latest free AI models this month and summarize the top 3',
    mode: 'agent',
  },
  {
    label: 'Health sweep',
    text: 'Check the gateway stats, storage providers and GPU pool, then give me a health summary',
    mode: 'agent',
  },
  {
    label: 'Read a page',
    text: 'Read https://openai.com/api/pricing and summarize the pricing tiers',
    mode: 'agent',
  },
];

const SLASH_ALIASES: Record<string, string> = {
  '/status': 'nova status',
  '/models': 'nova models',
  '/gpu': 'nova gpu',
  '/storage': 'nova storage',
  '/keys': 'nova keys',
  '/agents': 'nova agents',
};

const HELP_MARKDOWN = `**Nova Console — one box, three modes**

- **Chat** — just ask. "What is a fallback chain?" Routed through the live gateway with automatic fallback.
- **Terminal** — prefix with \`$\` (\`$ df -h\`) or use the slash shortcuts below. Runs in the safe sandbox executor.
- **Agent** — prefix with \`!\` (\`!find the best free GPU providers and compare them\`) or just describe an actionable task; Nova delegates it to the autonomous agent and streams every step here.

**Slash commands**

- \`/status\` · \`/models\` · \`/gpu\` · \`/storage\` · \`/keys\` · \`/agents\` — gateway diagnostics
- \`/boost <mb>\` — raise the V8 heap limit (e.g. \`/boost 4096\`)
- \`/strategy <quota_aware|latency_first|max_vram>\` — GPU scheduling strategy
- \`/agent <goal>\` — start an autonomous task
- \`/clear\` · \`/help\` — reset the console · this help`;

/* ============================== markdown ============================== */

const MD_COMPONENTS: Components = {
  p: ({ children }) => <p className="mb-2 leading-relaxed last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="mb-2 list-disc space-y-1 pl-5 last:mb-0">{children}</ul>,
  ol: ({ children }) => <ol className="mb-2 list-decimal space-y-1 pl-5 last:mb-0">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  h1: ({ children }) => <h1 className="mb-2 text-base font-semibold text-slate-100">{children}</h1>,
  h2: ({ children }) => <h2 className="mb-2 text-sm font-semibold text-slate-100">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-1.5 text-sm font-semibold text-slate-200">{children}</h3>,
  strong: ({ children }) => <strong className="font-semibold text-slate-100">{children}</strong>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-emerald-300 underline underline-offset-2">
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className="mb-2 overflow-x-auto rounded-lg border border-slate-800">
      <table className="w-full text-left text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-slate-800 bg-slate-900/60 px-2.5 py-1.5 font-medium text-slate-300">{children}</th>
  ),
  td: ({ children }) => <td className="border-b border-slate-800/60 px-2.5 py-1.5 text-slate-400">{children}</td>,
  blockquote: ({ children }) => (
    <blockquote className="mb-2 border-l-2 border-slate-700 pl-3 text-slate-400 last:mb-0">{children}</blockquote>
  ),
  pre: ({ children }) => (
    <pre className="mb-2 overflow-x-auto rounded-lg border border-slate-800 bg-black/60 p-3 font-mono text-[11px] leading-relaxed text-emerald-200 last:mb-0">
      {children}
    </pre>
  ),
  code: ({ className, children }) => {
    if (typeof className === 'string' && className.includes('language-')) {
      return <code className="font-mono text-[11px] text-emerald-200">{children}</code>;
    }
    return <code className="rounded bg-black/50 px-1 py-0.5 font-mono text-[0.85em] text-emerald-300">{children}</code>;
  },
  hr: () => <hr className="my-3 border-slate-800" />,
};

/* ============================== helpers ============================== */

let idCounter = 0;
function uid(): string {
  idCounter += 1;
  return `c-${Date.now().toString(36)}-${idCounter.toString(36)}`;
}

function detectMode(text: string): InputMode {
  const t = text.trimStart();
  if (!t) return 'chat';
  if (t.startsWith('$')) return 'terminal';
  if (t.startsWith('/')) return 'slash';
  if (t.startsWith('!') || /^(agent|task)[:\s]/i.test(t)) return 'agent';
  return 'chat';
}

/** Extract a delegation goal from a chat reply; null when the reply is a normal answer. */
function parseDelegate(content: string): string | null {
  const trimmed = content.trimStart();
  const direct = trimmed.startsWith(DELEGATE_TOKEN);
  /* safety net: the model may imitate the history note format '[agent task: ...]' */
  const imitated = /^\r?\[agent task\s*:\s*/i.test(trimmed);
  if (!direct && !imitated) return null;
  const line =
    content
      .split('\n')
      .map((l) => l.trim())
      .find((l) => l.startsWith(DELEGATE_TOKEN)) ??
    trimmed.split('\n')[0];
  if (!line) return null;
  try {
    const obj = JSON.parse(line.slice(DELEGATE_TOKEN.length).trim()) as { goal?: unknown };
    const goal = typeof obj.goal === 'string' ? obj.goal.trim() : '';
    return goal || null;
  } catch {
    let rest = line.slice(DELEGATE_TOKEN.length).trim();
    if (!direct) {
      rest = rest.replace(/^\[agent task\s*:\s*/i, '').replace(/\]$/, '');
    }
    rest = rest.replace(/^"?\{?\s*"?goal"?\s*:\s*"?/, '').replace(/"?\}?$/, '').trim();
    return rest || null;
  }
}

const ACTION_ICON: Record<string, LucideIcon> = {
  web_search: Globe,
  read_url: BookOpen,
  terminal: TerminalSquare,
  gateway_stats: Activity,
  storage_scan: HardDrive,
  think: Brain,
  finish: CheckCircle2,
};

function actionIcon(action: string): LucideIcon {
  return ACTION_ICON[action] ?? Wrench;
}

function AgentStatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    queued: { label: 'Queued', cls: 'border-slate-700 bg-slate-800/60 text-slate-300' },
    running: { label: 'Running', cls: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300' },
    completed: { label: 'Completed', cls: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300' },
    failed: { label: 'Failed', cls: 'border-rose-500/40 bg-rose-500/10 text-rose-300' },
    cancelled: { label: 'Cancelled', cls: 'border-amber-500/40 bg-amber-500/10 text-amber-300' },
  };
  const meta = map[status] ?? { label: status, cls: 'border-slate-700 bg-slate-800/60 text-slate-300' };
  return (
    <span className={cx('inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium', meta.cls)}>
      {status === 'running' && <Loader2 className="size-3 animate-spin" aria-hidden />}
      {meta.label}
    </span>
  );
}

function elapsedLabel(from: number, to: number): string {
  const s = Math.max(0, Math.round((to - from) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

/* ============================== sub-components ============================== */

function MetaChips({ nova, latencyMs }: { nova: ResponseMeta | null; latencyMs: number }) {
  if (!nova) return null;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      <span className="inline-flex items-center gap-1 rounded-md border border-slate-800 bg-slate-900/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
        <Sparkles className="size-3 text-emerald-400" aria-hidden />
        {nova.provider}
      </span>
      <span className="rounded-md border border-slate-800 bg-slate-900/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
        {nova.upstream_model}
      </span>
      {nova.fallback && (
        <span className="rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-300">
          fallback · stage {nova.stage}
        </span>
      )}
      {nova.spoofed && (
        <span className="rounded-md border border-purple-500/30 bg-purple-500/10 px-1.5 py-0.5 text-[10px] text-purple-300">
          identity spoofed
        </span>
      )}
      <span className="rounded-md border border-slate-800 bg-slate-900/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-500">
        {latencyMs} ms
      </span>
    </div>
  );
}

function TerminalBlock({ msg }: { msg: Extract<ConsoleMsg, { kind: 'terminal' }> }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="w-full max-w-3xl overflow-hidden rounded-xl border border-slate-800 bg-[#070b13]"
    >
      <div className="flex items-center justify-between gap-2 border-b border-slate-800/80 bg-slate-900/40 px-3 py-1.5">
        <div className="flex items-center gap-2 text-[11px] text-slate-500">
          <TerminalSquare className="size-3.5 text-sky-400" aria-hidden />
          <span className="font-mono">nova@gateway sandbox</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[10px] text-slate-600">{msg.durationMs} ms</span>
          <span
            className={cx(
              'rounded border px-1.5 py-0.5 font-mono text-[10px]',
              msg.exitCode === 0
                ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                : 'border-rose-500/30 bg-rose-500/10 text-rose-300',
            )}
          >
            exit {msg.exitCode}
          </span>
        </div>
      </div>
      <div className="max-h-72 overflow-y-auto px-3 py-2.5">
        <div className="font-mono text-[11px] leading-relaxed">
          <span className="text-emerald-400">nova@gateway:~$ </span>
          <span className="text-slate-200">{msg.command}</span>
        </div>
        <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-slate-300">
          {msg.output || '(no output)'}
        </pre>
      </div>
    </motion.div>
  );
}

function AgentCard({
  msg,
  onCancel,
}: {
  msg: Extract<ConsoleMsg, { kind: 'agent' }>;
  onCancel: (taskId: string) => void;
}) {
  const [openStep, setOpenStep] = useState<number | null>(null);
  const task = msg.task;
  const running = task ? task.status === 'queued' || task.status === 'running' : true;
  const steps = task?.steps ?? [];
  const done = steps.length;
  const now = Date.now();
  const startedAt = task?.started_at ?? task?.created_at ?? msg.at;
  const finishedAt = task?.finished_at ?? (running ? now : startedAt);

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="w-full max-w-3xl overflow-hidden rounded-xl border border-slate-800 bg-[#0d1322]"
    >
      {/* header */}
      <div className="flex items-start gap-3 border-b border-slate-800/80 bg-slate-900/40 px-4 py-3">
        <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg border border-emerald-500/30 bg-emerald-500/10">
          <Bot className="size-4 text-emerald-400" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-emerald-400">
              Nova Agent
            </span>
            <AgentStatusBadge status={task?.status ?? 'queued'} />
            <span className="inline-flex items-center gap-1 font-mono text-[10px] text-slate-500">
              <Clock className="size-3" aria-hidden />
              {running ? elapsedLabel(startedAt, now) : elapsedLabel(startedAt, finishedAt)}
            </span>
            <span className="font-mono text-[10px] text-slate-600">
              steps {done}/{msg.maxSteps}
            </span>
          </div>
          <p className="mt-1 line-clamp-2 text-sm text-slate-200" title={msg.goal}>
            {msg.goal}
          </p>
        </div>
        {running && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => onCancel(msg.taskId)}
            className="h-8 shrink-0 border-rose-500/30 px-2.5 text-rose-300 hover:bg-rose-500/10 hover:text-rose-200"
            aria-label="Cancel agent task"
          >
            <CircleStop className="size-3.5" aria-hidden />
            Stop
          </Button>
        )}
      </div>

      {/* steps timeline */}
      <div className="max-h-80 overflow-y-auto px-4 py-3">
        {steps.length === 0 && running && (
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span className="flex gap-1" aria-label="Agent is planning">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="size-1.5 animate-pulse rounded-full bg-emerald-400"
                  style={{ animationDelay: `${i * 180}ms` }}
                />
              ))}
            </span>
            Planning first move…
          </div>
        )}
        <ol className="space-y-2">
          {steps.map((s) => {
            const Icon = actionIcon(s.action);
            const open = openStep === s.step_number;
            return (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => setOpenStep(open ? null : s.step_number)}
                  aria-expanded={open}
                  className="flex w-full items-center gap-2.5 rounded-lg border border-transparent px-2 py-1.5 text-left transition-colors hover:border-slate-800 hover:bg-slate-900/50"
                >
                  <span
                    className={cx(
                      'flex size-6 shrink-0 items-center justify-center rounded-md border',
                      s.status === 'error'
                        ? 'border-rose-500/30 bg-rose-500/10 text-rose-300'
                        : 'border-slate-700 bg-slate-900 text-emerald-400',
                    )}
                  >
                    <Icon className="size-3.5" aria-hidden />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="truncate text-xs font-medium text-slate-200">{s.title}</span>
                      <span
                        className={cx(
                          'size-1.5 shrink-0 rounded-full',
                          s.status === 'error' ? 'bg-rose-400' : s.status === 'info' ? 'bg-sky-400' : 'bg-emerald-400',
                        )}
                        aria-hidden
                      />
                    </span>
                    <span className="mt-0.5 block truncate text-[11px] text-slate-500">
                      {s.description || s.action}
                    </span>
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-slate-600">{s.latency_ms} ms</span>
                  <ChevronDown
                    className={cx('size-3.5 shrink-0 text-slate-600 transition-transform', open && 'rotate-180')}
                    aria-hidden
                  />
                </button>
                {open && (
                  <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-800 bg-black/50 px-3 py-2 font-mono text-[10px] leading-relaxed text-slate-300">
                    {s.detail || '(no detail)'}
                  </pre>
                )}
              </li>
            );
          })}
        </ol>
        {running && steps.length > 0 && (
          <div className="mt-2 flex items-center gap-2 text-[11px] text-slate-500">
            <Loader2 className="size-3 animate-spin text-emerald-400" aria-hidden />
            Working — step {done} of {msg.maxSteps}…
          </div>
        )}
      </div>

      {/* final summary */}
      {task && (task.status === 'completed' || task.status === 'failed') && (
        <div className="border-t border-slate-800/80 px-4 py-3">
          {task.status === 'failed' ? (
            <p className="text-xs text-rose-300">
              Task failed: {task.error || 'unknown error'}
            </p>
          ) : (
            <>
              <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-emerald-400">
                <CheckCircle2 className="size-3.5" aria-hidden />
                Result
              </div>
              <div className="text-[13px] text-slate-300">
                <ReactMarkdown components={MD_COMPONENTS}>{task.summary || '(no summary)'}</ReactMarkdown>
              </div>
            </>
          )}
        </div>
      )}
    </motion.div>
  );
}

function WelcomeState({ onPick }: { onPick: (text: string) => void }) {
  const cards: Array<{ icon: LucideIcon; title: string; desc: string; examples: string[]; accent: string }> = [
    {
      icon: MessagesSquare,
      title: 'Chat',
      desc: 'Ask anything — answered through the live gateway routing pipeline.',
      examples: ['What is a fallback chain?', 'Explain identity spoofing'],
      accent: 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10',
    },
    {
      icon: TerminalSquare,
      title: 'Terminal',
      desc: 'Prefix with $ or use slash commands for the sandbox shell.',
      examples: ['$ nova status', '$ nova gpu'],
      accent: 'text-sky-400 border-sky-500/30 bg-sky-500/10',
    },
    {
      icon: Bot,
      title: 'Agent',
      desc: 'Prefix with ! — or just describe a task. Nova delegates and streams every step.',
      examples: ['!research the top free AI providers', '!scan storage and report usage'],
      accent: 'text-purple-400 border-purple-500/30 bg-purple-500/10',
    },
  ];
  return (
    <div className="flex h-full flex-col items-center justify-center px-4 py-8">
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
        className="w-full max-w-2xl"
      >
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 flex size-12 items-center justify-center rounded-2xl border border-emerald-500/30 bg-emerald-500/10">
            <Sparkles className="size-6 text-emerald-400" aria-hidden />
          </div>
          <h2 className="text-lg font-semibold text-slate-100">Nova Console</h2>
          <p className="mt-1 text-sm text-slate-400">
            One chatbox — chat with the gateway, run sandbox commands and launch autonomous tasks.
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          {cards.map((c) => (
            <div key={c.title} className="rounded-xl border border-slate-800 bg-[#0d1322] p-4">
              <div className={cx('mb-2 flex size-8 items-center justify-center rounded-lg border', c.accent)}>
                <c.icon className="size-4" aria-hidden />
              </div>
              <div className="text-sm font-semibold text-slate-100">{c.title}</div>
              <p className="mt-1 text-[11px] leading-relaxed text-slate-500">{c.desc}</p>
              <div className="mt-3 flex flex-col gap-1.5">
                {c.examples.map((ex) => (
                  <button
                    key={ex}
                    type="button"
                    onClick={() => onPick(ex)}
                    className="truncate rounded-md border border-slate-800 bg-black/30 px-2 py-1.5 text-left font-mono text-[10px] text-slate-400 transition-colors hover:border-emerald-500/40 hover:text-emerald-300"
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </motion.div>
    </div>
  );
}

/* ============================== main component ============================== */

export function ConsoleTab({
  meta,
  stats,
  models,
  onRefresh,
}: {
  meta: MetaConfig | null;
  stats: GatewayStats | null;
  models: Model[];
  onRefresh: () => void;
}) {
  const [msgs, setMsgs] = useState<ConsoleMsg[]>([]);
  const [input, setInput] = useState('');
  const [chatPending, setChatPending] = useState(false);
  const [modelId, setModelId] = useState('auto');
  const [maxSteps, setMaxSteps] = useState(8);
  const [history, setHistory] = useState<string[]>([]);
  const histIdxRef = useRef<number>(-1);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const enabledModels = useMemo(() => models.filter((m) => m.enabled), [models]);

  const modePreview = detectMode(input);
  const anyAgentRunning = msgs.some(
    (m) =>
      m.kind === 'agent' &&
      (!m.task || m.task.status === 'queued' || m.task.status === 'running'),
  );

  /* ---------- autoscroll ---------- */
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [msgs, chatPending]);

  /* ---------- live agent polling ---------- */
  const activeIdsRef = useRef<Set<string>>(new Set());
  const onRefreshRef = useRef(onRefresh);
  onRefreshRef.current = onRefresh;

  useEffect(() => {
    const active = new Set(
      msgs
        .filter(
          (m): m is Extract<ConsoleMsg, { kind: 'agent' }> =>
            m.kind === 'agent' && (!m.task || m.task.status === 'queued' || m.task.status === 'running'),
        )
        .map((m) => m.taskId),
    );
    activeIdsRef.current = active;
  }, [msgs]);

  useEffect(() => {
    const timer = window.setInterval(async () => {
      const ids = [...activeIdsRef.current];
      if (ids.length === 0) return;
      let settled = false;
      for (const id of ids) {
        try {
          const task = await api.getAgentTask(id);
          setMsgs((prev) =>
            prev.map((m) => (m.kind === 'agent' && m.taskId === id ? { ...m, task } : m)),
          );
          if (task.status === 'completed' || task.status === 'failed') {
            settled = true;
            activeIdsRef.current.delete(id);
          }
        } catch {
          /* transient poll error — retry on next tick */
        }
      }
      if (settled) onRefreshRef.current();
    }, 2000);
    return () => window.clearInterval(timer);
  }, []);

  /* ---------- message helpers ---------- */
  const push = useCallback((m: ConsoleMsg) => setMsgs((prev) => [...prev, m]), []);

  const runTerminal = useCallback(
    async (command: string) => {
      const cmd = command.trim();
      if (!cmd) {
        push({
          kind: 'terminal',
          id: uid(),
          command: 'help',
          output:
            'NovaRouter sandbox — type $ help for the command list.\nSlash shortcuts: /status /models /gpu /storage /keys /agents /boost <mb>',
          exitCode: 0,
          durationMs: 1,
          at: Date.now(),
        });
        return;
      }
      try {
        const res = await api.terminalExec(cmd);
        push({
          kind: 'terminal',
          id: uid(),
          command: cmd,
          output: res.output ?? '',
          exitCode: res.code ?? 0,
          durationMs: res.duration_ms ?? 0,
          at: Date.now(),
        });
        if (/^(nova\s+(boost|gpu)|clear)/.test(cmd)) onRefresh();
      } catch (err) {
        push({
          kind: 'terminal',
          id: uid(),
          command: cmd,
          output: `sandbox: ${err instanceof Error ? err.message : 'execution failed'}`,
          exitCode: 1,
          durationMs: 0,
          at: Date.now(),
        });
      }
    },
    [push, onRefresh],
  );

  const startAgent = useCallback(
    async (goal: string, steps = maxSteps) => {
      const trimmed = goal.trim();
      if (!trimmed) {
        push({
          kind: 'system',
          id: uid(),
          text: 'Give me a goal — e.g. !search the web for the latest free AI models and summarize them.',
          at: Date.now(),
        });
        return;
      }
      const msgId = uid();
      push({
        kind: 'agent',
        id: msgId,
        goal: trimmed,
        taskId: 'pending',
        task: null,
        maxSteps: steps,
        at: Date.now(),
      });
      try {
        const created = await api.createAgentTask({ goal: trimmed, max_steps: steps });
        const task = await api.getAgentTask(created.id).catch(() => null);
        setMsgs((prev) =>
          prev.map((m) =>
            m.kind === 'agent' && m.id === msgId ? { ...m, taskId: created.id, task } : m,
          ),
        );
      } catch (err) {
        setMsgs((prev) =>
          prev.map((m) =>
            m.kind === 'agent' && m.id === msgId
              ? {
                  ...m,
                  task: {
                    id: 'failed',
                    goal: trimmed,
                    status: 'failed',
                    model: 'auto',
                    max_steps: steps,
                    summary: '',
                    error: err instanceof Error ? err.message : 'Failed to create task',
                    created_at: Date.now(),
                    started_at: null,
                    finished_at: Date.now(),
                    steps: [],
                  },
                }
              : m,
          ),
        );
      }
    },
    [push, maxSteps],
  );

  const sendChat = useCallback(
    async (text: string) => {
      setChatPending(true);
      const startedAt = Date.now();
      /* compact the conversation so far into simple roles for context */
      const history = msgs.slice(-8).map((m): { role: string; content: string } => {
        if (m.kind === 'user') return { role: 'user', content: m.text };
        if (m.kind === 'chat') return { role: 'assistant', content: m.content.slice(0, 800) };
        if (m.kind === 'terminal')
          return { role: 'assistant', content: `(system note: the user ran the sandbox command "${m.command}", exit ${m.exitCode})` };
        if (m.kind === 'agent')
          return { role: 'assistant', content: `(system note: an autonomous agent task "${m.goal}" is handled outside this chat)` };
        return { role: 'assistant', content: m.text };
      });
      try {
        const res = await api.chatCompletion({
          model: modelId,
          messages: [
            { role: 'system', content: CONSOLE_SYSTEM_PROMPT },
            ...history,
            { role: 'user', content: text },
          ],
        });
        const content = res.choices?.[0]?.message?.content ?? '';
        const nova: ResponseMeta | null = (res as { _nova?: ResponseMeta })._nova ?? null;
        const latency = Date.now() - startedAt;
        const goal = parseDelegate(content);
        if (goal) {
          /* the model delegated this to the autonomous agent */
          push({
            kind: 'system',
            id: uid(),
            text: 'Task detected — handing it to the autonomous agent.',
            at: Date.now(),
          });
          await startAgent(goal);
        } else {
          push({
            kind: 'chat',
            id: uid(),
            content: content || '(empty response)',
            nova,
            latencyMs: latency,
            error: null,
            delegated: false,
            at: Date.now(),
          });
        }
        onRefresh();
      } catch (err) {
        push({
          kind: 'chat',
          id: uid(),
          content: '',
          nova: null,
          latencyMs: Date.now() - startedAt,
          error: err instanceof Error ? err.message : 'Chat request failed',
          delegated: false,
          at: Date.now(),
        });
      } finally {
        setChatPending(false);
      }
    },
    [msgs, modelId, push, startAgent, onRefresh],
  );

  const handleSlash = useCallback(
    async (raw: string) => {
      const [cmdRaw, ...rest] = raw.trim().split(/\s+/);
      const cmd = cmdRaw.toLowerCase();
      const argStr = rest.join(' ');
      if (cmd === '/clear') {
        setMsgs([]);
        return;
      }
      if (cmd === '/help' || cmd === '/?') {
        push({ kind: 'system', id: uid(), text: HELP_MARKDOWN, at: Date.now() });
        return;
      }
      if (cmd === '/agent' || cmd === '/task') {
        if (!argStr) {
          push({
            kind: 'system',
            id: uid(),
            text: 'Usage: /agent <goal> — e.g. /agent scan storage and report total usage',
            at: Date.now(),
          });
          return;
        }
        await startAgent(argStr);
        return;
      }
      if (cmd === '/boost') {
        if (!/^\d+$/.test(argStr)) {
          push({
            kind: 'system',
            id: uid(),
            text: 'Usage: /boost <mb> — e.g. /boost 4096 (minimum 128)',
            at: Date.now(),
          });
          return;
        }
        await runTerminal(`nova boost ${argStr}`);
        return;
      }
      if (cmd === '/strategy') {
        if (!['quota_aware', 'latency_first', 'max_vram'].includes(argStr)) {
          push({
            kind: 'system',
            id: uid(),
            text: 'Usage: /strategy <quota_aware|latency_first|max_vram>',
            at: Date.now(),
          });
          return;
        }
        await runTerminal(`nova gpu strategy ${argStr}`);
        return;
      }
      const alias = SLASH_ALIASES[cmd];
      if (alias) {
        await runTerminal(argStr ? `${alias} ${argStr}` : alias);
        return;
      }
      push({
        kind: 'system',
        id: uid(),
        text: `Unknown command \`${cmd}\` — type \`/help\` for everything the console understands.`,
        at: Date.now(),
      });
    },
    [push, startAgent, runTerminal],
  );

  const cancelAgent = useCallback(async (taskId: string) => {
    try {
      await api.cancelAgentTask(taskId);
      setMsgs((prev) =>
        prev.map((m) =>
          m.kind === 'agent' && m.taskId === taskId && m.task
            ? { ...m, task: { ...m.task, status: 'cancelled', finished_at: Date.now() } }
            : m,
        ),
      );
      activeIdsRef.current.delete(taskId);
    } catch {
      /* poller will reconcile the real status */
    }
  }, []);

  /* ---------- send ---------- */
  const send = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text || chatPending) return;
      setInput('');
      histIdxRef.current = -1;
      setHistory((prev) => (prev[prev.length - 1] === text ? prev : [...prev, text].slice(-50)));
      push({ kind: 'user', id: uid(), text, at: Date.now() });

      const mode = detectMode(text);
      if (mode === 'terminal') {
        await runTerminal(text.slice(1).trim());
        return;
      }
      if (mode === 'slash') {
        await handleSlash(text);
        return;
      }
      if (mode === 'agent') {
        const goal = text.startsWith('!') ? text.slice(1) : text.replace(/^(agent|task)[:\s]/i, '');
        await startAgent(goal);
        return;
      }
      await sendChat(text);
    },
    [chatPending, push, runTerminal, handleSlash, startAgent, sendChat],
  );

  /* ---------- keyboard ---------- */
  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void send(input);
      return;
    }
    if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && history.length > 0) {
      e.preventDefault();
      if (e.key === 'ArrowUp') {
        histIdxRef.current = histIdxRef.current < 0 ? history.length - 1 : Math.max(0, histIdxRef.current - 1);
        setInput(history[histIdxRef.current] ?? '');
      } else {
        if (histIdxRef.current < 0) return;
        histIdxRef.current += 1;
        if (histIdxRef.current >= history.length) {
          histIdxRef.current = -1;
          setInput('');
        } else {
          setInput(history[histIdxRef.current] ?? '');
        }
      }
    }
  };

  /* ---------- render ---------- */
  const modeChip: Record<InputMode, { label: string; cls: string }> = {
    chat: { label: 'Chat', cls: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300' },
    terminal: { label: 'Terminal', cls: 'border-sky-500/40 bg-sky-500/10 text-sky-300' },
    slash: { label: 'Command', cls: 'border-amber-500/40 bg-amber-500/10 text-amber-300' },
    agent: { label: 'Agent', cls: 'border-purple-500/40 bg-purple-500/10 text-purple-300' },
  };
  const chip = modeChip[modePreview];

  return (
    <div className="flex h-[calc(100vh-10.5rem)] min-h-[540px] flex-col overflow-hidden rounded-xl border border-slate-800 bg-[#0a0f1c]">
      {/* toolbar */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-slate-800 bg-[#0d1322]/80 px-4 py-2">
        <div className="flex items-center gap-2">
          <div className="flex size-7 items-center justify-center rounded-lg border border-emerald-500/30 bg-emerald-500/10">
            <Sparkles className="size-3.5 text-emerald-400" aria-hidden />
          </div>
          <div>
            <div className="text-xs font-semibold text-slate-100">Nova Console</div>
            <div className="text-[10px] text-slate-500">
              chat · terminal · agent — auto-routed from one box
            </div>
          </div>
          {input.trim() && (
            <span
              className={cx(
                'ml-2 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium',
                chip.cls,
              )}
            >
              {chip.label} mode
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Select value={modelId} onValueChange={setModelId}>
            <SelectTrigger
              size="sm"
              className="h-8 w-[150px] border-slate-800 bg-[#080c14] text-[11px] text-slate-300"
              aria-label="Chat model"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="border-slate-800 bg-[#0d1322] text-slate-200">
              <SelectItem value="auto" className="text-[11px]">
                auto — gateway routes
              </SelectItem>
              {enabledModels.slice(0, 40).map((m) => (
                <SelectItem key={m.id} value={m.exposed_id} className="text-[11px]">
                  {m.display_name}
                  {m.is_free ? ' · FREE' : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={String(maxSteps)}
            onValueChange={(v) => setMaxSteps(Number(v))}
          >
            <SelectTrigger
              size="sm"
              className="h-8 w-[104px] border-slate-800 bg-[#080c14] text-[11px] text-slate-300"
              aria-label="Agent max steps"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="border-slate-800 bg-[#0d1322] text-slate-200">
              {[4, 8, 12, 16].map((n) => (
                <SelectItem key={n} value={String(n)} className="text-[11px]">
                  {n} agent steps
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setMsgs([])}
                disabled={msgs.length === 0}
                className="h-8 border-slate-800 px-2.5 text-slate-400 hover:bg-slate-800/60 hover:text-slate-200"
                aria-label="Clear console"
              >
                <Eraser className="size-3.5" aria-hidden />
              </Button>
            </TooltipTrigger>
            <TooltipContent className="border border-slate-700 bg-[#0d1322] text-slate-200">
              Clear console
            </TooltipContent>
          </Tooltip>
        </div>
      </div>

      {/* stream */}
      <div ref={scrollRef} className="nova-scroll flex-1 overflow-y-auto px-4 py-4">
        {msgs.length === 0 && !chatPending ? (
          <WelcomeState onPick={(t) => void send(t)} />
        ) : (
          <div className="mx-auto flex max-w-4xl flex-col gap-4">
            {msgs.map((m) => {
              if (m.kind === 'system') {
                return (
                  <motion.div
                    key={m.id}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    className="mx-auto w-full max-w-2xl rounded-xl border border-slate-800 bg-[#0d1322]/70 px-4 py-3 text-[13px] text-slate-300"
                  >
                    <ReactMarkdown components={MD_COMPONENTS}>{m.text}</ReactMarkdown>
                  </motion.div>
                );
              }
              if (m.kind === 'user') {
                return (
                  <motion.div key={m.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="flex justify-end">
                    <div className="max-w-[85%] rounded-2xl rounded-tr-md border border-emerald-500/25 bg-emerald-500/10 px-4 py-2.5 text-sm text-slate-100">
                      <p className="whitespace-pre-wrap break-words">{m.text}</p>
                    </div>
                  </motion.div>
                );
              }
              if (m.kind === 'terminal') return <TerminalBlock key={m.id} msg={m} />;
              if (m.kind === 'agent')
                return (
                  <div key={m.id} className="flex justify-start">
                    <AgentCard msg={m} onCancel={(id) => void cancelAgent(id)} />
                  </div>
                );
              /* chat */
              return (
                <motion.div key={m.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="flex justify-start">
                  <div className="max-w-[85%] rounded-2xl rounded-tl-md border border-slate-800 bg-[#0d1322] px-4 py-3 text-sm">
                    {m.error ? (
                      <p className="flex items-center gap-1.5 text-rose-300">
                        <XCircle className="size-4 shrink-0" aria-hidden />
                        {m.error}
                      </p>
                    ) : (
                      <div className="text-slate-200">
                        <ReactMarkdown components={MD_COMPONENTS}>{m.content}</ReactMarkdown>
                      </div>
                    )}
                    <MetaChips nova={m.nova} latencyMs={m.latencyMs} />
                  </div>
                </motion.div>
              );
            })}
            {chatPending && (
              <div className="flex justify-start">
                <div className="flex items-center gap-2.5">
                  <div className="flex size-7 items-center justify-center rounded-full border border-slate-700 bg-slate-900">
                    <Sparkles className="size-3.5 text-emerald-400" aria-hidden />
                  </div>
                  <div
                    className="flex items-center gap-1.5 rounded-2xl rounded-tl-md border border-slate-800 bg-slate-900/70 px-4 py-3"
                    aria-label="Nova is thinking"
                  >
                    {[0, 1, 2].map((i) => (
                      <span
                        key={i}
                        className="size-1.5 animate-pulse rounded-full bg-emerald-400"
                        style={{ animationDelay: `${i * 180}ms` }}
                      />
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* composer */}
      <div className="shrink-0 border-t border-slate-800 bg-[#0d1322]/80 p-3">
        <div className="mx-auto max-w-4xl">
          {msgs.length === 0 && (
            <div className="mb-2 flex gap-1.5 overflow-x-auto pb-1">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s.label}
                  type="button"
                  onClick={() => setInput(s.text)}
                  className="shrink-0 rounded-full border border-slate-800 bg-black/30 px-3 py-1 text-[11px] text-slate-400 transition-colors hover:border-emerald-500/40 hover:text-emerald-300"
                >
                  {s.label}
                </button>
              ))}
            </div>
          )}
          <div className="rounded-xl border border-slate-800 bg-black/30 p-2.5">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Ask anything…  $ run a command   / slash   ! give the agent a task"
              rows={2}
              className="max-h-32 min-h-[44px] resize-none border-0 bg-transparent px-1 text-sm text-slate-100 placeholder:text-slate-600 focus-visible:ring-0"
              aria-label="Console input"
            />
            <div className="mt-1.5 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-[10px] text-slate-600">
                <span className="hidden sm:inline">
                  <kbd className="rounded border border-slate-800 px-1 font-mono">$</kbd> shell ·{' '}
                  <kbd className="rounded border border-slate-800 px-1 font-mono">/</kbd> commands ·{' '}
                  <kbd className="rounded border border-slate-800 px-1 font-mono">!</kbd> agent · Enter to send
                </span>
                {meta && (
                  <span className="hidden font-mono md:inline">
                    gateway {meta.version}
                  </span>
                )}
                {stats && (
                  <span className="hidden font-mono lg:inline">
                    {stats.active_models} models · {stats.connected_providers} providers
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                {anyAgentRunning && (
                  <Badge variant="outline" className="border-emerald-500/40 bg-emerald-500/10 text-[10px] text-emerald-300">
                    <Loader2 className="size-3 animate-spin" aria-hidden />
                    agent active
                  </Badge>
                )}
                <Button
                  size="sm"
                  onClick={() => void send(input)}
                  disabled={!input.trim() || chatPending}
                  className="h-9 bg-emerald-500/90 px-4 text-slate-950 hover:bg-emerald-400 disabled:opacity-40"
                  aria-label="Send message"
                >
                  {chatPending ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                  ) : (
                    <Send className="size-4" aria-hidden />
                  )}
                  Send
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
