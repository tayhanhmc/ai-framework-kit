# Work management

For work spanning sessions; single-session work needs none of this. Every commit passes
the Codex review in `docs/agent-workflows/cross-review.md`.

## Artifacts

Default to one task file in `ai/tasks/`: objective, acceptance criteria, target checkout,
important decisions, progress, evidence and next action. Add a separate analysis, spec
or plan only when complexity or collaboration makes it useful; the folders are optional
destinations, not a required sequence. A plan holds the choices, rejected
alternatives, order, machine-checkable success criteria, risks and exclusions. The user
approves the plan before execution.

## Task lists

- Items are `[ ]` open, `[x]` done, `[-]` dropped (with a reason).
- Put evidence beside each done item: the command and its key output, or `file:line`.
- The header has a `Target:` line naming the checkout and branch. The session hook prints
  it, so it survives compaction.

## Divergence

A departure from the approved approach needs a planned-vs-actual report and the user's
approval before continuing.

## Handoff

Write `ai/tasks/handoff_YYYYMMDD_HHMM.md` <!-- garden: ignore --> with the state, uncommitted context, the exact
next action and open questions only when the task file is insufficient. Keep one
authoritative current state; use a fresh session when context quality warrants it.

## Conclusion

1. Compare the result with the acceptance criteria and any applicable plan.
2. Add decisions to `docs/dev/decisions.md` (or the workspace's) that pass its four tests.
3. Propose `ai/project.md` additions.
4. List the corrections the user had to repeat and propose one rule for each (propose
   only). An accepted rule gets `<!-- added YYYY-MM-DD: <failure> -->`.
5. Move the artifacts to `ai/archive/<feature>/`.
6. Report the result, gates, Codex review outcome, limitations and unresolved findings.

## Retention

Weigh reproduction cost against recurrence. Fewer, truer artifacts.
