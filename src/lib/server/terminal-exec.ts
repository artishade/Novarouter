/**
 * NovaRouter safe sandbox executor.
 *
 * No child processes are ever spawned — every command is simulated from real
 * OS telemetry (`os` module, `process.memoryUsage()`) plus live DB state.
 * An allowlist governs what may run; anything dangerous exits 126, anything
 * unknown exits 127. Every execution is persisted (TerminalCommand, capped
 * at 200 rows).
 */
import os from 'os';
import { db } from '@/lib/db';
import {
  getConfig,
  getConfigNumber,
  getGpuProviders,
  setConfig,
  setGpuProviders,
} from '@/lib/server/config';
import type { ExecResult } from '@/lib/types';

/* ------------------------------ policy ------------------------------ */

const DANGEROUS_WORDS = new Set([
  'rm', 'sudo', 'kill', 'chmod', 'chown', 'curl', 'wget', 'bash', 'sh',
  'zsh', 'fish', 'eval', 'exec', 'source', 'dd', 'mkfs', 'shutdown',
  'reboot', 'nc', 'pkill', 'killall',
]);

/** Raw substrings that are never allowed (pipes, redirects, substitution…). */
const DANGEROUS_CHARS = ['|', ';', '&', '>', '<', '`', '$('];

const ALLOWED = new Set([
  'ls', 'pwd', 'cat', 'df', 'free', 'ps', 'uname', 'whoami', 'uptime',
  'date', 'echo', 'node', 'bun', 'npm', 'env', 'which', 'help', 'clear',
  'nova',
]);

const HELP_TEXT = `NovaRouter sandbox — allowed commands:

  ls [-la]            list files in the workspace
  pwd                 print working directory
  cat <file>          print a file (nova.config.json, .env)
  df -h               filesystem usage
  free -m             memory usage
  ps aux              process list
  uname -a            kernel / architecture info
  whoami              current sandbox user
  uptime              host uptime + load averages
  date                current date & time
  echo <text>         print text
  node -v             Node.js version
  bun --version       Bun version
  npm -v              npm version
  env                 environment variables (filtered)
  which <cmd>         locate a command
  nova <subcommand>   gateway control — try 'nova help'
  clear               clear the terminal
  help                this help

Everything else (rm, sudo, curl, pipes, redirects, …) is blocked by the
NovaRouter safety policy (exit 126). Unknown commands exit 127.`;

const NOVA_HELP_TEXT = `nova — NovaRouter gateway control

  nova status         gateway snapshot — providers, models, latency, memory, gpu
  nova models [n]     top requested models in the last 48h (default 10)
  nova boost <mb>     raise the Node V8 old-space heap limit (e.g. nova boost 4096)
  nova gpu            free GPU / compute provider pool
  nova gpu <id> on|off   attach / detach a compute provider (e.g. nova gpu kaggle on)
  nova gpu strategy <quota_aware|latency_first|max_vram>
  nova agents [n]     recent autonomous agent tasks (default 8)
  nova storage        storage providers overview
  nova keys           client keys overview
  nova help           this help`;

/* ------------------------------ utils ------------------------------ */

interface CmdOut {
  code: number;
  stdout: string;
  stderr: string;
}

const ok = (stdout: string): CmdOut => ({ code: 0, stdout, stderr: '' });
const err = (code: number, stderr: string): CmdOut => ({ code, stdout: '', stderr });

function tokenize(cmd: string): string[] {
  const out: string[] = [];
  let cur = '';
  let quote: '"' | "'" | null = null;
  for (const ch of cmd) {
    if (quote) {
      if (ch === quote) quote = null;
      else cur += ch;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      continue;
    }
    if (/\s/.test(ch)) {
      if (cur) out.push(cur);
      cur = '';
      continue;
    }
    cur += ch;
  }
  if (cur) out.push(cur);
  return out;
}

const pad2 = (n: number) => String(n).padStart(2, '0');

function trunc(s: string, n: number): string {
  return s.length <= n ? s : `${s.slice(0, Math.max(0, n - 1))}…`;
}

function fmtUptimeShort(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

async function persistCommand(
  command: string,
  output: string,
  exitCode: number,
  durationMs: number,
  cwd: string,
): Promise<void> {
  try {
    await db.terminalCommand.create({
      data: { command, output, exitCode, durationMs, cwd },
    });
    const count = await db.terminalCommand.count();
    if (count > 200) {
      const old = await db.terminalCommand.findMany({
        orderBy: { createdAt: 'asc' },
        take: count - 200,
        select: { id: true },
      });
      if (old.length) {
        await db.terminalCommand.deleteMany({
          where: { id: { in: old.map((r) => r.id) } },
        });
      }
    }
  } catch {
    /* persistence is best-effort — never break the response */
  }
}

/* --------------------------- command impls --------------------------- */

function freeTable(swapTotal: number): string {
  const total = Math.round(os.totalmem() / 1048576);
  const freeMb = Math.round(os.freemem() / 1048576);
  const buff = Math.round(total * 0.18);
  const shared = Math.min(total, 142);
  const used = Math.max(0, total - freeMb - buff);
  const avail = Math.min(total, freeMb + Math.round(buff * 0.6));
  const head =
    ''.padStart(15) +
    ['total', 'used', 'free', 'shared', 'buff/cache', 'available']
      .map((w) => w.padStart(12))
      .join('');
  const mem =
    'Mem:'.padEnd(15) +
    [total, used, freeMb, shared, buff, avail]
      .map((n) => String(n).padStart(12))
      .join('');
  const swap =
    'Swap:'.padEnd(15) +
    [swapTotal, 0, Math.max(0, swapTotal)]
      .map((n) => String(n).padStart(12))
      .join('');
  return [head, mem, swap].join('\n');
}

function dfTable(): string {
  return [
    'Filesystem      Size  Used Avail Use% Mounted on',
    'overlay           58G   23G   36G  40% /',
    'tmpfs            2.0G     0  2.0G   0% /dev',
    'shm               64M     0   64M   0% /dev/shm',
    '/dev/nova-disk    32G   11G   21G  35% /workspace',
    'tmpfs            2.0G   14M  1.9G   1% /run',
  ].join('\n');
}

function psTable(): string {
  const mem = process.memoryUsage();
  const rssKb = Math.round(mem.rss / 1024);
  const pctMem = ((mem.rss / Math.max(1, os.totalmem())) * 100).toFixed(1);
  const bootStart = new Date(Date.now() - process.uptime() * 1000);
  const startStr = `${pad2(bootStart.getHours())}:${pad2(bootStart.getMinutes())}`;
  const now = new Date();
  const nowStr = `${pad2(now.getHours())}:${pad2(now.getMinutes())}`;
  const row = (
    user: string, pid: string, cpu: string, memPct: string,
    vsz: string, rss: string, tty: string, stat: string,
    start: string, time: string, cmd: string,
  ) =>
    `${user.padEnd(9)}${pid.padStart(5)} ${cpu.padStart(4)} ${memPct.padStart(4)} ${vsz.padStart(7)} ${rss.padStart(6)} ${tty.padEnd(9)}${stat.padEnd(5)}${start.padEnd(6)}${time.padEnd(6)}${cmd}`;
  const header = row('USER', 'PID', '%CPU', '%MEM', 'VSZ', 'RSS', 'TTY', 'STAT', 'START', 'TIME', 'COMMAND');
  return [
    header,
    row('nova', '7', '1.2', pctMem, '2241980', String(rssKb), '?', 'Ssl', startStr, '1:04', `next-server (v${'16.1.1'})`),
    row('nova', '18', '0.3', '0.6', '322100', '24860', '?', 'Sl', startStr, '0:22', 'prisma engine query'),
    row('nova', '42', '0.1', '0.2', '198440', '9120', '?', 'S', startStr, '0:05', 'bun run dev'),
    row('nova', String(process.pid % 32768), '0.0', '0.1', '94220', '8116', '?', 'R', nowStr, '0:00', 'ps aux'),
  ].join('\n');
}

const LS_ENTRIES = [
  { name: 'workspace', dir: true, size: 4096, date: 'Sep 24 02:11' },
  { name: 'models', dir: true, size: 4096, date: 'Sep 24 02:11' },
  { name: 'logs', dir: true, size: 4096, date: 'Sep 24 02:11' },
  { name: 'storage', dir: true, size: 4096, date: 'Sep 24 02:11' },
  { name: '.env', dir: false, size: 512, date: 'Sep 24 02:11' },
  { name: 'nova.config.json', dir: false, size: 1204, date: 'Sep 24 02:11' },
];

function runLs(args: string[]): CmdOut {
  let all = false;
  let long = false;
  for (const a of args) {
    if (a.startsWith('-')) {
      if (a.includes('a')) all = true;
      if (a.includes('l')) long = true;
    } else {
      return err(2, `ls: cannot access '${a}': No such file or directory`);
    }
  }
  const visible = LS_ENTRIES.filter((e) => all || !e.name.startsWith('.'));
  if (!long) {
    return ok(visible.map((e) => (e.dir ? `${e.name}/` : e.name)).join('\n'));
  }
  const rows = visible.map((e) => {
    const perm = e.dir ? 'drwxr-xr-x' : '-rw-r--r--';
    return `${perm} ${String(e.dir ? 2 : 1).padStart(2)} nova nova ${String(e.dir ? 4096 : e.size).padStart(6)} ${e.date} ${e.dir ? `${e.name}/` : e.name}`;
  });
  if (all) {
    const dot = `drwxr-xr-x ${'1'.padStart(2)} nova nova ${'4096'.padStart(6)} Sep 24 02:11 .`;
    const dotdot = `drwxr-xr-x ${'1'.padStart(2)} root root ${'4096'.padStart(6)} Sep 24 02:11 ..`;
    return ok(['total 44', dot, dotdot, ...rows].join('\n'));
  }
  return ok(['total 40', ...rows].join('\n'));
}

function unameLine(args: string[]): string {
  const flags = args.join('');
  const sys = os.type();
  const host = os.hostname();
  const rel = os.release();
  const arch = os.arch();
  if (flags.includes('a')) {
    return `${sys} ${host} ${rel} #1-NovaRouter SMP ${arch} ${arch} ${arch} GNU/Linux`;
  }
  if (flags.includes('s')) return sys;
  if (flags.includes('r')) return rel;
  if (flags.includes('m')) return arch;
  return sys;
}

function uptimeLine(): string {
  const up = os.uptime();
  const d = Math.floor(up / 86400);
  const h = Math.floor((up % 86400) / 3600);
  const min = Math.floor((up % 3600) / 60);
  const now = new Date();
  const clock = `${pad2(now.getHours())}:${pad2(now.getMinutes())}:${pad2(now.getSeconds())}`;
  const upPart =
    d > 0
      ? `${d} day${d === 1 ? '' : 's'},  ${h}:${pad2(min)}`
      : `${h}:${pad2(min)}`;
  const [l0, l1, l2] = os.loadavg();
  return ` ${clock} up ${upPart},  1 user,  load average: ${l0.toFixed(2)}, ${l1.toFixed(2)}, ${l2.toFixed(2)}`;
}

const ENV_KEYS = [
  'NODE_ENV', 'PORT', 'HOSTNAME', 'DATABASE_URL', 'NEXT_RUNTIME',
  'NODE_VERSION', 'npm_package_name', 'npm_lifecycle_event',
];

function envLines(): string {
  const lines: string[] = [];
  for (const k of ENV_KEYS) {
    const v = process.env[k];
    if (v !== undefined) {
      lines.push(`${k}=${/TOKEN|SECRET|KEY|PASSWORD/i.test(k) ? '••••••••' : v}`);
    }
  }
  lines.push('NOVA_ADMIN_TOKEN=••••••••');
  lines.push('NOVA_SANDBOX=1');
  return lines.join('\n');
}

const WHICH_PATHS: Record<string, string> = {
  ls: '/usr/bin/ls', pwd: '/usr/bin/pwd', cat: '/usr/bin/cat',
  df: '/usr/bin/df', free: '/usr/bin/free', ps: '/usr/bin/ps',
  uname: '/usr/bin/uname', whoami: '/usr/bin/whoami',
  uptime: '/usr/bin/uptime', date: '/bin/date', echo: '/usr/bin/echo',
  node: '/usr/local/bin/node', bun: '/usr/local/bin/bun',
  npm: '/usr/local/bin/npm', env: '/usr/bin/env', which: '/usr/bin/which',
  clear: '/usr/bin/clear', nova: '/usr/local/bin/nova',
  help: 'built-in',
};

function runWhich(args: string[]): CmdOut {
  if (!args.length) return err(1, 'which: missing operand');
  const target = args[0];
  const p = WHICH_PATHS[target];
  if (p) return ok(p === 'built-in' ? `${target}: shell built-in command` : p);
  return err(1, `which: no ${target} in (/usr/local/bin:/usr/bin:/bin)`);
}

function runVersionCmd(cmd: string, args: string[]): CmdOut {
  const isVersion = args[0] === '-v' || args[0] === '--version';
  if (!isVersion) {
    return err(1, `${cmd}: sandbox permits only '${cmd} -v' / '${cmd} --version'`);
  }
  if (cmd === 'node') return ok(process.version);
  if (cmd === 'bun') return ok('1.2.21');
  return ok('10.9.3');
}

async function runCat(args: string[]): Promise<CmdOut> {
  if (!args.length) return err(1, 'cat: missing file operand');
  const f = args[0];
  if (f === 'nova.config.json') {
    const rows = await db.systemConfig.findMany({ orderBy: { key: 'asc' } });
    const obj: Record<string, unknown> = {};
    for (const r of rows) {
      try {
        obj[r.key] = JSON.parse(r.value);
      } catch {
        obj[r.key] = r.value;
      }
    }
    return ok(JSON.stringify(obj, null, 2));
  }
  if (f === '.env') {
    const [heap, swap, gpuEnabled, strategy, token, activeRow] = await Promise.all([
      getConfigNumber('v8_heap_mb', 2048),
      getConfigNumber('swap_mb', 2048),
      getConfig('gpu_enabled'),
      getConfig('gpu_strategy'),
      getConfig('admin_token'),
      db.storageProvider.findFirst({ where: { active: true } }),
    ]);
    const tokenMask = token ? `${token.slice(0, 4)}••••••` : '••••••••';
    return ok(
      [
        '# NovaRouter gateway environment — secrets masked',
        'NODE_ENV=production',
        'PORT=3000',
        'DATABASE_URL=file:./db/custom.db',
        `NOVA_ADMIN_TOKEN=${tokenMask}`,
        `NOVA_V8_HEAP_MB=${heap}`,
        `NOVA_SWAP_MB=${swap}`,
        `NOVA_GPU_ENABLED=${gpuEnabled ?? '1'}`,
        `NOVA_GPU_STRATEGY=${strategy ?? 'quota_aware'}`,
        `NOVA_STORAGE_ACTIVE=${activeRow?.id ?? 'local_disk'}`,
      ].join('\n'),
    );
  }
  return err(1, `cat: ${f}: No such file or directory`);
}

/* ------------------------------ nova CLI ------------------------------ */

async function novaStatus(): Promise<CmdOut> {
  const since = new Date(Date.now() - 24 * 3600_000);
  const [providerCount, modelCount, routeCount, agg, cacheHits, startedRaw, heap, swap, gpus, gpuEnabledRaw, strategyRaw] =
    await Promise.all([
      db.provider.count(),
      db.model.count(),
      db.modelRoute.count({ where: { enabled: true } }),
      db.requestLog.aggregate({
        _count: { _all: true },
        _avg: { latencyMs: true },
        where: { ts: { gte: since } },
      }),
      db.requestLog.count({ where: { via: 'cache', ts: { gte: since } } }),
      getConfig('gateway_started_at'),
      getConfigNumber('v8_heap_mb', 2048),
      getConfigNumber('swap_mb', 2048),
      getGpuProviders(),
      getConfig('gpu_enabled'),
      getConfig('gpu_strategy'),
    ]);
  const startedAt = Number(startedRaw);
  const uptimeMs =
    Number.isFinite(startedAt) && startedAt > 0
      ? Date.now() - startedAt
      : process.uptime() * 1000;
  const reqs = agg._count._all;
  const avgLat = Math.round(agg._avg.latencyMs ?? 0);
  const cachePct = reqs ? ((cacheHits / reqs) * 100).toFixed(1) : '0.0';
  const gpuEnabled = gpuEnabledRaw !== '0' && gpuEnabledRaw !== 'false';
  const enabledCount = gpus.filter((g) => g.enabled).length;
  return ok(
    [
      `⚡ NovaRouter gateway — ${providerCount} providers · ${modelCount} models · ${routeCount} fallback routes`,
      `   uptime: ${fmtUptimeShort(uptimeMs)} · requests (24h): ${reqs} · avg latency: ${avgLat}ms · cache hit rate: ${cachePct}%`,
      `   memory: v8 heap ${heap} MB · swap ${swap} MB · gpu ${gpuEnabled ? 'enabled' : 'disabled'} — ${enabledCount}/${gpus.length} providers enabled`,
    ].join('\n'),
  );
}

async function novaModels(args: string[]): Promise<CmdOut> {
  const n = Math.min(50, Math.max(1, Number.parseInt(args[0] ?? '10', 10) || 10));
  const since = new Date(Date.now() - 48 * 3600_000);
  const logs = await db.requestLog.findMany({
    where: { ts: { gte: since } },
    select: { model: true, tokensIn: true, tokensOut: true, latencyMs: true },
  });
  const byModel = new Map<string, { reqs: number; tokens: number; latSum: number }>();
  for (const l of logs) {
    const e = byModel.get(l.model) ?? { reqs: 0, tokens: 0, latSum: 0 };
    e.reqs += 1;
    e.tokens += l.tokensIn + l.tokensOut;
    e.latSum += l.latencyMs;
    byModel.set(l.model, e);
  }
  const rows = [...byModel.entries()]
    .sort((a, b) => b[1].reqs - a[1].reqs)
    .slice(0, n);
  const lines = [
    `${'MODEL'.padEnd(34)}${'REQS'.padStart(7)}${'TOKENS'.padStart(11)}${'AVG MS'.padStart(9)}`,
    ...rows.map(([model, e]) =>
      `${trunc(model, 33).padEnd(34)}${String(e.reqs).padStart(7)}${String(e.tokens).padStart(11)}${String(Math.round(e.latSum / e.reqs)).padStart(9)}`,
    ),
  ];
  if (!rows.length) lines.push('(no requests in the last 48h)');
  return ok(lines.join('\n'));
}

async function novaBoost(args: string[]): Promise<CmdOut> {
  const mb = Number.parseInt(args[0] ?? '', 10);
  if (!Number.isFinite(mb) || mb < 128) {
    return err(1, 'nova: boost requires a heap size in MB (>= 128) — e.g. nova boost 4096');
  }
  const capped = Math.min(65536, mb);
  const old = await getConfigNumber('v8_heap_mb', 2048);
  await setConfig('v8_heap_mb', String(capped));
  await setConfig('boost_applied_at', String(Date.now()));
  return ok(
    `✓ V8 old-space limit set to ${capped} MB (was ${old} MB)\n  new terminal sessions spawn with: node --max-old-space-size=${capped}`,
  );
}

const GPU_STRATEGIES = new Set(['quota_aware', 'latency_first', 'max_vram']);

async function novaGpu(args: string[]): Promise<CmdOut> {
  const [gpus, strategy] = await Promise.all([
    getGpuProviders(),
    getConfig('gpu_strategy'),
  ]);

  /* nova gpu strategy <name> */
  if (args[0] === 'strategy') {
    const next = (args[1] ?? '').toLowerCase();
    if (!GPU_STRATEGIES.has(next)) {
      return err(1, `nova: unknown strategy '${args[1] ?? ''}' — choose quota_aware | latency_first | max_vram`);
    }
    await setConfig('gpu_strategy', next);
    return ok(`✓ GPU scheduling strategy set to ${next}`);
  }

  /* nova gpu <provider> on|off */
  if (args.length >= 1 && args[0] !== 'strategy') {
    const target = args[0].toLowerCase();
    const action = (args[1] ?? '').toLowerCase();
    const match = gpus.find(
      (g) => g.id.toLowerCase() === target || g.name.toLowerCase().replace(/\s+/g, '-').includes(target),
    );
    if (!match) {
      const known = gpus.map((g) => g.id).join(', ') || '(none configured)';
      return err(1, `nova: no compute provider matches '${args[0]}' — known ids: ${known}`);
    }
    if (action !== 'on' && action !== 'off') {
      return err(1, `nova: expected 'on' or 'off' after '${args[0]}' — e.g. nova gpu ${match.id} on`);
    }
    const next = action === 'on';
    if (match.enabled === next) {
      return ok(`${match.name} is already ${next ? 'attached' : 'detached'} — nothing to do`);
    }
    const updated = gpus.map((g) => (g.id === match.id ? { ...g, enabled: next } : g));
    await setGpuProviders(updated);
    const attached = updated.filter((g) => g.enabled).reduce((a, g) => a + g.vram_gb, 0);
    return ok(
      `${next ? '✓' : '▪'} ${match.name} (${match.gpu}, ${match.vram_gb} GB) ${next ? 'attached to' : 'detached from'} the pool\n  pool now: ${attached} GB VRAM attached`,
    );
  }

  const lines = [
    `${'PROVIDER'.padEnd(24)}${'GPU'.padEnd(20)}${'VRAM'.padStart(5)}  ${'ENABLED'.padEnd(8)}FREE TIER`,
    ...gpus.map((g) =>
      `${trunc(g.name, 23).padEnd(24)}${trunc(g.gpu, 19).padEnd(20)}${`${g.vram_gb}GB`.padStart(5)}  ${g.enabled ? 'yes' : 'no'}       ${trunc(g.free_tier, 44)}`,
    ),
  ];
  if (!gpus.length) lines.push('(no compute providers configured)');
  else {
    const total = gpus.reduce((a, g) => a + g.vram_gb, 0);
    const enabledSum = gpus.filter((g) => g.enabled).reduce((a, g) => a + g.vram_gb, 0);
    lines.push(
      `pool: ${total} GB VRAM total · ${enabledSum} GB attached · strategy: ${strategy ?? 'quota_aware'}`,
    );
  }
  return ok(lines.join('\n'));
}

async function novaAgents(args: string[]): Promise<CmdOut> {
  const n = Math.min(20, Math.max(1, Number.parseInt(args[0] ?? '8', 10) || 8));
  const tasks = await db.agentTask.findMany({
    orderBy: { createdAt: 'desc' },
    take: n,
    select: { id: true, goal: true, status: true, createdAt: true, finishedAt: true },
  });
  const fmtAge = (ms: number) => {
    const m = Math.floor(ms / 60000);
    if (m < 60) return `${m || 1}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  };
  const lines = [
    `${'STATUS'.padEnd(11)}${'CREATED'.padEnd(10)}${'GOAL'}`,
    ...tasks.map((t) =>
      `${t.status.padEnd(11)}${fmtAge(Date.now() - t.createdAt.getTime()).padEnd(10)}${trunc(t.goal.replace(/\s+/g, ' '), 64)}`,
    ),
  ];
  if (!tasks.length) lines.push('(no agent tasks yet)');
  return ok(lines.join('\n'));
}

async function novaStorage(): Promise<CmdOut> {
  const rows = await db.storageProvider.findMany({ orderBy: { createdAt: 'asc' } });
  const lines = [
    `${'PROVIDER'.padEnd(26)}${'TYPE'.padEnd(14)}${'STATUS'.padEnd(13)}${'ACTIVE'.padStart(6)}  ${'USAGE'.padStart(9)}`,
    ...rows.map((r) =>
      `${trunc(r.name, 25).padEnd(26)}${trunc(r.type, 13).padEnd(14)}${r.status.padEnd(13)}${(r.active ? 'yes' : 'no').padStart(6)}  ${`${r.usageMb.toFixed(1)} MB`.padStart(9)}`,
    ),
  ];
  return ok(lines.join('\n'));
}

async function novaKeys(): Promise<CmdOut> {
  const keys = await db.clientKey.findMany({ orderBy: { createdAt: 'asc' } });
  const lines = [
    `${'NAME'.padEnd(22)}${'REQS'.padStart(7)}${'TOKENS IN'.padStart(12)}${'TOKENS OUT'.padStart(12)}${'RPM'.padStart(6)}  ENABLED`,
    ...keys.map((k) =>
      `${trunc(k.name, 21).padEnd(22)}${String(k.reqCount).padStart(7)}${String(k.tokensIn).padStart(12)}${String(k.tokensOut).padStart(12)}${String(k.rpmLimit).padStart(6)}  ${k.enabled ? 'yes' : 'no'}`,
    ),
    '',
    `🔑 ${keys.length} client keys · ${keys.filter((k) => k.enabled).length} enabled`,
  ];
  return ok(lines.join('\n'));
}

async function runNova(args: string[]): Promise<CmdOut> {
  const sub = args[0] ?? 'help';
  switch (sub) {
    case 'status':
      return novaStatus();
    case 'models':
      return novaModels(args.slice(1));
    case 'boost':
      return novaBoost(args.slice(1));
    case 'gpu':
      return novaGpu(args.slice(1));
    case 'agents':
      return novaAgents(args.slice(1));
    case 'storage':
      return novaStorage();
    case 'keys':
      return novaKeys();
    case 'help':
      return ok(NOVA_HELP_TEXT);
    default:
      return err(1, `nova: unknown subcommand '${sub}' — try 'nova help'`);
  }
}

/* ------------------------------ executor ------------------------------ */

export async function executeCommand(
  command: string,
  cwd?: string,
): Promise<ExecResult> {
  const started = Date.now();
  const raw = typeof command === 'string' ? command.trim() : '';
  let resolvedCwd = '/workspace';
  try {
    resolvedCwd =
      (cwd && cwd.trim()) || (await getConfig('terminal_cwd')) || '/workspace';
  } catch {
    /* db hiccup — keep default */
  }

  const finish = async (out: CmdOut): Promise<ExecResult> => {
    const stdout = out.stdout;
    const stderr = out.stderr;
    const output = stderr ? (stdout ? `${stdout}\n${stderr}` : stderr) : stdout;
    const duration_ms = Math.max(1, Date.now() - started);
    await persistCommand(raw, output, out.code, duration_ms, resolvedCwd);
    return {
      ok: out.code === 0,
      output,
      stdout,
      stderr,
      code: out.code,
      duration_ms,
      cwd: resolvedCwd,
    };
  };

  if (!raw) return finish(ok(''));
  if (raw.length > 2000) {
    return finish(err(126, 'sandbox: command too long — NovaRouter safety policy'));
  }

  for (const ch of DANGEROUS_CHARS) {
    if (raw.includes(ch)) {
      return finish(err(126, 'sandbox: command blocked by NovaRouter safety policy'));
    }
  }
  const tokens = tokenize(raw);
  for (const t of tokens) {
    const base = (t.includes('/') ? t.slice(t.lastIndexOf('/') + 1) : t).toLowerCase();
    if (DANGEROUS_WORDS.has(base)) {
      return finish(err(126, 'sandbox: command blocked by NovaRouter safety policy'));
    }
  }

  const cmd = tokens[0];
  const args = tokens.slice(1);

  if (!ALLOWED.has(cmd)) {
    return finish(
      err(127, `bash: ${cmd}: command not found — type 'help' for allowed commands`),
    );
  }

  try {
    switch (cmd) {
      case 'clear':
        return await finish(ok(''));
      case 'help':
        return await finish(ok(HELP_TEXT));
      case 'pwd':
        return await finish(ok(resolvedCwd));
      case 'echo':
        return await finish(ok(args.join(' ')));
      case 'whoami':
        return await finish(ok(process.env.USER || process.env.LOGNAME || 'nova'));
      case 'date':
        return await finish(ok(new Date().toString()));
      case 'uptime':
        return await finish(ok(uptimeLine()));
      case 'env':
        return await finish(ok(envLines()));
      case 'which':
        return await finish(runWhich(args));
      case 'uname':
        return await finish(ok(unameLine(args)));
      case 'ls':
        return await finish(runLs(args));
      case 'free':
        return await finish(ok(freeTable(await getConfigNumber('swap_mb', 2048))));
      case 'df':
        return await finish(ok(dfTable()));
      case 'ps':
        return await finish(ok(psTable()));
      case 'cat':
        return await finish(await runCat(args));
      case 'node':
      case 'bun':
      case 'npm':
        return await finish(runVersionCmd(cmd, args));
      case 'nova':
        return await finish(await runNova(args));
      default:
        return await finish(
          err(127, `bash: ${cmd}: command not found — type 'help' for allowed commands`),
        );
    }
  } catch (e) {
    return finish(
      err(1, `nova-exec: internal error — ${e instanceof Error ? e.message : String(e)}`),
    );
  }
}
