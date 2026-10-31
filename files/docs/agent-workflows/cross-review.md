# Cross-model review

Claude implements; a reviewer from another model family checks every commit, every plan
Claude leaves plan mode with, and every change before it lands. Each machine picks its
reviewer with `CROSS_REVIEW_PROVIDER` (`codex` or `cursor`); unset, it is Codex when
`codex` is on PATH, else Cursor's `agent` CLI. Both run the same flow. macOS and Linux;
Claude Code 2.1.295 or later for `onFailure: block`.

## Contents

- [Reviewers](#reviewers)
- [The three checkpoints](#the-three-checkpoints)
- [Acting on findings](#acting-on-findings)
- [Enforcement](#enforcement)
- [Data and limits](#data-and-limits)
- [References](#references)

## Reviewers

| Checkpoint | Codex | Cursor | Blocks on |
|---|---|---|---|
| Commit | `$CROSS_REVIEW_MODEL`, else Codex's configured model | `$CROSS_REVIEW_FAST`, else `FAST_REVIEWER` | P0, P1 |
| Plan, range | the same one model | the panel: `$CROSS_REVIEW_PANEL`, else `PANEL`, four model families | P0, P1, P2 |

The Cursor panel reviews in two stages: each model alone, then each votes on the
others' findings (skipped when nothing could block). A P0-P2 finding blocks when it has
at least two agree votes and more agree than disagree, its author counting as one. At
least three models must finish both stages. Anthropic models, and Cursor's `auto`, are
refused. Each reviewer call may run `$CROSS_REVIEW_TIMEOUT` seconds (default 1200),
counting time the machine sleeps. Reviews may run concurrently, except two of the same
change or the same round lineage.

## The three checkpoints

Run the script from the framework root with the framework's uv project, which provides
the secret scanner (on a machine whose uv is older than the pinned one, prefix
`uvx --from 'uv>=0.12.8'`).

1. **Commit.** Claude commits freely. To review before committing, stage exactly the
   intended commit, then run:

   ```sh
   uv run --locked python ai/scripts/cross_review.py review --target shop/repo --context ai/tasks/checkout.md # garden: ignore
   ```

   The reviewer gets the staged tree's diff from HEAD in its prompt, frozen, never the
   live index. The receipt is named by both, so any change to the index or HEAD needs a
   new review. `--commit <rev>` reviews an existing commit the same way.
2. **Plan.** When Claude leaves plan mode, the `ExitPlanMode` hook reviews the plan
   against a snapshot of the checkouts it concerns (the framework and, from a workspace,
   that workspace's clone and lanes): each HEAD, its staged diff, and a tree of all its
   staged, unstaged and untracked work. Blocking findings deny it and return to Claude;
   the same plan submitted again against the same snapshot goes to the user to decide.
   Outside plan mode, review a written plan with
   `cross_review.py review --target . --plan ai/plans/<name>.md`. <!-- garden: ignore -->
3. **Landing.** Before work lands (a push to the default branch or a PR), run
   `cross_review.py check --target <lane> --range <base>..HEAD`. It passes only with a
   current passing review of everything the range adds, from the merge base to the head
   tree: run `review --target <lane> --range <base>..<branch>`, act on the findings,
   then check again. A range that changes only Markdown files needs no review; every
   commit in it is still screened for secrets. <!-- garden: ignore -->

An incomplete review, timeout or invalid output never passes; an error is retried on the
next run. A result is reused, and an approval counts, only while its identity holds: the
reviewed content, the `--context` files, the provider and models, and the review policy
version (`POLICY` in the script). A changed or deleted context file revokes the approval
of staged work, so pass requirement files as context, not a task file whose progress you
will edit before committing. A range check does not re-judge context. `--force` reviews
again regardless. Receipts record provider, models, results, votes, policy, time and
context.

## Acting on findings

<!-- added 2026-10-14: fixing every finding grew changes past their task (Windows support built over five commits, then scoped out) -->

Sort each blocking finding before changing code. Fix it with the smallest change when its
failure is real and inside the task. Dispute it with evidence in a file passed as
`--feedback <file>`: the reviewer rereviews with its earlier findings and your response.
Take it to the user when fixing it would widen the task. Leave P3 findings, and a
commit's P2 findings, unfixed unless the user asks. Sort findings from a reviewer that
does not rank them, such as Claude's `/code-review`, by the same rule: fix real failures
in current behaviour inside the task; leave hardening and style unless the user asks.

The script enforces two rounds per lineage since its last approval: a commit's branch and
subject, a range's head branch, or a plan file's path. The third refuses with exit 3
before any model call; take the findings to the user with your assessment. Only the user
lifts the cap, with `--owner-override "<reason>"`, which the receipt records; the Bash
hook asks the user before Claude passes it. The `ExitPlanMode` hook counts its rounds
against the plan file Claude Code writes (the same lineage as `--plan` on that file), else
the session; past the cap the next plan goes to the user without a model call, to approve
it as it is or lift the cap. `check` reports the rounds used.

## Enforcement

Nothing blocks a commit. The landing range check is the gate. The `PreToolUse` hook on
`ExitPlanMode` enforces plan review; a hook that cannot run denies the plan.

This is a workflow guard, not a security boundary: the range check runs when the landing
workflow runs it. Use server-side branch checks if remote enforcement is needed.

## Data and limits

Review input goes to the provider's service: OpenAI through Codex, or Cursor's service
through `agent`. Every upload, and every panel finding passed to the other models, is
screened with detect-secrets first, against the base's audited `.secrets.baseline`;
a hit refuses the review and revokes an earlier approval. Never pass secrets as context.

Reviewers run read-only (`codex exec --sandbox read-only`, `agent --mode ask`) in a
temporary export of exactly the tree under review, without Git metadata, ignored files
or files matching the deny rules' secret patterns. They still run as the user with no
filesystem sandbox: repository text that talks a reviewer into reading another absolute
path is not stopped (`docs/dev/decisions.md`, "Integration: cross-model review").
Keep secrets in environment variables, not in files inside checkouts.

Receipts and raw runs live under each repository's Git common directory in
`ai-framework-review/`, so each machine keeps its own. Reviews of different changes and
lineages run at once; two of the same change or lineage run one at a time. Editing during a review is safe: it covers the snapshot it started with. A plan
snapshot writes Git objects through a temporary index; the real index is untouched. An
interrupted review, like a failed one, never approves and revokes an earlier approval of
the same content.

## References

- [Codex noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Cursor CLI](https://cursor.com/docs/cli/overview)
- [Claude hooks](https://code.claude.com/docs/en/hooks)
