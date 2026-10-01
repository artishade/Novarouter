---
name: nova-memory-sync
description: Keep NovaRouter agent memory accurate. Load after landing structural changes (new module, tab, router, convention, or reversed decision) to update AGENTS.md and agent-ctx files, or to repair memory that drifted from the code.
metadata:
  category: documentation
  project: novarouter
---

# Nova memory-sync — keep agent memory matching the code

Agent memory for this repo lives in exactly five places:

| File | Always loaded? | Content |
| --- | --- | --- |
| `AGENTS.md` | Yes (root) | Repo map, architecture paragraph, non-negotiable conventions, quick pointers |
| `agent-ctx/project-map.md` | No | Module-by-module responsibilities, flows, extension points |
| `agent-ctx/conventions.md` | No | Numbered coding invariants (stack, DB, API, UI, terminal, testing) |
| `agent-ctx/recipes.md` | No | Step-by-step customization recipes with file lists |
| `agent-ctx/decisions.md` | No | Why things are the way they are (protect intentional tradeoffs) |

`worklog.md` and `agent-ctx/api-contract.md` are **historical** (Next.js era) —
never update them to describe current code.

## When to run this

After landing any of: a new module/file family, a new dashboard tab or router
mount, a changed API shape, a new env/config knob, a new convention, or a
decision that reverses something in `decisions.md`.

## How to update (small diffs, not rewrites)

1. **New files/module** → add a row in the relevant `project-map.md` table; add
   a one-line pointer in `AGENTS.md` "Where to add things" if it's an extension
   point.
2. **New rule the next agent must follow** → append (don't renumber) to
   `conventions.md`; if truly top-priority, one line in `AGENTS.md`.
3. **New repeatable change pattern** → add a numbered recipe in `recipes.md`
   (files to touch, in order, plus verification).
4. **Reversed/intentional tradeoff** → dated entry at the top of the relevant
   section in `decisions.md`; adjust the code description in `project-map.md`
   if behaviour changed.
5. **Nothing else** — keep the files scannable. Memory is only useful while it
   fits in a fast read.

## Repair mode (memory drifted)

If `project-map.md` contradicts the code: trust the code, fix the memory. Spot
symptoms: file listed but missing, module described with a different
responsibility, recipe steps referencing moved functions. Re-check claims with
`code_search`/`read_files` before writing them back.

## Sanity checklist after editing memory

- [ ] Every file path mentioned exists (glob to confirm).
- [ ] Frontmatter files intact (`AGENTS.md` starts with `# AGENTS.md`; skills
      keep valid YAML frontmatter).
- [ ] No duplicate/conflicting statements between `AGENTS.md` and
      `conventions.md` (AGENTS.md stays the summary layer).
