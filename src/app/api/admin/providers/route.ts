import { db } from '@/lib/db';
import { NextRequest, NextResponse } from 'next/server';
import { syncProvider } from '@/lib/server/model-sync';
import type { Provider, ProviderPreset, ProviderSessionInfo } from '@/lib/types';

export const dynamic = 'force-dynamic';

/** Built-in provider catalogue — kept in sync with GET /api/admin/meta. */
const PRESETS: ProviderPreset[] = [
  {
    key: 'novafree', name: 'NovaFree Engine', kind: 'builtin',
    base_url: 'internal://nova-engine', prefix: 'nova/', key_hint: 'no key needed',
    free_tier: 'Built-in — every model here is free, no key required',
    docs_url: 'https://github.com/artishade/Novarouter', auth_url: null,
    requires_signin: false, color: '#10b981', priority: 1,
  },
  {
    key: 'openrouter', name: 'OpenRouter', kind: 'openai',
    base_url: 'https://openrouter.ai/api/v1', prefix: 'openrouter/', key_hint: 'sk-or-v1-…',
    free_tier: 'Dozens of :free models, 200 req/day without credits',
    docs_url: 'https://openrouter.ai/docs', auth_url: 'https://openrouter.ai/settings/keys',
    requires_signin: true, color: '#8b5cf6', priority: 10,
  },
  {
    key: 'groq', name: 'Groq Cloud', kind: 'openai',
    base_url: 'https://api.groq.com/openai/v1', prefix: 'groq/', key_hint: 'gsk_…',
    free_tier: 'Free tier: 30 req/min, 14,400 req/day',
    docs_url: 'https://console.groq.com/docs', auth_url: 'https://console.groq.com/keys',
    requires_signin: true, color: '#f97316', priority: 10,
  },
  {
    key: 'gemini', name: 'Google AI Studio', kind: 'gemini',
    base_url: 'https://generativelanguage.googleapis.com/v1beta', prefix: 'gemini/', key_hint: 'AIza…',
    free_tier: 'Gemini API free tier: 15 RPM, 1,500 req/day (Flash)',
    docs_url: 'https://ai.google.dev/docs', auth_url: 'https://aistudio.google.com/app/apikey',
    requires_signin: true, color: '#22c55e', priority: 15,
  },
  {
    key: 'cerebras', name: 'Cerebras Inference', kind: 'openai',
    base_url: 'https://api.cerebras.ai/v1', prefix: 'cerebras/', key_hint: 'csk-…',
    free_tier: 'Free tier: ~1M tokens/day at 2,000+ tok/s',
    docs_url: 'https://inference-docs.cerebras.ai', auth_url: 'https://cloud.cerebras.ai',
    requires_signin: true, color: '#f59e0b', priority: 20,
  },
  {
    key: 'github-models', name: 'GitHub Models', kind: 'openai',
    base_url: 'https://models.inference.ai.azure.com', prefix: 'github/', key_hint: 'ghp_… / github_pat_…',
    free_tier: 'Free with any GitHub account — GPT-4o, Llama, Phi, DeepSeek',
    docs_url: 'https://docs.github.com/en/github-models', auth_url: 'https://github.com/settings/tokens',
    requires_signin: true, color: '#e2e8f0', priority: 25,
  },
  {
    key: 'mistral', name: 'Mistral La Plateforme', kind: 'openai',
    base_url: 'https://api.mistral.ai/v1', prefix: 'mistral/', key_hint: '32-char token',
    free_tier: 'Free experiment plan: 1 req/s, 500k tokens/min',
    docs_url: 'https://docs.mistral.ai', auth_url: 'https://console.mistral.ai/api-keys',
    requires_signin: true, color: '#fb923c', priority: 30,
  },
  {
    key: 'nvidia', name: 'NVIDIA NIM', kind: 'openai',
    base_url: 'https://integrate.api.nvidia.com/v1', prefix: 'nvidia/', key_hint: 'nvapi-…',
    free_tier: '1,000 free credits on signup for hosted NIM endpoints',
    docs_url: 'https://docs.api.nvidia.com', auth_url: 'https://build.nvidia.com',
    requires_signin: true, color: '#76b900', priority: 35,
  },
  {
    key: 'deepseek', name: 'DeepSeek API', kind: 'openai',
    base_url: 'https://api.deepseek.com/v1', prefix: 'deepseek/', key_hint: 'sk-…',
    free_tier: 'Pay-as-you-go — new accounts receive trial credits',
    docs_url: 'https://api-docs.deepseek.com', auth_url: 'https://platform.deepseek.com/api_keys',
    requires_signin: true, color: '#64748b', priority: 40,
  },
  {
    key: 'together', name: 'Together AI', kind: 'openai',
    base_url: 'https://api.together.xyz/v1', prefix: 'together/', key_hint: '64-char token',
    free_tier: '$1 free credit on signup',
    docs_url: 'https://docs.together.ai', auth_url: 'https://api.together.ai/settings/api-keys',
    requires_signin: true, color: '#0f766e', priority: 45,
  },
  {
    key: 'xai', name: 'xAI Grok', kind: 'openai',
    base_url: 'https://api.x.ai/v1', prefix: 'xai/', key_hint: 'xai-…',
    free_tier: '$25/month free credits while data sharing is enabled',
    docs_url: 'https://docs.x.ai', auth_url: 'https://console.x.ai',
    requires_signin: true, color: '#e5e7eb', priority: 50,
  },
  {
    key: 'fireworks', name: 'Fireworks AI', kind: 'openai',
    base_url: 'https://api.fireworks.ai/inference/v1', prefix: 'fireworks/', key_hint: 'fw_…',
    free_tier: '$1 free credits + serverless free tier for small models',
    docs_url: 'https://docs.fireworks.ai', auth_url: 'https://fireworks.ai/account/api-keys',
    requires_signin: true, color: '#fb7185', priority: 55,
  },
  {
    key: 'ollama', name: 'Ollama (local)', kind: 'openai',
    base_url: 'http://localhost:11434/v1', prefix: 'ollama/', key_hint: 'not required',
    free_tier: '100% free — runs on your machine',
    docs_url: 'https://github.com/ollama/ollama', auth_url: null,
    requires_signin: false, color: '#94a3b8', priority: 80,
  },
  {
    key: 'openai', name: 'OpenAI', kind: 'openai',
    base_url: 'https://api.openai.com/v1', prefix: 'openai/', key_hint: 'sk-…',
    free_tier: null,
    docs_url: 'https://platform.openai.com/docs', auth_url: 'https://platform.openai.com/api-keys',
    requires_signin: true, color: '#0ea36e', priority: 90,
  },
  {
    key: 'anthropic', name: 'Anthropic', kind: 'anthropic',
    base_url: 'https://api.anthropic.com', prefix: 'anthropic/', key_hint: 'sk-ant-…',
    free_tier: null,
    docs_url: 'https://docs.anthropic.com', auth_url: 'https://console.anthropic.com/settings/keys',
    requires_signin: true, color: '#d97757', priority: 90,
  },
];

type SessionRow = Awaited<ReturnType<typeof db.providerSession.findMany>>[number];
type ProviderRow = Awaited<ReturnType<typeof db.provider.findMany>>[number];

function toSession(s: SessionRow): ProviderSessionInfo {
  return {
    username: s.username,
    display_name: s.displayName,
    plan: s.plan,
    status: s.status,
    token_mask: s.tokenMask,
    connected_at: s.connectedAt.getTime(),
  };
}

function toProvider(
  p: ProviderRow,
  keyCount: number,
  modelStatuses: string[],
  session: SessionRow | undefined,
): Provider {
  return {
    id: p.id,
    key: p.key,
    name: p.name,
    kind: p.kind,
    base_url: p.baseUrl,
    prefix: p.prefix,
    enabled: p.enabled,
    priority: p.priority,
    color: p.color,
    docs_url: p.docsUrl,
    auth_url: p.authUrl,
    requires_auth: p.requiresAuth,
    free_tier: p.freeTier,
    created_at: p.createdAt.getTime(),
    key_count: keyCount,
    model_count: modelStatuses.length,
    ok_count: modelStatuses.filter((s) => s === 'healthy').length,
    cooling_count: modelStatuses.filter((s) => s === 'cooling').length,
    session: session ? toSession(session) : null,
  };
}

/** GET /api/admin/providers — all providers with counts + latest session. */
export async function GET() {
  const [providers, sessions] = await Promise.all([
    db.provider.findMany({
      orderBy: [{ priority: 'asc' }, { id: 'asc' }],
      include: {
        keys: { select: { id: true } },
        models: { select: { status: true } },
      },
    }),
    db.providerSession.findMany({ orderBy: { connectedAt: 'desc' } }),
  ]);

  const latestSession = new Map<string, SessionRow>();
  for (const s of sessions) {
    if (!latestSession.has(s.providerKey)) latestSession.set(s.providerKey, s);
  }

  const out = providers.map((p) => toProvider(p, p.keys.length, p.models.map((m) => m.status), latestSession.get(p.key)));
  return NextResponse.json(out);
}

const slugify = (s: string): string =>
  s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'provider';

/** POST /api/admin/providers — create a provider (reuses preset data when the name matches). */
export async function POST(req: NextRequest) {
  let body: Record<string, unknown> = {};
  try {
    const parsed: unknown = await req.json();
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) body = parsed as Record<string, unknown>;
  } catch {
    /* empty body */
  }

  const name = typeof body.name === 'string' ? body.name.trim() : '';
  if (!name) {
    return NextResponse.json({ error: 'Provider name is required' }, { status: 400 });
  }

  const asStr = (v: unknown): string | undefined => (typeof v === 'string' && v.trim() !== '' ? v.trim() : undefined);
  const asNum = (v: unknown): number | undefined => {
    const n = typeof v === 'number' ? v : typeof v === 'string' ? Number(v) : NaN;
    return Number.isFinite(n) ? n : undefined;
  };

  // Reuse preset data when the name matches (case-insensitive) or the slug matches a preset key.
  const slug = slugify(name);
  const preset =
    PRESETS.find((p) => p.name.toLowerCase() === name.toLowerCase()) ??
    PRESETS.find((p) => p.key === slug) ??
    null;

  // Generate a unique key slug from the name (prefer the preset key when available).
  let key = preset ? preset.key : slug;
  let suffix = 2;
  while (await db.provider.findUnique({ where: { key } })) {
    key = `${slug}-${suffix++}`;
    if (suffix > 50) {
      key = `${slug}-${Date.now().toString(36)}`;
      break;
    }
  }

  const created = await db.provider.create({
    data: {
      key,
      name: preset ? preset.name : name,
      kind: asStr(body.kind) ?? preset?.kind ?? 'openai',
      baseUrl: asStr(body.base_url) ?? preset?.base_url ?? '',
      prefix: asStr(body.prefix) ?? preset?.prefix ?? `${slug}/`,
      priority: asNum(body.priority) ?? preset?.priority ?? 100,
      color: preset?.color ?? '#10b981',
      docsUrl: asStr(body.docs_url) ?? preset?.docs_url ?? null,
      authUrl: asStr(body.auth_url) ?? preset?.auth_url ?? null,
      requiresAuth: (asStr(body.auth_url) ?? preset?.auth_url) != null,
      freeTier: asStr(body.free_tier) ?? preset?.free_tier ?? null,
    },
  });

  // Optional multiline/comma-separated key list.
  const apiKeysRaw = asStr(body.api_keys) ?? '';
  const keys = apiKeysRaw
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);

  if (keys.length > 0) {
    await db.providerKey.createMany({
      data: keys.map((apiKey, i) => ({
        providerId: created.id,
        label: keys.length === 1 ? 'key' : `key-${i + 1}`,
        apiKey,
      })),
    });
  }

  // Auto-sync the new provider's live model catalogue so it is usable immediately.
  // Best-effort: a discovery failure must not fail the creation — the dashboard
  // "Sync models" button can retry, and the error is surfaced honestly.
  let modelsAdded = 0;
  let syncError: string | null = null;
  try {
    const report = await syncProvider({
      id: created.id,
      key: created.key,
      name: created.name,
      kind: created.kind,
      baseUrl: created.baseUrl,
      prefix: created.prefix,
    });
    modelsAdded = report.created;
    if (!report.ok) syncError = report.error ?? 'model discovery failed';
  } catch (err) {
    syncError = err instanceof Error ? err.message : 'model discovery failed';
  }

  return NextResponse.json({
    id: created.id,
    keys_added: keys.length,
    models_added: modelsAdded,
    ...(syncError ? { sync_error: syncError } : {}),
  });
}
