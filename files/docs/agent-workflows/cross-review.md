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
   frozen Git objects rather than the live index. The receipt is named by both, so any
   change to the index or HEAD needs a new review. Unstaged and untracked files are not
   committed or reviewed. `--commit <rev>` reviews an existing commit the same way.
2. **Plan.** When Claude leaves plan mode, the `ExitPlanMode` hook sends the plan to
   Codex with a snapshot of the checkouts it concerns (the framework and, from a
   workspace, that workspace's clone and lanes): each HEAD plus its staged, unstaged and
   untracked work, frozen as a Git tree. Blocking findings deny it and return to Claude;
   the same plan submitted again against the same snapshot goes to the user to decide.
   Outside plan mode, review a written plan with
   `cross_review.py review --target . --plan ai/plans/<name>.md`. <!-- garden: ignore -->
3. **Landing.** Before work lands (a push to the default branch or a PR), run
   `cross_review.py check --target <lane> --range <base>..HEAD`: every commit (a merge
   against its first parent) must have a current passing receipt. <!-- garden: ignore -->

P0/P1/P2 findings block; P3 is advisory. An incomplete review, timeout or invalid output
never passes; an error is retried on the next run. A result is reused, and an approval
counts, only while its identity holds: the reviewed content (staged tree and parent, or
plan text and snapshot), the `--context` files, the requested `--model` and the review
policy version (`POLICY` in the script). A changed or deleted context file revokes the
approval of staged work, so pass requirement files as context, not a task file whose
progress you will edit before committing. A landed commit keeps its review: the range
check does not re-judge it against later context. An amend that changes content needs
`review --commit HEAD` afterwards (the hook says so); a message-only amend keeps its
review. `--force` reviews again regardless. Receipts record the model, policy,
time and context digests. To dispute a finding, write the evidence to a file and pass
`--feedback <file>`: Codex sees its earlier findings and the response, and reviews again.
Fix confirmed issues, restage and review. When Codex and Claude still disagree after a
rebuttal, report the findings to the user.

## Enforcement

The Claude `PreToolUse` hook on `Bash` denies a
`git commit` when the checkout it commits in (resolved from the session folder, `cd` and
`git -C`; only a literal `cd <dir>` and read-only Git may come before the commit) has
staged changes without a current passing receipt; staged work in other
checkouts does not block it. The denial names the review command. It allows only
commits whose content it can check beforehand: `git [-C <dir>] commit` with message,
author and amend options, optionally after `cd` or beside read-only Git commands
(`status`, `log`, `show`, `diff`, `push`). It denies every other form that may commit:
`-a`, `-p`, pathspecs, staging in the same command, wrappers (`bash -c`, `time`, `{ }`),
command substitution, environment prefixes and other Git options. Stage in one command,
review, then commit in another with `-m <message>` or `-F <file>`, not
`-m "$(cat <<EOF ...)"`. A hook that cannot run denies. The hook's `if: Bash(*commit*)`
filter is best-effort: Claude Code also runs it for commands it cannot parse or that use
`$()` or `$VAR`, and the hook allows those when they run no commit.

The gate checks the index before Git runs. Git then runs the repository's own hooks
during the commit (`pre-commit`, `prepare-commit-msg`, ...), and those may restage files;
another process may also change the index meanwhile. So the commit that lands is
compared with the allowed tree afterwards: the `PostToolUse` and `PostToolUseFailure`
hooks on `Bash` tell Claude when they differ, with the commands to inspect the difference
and review the landed commit (`--commit HEAD`). The landing range check is the backstop
when that message is missed. Product hooks stay as they are.

Only Claude's commits are checked. Human commits, merges in a terminal and other tools
are not. This is a workflow guard, not a security boundary: a script that commits
indirectly, a Git alias (`git ci`) or a quoted subcommand (`git c""ommit`) is not seen by
the gate; the landing range check still lists such commits. Plan snapshots skip
ignored files and files marked skip-worktree or assume-unchanged. Use server-side branch
checks if remote enforcement is needed.

## Receipts and limits

Receipts and raw Codex runs live under each repository's Git common directory in
`ai-framework-review/`. Review input can include proprietary source and goes to the
configured Codex service; never pass secrets as context. Codex runs with a read-only
sandbox, no approvals, hooks or sub-agents, `--ephemeral`, and a 300 s timeout; one
review runs per repository at a time. Editing during a review is safe: the review
covers the snapshot it started with. A plan snapshot writes Git objects (as
`git stash create` would) through a temporary index; the real index is untouched.

## References

- [Codex noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Claude hooks](https://code.claude.com/docs/en/hooks)
