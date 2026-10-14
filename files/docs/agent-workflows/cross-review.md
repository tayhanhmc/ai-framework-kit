# Cross-model review

Claude implements; a fresh Codex process reviews, so a second model family checks every
commit Claude makes and every plan it leaves plan mode with. It uses the existing Codex
login and configured model (or `--model`). macOS and Linux; Claude Code 2.1.295 or later
for `onFailure: block`.

## The two checkpoints

1. **Commit.** Stage exactly the intended commit, then from the framework root run:

   ```sh
   uv run --locked python ai/scripts/cross_review.py review --target shop/repo --context ai/tasks/checkout.md # garden: ignore
   ```

   Codex reviews the staged tree (`git write-tree`) as a change from HEAD, reading those
   frozen Git objects rather than the live index. The receipt is named by both: a commit
   of exactly that content on that parent passes, and any change to the index or HEAD
   needs a new review. Unstaged and untracked files are not committed or reviewed.
2. **Plan.** When Claude leaves plan mode, the `ExitPlanMode` hook sends the plan to
   Codex. Blocking findings deny it and return to Claude; the same plan submitted again
   goes to the user to decide. Outside plan mode, review a written plan with
   `cross_review.py review --target . --plan ai/plans/<name>.md`. <!-- garden: ignore -->

P0/P1/P2 findings block; P3 is advisory. An incomplete review, timeout or invalid output
never passes; an error is retried on the next run. A result is reused only for the same
content and context: a changed task file, or a plan after any checkout moved, is reviewed
again. To dispute a finding, write the evidence to a file and pass `--feedback <file>`:
Codex sees its earlier findings and the response, and reviews again. Fix confirmed issues, restage and review. When
Codex and Claude still disagree after a rebuttal, report the findings to the user.

## Enforcement

The Claude `PreToolUse` hook on `Bash` denies a
`git commit` while any framework or product checkout, including lanes, has staged changes
without a passing receipt. The denial lists the review command for each. It allows only
commits whose content it can check beforehand: `git [-C <dir>] commit` with message,
author and amend options, optionally after `cd` or beside read-only Git commands
(`status`, `log`, `show`, `diff`, `push`). It denies every other form that may commit:
`-a`, `-p`, pathspecs, staging in the same command, wrappers (`bash -c`, `time`, `{ }`),
command substitution, environment prefixes and other Git options. Stage in one command,
review, then commit in another with `-m <message>` or `-F <file>`, not
`-m "$(cat <<EOF ...)"`. A hook that cannot run denies. The hook's `if: Bash(*commit*)`
filter is best-effort: Claude Code also runs it for commands it cannot parse or that use
`$()` or `$VAR`, and the hook allows those when they run no commit.

Only Claude's commits are checked. Human commits, merges in a terminal and other tools
are not. This is a workflow guard, not a security boundary: a script that commits
indirectly is not seen. Use server-side branch checks if remote enforcement is needed.

## Receipts and limits

Receipts and raw Codex runs live under each repository's Git common directory in
`ai-framework-review/`. Review input can include proprietary source and goes to the
configured Codex service; never pass secrets as context. Codex runs with a read-only
sandbox, no approvals, hooks or sub-agents, `--ephemeral`, and a 300 s timeout; one
review runs per repository at a time. Editing during a review is safe: the review
covers the snapshot it started with.

## References

- [Codex noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Claude hooks](https://code.claude.com/docs/en/hooks)
