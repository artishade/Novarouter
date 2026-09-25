import { NextResponse } from 'next/server';
import type { ProviderPreset } from '@/lib/types';

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

/** GET /api/admin/providers/presets — built-in provider preset catalogue. */
export async function GET() {
  return NextResponse.json(PRESETS);
}
