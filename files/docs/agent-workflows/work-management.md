# Work management

For work spanning sessions; single-session work needs none of this. Every commit passes
the Codex review in `docs/agent-workflows/cross-review.md` before it lands.

## Artifacts

Default to one task file in `ai/tasks/`: objective, acceptance criteria, target checkout,
important decisions, progress, evidence and next action. Add a separate analysis, spec
or plan only when complexity or collaboration makes it useful; the folders are optional
destinations, not a required sequence. A plan's contents and depth follow
`docs/agent-workflows/plan-review.md`. The user approves the plan before execution.

## Task lists

- Items are `[ ]` open, `[x]` done, `[-]` dropped (with a reason).
- Put evidence beside each done item: the command and its key output, or `file:line`.
- The header has a `Target:` line naming the checkout and branch. The session hook prints
  it, so it survives compaction.

## Divergence

<!-- changed 2026-10-14: any departure needed approval, so routine fixes stalled on estimates -->

Within an approved plan, mechanical repairs and implementation adjustments are already
authorized: a different helper, an extra test, a fix the gates or reviewers found, more
or fewer lines than estimated. Record them in the task file and continue. Stop for the
user's approval, with a planned-vs-actual report, before changing a business rule, a
public contract, the scope, permissions or credentials, or taking a destructive or
outward-facing action the plan did not name.

## Unattended runs (optional)

<!-- added 2026-10-14: replaced a categorical ban; /goal judges transcripts and enforces no limits, so limits and acceptance stay external -->

Only for a task with an approved plan, a lane of its own and acceptance criteria that
are commands. The limits must be enforced outside the model:

- Run `claude -p "<task file and plan path>" --max-budget-usd <n>` from the workspace,
  under a wall-clock limit (the CI job's timeout, or coreutils `timeout`, which macOS
  lacks by default). The deny
  rules and hooks apply as in any session; nothing pushes, merges or deploys. Its commits
  are reviewed at the pre-land range check like any others. In `-p` the plan hook does not run and an `ask` becomes a deny, so plan first,
  interactively.
- `/goal` keeps an interactive session working until a condition holds, but a small
  model judges the condition from the transcript; it runs nothing and enforces no time
  or cost limit. Use it for convenience, never as the acceptance check.
- After the run, a script or the user runs the acceptance commands on the lane. The
  task file records one outcome: completed (with that command output), failed (with the
  failing output) or escalated (the decision the divergence rule above reserves for the
  user). Progress stays in the task file, so a stopped run resumes from it.

Claude's built-in sandbox, when enabled, confines Bash commands only. Hooks, MCP
servers, language servers and the file tools run outside it, and deny rules on file
tools are not process isolation. Record which boundary a run relied on.

## Handoff

Write `ai/tasks/handoff_YYYYMMDD_HHMM.md` <!-- garden: ignore --> with the state, uncommitted context, the exact
next action and open questions only when the task file is insufficient. Keep one
authoritative current state; use a fresh session when context quality warrants it.

## Conclusion

1. Compare the result with the acceptance criteria and any applicable plan.
2. Add decisions to `docs/dev/decisions.md` (or the workspace's) that pass its four tests.
3. Propose `ai/project.md` additions the task discovered, if any.
4. List the corrections the user had to repeat and propose one rule for each (propose
   only). An accepted rule gets `<!-- added YYYY-MM-DD: <failure> -->`.
5. Move the artifacts to `ai/archive/<feature>/`.
6. Report the result, gates, Codex review outcome, limitations and unresolved findings.

## Retention

Weigh reproduction cost against recurrence. Fewer, truer artifacts.
