# Prompt: Build a 2026 cross-assistant coding environment in this workspace

Copy everything below this line into Claude Code or Codex in the target workspace.

---

Set up this workspace's shared coding-agent environment. The result must work from a
clean clone with stock Claude Code and stock Codex, without relying on either tool's
user-level configuration. Python with `uv` is the primary profile. Add support for
other languages only when the repository already contains them or the user requests
them. Stay within this prompt's scope, preserve existing project choices, and do not
silently overwrite or replace working configuration.

Document date: 2026-08-31. Tool behaviors asserted here (CLI defaults, config keys,
size caps) may have drifted since then; when an observed behavior contradicts this
document, trust the probe and report the discrepancy. This run must be completable
in one session on a fresh machine: install missing prerequisites per Step P, batch
all discovery questions into one exchange at the start, and afterwards ask only at
the explicit ask-before points.

## Design principles (why the env is shaped this way)

1. **One shared contract.** `AGENTS.md` is the canonical, tool-neutral steering file.
   Claude imports it through `CLAUDE.md`; Codex reads it natively.
2. **Context economics over instruction volume.** Keep shared always-loaded steering
   under 100 lines. Put procedures and language guidance behind skills.
3. **State lives on disk.** Plans, decisions, and non-obvious project facts are small,
   reviewed artifacts that survive sessions without bloating every prompt.
4. **Verification loops, not ceremony.** Behavior changes get relevant runnable
   checks. No forced TDD ritual or mandatory session-start ceremony.
5. **Deterministic checks for machine-checkable rules.** Use tests, linters,
   pre-commit, hooks, and CI where they can actually enforce a rule. Approval
   boundaries and judgment calls remain concise shared instructions.
6. **The harness grows from evidence.** Add ordinary workflow guidance after a
   repeated failure. Add severe safety, data-loss, security, or correctness guidance
   immediately. Remove rules that no longer prevent a real failure.

Assumptions: Python projects use `uv`. User-level Claude or Codex settings may enhance
the environment but are never prerequisites. Replace every `<PLACEHOLDER>` below with
an inspected or user-provided value; no placeholder may remain in generated files.

## Step P — Machine prerequisites (fresh laptop)

Probe before installing; install only what is missing. This setup request authorizes
these installs, from official distributions only.

1. Record OS, architecture, and whether elevated privileges are available. Without
   them, install to user-local locations (`~/.local/bin`) and ensure PATH covers them.
2. `git`: if missing, install with the platform's package manager (macOS:
   `xcode-select --install` or Homebrew; Windows: winget; Linux: the distro package
   manager). Verify with `git --version`. Confirm `user.name` and `user.email` are
   set; if not, ask for them in the Step 0 question batch.
3. `uv`: if missing, install from the official Astral distribution — the platform
   package manager where it packages uv (brew, winget, pacman, dnf) or the official
   standalone installer from https://astral.sh/uv. Verify with `uv --version` and use
   that release throughout. Never install uv from any other source.
4. Python: do not install a system Python for the project; uv provisions
   interpreters on demand. Confirm with `uv python find 3.11` or let uv download one
   when first needed.
5. Verify network reachability to the package indexes the project needs (PyPI,
   GitHub) before Step 6. If a required index is unreachable, stop and report — do
   not substitute mirrors.
6. From this point on, maintain `ai/tasks/env_setup.md`: one checklist line per step
   with the verification command run and its observed output. Update it after every
   verified step so an interrupted run can resume from the last checkpoint. It is
   session state, not part of the delivered harness: move it to
   `ai/archive/session_handoffs/` at completion and exclude it from the verifier's
   expected-file list.

## Step 0 — Discovery and collision policy

Before writing anything:

1. Identify the project root and whether it is a Git repository. If it is not, ask
   before `git init`; pre-commit installation remains pending until Git exists. Record
   the active assistant and version; record the other assistant's version when its CLI
   is installed, but do not require both CLIs. Derive a short lowercase project skill
   prefix (`<SKILL_PREFIX>`) that keeps every skill name within 64 characters and is
   unlikely to collide with user-level skills.
2. Inventory root and nested `AGENTS.md`, `AGENTS.override.md`, `CLAUDE.md`, manifests,
   `.claude/skills/`, `.agents/skills/`, source/test layouts, build backend, Python
   floor, package manager, CI files, remotes, hooks, quality tools, dependency groups,
   and project `.codex/config.toml` layers (including `project_root_markers`,
   `project_doc_fallback_filenames`, and `project_doc_max_bytes`). Reconcile existing
   skill names and canonical content instead of creating a competing adapter. Keep a
   pre-edit path-and-content-hash manifest for every file that may be changed, and
   re-read each file immediately before writing so concurrent user or agent edits are
   not overwritten.
3. For an empty workspace, ask for project name and shape: distributable package,
   application, or non-packaged scripts/notebooks. Also ask for the supported Python
   range or application runtime. Ask these together with every other open discovery
   question (CI provider, git identity, toolchain conflicts) as one batch, so the
   run does not stall repeatedly mid-execution.
4. Preserve existing build backends, CI structure, commands, and conventions.
   Merge additive settings. Before replacing or materially changing any of them,
   explain the conflict and get approval.
5. If a root `AGENTS.override.md` exists, stop and ask to reconcile it: Codex will
   choose it instead of root `AGENTS.md`. Merge durable shared guidance into
   `AGENTS.md` and remove or rename the root override only with approval; leaving any
   root override in place is not a valid completed state.
6. Review nested Codex instruction files as scoped additions and flag contradictions.
   For each selected nested `AGENTS.md` or `AGENTS.override.md`, create or merge a
   nested `CLAUDE.md` that imports that selected file so Claude receives the same
   scoped rules. Preserve any reviewed Claude-only nested appendix. Know the
   asymmetry: Codex reads nested files only on its launch path (repo root down to the
   directory it was started in) — a nested AGENTS.md deeper than the launch directory
   is not picked up when Codex edits files there — while Claude Code loads nested
   CLAUDE.md files on demand when working in a subtree. A subtree rule that must
   always hold therefore also needs a line in the root `AGENTS.md`. Codex builds this
   chain once per run and caps it at `project_doc_max_bytes` (32 KiB by default);
   keep the selected chain below the configured cap. Reconcile project fallback
   instruction filenames into the same Claude adapter policy.
7. Never copy template guidelines over existing project guidance without reviewing
   and reconciling both. Never create a second canonical source for the same rule.
8. Record the discovered values and intended files before editing. This setup request
   authorizes the Python development dependencies listed in Step 6 when they do not
   conflict with an existing equivalent tool.
9. Never put credentials, tokens, private logs, personal data, or raw transcripts in
   generated instructions, project knowledge, plans, handoffs, reports, or archives.

---

## Step 1 — Directory skeleton

Create files only when a step below gives them content. Do not create empty
directories: Git cannot preserve them. The final logical layout is:

```
.agents/
  skills/
    <SKILL_PREFIX>-work-management/
    <SKILL_PREFIX>-plan-review/
    <SKILL_PREFIX>-python-coding/
    <SKILL_PREFIX>-python-testing/
    <SKILL_PREFIX>-skill-authoring/
.claude/
  skills/
    <SKILL_PREFIX>-work-management/
    <SKILL_PREFIX>-plan-review/
    <SKILL_PREFIX>-python-coding/
    <SKILL_PREFIX>-python-testing/
    <SKILL_PREFIX>-skill-authoring/
ai/
  README.md
  project.md
  scripts/
    verify_agent_env.py
docs/
  agent-workflows/
    work-management.md
    plan-review.md
  dev/
    architecture.md
    decisions.md
  guidelines/
    python/
      coding.md
      testing.md
    skill_authoring.md
  reference/
```

Create `ai/analysis/`, `ai/archive/`, `ai/plans/`, `ai/specs/`, `ai/tasks/`,
`ai/reports/`, `docs/reference/`, and language-specific guideline directories only
when the first real file for that category is written.

Merge these entries into `.gitignore` without duplicating existing patterns:

```
.venv/
__pycache__/
*.py[co]
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
coverage.xml
htmlcov/
build/
dist/
.claude/settings.local.json
.claude/scheduled_tasks.lock
.claude/scheduled_tasks.json
.claude/worktrees/
```

## Step 2 — Shared root instructions

Write root `AGENTS.md` from this template, adapting commands to the discovered
project. Keep it under 100 lines. Do not put Claude-only or Codex-only tool names in
its behavioral rules — a rule must be followable by both assistants. Naming the two
invocation forms side by side for the same skill (`/name`; `$name`) is fine and is
not a violation of this rule.

```markdown
# <PROJECT_NAME>

<ONE_SENTENCE_PROJECT_DESCRIPTION>

## Commands
- Run repository commands from the project root unless a command says otherwise.
- Setup: `uv sync --locked` (after the committed lock exists)
- Install Git hooks: `uv run --locked pre-commit install` (Git repositories only)
- Test: `uv run --locked poe test` · fast targeted: `uv run --locked poe test-quick [path]`
- Lint + types: `uv run --locked poe lint`
- Format: `uv run --locked poe format`
- Harness check: `uv run --isolated --no-config --no-project --python 3.11 -- python ai/scripts/verify_agent_env.py`
- Pre-commit runs the harness, lock, Ruff, mypy, and deptry checks. Product tests run in CI.

## Verify your work
Before editing, inspect relevant tests, call sites, and nearby patterns. Clarify
ambiguity that would change public behavior, APIs, data, or dependencies. Prefer the
smallest implementation that meets explicit acceptance criteria; do not add
speculative abstractions. Before calling work done, run relevant tests and checks,
inspect the final diff for scope drift and placeholders, and report the evidence.
Add a behavior or regression test when the change is automatable; otherwise explain
the strongest check you performed.

## Codebase map
`docs/dev/architecture.md` — per-module responsibilities, dataflows, entry points.
Read the relevant section before modifying production source. Keep it current when
module responsibilities change; do not require it for initial discovery.

## Persistent state (read on demand, never preloaded)
- `ai/tasks/` — active task lists. Before starting multi-session work, check for an
  active list and invoke the work-management skill
  (Claude: `/<SKILL_PREFIX>-work-management`;
  Codex: `$<SKILL_PREFIX>-work-management`).
- `ai/project.md` — non-obvious project facts (tooling quirks, gotchas, conventions
  not visible in code). Project-scoped facts you discover belong there, not in user
  memory.
- `docs/dev/decisions.md` — binding decision record. Consult it when a change touches
  a recorded decision; update it through the work-management conclusion process.

## Conventions
Coding standards live in `docs/guidelines/` and load through skills
(Claude `/<SKILL_PREFIX>-python-coding`;
Codex `$<SKILL_PREFIX>-python-coding`, and equivalent testing skill).
Follow them; they are not restated here.

## Etiquette
- Ask before: destructive operations, pushing, adding dependencies, or changing an
  approved approach mid-plan. Otherwise proceed.
- Match scope strictly: a bug fix brings no drive-by refactors or cleanup.
- Never expose or persist secrets, and inspect staged/untracked files for sensitive
  data before committing.
```

If root `CLAUDE.md` does not exist, write it exactly as:

```markdown
@AGENTS.md
```

If it already exists, place `@AGENTS.md` as its first nonblank line, migrate shared
guidance into `AGENTS.md`, and preserve a reviewed Claude-only appendix. A preserved
appendix must begin with the header `## Claude Code` — the verification checklist's
script enforces exactly that header. Ask before removing conflicting or material existing guidance.
Do not duplicate shared instructions below the import. Record the shared instruction
admission policy as the first entry in `docs/dev/decisions.md` in Step 4.

## Step 3 — `ai/` scaffolding

**`ai/README.md`** (verbatim):

```markdown
# ai/ — agent working directory

Markdown artifacts that carry work state across sessions. Context lives here, not in
the conversation.

| Dir | Contents |
|---|---|
| `analysis/` | Findings and investigations feeding a plan. Archived at feature close. |
| `specs/` | Approved requirement statements (external input to plans). |
| `plans/` | Approved implementation plans. Archived at feature close. |
| `tasks/` | Active task lists + session handoffs (`handoff_YYYYMMDD_HHMM.md`). |
| `reports/` | Stakeholder-facing deliverables. Not archived. |
| `archive/` | Closed features: `<feature>/{analysis,plans,specs,tasks}/`; superseded plan versions as `<feature>/v#/`; old handoffs in `session_handoffs/`. |
| `scripts/` | Scripts supporting agent workflows (not product code). |

Directories are created on first use; an absent directory means it has no artifacts yet.
`ai/project.md` is the project knowledge file — see its header for admission rules.
Formats and lifecycle: the work-management skill.
```

**`ai/project.md`** seed (verbatim):

```markdown
# Project knowledge

Non-obvious, project-scoped facts an agent cannot derive from the code: tooling
quirks, environment gotchas, implicit conventions, load-bearing external context.
Never store credentials, personal data, raw private logs, or transcript excerpts.

Admission rules (binding):
- Only original content — anything documented elsewhere gets a one-line pointer, not a copy.
- Every entry costs tokens whenever this file is read: skip one-off facts unlikely to recur.
- Prune entries that are no longer true — stale entries mislead as much as missing ones.
- Hard budget: 150 lines. At the cap, something must be pruned before anything is added.

## Environment

- Build and run: <PROJECT_BUILD_AND_RUN_SUMMARY>
- Required external tools or modules: <REQUIRED_EXTERNAL_TOOLS_OR_NONE>
- Environment facts worth retaining: <DURABLE_ENVIRONMENT_FACTS_OR_NONE>

## Gotchas

None recorded.
```

## Step 4 — `docs/`

**`docs/dev/decisions.md`** seed (verbatim):

```markdown
# Decision record

Current-state record of binding decisions, not a log. When a decision changes, edit
its entry and add a line to its `### Changes` list.

A candidate entry must pass all four tests:
1. **Silent-violation** — could someone unknowingly violate it?
2. **No-other-home** — is it not already enforced at its point of use (type, test, lint, hook)?
3. **With-or-without-AI** — does it matter to a human contributor too?
4. **Non-locality** — does it constrain work beyond the feature that produced it?

Format per entry: `## <title>` then **Decision** / **Rationale** / **Context** / `### Changes`.

## Shared agent-instruction admission policy
**Decision:** `AGENTS.md` is the canonical shared instruction file and stays under
100 lines. Add ordinary workflow guidance only after a repeated failure; add severe
safety, security, data-loss, or correctness guidance immediately. Remove guidance
that no longer prevents an observed failure. `CLAUDE.md` remains an import-only
adapter.
**Rationale:** Concise, non-duplicated instructions are easier for both humans and
agents to follow; severity-aware admission does not wait for a dangerous failure to
repeat.
**Context:** Adopted at environment creation for Claude Code and Codex parity.
### Changes
- <TODAYS_DATE>: created.
```

**`docs/dev/architecture.md`**: discover every production source root and write the
module map now when code exists (per-module responsibilities, package connections,
dataflows, entry points, and cross-language boundaries — target under 300 lines). If
the project is empty, create the file with a header and note that the map is written
when the first module lands. Do not assume a single `src/` tree.

**`docs/guidelines/python/coding.md`** and
**`docs/guidelines/python/testing.md`**: derive real conventions from existing code
and configuration. Reconcile any supplied template with those conventions; never
copy it blindly. For a new project, use Ruff formatting at line length 100, typed
public boundaries, strict mypy, and Google-style docstrings only where intent is not
obvious. Tests use pytest, assert observable behavior, cover boundary and failure
paths, avoid mocking the unit under test, and never use meaningless `assert True`
smoke tests. Use `tests/unit/` and `tests/integration/` only when both categories
exist. Prefer cohesive files; split only when responsibilities, navigation, or test
isolation materially improve. These are canonical; skills only point to them.

**`docs/guidelines/skill_authoring.md`** — one canonical copy. Document the shared
`SKILL.md` format with YAML frontmatter (`name`, `description`); the description says
what the skill does and when to use it, with concrete triggers. Keep combined routing
metadata within the portable Agent Skills limits: names are 1–64 lowercase
alphanumeric/hyphen characters with no leading, trailing, or consecutive hyphen, and
descriptions are 1–1,024 characters. Keep the body under 500 lines. Add a table of
contents to reference files over 100 lines. Resolve file references relative to the
skill root, keep references shallow and purposeful, but do not invent a platform
restriction on nesting. Prefer a checked-in runnable script over prose telling an
agent to recreate one. Explain that `.claude/skills/` and `.agents/skills/` are
discovery adapters; canonical workflow content lives in `docs/agent-workflows/` or
`docs/guidelines/`.

## Step 5 — Canonical workflows and dual skill adapters

Workflow and guideline content has one canonical home. Discovery metadata is the only
content duplicated between assistants.

For every project skill, create both:

- `.claude/skills/<SKILL_PREFIX>-<name>/SKILL.md`
- `.agents/skills/<SKILL_PREFIX>-<name>/SKILL.md`

Each adapter contains the same `name` and routing `description`, followed only by a
skill-root-relative pointer to the canonical document. Project-prefix every skill to
avoid user-level collisions, and verify the final name satisfies the 64-character
portable limit. Do not use symlinks; checked-out behavior must be portable to Windows.
Example for both work-management adapters:

```markdown
---
name: <SKILL_PREFIX>-work-management
description: Manage multi-session work through plans, task lists, handoffs, project knowledge, and decisions. Use when work will span sessions, when creating or updating a plan or task list, or when concluding and archiving a feature.
---

Read `../../../docs/agent-workflows/work-management.md` relative to this `SKILL.md`
and follow it. That file is canonical; do not restate it here.
```

### `docs/agent-workflows/work-management.md`

```markdown
# Work management

Work spanning sessions proceeds through reviewed artifacts: analysis informs a plan,
the plan produces a task list, and execution works the task list. Once reviewed by
the user, an artifact is trusted context. Single-session work needs none of this.

## Document types

- **Analysis** (`ai/analysis/<topic>.md`) — what was examined, found, and implied.
- **Spec** (`ai/specs/<name>.md`) — approved requirements external to the plan.
- **Plan** (`ai/plans/<feature>.md`) — choices and rejected alternatives, order,
  machine-checkable success criteria, validation, risks, and explicit scope exclusions.
  Get user approval before execution.
- **Tasks** (`ai/tasks/<feature>.md`) — ordered checklist from an approved plan.
  Statuses: `[ ]` open, `[x]` done, `[-]` dropped with reason. `Reminder` notes are
  ephemeral; `Note` lines record execution that diverged from the plan.

Create an artifact's directory when writing its first file.

## Execution

- Work tasks in order unless the list says otherwise. Record verification evidence
  beside each completed task.
- A different algorithm, new dependency, or workaround that changes the approved
  approach requires planned-vs-actual reporting and user approval. Small helpers and
  typo fixes are not divergence.
- Queue non-obvious project discoveries and surface them at conclusion.

## Conclusion

1. Diff the result against the plan and record material divergence.
2. Add binding decisions to `docs/dev/decisions.md` only if they pass its four tests.
3. Propose durable `ai/project.md` additions within its 150-line budget.
4. Move this feature's artifacts into `ai/archive/<feature>/`, preserving category.
5. Report what shipped, verification evidence, surprises, and the next action.

## Handoff

When stopping mid-feature, write `ai/tasks/handoff_YYYYMMDD_HHMM.md` with current
task state, in-flight uncommitted context, exact next action, and open questions.
Handoffs are session state, not operational truth. At feature close move them to
`ai/archive/session_handoffs/`. On resume read the newest relevant handoff first.

## Retention

Before persisting anything, weigh reproduction cost against recurrence. Skip content
that is cheap to rediscover or unlikely to recur. Fewer, truer artifacts beat complete
ones.
```

### `docs/agent-workflows/plan-review.md`

```markdown
# Plan review

Use one fresh, read-only-capable or exploration-oriented subagent as an independence
gate. Do not assume a built-in subagent type name. If read-only enforcement is
unavailable, give an explicit no-write brief and verify afterward that the working
tree is unchanged.

Give the reviewer the artifact paths and execution context (where work runs and what
it costs), but not the parent's assessment. Baseline repository instructions may
still load; “fresh” means no task-specific conclusions are leaked.

Review:
1. Unverified resource assumptions: paths, versions, licenses, permissions, quotas.
2. Version or compatibility mismatches.
3. Concrete values that look copied rather than checked.
4. Steps depending on outputs no earlier step produces.
5. Partial-failure and recovery behavior.
6. Success criteria a machine cannot check.
7. Simpler alternatives the plan did not evaluate.

Return at most 80 lines ranked BLOCKER / RISK / NOTE. The parent independently
verifies every claimed blocker before presenting or acting on it.
```

Create `<SKILL_PREFIX>-plan-review` adapters in both skill roots with this routing
description:
"Adversarial fresh-context review of plans and task lists. Use when work will commit
hours of compute or effort, run migrations or destructive batches, or when the user
asks for a plan review." Point both to
`../../../docs/agent-workflows/plan-review.md` relative to each `SKILL.md`.

### Guideline pointer skills

Create `<SKILL_PREFIX>-python-coding`, `<SKILL_PREFIX>-python-testing`, and
`<SKILL_PREFIX>-skill-authoring` adapters in both skill roots. Each body only reads
its canonical file using a skill-root-relative `../../../` path:

- `python-coding` → `docs/guidelines/python/coding.md`
- `python-testing` → `docs/guidelines/python/testing.md`
- `skill-authoring` → `docs/guidelines/skill_authoring.md`

Descriptions must say what the skill covers and when to use it. The skill-authoring
trigger covers both `.claude/skills/` and `.agents/skills/`; use the literal routing
phrase `Use when` so the verifier can enforce it mechanically.

### Optional operations-runbook skill

Only for expensive external flows, create `ai/operations_runbook.md` with real
sections for exact commands, version pins, preflight checks, recovery, and known
traps. Create `<SKILL_PREFIX>-flow-run` adapters in both skill roots pointing to it.
Tell agents to list headings with their available search tool and read only the
relevant section; do not embed a POSIX-only command. New traps are added once to the
runbook, preferably with a deterministic guard.

## Step 6 — Python tooling

### Project shape and dependencies

- When applying the Python/uv profile, require `uv` from its official distribution
  before changing Python tooling. Read `uv --version`, use that tested release
  throughout local config and CI, and set
  `[tool.uv] required-version = "==<UV_VERSION>"`. If uv is missing, install it per
  Step P (official distribution only) and record the installed version in
  `ai/tasks/env_setup.md`. This prerequisite does not apply when the user approves
  preserving a non-uv toolchain and skipping the uv profile.
- Preserve an existing PEP 621 project, build backend, package layout, and Python
  support range. Do not replace Poetry/PDM/Hatch configuration without approval; if
  the project is being standardized on uv, use uv for locking and execution while
  retaining a compatible backend.
- If an existing repository uses an incompatible package manager or equivalent
  formatter/type-checker/task runner, present an explicit migration plan. If migration
  is declined, keep the existing tools, adapt `AGENTS.md` to their commands, create
  only the shared assistant/workflow layer, and skip conflicting uv/Poe/tool sections.
  Never stack duplicate quality tools merely to satisfy this profile.
- Inventory every dependency group and the project's default groups. Put newly added
  tools in the existing equivalent group when one exists; otherwise use `dev`. Adapt
  sync, run, and CI commands to include every required non-default group and test each
  supported combination declared by `tool.uv.conflicts`.
- For a new distributable pure-Python package, use `src/<PACKAGE_NAME>/`, Hatchling,
  a real `requires-python`, and `.python-version` matching the selected floor.
- For a new application or non-packaged workspace, do not add a build backend merely
  to satisfy a template. Configure uv with `package = false` where appropriate.
- Add the development tools with:
  `uv add --dev poethepoet ruff mypy deptry pre-commit pytest pytest-cov`.
  Let uv resolve current compatible versions and commit `uv.lock`.
- For a new package, add a meaningful import/public-boundary scaffold test. For a new
  application, test the scaffold's public entry point. Never add `assert True`. In an
  existing project with no tests, report test verification as incomplete instead of
  converting pytest exit code 5 into success.

For a new distributable package, include:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

For a new non-packaged application or scripts workspace, set `package = false` in the
same `[tool.uv]` table as `required-version`. Never add either project model over an
existing one without reconciling it first.

### `pyproject.toml`

Merge, do not replace, equivalent existing sections. Fill all uppercase placeholders
from discovery; lists contain every relevant package/root in a workspace or monorepo.

```toml
[tool.uv]
required-version = "==<UV_VERSION>"

[tool.ruff]
line-length = 100
extend-exclude = ["docs"]

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F", "I", "UP", "B", "SIM", "S", "ARG", "RUF"]

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S101", "ARG001"]

[tool.mypy]
strict = true
files = [<MYPY_PATHS_AS_QUOTED_TOML_VALUES>]

[tool.pytest.ini_options]
addopts = ["--import-mode=importlib", "--strict-config", "--strict-markers"]
testpaths = ["tests"]
markers = ["slow: long-running test excluded from quick local loops"]

[tool.coverage.run]
branch = true
source = [<COVERAGE_PACKAGES_AS_QUOTED_TOML_VALUES>]

[tool.coverage.report]
show_missing = true
fail_under = 80

[tool.poe.executor]
type = "uv"
locked = true

[tool.poe.tasks]
ruff-check = "ruff check ."
ruff-format-check = "ruff format --check ."
format = "ruff format ."
mypy = "mypy"
deptry = "deptry <PRODUCTION_SOURCE_PATHS>"
test-quick = 'pytest -x -m "not slow"'
test = "pytest --cov --cov-report=term-missing --cov-report=xml"

[tool.poe.tasks.lint]
sequence = ["ruff-check", "ruff-format-check", "mypy", "deptry"]
```

Adapt all source, import, coverage, and test paths to the actual layout, including
workspace members and multiple packages. Prefer standard `project.requires-python`,
which Ruff reads automatically; set Ruff `target-version` only when no usable
`requires-python` exists. Add `mypy_path` and `explicit_package_bases` only when the
discovered namespace or multi-root layout requires them. Add deptry
`known_first_party` only when its automatic first-party detection fails; because
deptry exclusions are unanchored regular expressions, add an exclusion only when its
anchored pattern has been tested against every production path. For existing code, do
not weaken an existing quality gate. If strict mypy or 80% coverage cannot pass
immediately, measure the baseline, propose a ratchet that blocks regression, and get
approval before recording exceptions. Add `test-unit` only when `tests/unit/` exists.
BDD naming is adopted only if already used.

### `.pre-commit-config.yaml`

Merge with existing hooks. Use `fail_fast: false` so one run returns all actionable
failures. Define local hooks with these exact semantics, substituting discovered
source paths:

```yaml
minimum_pre_commit_version: "4.4.0"
fail_fast: false
repos:
  - repo: local
    hooks:
      - id: agent-env
        name: shared agent environment
        entry: uv run --isolated --no-config --no-project --python 3.11 -- python ai/scripts/verify_agent_env.py
        language: unsupported
        pass_filenames: false
        always_run: true
      - id: uv-lock
        name: uv lock is current
        entry: uv lock --check
        language: unsupported
        pass_filenames: false
        always_run: true
      - id: ruff-check
        name: ruff check
        entry: uv run --locked ruff check
        language: unsupported
        types: [python]
      - id: ruff-format-check
        name: ruff format check
        entry: uv run --locked ruff format --check
        language: unsupported
        types: [python]
      - id: mypy
        name: mypy
        entry: uv run --locked mypy
        language: unsupported
        pass_filenames: false
        always_run: true
      - id: deptry
        name: deptry
        entry: uv run --locked deptry <PRODUCTION_SOURCE_PATHS>
        language: unsupported
        pass_filenames: false
        always_run: true
```

Do not scan tests with deptry; test-only imports are development dependencies. Do not
run pytest in pre-commit; tests run locally on demand and in CI. The harness hook
prevents later instruction/skill drift. Install the Git hook only after confirming
`.git/` exists.

### CI

Preserve and extend the existing provider. Detect existing CI files first; remotes are
only hints because mirrors and multiple remotes can disagree:

- Existing GitHub Actions → `.github/workflows/ci.yml`
- Existing GitLab CI → `.gitlab-ci.yml`
- Existing other provider → integrate its native configuration
- No CI file or ambiguous signals → ask for GitHub, GitLab, another provider, or
  local-only; do not guess

CI must declare least-privilege permissions (normally `contents: read`) and sensible
concurrency cancellation. Install the selected Python and exact `<UV_VERSION>`; on
GitHub prefer SHA-pinned `astral-sh/setup-uv` with
`version-file: pyproject.toml`. If the project has no mutually exclusive extras or
groups, sync all required compatible extras and dependency groups. If
`tool.uv.conflicts` or equivalent constraints exist, sync the default development
environment and test each supported valid extra/group combination as separate matrix
entries; never force an incompatible `--all-extras` or `--all-groups`. Run the
standalone harness verifier, then `uv run --locked poe lint` and
`uv run --locked poe test`. Build packaged projects with `uv build --no-sources` and
smoke-test the wheel and source distribution in isolated environments.
Libraries test every supported Python minor; applications test their pinned runtime.
Pin third-party GitHub actions to full commit SHAs with a version comment; pin
container images by immutable version or digest. Cache by `uv.lock`, retain coverage
XML, and add no publishing job unless requested.

## Step 7 — Optional assistant hooks (mechanical only)

Offer this after core setup and create it only on acceptance:

**Best-effort format-after-edit.** Create one checked-in
`ai/scripts/format_changed_python.py` that:

- reads each platform's JSON hook input from stdin;
- recognizes Claude `Edit`/`Write` payloads through absolute
  `tool_input.file_path`; for Codex, recognizes canonical
  `tool_name: "apply_patch"` and extracts every path from `*** Update File:` and
  `*** Add File:` headers in `tool_input.command` (`Edit`/`Write` are matcher aliases
  only and never appear in the payload);
- resolves candidate paths, rejects paths outside the repository, and runs
  `uv run --locked ruff format` with argument arrays only on existing `.py` files;
- uses argument arrays without shell interpolation and exits safely when no Python
  file can be identified. Add fixture tests for multi-file patches, spaces, Windows
  separators, traversal attempts, malformed JSON, deleted files, and non-Python files.

Wire that same script from Claude's `.claude/settings.json` and Codex's
`.codex/hooks.json`. Resolve the script from the repository root rather than session
CWD and provide Windows command configuration when the platform schema requires it.
Current Codex enables hooks by default; if the installed version or existing config
disables them, use canonical `[features].hooks = true`. Merge existing settings and
include each platform's published schema where supported. Explain both platforms'
workspace trust behavior. Codex additionally requires review of the exact hook
definition; have the user inspect it through `/hooks`, trust it, and verify it is
enabled. Shell-created rewrites may not emit file paths, so `poe lint` and pre-commit
remain authoritative.

Do not rely on uncommitted user-level hooks. Do not add Stop-hook review gates,
prompt loggers, or behavior-shaping hooks.

## Step 8 — Additional languages on demand

Python/uv is the default profile, not a ban on other languages. If discovery finds
another language or the user requests one:

1. Preserve its existing package manager, formatter, linter, test runner, and build
   system.
2. Add its real setup/test/lint/build commands to `AGENTS.md` while staying under the
   shared line budget.
3. Create `docs/guidelines/<language>/` only with reviewed project-specific content.
4. Create matching `<SKILL_PREFIX>-<language>-...` pointer skill adapters under both
   `.claude/skills/` and `.agents/skills/`.
5. Add its checks to pre-commit and CI only when they are fast, deterministic, and
   already accepted for the project.
6. Update `docs/dev/architecture.md` with cross-language boundaries.

Do not pre-create generic Bash, TypeScript, Rust, notebook, Tcl, or other profiles.
Grow them from actual repository needs.

## Do NOT build (stripped from the old template — do not reintroduce)

- **No mandatory session bringup.** No acknowledgment ritual or instruction to read a
  fixed list before every response. Skills load when relevant.
- **No second shared prompt.** `AGENTS.md` is canonical; `CLAUDE.md` imports it.
- **No reliance on user-level configuration.** The clean-clone project must retain its
  documented quality and safety behavior in local, cloud, and teammate environments.
- **No approval-before-every-action persona.** Default is autonomy within scope and an
  approved plan; use the short ask-first list in `AGENTS.md`.
- **No forced TDD.** Require behavior evidence and regression tests where applicable,
  not a ritual ordering for every edit.
- **No subagent orchestration framework.** Use subagents for genuinely independent,
  read-heavy work or fresh-context plan review, not mandatory role pipelines.
- **No assumptions about built-in tool names.** Built-in planning/task tools vary by
  assistant; persistent markdown is only for state that must survive sessions.
- **No duplicate workflow bodies.** Duplicate skill metadata adapters are allowed;
  all substantive guidance has one canonical document.
- **No speculative language profiles, empty directories, placeholder READMEs,
  commented-out config, legacy `.cursorrules`, or unverified shims.**
- **No platform-specific workflow command when a small stdlib Python script can make
  the check portable.**
- **No persona preambles or emphasis walls.** Use direct, testable instructions.

## Verification checklist (run before reporting done)

Create `ai/scripts/verify_agent_env.py` using only the Python 3.11+ standard library,
including `tomllib`. Run it in a uv-provided, project-independent verifier runtime so
the product's Python floor does not matter. Do not parse YAML in this script;
`pre-commit validate-config` is the YAML authority. The script must run on Linux,
macOS, and Windows and fail non-zero when:

1. `AGENTS.md` is 100 lines or more; root `AGENTS.override.md` remains;
   `CLAUDE.md` does not have `@AGENTS.md` as its first nonblank line; its optional
   appendix does not begin with `## Claude Code`; or a generated environment file
   contains an unresolved uppercase `<PLACEHOLDER>`. Review appendix duplication
   manually because semantic equivalence is not a reliable script check.
2. The skill-name sets under `.claude/skills/` and `.agents/skills/` differ; a
   `SKILL.md` lacks the constrained frontmatter used here (`name` and one-line
   `description` parsed line-by-line), a portable 1–64 character skill name, a
   1–1,024 character description containing an explicit `Use when` routing clause, a
   matching directory name, exactly one canonical pointer, or has a body of 500 lines
   or more; or the short adapter body exceeds four nonblank lines; or the Claude and
   Codex adapters differ. The verifier validates this narrow schema; it does not claim
   to parse arbitrary YAML.
3. A referenced canonical workflow/guideline path does not exist, a canonical file is
   duplicated, a skill adapter is a symlink, or a selected nested Codex instruction
   file lacks a matching nested `CLAUDE.md` import adapter. Reviewed vendored,
   generated, example, or test-fixture instruction files may be listed in the
   verifier's explicit ignore set; no other nested instruction is skipped.
4. When the Python/uv profile was applied, the pinned uv requirement or required Poe
   task names are missing, the Poe uv executor is not locked, or `lint` omits a
   required task or references an undefined task.
5. A required generated/merged file is missing, an expected skill is missing or
   unexpected, or a managed harness directory is empty. Ignore pre-existing
   non-harness directories and tool-managed caches.

Populate every uppercase placeholder, especially `EXPECTED_MANAGED_FILES`,
`EXPECTED_SKILLS`, the uv-profile constants, and the reviewed nested-instruction
ignore list. Write the resulting script exactly as follows — do not re-derive it from
the list above. Adjust `REQUIRED_POE_TASKS` and `REQUIRED_LINT_TASKS` together only
under the approved existing-toolchain branch:

```python
#!/usr/bin/env python3
"""Verify the shared Claude Code + Codex harness. Stdlib only, Python 3.11+.

Deterministic checks for the environment contract (see docs/dev/decisions.md,
"Shared agent-instruction admission policy"). YAML is out of scope here;
`pre-commit validate-config` is the YAML authority.
Exit codes: 0 pass, 1 contract violations, 2 verifier could not run.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOTS = (ROOT / ".claude" / "skills", ROOT / ".agents" / "skills")
CANONICAL_HOMES = (ROOT / "docs" / "agent-workflows", ROOT / "docs" / "guidelines")
MANAGED_DIRS = (*SKILL_ROOTS, ROOT / "docs" / "agent-workflows")
CORE_FILES = (
    "AGENTS.md", "CLAUDE.md", ".gitignore", "ai/README.md", "ai/project.md",
    "docs/agent-workflows/work-management.md",
    "docs/agent-workflows/plan-review.md",
    "docs/dev/architecture.md", "docs/dev/decisions.md",
    "docs/guidelines/skill_authoring.md",
)
EXPECTED_MANAGED_FILES = [<EXPECTED_MANAGED_FILES_AS_QUOTED_STRINGS>]
EXPECTED_SKILLS = {<EXPECTED_SKILL_NAMES_AS_QUOTED_STRINGS>}
UV_PROFILE_APPLIED = <UV_PROFILE_APPLIED_AS_TRUE_OR_FALSE>
PINNED_UV_VERSION = "<PINNED_UV_VERSION_OR_EMPTY>"
IGNORED_NESTED_CODEX_FILES = {
    Path(p) for p in [<IGNORED_NESTED_CODEX_FILES_AS_QUOTED_STRINGS_OR_EMPTY>]
}
MANAGED_TREES = (
    (".agents", {".md", ".yaml", ".yml"}),
    (".claude", {".md", ".json"}),
    (".codex", {".md", ".json", ".toml"}),
    (".github/workflows", {".yaml", ".yml"}),
    ("ai", {".md", ".py"}),
    ("docs/agent-workflows", {".md"}),
    ("docs/dev", {".md"}),
    ("docs/guidelines", {".md"}),
)
SKIP_DIRS = {
    ".agents", ".claude", ".codex", ".git", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".venv", "__pycache__", "node_modules",
}
PLACEHOLDER = re.compile(r"<[A-Z][A-Z0-9_]*>")
POINTER = re.compile(r"(?:docs|ai)/[\w./-]+?\.md")
SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
REQUIRED_POE_TASKS = {"ruff-check", "ruff-format-check", "format", "mypy", "deptry",
                      "test-quick", "test", "lint"}
REQUIRED_LINT_TASKS = {"ruff-check", "ruff-format-check", "mypy", "deptry"}

FAILS: list[str] = []


def fail(msg: str) -> None:
    FAILS.append(msg)


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def nonblank(s: str) -> list[str]:
    return [ln for ln in s.splitlines() if ln.strip()]


def check_root_instructions() -> None:
    agents = ROOT / "AGENTS.md"
    if not agents.is_file():
        fail("AGENTS.md missing at repo root")
    elif (n := len(text(agents).splitlines())) >= 100:
        fail(f"AGENTS.md has {n} lines (must stay under 100)")
    if (ROOT / "AGENTS.override.md").exists():
        fail("root AGENTS.override.md remains; merge it into AGENTS.md")
    claude = ROOT / "CLAUDE.md"
    if not claude.is_file():
        fail("CLAUDE.md missing at repo root")
        return
    lines = nonblank(text(claude))
    if not lines or lines[0].strip() != "@AGENTS.md":
        fail("CLAUDE.md first nonblank line must be @AGENTS.md")
    elif len(lines) > 1 and lines[1].strip() != "## Claude Code":
        fail("CLAUDE.md appendix must begin with '## Claude Code'")


def check_placeholders() -> None:
    paths = {ROOT / rel for rel in (*CORE_FILES, *EXPECTED_MANAGED_FILES)}
    for rel, suffixes in MANAGED_TREES:
        home = ROOT / rel
        if not home.is_dir():
            continue
        paths.update(
            p for p in home.rglob("*")
            if p.is_file() and not p.is_symlink() and p.suffix in suffixes
        )
    for p in sorted(paths):
        if p.is_file() and (m := PLACEHOLDER.search(text(p))):
            fail(f"{p.relative_to(ROOT)}: unresolved placeholder {m.group(0)}")


def check_expected_files() -> None:
    for rel_text in (*CORE_FILES, *EXPECTED_MANAGED_FILES):
        rel = Path(rel_text)
        if rel.is_absolute() or ".." in rel.parts:
            fail(f"invalid expected managed path {rel_text!r}")
        elif not (ROOT / rel).is_file():
            fail(f"required managed file missing: {rel}")


def parse_skill(path: Path) -> tuple[dict[str, str], list[str]]:
    lines = text(path).splitlines()
    meta: dict[str, str] = {}
    rel = path.relative_to(ROOT)
    if not lines or lines[0].strip() != "---":
        fail(f"{rel}: frontmatter must start with ---")
        return meta, lines
    closing = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if closing is None:
        fail(f"{rel}: frontmatter has no closing ---")
        return meta, lines
    for line_no, raw in enumerate(lines[1:closing], start=2):
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*):\s*(\S.*)", raw)
        if not match:
            fail(f"{rel}:{line_no}: frontmatter must be one-line key: value")
            continue
        key, value = match.groups()
        if key in meta:
            fail(f"{rel}:{line_no}: duplicate frontmatter key {key}")
        meta[key] = value.strip()
    if set(meta) != {"name", "description"}:
        fail(f"{rel}: frontmatter keys must be exactly name and description")
    return meta, lines[closing + 1:]


def check_skills() -> None:
    names: dict[Path, set[str]] = {}
    for root in SKILL_ROOTS:
        found: set[str] = set()
        names[root] = found
        if not root.is_dir():
            fail(f"{root.relative_to(ROOT)} is missing")
            continue
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            found.add(d.name)
            sk = d / "SKILL.md"
            rel = sk.relative_to(ROOT)
            if d.is_symlink() or sk.is_symlink():
                fail(f"{rel}: skill adapters must not be symlinks")
            if not sk.is_file():
                fail(f"{rel} missing")
                continue
            meta, body = parse_skill(sk)
            if "name" not in meta or "description" not in meta:
                fail(f"{rel}: frontmatter must define one-line name and description")
            name = meta.get("name", "")
            description = meta.get("description", "")
            if name != d.name:
                fail(f"{rel}: frontmatter name must equal directory name {d.name!r}")
            if len(name) > 64 or not SKILL_NAME.fullmatch(name):
                fail(f"{rel}: name must be 1-64 lowercase letters, digits, or hyphens")
            if not 1 <= len(description) <= 1024:
                fail(f"{rel}: description must be 1-1024 characters")
            if "Use when" not in description:
                fail(f"{rel}: description lacks a 'Use when' routing clause")
            if len(body) >= 500:
                fail(f"{rel}: body has {len(body)} lines (must be under 500)")
            targets = POINTER.findall("\n".join(body))
            if len(targets) != 1:
                fail(f"{rel}: body must point to exactly one canonical document")
            if len(nonblank("\n".join(body))) > 4:
                fail(f"{rel}: adapter body must remain a short canonical pointer")
            for t in targets:
                if not (ROOT / t).is_file():
                    fail(f"{rel}: pointer target {t} does not exist")
    a, b = (names[r] for r in SKILL_ROOTS)
    if a != b:
        fail(f"skill sets differ: only-.claude={sorted(a - b)} only-.agents={sorted(b - a)}")
    for root, found in names.items():
        if found != EXPECTED_SKILLS:
            fail(f"{root.relative_to(ROOT)}: expected skills {sorted(EXPECTED_SKILLS)}, "
                 f"got {sorted(found)}")
    for skill_name in sorted(a & b):
        claude = SKILL_ROOTS[0] / skill_name / "SKILL.md"
        codex = SKILL_ROOTS[1] / skill_name / "SKILL.md"
        if claude.is_file() and codex.is_file() and text(claude) != text(codex):
            fail(f"{skill_name}: Claude and Codex skill adapters differ")


def check_canonical_duplicates() -> None:
    seen: dict[str, Path] = {}
    for home in CANONICAL_HOMES:
        if not home.is_dir():
            continue
        for p in sorted(home.rglob("*.md")):
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            if digest in seen:
                fail(f"duplicate canonical content: {p.relative_to(ROOT)} == "
                     f"{seen[digest].relative_to(ROOT)}")
            else:
                seen[digest] = p


def iter_nested_codex_files(d: Path) -> Iterator[Path]:
    def onerror(error: OSError) -> None:
        fail(f"cannot scan nested instructions: {error}")

    for current, dir_names, file_names in os.walk(d, topdown=True, onerror=onerror,
                                                   followlinks=False):
        current_path = Path(current)
        dir_names[:] = sorted(
            name for name in dir_names
            if name not in SKIP_DIRS and not (current_path / name).is_symlink()
        )
        for name in sorted(file_names):
            if name in ("AGENTS.md", "AGENTS.override.md") and current_path != ROOT:
                yield current_path / name


def check_nested_codex() -> None:
    for rel in sorted(IGNORED_NESTED_CODEX_FILES):
        if rel.is_absolute() or ".." in rel.parts:
            fail(f"invalid ignored nested instruction path {rel}")
        elif not (ROOT / rel).is_file():
            fail(f"ignored nested instruction no longer exists: {rel}")
    for f in iter_nested_codex_files(ROOT):
        d = f.parent
        selected = d / "AGENTS.override.md"
        if not selected.exists():
            selected = d / "AGENTS.md"
        if f != selected:
            continue
        if selected.relative_to(ROOT) in IGNORED_NESTED_CODEX_FILES:
            continue
        cl = d / "CLAUDE.md"
        ok = (cl.is_file() and (nb := nonblank(text(cl)))
              and nb[0].strip() == f"@{selected.name}")
        if not ok:
            fail(f"{selected.relative_to(ROOT)}: no sibling CLAUDE.md importing "
                 f"@{selected.name}")


def check_uv_profile() -> None:
    if not UV_PROFILE_APPLIED:
        if PINNED_UV_VERSION:
            fail("verifier constants disagree: uv profile is false but uv version is set")
        return
    if not PINNED_UV_VERSION:
        fail("verifier constant PINNED_UV_VERSION is empty for active uv profile")
        return
    pj = ROOT / "pyproject.toml"
    if not pj.is_file():
        fail("pyproject.toml missing for active uv profile")
        return
    try:
        tool = tomllib.loads(text(pj)).get("tool", {})
    except tomllib.TOMLDecodeError as e:
        fail(f"pyproject.toml does not parse: {e}")
        return
    if not isinstance(tool, dict):
        fail("pyproject.toml: tool must be a table")
        return
    uv = tool.get("uv", {})
    if not isinstance(uv, dict):
        fail("pyproject.toml: [tool.uv] must be a table")
        return
    required = uv.get("required-version", "")
    if required != f"=={PINNED_UV_VERSION}":
        fail("pyproject.toml: [tool.uv] required-version must equal "
             f"'=={PINNED_UV_VERSION}'")
    poe = tool.get("poe", {})
    if not isinstance(poe, dict):
        fail("pyproject.toml: [tool.poe] must be a table")
        return
    executor = poe.get("executor", {})
    if not isinstance(executor, dict) or executor.get("type") != "uv":
        fail("pyproject.toml: Poe executor must be type 'uv'")
    elif executor.get("locked") is not True:
        fail("pyproject.toml: Poe uv executor must set locked = true")
    tasks = poe.get("tasks", {})
    if not isinstance(tasks, dict):
        fail("pyproject.toml: [tool.poe.tasks] must be a table")
        return
    if missing := sorted(REQUIRED_POE_TASKS - set(tasks)):
        fail(f"pyproject.toml: missing poe tasks {missing}")
    lint = tasks.get("lint")
    seq = lint.get("sequence", []) if isinstance(lint, dict) else []
    if not isinstance(seq, list) or not all(isinstance(s, str) for s in seq):
        fail("pyproject.toml: lint sequence must be a list of task names")
        return
    if undefined := sorted(s for s in seq if s not in tasks):
        fail(f"pyproject.toml: lint sequence references undefined tasks {undefined}")
    if missing_lint := sorted(REQUIRED_LINT_TASKS - set(seq)):
        fail(f"pyproject.toml: lint sequence omits required tasks {missing_lint}")


def check_managed_dirs() -> None:
    for d in MANAGED_DIRS:
        if not d.is_dir() or not any(d.iterdir()):
            fail(f"managed harness directory {d.relative_to(ROOT)} is missing or empty")


def main() -> int:
    if not (ROOT / "AGENTS.md").exists() and not (ROOT / ".claude").exists():
        print(f"verifier: {ROOT} does not look like the harness root", file=sys.stderr)
        return 2
    try:
        for check in (check_root_instructions, check_placeholders, check_expected_files,
                      check_skills, check_canonical_duplicates, check_nested_codex,
                      check_uv_profile, check_managed_dirs):
            check()
    except OSError as e:
        print(f"verifier could not read the harness: {e}", file=sys.stderr)
        return 2
    if FAILS:
        print(f"FAIL ({len(FAILS)}):")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("verify_agent_env: harness contract checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Then run, in order:

1. Confirm `uv --version` satisfies the exact project requirement.
2. `uv lock --check`
3. Sync with `uv sync --locked --all-extras --all-groups` only when all extras and
   groups are compatible; otherwise sync and test every declared supported
   extra/group combination.
4. `uv run --isolated --no-config --no-project --python 3.11 -- python ai/scripts/verify_agent_env.py`
5. `uv run --locked pre-commit validate-config`
6. `uv run --locked poe lint`
7. `uv run --locked poe test-quick`
8. `uv run --locked poe test`, plus each supported extra/group combination
9. `uv run --locked pre-commit run --all-files` when Git exists
10. `uv build --no-sources` plus isolated wheel and source-distribution smoke tests
    when the project is packaged
11. The CI provider's config validator when available
12. In a temporary fixture or copy, seed one representative contract violation and
    confirm the verifier returns exit 1 with the expected finding; do not mutate the
    working tree for this negative test.
13. Verify runtime discovery in the active assistant: Claude Code `/context` shows
    the root `CLAUDE.md` and `/skills` shows the expected project skills; Codex
    `/skills` shows the expected project skills and `/hooks` shows accepted project
    hooks. When the other CLI is installed, repeat its checks; otherwise report that
    cross-client runtime verification was unavailable.
14. When Git exists, `git diff --check` followed by review of the full diff, untracked
    files, and pre-edit content hashes. Without Git, compare the pre-edit
    path-and-content-hash manifest and review every created or modified file.

If the Python/uv profile was skipped under the approved existing-toolchain branch,
replace steps 1–3 and 5–10 with that toolchain's lock, config, lint, test, and build
commands. The cross-assistant verifier in step 4 still runs through standalone uv;
if uv was intentionally not installed, run it with any available Python 3.11+ and
record that prerequisite explicitly.

Perform a dry second pass through Steps P–8 and confirm it proposes no additional
changes. Do not call setup complete if a required check fails, the lock changes during
verification, placeholders remain, or a second pass is not idempotent. For an existing
testless repository, the harness may complete only with the explicit status “harness
complete; product-test verification unavailable”; never fabricate a passing test or
hide pytest exit code 5.

## Binding constraints (recap)

1. No unresolved uppercase `<PLACEHOLDER>` may remain in any generated file.
2. Never silently overwrite or replace working configuration; replacements need
   explicit approval.
3. No credentials, tokens, personal data, or private logs in any generated file.
4. Never report a step done without recording its verification command and output in
   `ai/tasks/env_setup.md`; never fabricate or soften a failing check.
5. Setup is complete only when the full verification checklist passes and the dry
   second pass proposes no changes.
6. Where observed tool behavior contradicts this document, trust the probe and
   report the discrepancy.

Finally report: discovered project shape, files created/merged, preserved existing
choices, dependency and lock changes, commands and outcomes, any approved exception,
and recommendations for the optional operations-runbook and dual formatter hook.
