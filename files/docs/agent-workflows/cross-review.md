# Cross-model review

Claude implements; a fresh Codex process reviews, so a second model family checks every
commit before it lands and every plan Claude leaves plan mode with. It uses the existing
Codex login and configured model (or `--model`). macOS and Linux; Claude Code 2.1.295 or
later for `onFailure: block`.

## The two checkpoints

1. **Commit.** Claude commits freely. To review before committing, stage exactly the
   intended commit, then from the framework root run:

   ```sh
   uv run --locked python ai/scripts/cross_review.py review --target shop/repo --context ai/tasks/checkout.md # garden: ignore
   ```

   Codex reviews the staged tree (`git write-tree`) as a change from HEAD, reading those
   frozen Git objects rather than the live index. The receipt is named by both, so any
   change to the index or HEAD needs a new review. Unstaged and untracked files are not
   committed or reviewed. `--commit <rev>` reviews an existing commit the same way.
2. **Plan.** When Claude leaves plan mode, the `ExitPlanMode` hook sends the plan to
   Codex with a snapshot of the checkouts it concerns (the framework and, from a
   workspace, that workspace's clone and lanes): each HEAD, its staged tree, and a tree
   of all its staged, unstaged and untracked work. Blocking findings deny it and return to Claude;
   the same plan submitted again against the same snapshot goes to the user to decide.
   Outside plan mode, review a written plan with
   `cross_review.py review --target . --plan ai/plans/<name>.md`. <!-- garden: ignore -->
3. **Landing.** Before work lands (a push to the default branch or a PR), run
   `cross_review.py check --target <lane> --range <base>..HEAD`: it lists every commit
   (a merge against its first parent) without a current passing receipt. Review each
   with `review --target <lane> --commit <sha>`, fix, re-review, then land. <!-- garden: ignore -->

P0/P1/P2 findings block; P3 is advisory. An incomplete review, timeout or invalid output
never passes; an error is retried on the next run. A result is reused, and an approval
counts, only while its identity holds: the reviewed content (staged tree and parent, or
plan text and snapshot), the `--context` files, the requested `--model` and the review
policy version (`POLICY` in the script). A changed or deleted context file revokes the
approval of staged work, so pass requirement files as context, not a task file whose
progress you will edit before committing. A landed commit keeps its review: the range
check does not re-judge it against later context. An amend that changes content needs
`review --commit HEAD` afterwards; a message-only amend keeps its review. `--force`
reviews again regardless. Receipts record model, policy, time and context.

## Acting on findings

<!-- added 2026-10-14: fixing every finding grew changes past their task (Windows support built over five commits, then scoped out) -->
<!-- changed 2026-10-26: an agent ran rounds 3 and 4 on one commit; round 3 removed as unneeded the option round 2 had asked for -->

Sort each blocking finding before changing code. Fix it with the smallest change when its
failure is real and inside the task. Dispute it with evidence in a file passed as
`--feedback <file>`: Codex rereviews with its earlier findings and your response. Take
it to the user when fixing it would widen the task. After two review rounds, findings
still blocking go to the user with your assessment, not into another fix: later rounds
start contradicting earlier ones. The script enforces this; when `review` exits 4, take
the printed findings to the user. If the user allows one more round, rerun with
`--owner-approved`. Leave P3 findings unfixed unless the user asks.

## Enforcement

Nothing blocks a commit. The landing range check is the gate: it judges each commit by
its first parent and tree, so content a Git hook restaged or a concurrent edit changed
during the commit is listed like any unreviewed commit. The `PreToolUse` hook on
`ExitPlanMode` enforces plan review; a hook that cannot run denies the plan.

`review` counts rounds: a complete review with a P0/P1/P2 finding is one; an approval,
an incomplete review or a failed run is none. A commit's rounds count per branch and
first parent, so amends and `--commit` reviews of the amended commit share one count,
and a new branch or a rebase onto a new base starts at zero. A plan's rounds count per
plan file (the hook's `planFilePath`, or its session when the payload has none). After
two rounds, `review` calls no Codex and exits 4 with the latest findings; the hook
denies with them. `--force` and `--feedback` do not lift the limit, and a current
approval is still reused. Each `--owner-approved` review past the limit allows one more
round and is recorded in its receipt; below the limit the flag grants nothing. Rounds
are kept in `ai-framework-review/rounds-*.json`.

This is a workflow guard, not a security boundary: the range check runs when the landing
workflow runs it. Plan snapshots skip ignored files, files marked skip-worktree or
assume-unchanged, and untracked files that look like secrets (the deny rules' patterns),
which are never written into Git. Use server-side branch checks if remote enforcement is
needed.

## Receipts and limits

Receipts and raw Codex runs live under each repository's Git common directory in
`ai-framework-review/`. Review input can include proprietary source and goes to the
configured Codex service; never pass secrets as context. Codex runs with a read-only
sandbox, no approvals, hooks or sub-agents, `--ephemeral`, and a 300 s timeout; one
review runs per repository at a time. Editing during a review is safe: the review
covers the snapshot it started with. A plan snapshot writes Git objects (as
`git stash create` would) through a temporary index; the real index is untouched. An
interrupted review, like a failed one, never approves and revokes an earlier approval of
the same content.

## References

- [Codex noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Claude hooks](https://code.claude.com/docs/en/hooks)
