# NovaRouter — Coding Conventions & Invariants (Python era)

Rules that keep changes consistent. Break these only with a documented reason
(then record it in `decisions.md`).

## Language & stack

1. **Python everywhere.** No Node build step, no React, no new JS frameworks.
   `static/app.js` is the only hand-written frontend JS. The engine sidecar
   (`engine/index.js`) must stay zero-npm-dependency bun/node code.
2. Docstrings at the top of every module explaining what it owns (existing style).
3. Type hints with `from __future__ import annotations`; `dict[str, Any]` style
   generics; `str | None` unions.

## Database (SQLAlchemy 2)

4. `nova/models.py` is the single source of schema truth. Never hand-edit the
   DB file; never write destructive migrations. `nova/bootstrap.py` creates
   missing tables additively at boot.
5. Request-scoped DB access uses the `get_db()` dependency. Long-lived work
   (agent loop, background tasks) opens its own `SessionLocal()` per operation —
   never hold one session across a whole task run.
6. SystemConfig is the KV table — simple config knobs go through `nova/kv.py`
   helpers instead of new columns.
7. Prisma-style `DATABASE_URL` values are normalized in `nova/config.py`
   (`file:…`, `postgres://`, pgbouncer params). Keep that tolerant.

## API conventions

8. **JSON wire format is snake_case**; DB columns/rows are the ORM's business.
   Keep parity with `agent-ctx/api-contract.md` (historical Next.js contract,
   still the shape reference).
9. Gateway endpoints exist under **both** `/v1/*` and `/api/v1/*` (mount the same
   router twice in `main.py`).
10. Admin APIs under `/api/admin/*` return `{ok: true}` style responses for
    mutations and use `routers/_common.py` helpers (`json_body`, `not_found`,
    `invalid_id`, `mask_key`, `epoch_ms`).
11. Errors are JSON `{error: "message"}` with correct 4xx/5xx status codes.
    A 4xx/5xx from the gateway must also write a RequestLog row.
12. Timeouts for upstream calls are env-tunable (`NOVA_CONNECT_TIMEOUT`,
    `NOVA_UPSTREAM_READ_TIMEOUT`, `NOVA_NONSTREAM_READ_TIMEOUT`) and share pooled
    `httpx.AsyncClient` instances — never build a fresh client per request.
13. Never expose real API keys; use `mask_key()` (first 8 + … + last 4).

## Gateway invariants (do not break)

14. Response `model` field ALWAYS equals the requested id (identity spoofing);
    the truth goes in `_nova` metadata (`upstream_model`, `provider`, `stage`,
    `fallback`, `spoofed`).
15. Fallback order: Stage 1 direct upstream → Stage 2..N explicit route chain →
    final NovaFree engine. The engine degrades honestly (502, never fabricated).
16. `nova/*` tier aliases (`nova/pro`, `nova/air`, `nova/mini`) resolve via
    `nova/freemodels.py` onto the free-models catalogue.
17. Every gateway request — success or failure — is logged (`RequestLog` via
    `gwlog.record_request`).

## UI conventions (Jinja2 + HTMX)

18. Tabs are server-rendered fragments swapped into `#tab-content` by HTMX; a
    tab module owns `GET /partials/tab/<key>` + optional `POST /ui/<key>/...`
    fragment-returning actions. Fragments, not JSON, drive UI updates.
19. Theme: bg `#080c14`, panels `#0d1322/80` + slate-800 borders, **emerald**
    accent, amber warnings, rose errors, purple reserved for RAM/heap/spoof
    accents, sky only for tiny info chips, mono for ids/commands. **No
    indigo/blue**, no emojis in the UI.
20. aria-labels on icon-only buttons; status dots for healthy/cooling/dead/
    unknown; skeletons or graceful empty states when APIs fail (tabs must not
    blank out).
21. New config surfaced to the UI goes through `/api/admin/meta` (MetaConfig).

## Terminal duality (easy to confuse)

22. **Command mode** (one-shot exec, history, `$ cmd` in console) =
    `terminal/sandbox.py` — simulated allowlist executor, NO child processes
    (only exception: `--version` probes of node/bun/npm binaries on PATH).
    Dangerous tokens exit 126, unknown exit 127, history capped at 200 rows.
23. **Session tabs** (interactive bash, ssh, vim) = `terminal/pty.py` —
    real PTYs, max 8 concurrent, 30-min idle reap, `close_all()` on shutdown.
24. When touching terminal behavior, first decide which of the two it belongs
    to. Sandbox policy changes go in `terminal/sandbox.py`; session lifecycle in
    `terminal/pty.py` + `terminal/link.py` (the dashboard glue in
    `routers/admin_terminal.py` just mounts those routes and adds DB history).
25. **Everything terminal lives under `terminal/`**, imported from there as
    `terminal.*`. `terminal/` may use `nova`'s pure config/model helpers, never
    the reverse — that one-way dependency is what lets the terminal be hosted on
    its own (`python3 -m terminal.service`, `terminal/README.md`).

## Testing & verification

25. Full offline check: `sh scripts/test.sh` (byte-compile → unittest → node).
    Quick syntax lint: `python3 -m compileall -q main.py nova routers ui`.
    Targeted run: `python3 -m unittest tests.test_<name> -v`.
26. Provider protocol tests use httpx MockTransport; the gateway needs no keys.
    Keep tests offline — no network in CI.
27. Server binds `0.0.0.0:$PORT` (never hardcode); the engine sidecar never
    shares the app's port (`NOVA_ENGINE_PORT`, default 3099).

## Process discipline

28. `worklog.md` + `agent-ctx/api-contract.md` are historical Next.js documents —
    read-only reference, do not "fix" them to match Python. Current memory lives
    in `AGENTS.md` + `agent-ctx/*.md` (keep those fresh instead).
29. Bootstrap never seeds mock/demo data; legacy demo rows are cleaned on boot.
30. Additive-only schema changes; new deploys must never wipe user data
    (that's why Postgres `DATABASE_URL` is recommended and SQLite is flagged
    as ephemeral in `/health`).
