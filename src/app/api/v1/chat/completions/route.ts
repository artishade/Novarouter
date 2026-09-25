/**
 * POST /api/v1/chat/completions — OpenAI-compatible NON-STREAMING gateway endpoint.
 *
 * Routing pipeline:
 *   Stage 1        — direct upstream (requested model, provider with enabled key, non-builtin)
 *   Stage 2..N     — explicit fallback chain from ModelRoute (publicId match or '*' default chain)
 *   Final stage    — NovaFree engine (z-ai-web-dev-sdk, always available)
 *
 * The response `model` field is ALWAYS the requested model id (identity spoofing);
 * `_nova` metadata exposes what actually served the request. Every request is logged.
 */
import { NextRequest, NextResponse } from 'next/server';
import { randomBytes } from 'crypto';
import type { Model, Provider } from '@prisma/client';
import { db } from '@/lib/db';
import type { NovaMeta } from '@/lib/types';

const UPSTREAM_TIMEOUT_MS = 15_000;

type IncomingMessage = { role?: unknown; content?: unknown };

interface AttemptResult {
  stage: number;
  content: string;
  upstreamModel: string;
  upstreamExposedId: string;
  providerName: string;
  via: 'upstream' | 'nova-engine';
  usage: { prompt_tokens?: number; completion_tokens?: number } | null;
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

function estimateTokens(text: string): number {
  return Math.max(1, Math.ceil(text.length / 4));
}

function normalizeMessages(raw: IncomingMessage[]): Array<{ role: 'system' | 'user' | 'assistant'; content: string }> {
  return raw.map((m) => {
    const role = m.role === 'system' || m.role === 'assistant' ? m.role : 'user';
    const content =
      typeof m.content === 'string'
        ? m.content
        : Array.isArray(m.content)
          ? m.content
              .map((part) => {
                if (part && typeof part === 'object' && 'text' in part) return String((part as { text?: unknown }).text ?? '');
                return '';
              })
              .filter(Boolean)
              .join('\n')
          : String(m.content ?? '');
    return { role, content };
  });
}

function extractUpstreamContent(payload: unknown): string | null {
  const choices = (payload as { choices?: unknown })?.choices;
  if (!Array.isArray(choices) || choices.length === 0) return null;
  const message = (choices[0] as { message?: { content?: unknown } })?.message;
  const content = message?.content;
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    const joined = content
      .map((part) => (part && typeof part === 'object' && 'text' in part ? String((part as { text?: unknown }).text ?? '') : ''))
      .filter(Boolean)
      .join('\n');
    return joined || null;
  }
  return null;
}

/** Prefer the highest-weight enabled key that is not in cooldown; fall back to any enabled key. */
async function selectUpstreamKey(providerId: number) {
  const keys = await db.providerKey.findMany({
    where: { providerId, enabled: true },
    orderBy: [{ weight: 'desc' }, { id: 'asc' }],
    select: { id: true, apiKey: true, cooldownUntil: true },
  });
  if (keys.length === 0) return null;
  const now = Date.now();
  const ready = keys.find((k) => !k.cooldownUntil || k.cooldownUntil.getTime() <= now);
  return ready ?? keys[0];
}

/**
 * Attempt a real upstream OpenAI-format call for one Model row.
 * Returns null when the row is not routable (builtin/anthropic provider, no key, upstream error).
 */
async function attemptUpstream(
  row: Model & { provider: Provider },
  messages: Array<{ role: 'system' | 'user' | 'assistant'; content: string }>,
  temperature: number | undefined
): Promise<AttemptResult | null> {
  const provider = row.provider;
  if (!provider || !provider.enabled) return null;
  if (provider.kind === 'builtin') return null;
  // Anthropic uses a different wire shape (/v1/messages + x-api-key) — not attempted in the
  // simple direct stage; such chains fall through to the next stage / NovaFree engine.
  if (provider.kind === 'anthropic') return null;

  const key = await selectUpstreamKey(provider.id);
  if (!key) return null;

  const base = provider.baseUrl.replace(/\/+$/, '');
  if (!base || base.startsWith('internal://')) return null;

  const body: Record<string, unknown> = { model: row.modelId, messages };
  if (typeof temperature === 'number' && Number.isFinite(temperature)) body.temperature = temperature;

  try {
    const res = await fetch(`${base}/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${key.apiKey}`,
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
    if (!res.ok) return null;

    const payload = (await res.json().catch(() => null)) as
      | { choices?: unknown; usage?: { prompt_tokens?: number; completion_tokens?: number } }
      | null;
    const content = extractUpstreamContent(payload);
    if (content === null) return null;

    const usage =
      payload?.usage && typeof payload.usage === 'object'
        ? {
            prompt_tokens:
              typeof payload.usage.prompt_tokens === 'number' ? payload.usage.prompt_tokens : undefined,
            completion_tokens:
              typeof payload.usage.completion_tokens === 'number' ? payload.usage.completion_tokens : undefined,
          }
        : null;

    return {
      stage: 0, // set by caller
      content,
      upstreamModel: row.modelId,
      upstreamExposedId: row.exposedId,
      providerName: provider.name,
      via: 'upstream',
      usage,
    };
  } catch {
    return null;
  }
}

/** Find a routable Model row by exposedId OR modelId (provider joined). */
async function findModel(idOrExposed: string) {
  return db.model.findFirst({
    where: { OR: [{ exposedId: idOrExposed }, { modelId: idOrExposed }] },
    include: { provider: true },
    orderBy: { id: 'asc' },
  });
}

interface LogParams {
  model: string;
  upstreamModel: string;
  providerName: string;
  status: number;
  latencyMs: number;
  tokensIn: number;
  tokensOut: number;
  via: string;
  spoofed: boolean;
  error?: string;
}

function writeLog(p: LogParams) {
  return db.requestLog
    .create({
      data: {
        model: p.model,
        upstreamModel: p.upstreamModel,
        providerName: p.providerName,
        endpoint: '/v1/chat/completions',
        status: p.status,
        latencyMs: p.latencyMs,
        tokensIn: p.tokensIn,
        tokensOut: p.tokensOut,
        via: p.via,
        spoofed: p.spoofed,
        error: p.error ?? '',
      },
    })
    .catch(() => undefined); // logging must never break the response
}

function buildResponse(opts: {
  requested: string;
  content: string;
  nova: NovaMeta;
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
}) {
  return {
    id: `chatcmpl-${randomBytes(12).toString('hex')}`,
    object: 'chat.completion',
    created: Math.floor(Date.now() / 1000),
    model: opts.requested,
    choices: [
      {
        index: 0,
        message: { role: 'assistant', content: opts.content },
        finish_reason: 'stop',
      },
    ],
    usage: opts.usage,
    _nova: opts.nova,
  };
}

/* ------------------------------------------------------------------ */
/* Handler                                                             */
/* ------------------------------------------------------------------ */

export async function POST(req: NextRequest) {
  const startedAt = Date.now();

  // Whole-handler guard: any unexpected exception → log 500 + {error}.
  try {
    const body = (await req.json().catch(() => null)) as
      | { model?: unknown; messages?: unknown; temperature?: unknown; stream?: unknown }
      | null;

    const requested = typeof body?.model === 'string' ? body.model.trim() : '';
    const rawMessages = Array.isArray(body?.messages) ? (body?.messages as IncomingMessage[]) : [];

    if (!requested) {
      await writeLog({
        model: requested, upstreamModel: '', providerName: '', status: 400,
        latencyMs: Date.now() - startedAt, tokensIn: 0, tokensOut: 0, via: '', spoofed: false,
        error: "Missing required parameter: 'model'",
      });
      return NextResponse.json({ error: "Missing required parameter: 'model'" }, { status: 400 });
    }
    if (rawMessages.length === 0) {
      await writeLog({
        model: requested, upstreamModel: '', providerName: '', status: 400,
        latencyMs: Date.now() - startedAt, tokensIn: 0, tokensOut: 0, via: '', spoofed: false,
        error: "Missing required parameter: 'messages' (non-empty array)",
      });
      return NextResponse.json({ error: "Missing required parameter: 'messages' (non-empty array)" }, { status: 400 });
    }

    const messages = normalizeMessages(rawMessages);
    const temperature = typeof body?.temperature === 'number' && Number.isFinite(body.temperature) ? body.temperature : undefined;
    const tokensInEstimate = estimateTokens(messages.map((m) => `${m.role}:${m.content}`).join('\n'));

    // ── Route resolution: explicit chain for this model, else the '*' default chain ──
    const route =
      (await db.modelRoute.findFirst({ where: { publicId: requested, enabled: true } })) ??
      (await db.modelRoute.findFirst({ where: { publicId: '*', enabled: true } }));

    let fallbacks: string[] = [];
    if (route) {
      try {
        const parsed = JSON.parse(route.fallbacks);
        if (Array.isArray(parsed)) fallbacks = parsed.filter((f): f is string => typeof f === 'string');
      } catch {
        fallbacks = [];
      }
    }
    const chain = fallbacks.filter((f) => f !== requested);

    let stage = 0;
    let result: AttemptResult | null = null;

    // ── Stage 1: direct upstream for the requested model ──
    const direct = await findModel(requested);
    if (direct) {
      stage = 1;
      result = await attemptUpstream(direct, messages, temperature);
      if (result) result.stage = 1;
    }

    // ── Stage 2..N: explicit fallback chain ──
    if (!result) {
      for (let i = 0; i < chain.length; i++) {
        stage = i + 2; // chain stages start at 2
        const row = await findModel(chain[i]);
        if (!row) continue;
        result = await attemptUpstream(row, messages, temperature);
        if (result) {
          result.stage = stage;
          break;
        }
      }
    }

    let usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
    let nova: NovaMeta;

    if (result) {
      const promptTokens = result.usage?.prompt_tokens ?? tokensInEstimate;
      const completionTokens = result.usage?.completion_tokens ?? estimateTokens(result.content);
      usage = {
        prompt_tokens: promptTokens,
        completion_tokens: completionTokens,
        total_tokens: promptTokens + completionTokens,
      };
      nova = {
        upstream_model: result.upstreamModel,
        provider: result.providerName,
        fallback: result.stage > 1,
        hedged: false,
        cached: false,
        spoofed: requested !== result.upstreamModel,
        stage: result.stage,
      };

      const latencyMs = Date.now() - startedAt;
      await writeLog({
        model: requested,
        upstreamModel: result.upstreamModel,
        providerName: result.providerName,
        status: 200,
        latencyMs,
        tokensIn: usage.prompt_tokens,
        tokensOut: usage.completion_tokens,
        via: result.via,
        spoofed: nova.spoofed,
        error: '',
      });

      return NextResponse.json(
        buildResponse({ requested, content: result.content, nova, usage })
      );
    }

    // ── Final stage: NovaFree engine (always available) ──
    stage = chain.length + 2;
    try {
      const ZAI = (await import('z-ai-web-dev-sdk')).default;
      const zai = await ZAI.create();
      const completion = (await zai.chat.completions.create({
        messages,
        thinking: { type: 'disabled' },
      })) as { choices?: Array<{ message?: { content?: unknown } }> };

      const content =
        typeof completion?.choices?.[0]?.message?.content === 'string'
          ? (completion.choices[0].message?.content as string)
          : String(completion?.choices?.[0]?.message?.content ?? '');

      const promptTokens = tokensInEstimate;
      const completionTokens = estimateTokens(content);
      usage = {
        prompt_tokens: promptTokens,
        completion_tokens: completionTokens,
        total_tokens: promptTokens + completionTokens,
      };
      nova = {
        upstream_model: 'nova-engine',
        provider: 'NovaFree Engine',
        fallback: true,
        hedged: false,
        cached: false,
        spoofed: requested !== 'nova-engine',
        stage,
      };

      await writeLog({
        model: requested,
        upstreamModel: 'nova-engine',
        providerName: 'NovaFree Engine',
        status: 200,
        latencyMs: Date.now() - startedAt,
        tokensIn: usage.prompt_tokens,
        tokensOut: usage.completion_tokens,
        via: 'nova-engine',
        spoofed: nova.spoofed,
        error: '',
      });

      return NextResponse.json(buildResponse({ requested, content, nova, usage }));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      await writeLog({
        model: requested,
        upstreamModel: 'nova-engine',
        providerName: 'NovaFree Engine',
        status: 502,
        latencyMs: Date.now() - startedAt,
        tokensIn: 0,
        tokensOut: 0,
        via: 'nova-engine',
        spoofed: false,
        error: `All routing stages failed: ${msg}`,
      });
      return NextResponse.json(
        {
          error:
            `All routing stages failed: ${msg}. ` +
            'Fix checklist: (1) Dashboard → Providers → Test your provider (key must authenticate upstream); ' +
            '(2) Dashboard → Models → Sync models to pull the live catalogue; ' +
            '(3) request a model id that exists there (e.g. "nova/air" for the built-in engine).',
        },
        { status: 502 }
      );
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    await writeLog({
      model: '', upstreamModel: '', providerName: '', status: 500,
      latencyMs: Date.now() - startedAt, tokensIn: 0, tokensOut: 0, via: '', spoofed: false,
      error: message,
    });
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
