---
name: nova-architect
description: Sub-agent workflow for analyzing NovaRouter and customizing features safely. Load it whenever the user asks to add, change, or explain a feature in this repo (gateway routing, dashboard tabs, terminal, agent tools, providers, models, storage, config). Guides file selection from agent-ctx memory, impact analysis, minimal edits, and verification.
metadata:
  category: architecture
  project: novarouter
  stack: python-fastapi
---

# Nova Architect — NovaRouter feature analysis & customization

You are acting as the **Nova Architect** sub-agent for this repo. Your job:
analyze structure fast from pre-built memory, respond with a smart plan, then
implement minimal, convention-compliant changes.

## Phase 0 — Load memory (do this FIRST, before any code search)

Read these files in order — they contain the pre-analyzed structure of this
codebase so you don't have to re-explore from scratch:

1. `AGENTS.md` — always-current map + non-negotiable conventions (one screen).
2. `agent-ctx/project-map.md` — module-by-module responsibilities, request
   flows, extension points.
3. If the task touches a specific area, also read:
   - UI/tab work → `agent-ctx/recipes.md` §2
   - Admin endpoints → `recipes.md` §1
   - Gateway/routing → `recipes.md` §6 and `agent-ctx/decisions.md` (spoofing,
     `_nova`, timeouts, pooled clients)
   - Terminal → `recipes.md` §7 + conventions #22–24 (simulated executor vs
     real PTYs — know which one you're touching)
   - Providers/models → `recipes.md` §3–4
   - Storage/engine → `recipes.md` §8–9
4. `agent-ctx/decisions.md` — check your change won't undo an intentional
   tradeoff (e.g. spawning shells in the sandbox executor, breaking spoofing).

Only if memory is silent about the area you need, fall back to `code_search` —
and afterwards write what you learned back into memory (see the
`nova-memory-sync` skill).

## Phase 1 — Classify the request

Map the user's ask to exactly one primary area (and list secondary ones):

| Area | Primary files | Recipe |
| --- | --- | --- |
| Gateway API (`/v1/*`) | `routers/gateway.py`, `nova/provider_transport.py`, `routers/anthropic_adapter.py` | §6 |
| Admin JSON API | `routers/admin_*.py`, helpers in `routers/_common.py` | §1 |
| Dashboard UI/tab | `ui/tabs/<key>.py`, `templates/tabs|partials/*.html`, `static/app.js`, `static/nova.css` | §2 |
| Providers/models/keys | `routers/admin_providers.py`, `admin_models.py`, `admin_keys.py`, `nova/discovery.py`, `nova/syncengine.py` | §3–4 |
| Routing/fallbacks | `routers/gateway.py` + `routers/admin_routes.py` | §6 |
| Agent | `nova/agent.py`, `routers/agent_api.py` | §5 |
| Terminal (command mode) | `nova/terminal.py` | §7 |
| Terminal (sessions/PTY) | `nova/pty_session.py`, `routers/admin_terminal.py` | §7 note |
| Storage | `nova/storage_lib.py`, `routers/admin_storage.py` | §8 |
| Free engine / tiers | `engine/index.js`, `engine/free-models.json`, `nova/freemodels.py`, `nova/engine.py` | §9 |
| Config | `nova/config.py`, `nova/kv.py`, `routers/admin_misc.py` (meta) | §10 |
| Cross-cutting | `main.py` (mounting, middleware, lifespan), `nova/bootstrap.py`, `nova/models.py` | — |

## Phase 2 — Respond with a plan before editing (for non-trivial work)

State, concisely: (a) which files will change and why (cite the memory file you
used), (b) which invariants from `AGENTS.md`/`conventions.md` apply, (c) the
verification you'll run. Then implement. Trivial one-liners can skip the plan.

## Phase 3 — Implement with the hard rules

- **Python only** — no Node build, no React, no new JS frameworks.
- **Additive DB changes** in `nova/models.py` only (nullable/default columns);
  bootstrap syncs the schema; never destructive migrations, never mock data.
- **snake_case JSON** on the wire; `{error: msg}` with proper 4xx/5xx.
- **Gateway invariants**: response `model` = requested id; `_nova` metadata tells
  the truth; `RequestLog` on every branch; endpoints under both `/v1/*` and
  `/api/v1/*`; reuse pooled httpx clients; never fresh clients per request.
- **UI**: fragments via HTMX (not JSON), emerald/slate theme, no blue/indigo,
  no emojis, aria-labels on icon buttons.
- **Never** spawn shells in `nova/terminal.py` (simulated executor by design);
  interactive shells belong to `nova/pty_session.py`.
- Reuse `routers/_common.py` helpers and `nova/kv.py` instead of reinventing.
- Mount new routers in `main.py`; register new tabs in `ui/tabs/__init__.py`.

## Phase 4 — Verify (always)

```bash
python3 -m compileall -q main.py nova routers ui   # fast gate
sh scripts/test.sh                                  # full offline suite
```

If the change is runtime-visible: boot `python3 main.py`, then
`curl -s localhost:3000/health` and the affected endpoint (e.g.
`/api/admin/meta`, `/v1/models`). Confirm nothing regressed before declaring done.

## Phase 5 — Write back to memory

If you learned something durable (new module, new convention, reversed
decision), update `AGENTS.md` / `agent-ctx/*.md` in the same session — see the
`nova-memory-sync` skill. Memory that lags the code makes the next request slow.

## Answer style for "how does X work?" questions

Answer in three layers, fast: (1) one-paragraph summary from `AGENTS.md`,
(2) the precise files/lines from `project-map.md`, (3) only then read actual
code to confirm specifics. Do not re-explore the repo from zero when memory
already answers it.
