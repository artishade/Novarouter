/**
 * NovaFree engine sidecar — bridges the z-ai-web-dev-sdk (Node-only) to the
 * Python gateway over localhost HTTP. Never exposed publicly.
 *
 *   POST /chat     {messages, stream?, thinking?} → OpenAI-shaped JSON or SSE stream
 *   POST /search   {query}  → {results: [{url, name, snippet, host_name, date}]}
 *   POST /read_url {url}    → {title, text, published_time, url}
 *   GET  /health            → 200 {ok: true} only when the SDK is usable; 503 otherwise
 */
import ZAI from 'z-ai-web-dev-sdk';

const PORT = Number(process.env.ENGINE_PORT || 3099);

let zaiPromise = null;
async function zai() {
  if (!zaiPromise) zaiPromise = ZAI.create();
  return zaiPromise;
}

function json(res, status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/* ------------------------------ /health ------------------------------ */

async function handleHealth() {
  try {
    await zai();
    return json(await Promise.resolve(), 200, { ok: true, engine: 'novafree' });
  } catch (err) {
    return json(await Promise.resolve(), 503, {
      ok: false,
      error: String(err?.message || err),
    });
  }
}

/* ------------------------------ /chat ------------------------------ */

async function handleChat(req) {
  let body;
  try {
    body = await req.json();
  } catch {
    return json(await Promise.resolve(), 400, { error: 'invalid JSON body' });
  }
  const messages = Array.isArray(body.messages) ? body.messages : null;
  if (!messages || messages.length === 0) {
    return json(await Promise.resolve(), 400, { error: "missing 'messages'" });
  }
  const stream = body.stream === true;
  const thinking = body.thinking === undefined ? { type: 'disabled' } : body.thinking;

  try {
    const client = await zai();
    const payload = { messages, thinking };
    if (stream) payload.stream = true;
    const result = await client.chat.completions.create(payload);

    if (!stream) {
      return json(await Promise.resolve(), 200, result);
    }

    // The SDK's stream:true returns a raw SSE byte stream (not parsed chunks).
    // Normalize: decode bytes, split lines, parse `data:` payloads, re-emit clean
    // OpenAI-shaped SSE. Already-parsed objects are passed through untouched.
    const encoder = new TextEncoder();
    const decoder = new TextDecoder();
    const sse = new ReadableStream({
      async start(controller) {
        const send = (obj) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(obj)}\n\n`));
        try {
          if (result && typeof result[Symbol.asyncIterator] === 'function') {
            let buf = '';
            for await (const chunk of result) {
              if (chunk instanceof Uint8Array) {
                buf += decoder.decode(chunk, { stream: true });
              } else if (typeof chunk === 'string') {
                buf += chunk;
              } else if (typeof chunk === 'object' && chunk !== null) {
                if (Array.isArray(chunk) || ArrayBuffer.isView(chunk)) {
                  buf += decoder.decode(new Uint8Array(chunk.buffer ?? chunk), { stream: true });
                } else {
                  send(chunk); // already-parsed completion object
                }
              } else {
                buf += String(chunk);
              }
              let idx;
              while ((idx = buf.indexOf('\n')) !== -1) {
                const line = buf.slice(0, idx).trim();
                buf = buf.slice(idx + 1);
                if (!line.startsWith('data: ')) continue;
                const payload = line.slice(6).trim();
                if (payload === '[DONE]') continue; // we append our own DONE
                try {
                  send(JSON.parse(payload));
                } catch { /* skip malformed line */ }
              }
            }
          } else {
            // SDK ignored stream — emit the full completion as one chunk.
            send(result);
          }
          controller.enqueue(encoder.encode('data: [DONE]\n\n'));
        } catch (err) {
          send({ error: String(err?.message || err) });
          controller.enqueue(encoder.encode('data: [DONE]\n\n'));
        } finally {
          controller.close();
        }
      },
    });
    return new Response(sse, {
      headers: {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
      },
    });
  } catch (err) {
    return json(await Promise.resolve(), 502, { error: String(err?.message || err) });
  }
}

/* --------------------------- /search, /read_url --------------------------- */

async function handleSearch(req) {
  let body;
  try {
    body = await req.json();
  } catch {
    return json(await Promise.resolve(), 400, { error: 'invalid JSON body' });
  }
  const query = typeof body.query === 'string' ? body.query.trim() : '';
  if (!query) return json(await Promise.resolve(), 400, { error: "missing 'query'" });
  try {
    const client = await zai();
    const results = await client.functions.invoke('web_search', { query });
    return json(await Promise.resolve(), 200, { results: Array.isArray(results) ? results : [] });
  } catch (err) {
    return json(await Promise.resolve(), 502, { error: String(err?.message || err) });
  }
}

async function handleReadUrl(req) {
  let body;
  try {
    body = await req.json();
  } catch {
    return json(await Promise.resolve(), 400, { error: 'invalid JSON body' });
  }
  const url = typeof body.url === 'string' ? body.url.trim() : '';
  if (!/^https?:\/\//.test(url)) {
    return json(await Promise.resolve(), 400, { error: "missing or invalid 'url'" });
  }
  try {
    const client = await zai();
    const page = await client.functions.invoke('page_reader', { url });
    const html = typeof page?.data?.html === 'string' ? page.data.html : '';
    const text = html
      .replace(/<script[\s\S]*?<\/script>/gi, ' ')
      .replace(/<style[\s\S]*?<\/style>/gi, ' ')
      .replace(/<[^>]+>/g, ' ')
      .replace(/&nbsp;/g, ' ')
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&#39;/g, "'")
      .replace(/&quot;/g, '"')
      .replace(/\s+/g, ' ')
      .trim();
    return json(await Promise.resolve(), 200, {
      title: page?.data?.title ?? '',
      text: text.slice(0, 12000),
      published_time: page?.data?.publishedTime ?? null,
      url: page?.data?.url ?? url,
    });
  } catch (err) {
    return json(await Promise.resolve(), 502, { error: String(err?.message || err) });
  }
}

/* --------------------------------- server --------------------------------- */

Bun.serve({
  port: PORT,
  hostname: '127.0.0.1',
  async fetch(req) {
    const url = new URL(req.url);
    try {
      if (req.method === 'GET' && url.pathname === '/health') return await handleHealth();
      if (req.method === 'POST' && url.pathname === '/chat') return await handleChat(req);
      if (req.method === 'POST' && url.pathname === '/search') return await handleSearch(req);
      if (req.method === 'POST' && url.pathname === '/read_url') return await handleReadUrl(req);
      return json(await Promise.resolve(), 404, { error: 'not found' });
    } catch (err) {
      return json(await Promise.resolve(), 500, { error: String(err?.message || err) });
    }
  },
});

console.log(`[engine] novafree sidecar listening on 127.0.0.1:${PORT}`);