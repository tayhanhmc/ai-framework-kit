# Skill authoring

Every skill is a pointer adapter to one canonical document.

## Rules

- The canonical procedure lives in `docs/agent-workflows/<name>.md` (or a guideline doc).
  The skill never restates it.
- Write the adapter twice, byte-identical, as real files (not symlinks):
  `.claude/skills/__P__-<name>/SKILL.md` and `.agents/skills/__P__-<name>/SKILL.md`.
- `name` equals the folder name: lowercase letters, digits and single hyphens, at most 64
  characters, starting with `__P__-` so it cannot collide with user-level skills.
- `description` is at most 1024 characters and contains `Use when` followed by concrete
  triggers.
- The body is at most four lines and cites one path, relative to the `SKILL.md`.
- Add the skill to `EXPECTED_SKILLS` in `ai/scripts/verify_agent_env.py`.
- A product skill is named `__P__-<product>-<purpose>` and is written only when the team's
  flow needs one the framework lacks.

## Template

```
---
name: __P__-<name>
description: <what it covers>. Use when <concrete triggers>.
---

Read `../../../docs/agent-workflows/<name>.md` relative to this `SKILL.md`
and follow it. That file is canonical; do not restate it here.
```
