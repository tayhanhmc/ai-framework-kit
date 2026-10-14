# Pre-land review

Run before work lands on the default branch: before opening a PR when the project uses
PRs, or before pushing when commits go straight to the default branch. `<base>` is the
remote default branch (`origin/main`); `<lane>` is the checkout holding the work (a lane,
or `repo/` itself when committing to main), so `<base>..HEAD` is exactly what is about to
land. It reports; the user decides. Under about 100 changed lines, run step 1 and the
Codex checkpoint in step 2 only.

## 1. Deterministic step

- Gates: `check_code.py lint` and `check_code.py test` with `--target <lane>`.
- Net lines from `git diff --shortstat <base>...HEAD` against the plan's estimate.
- `garden.py --changes <base> --target <lane>`: fix mechanical doc drift in this same
  change; list judgement calls.
- `garden.py --workspace <product> --target <lane>`: fix every path and command finding,
  in the framework repo and not the product's history, before the work lands; list the
  rest. Drift the change did not cause otherwise sits unfixed change after change.
  Resolve freshness findings against evidence; never update a marker just to clear one.
- `uv run --locked detect-secrets scan --exclude-lines 'verified_commit = "[0-9a-f]{40}"' <changed files>` from the framework root.
- Reread each doc the change added or edited under `docs/` against
  `docs/guidelines/agent_docs.md`, and fix what breaks it.
- Duplicate scan, below.

### Duplicate scan

Copies get written on lanes that cannot see each other, so only a cross-lane scan
catches them.

1. List the open lanes: the heads of open PRs (`gh pr list`, when the repo is on GitHub)
   plus local `.worktrees/`.
2. Copy each other lane's changed files into a scratch folder with `git show`.
3. Run `npx -y jscpd@5.4.1 --min-lines 6 --min-tokens 50` over this lane and that folder.
4. Report each clone with one side in this lane, naming the shared helper it should
   become.

## 2. Correctness

Built-in `/code-review` in Claude. Every commit already passed the Codex review in
`docs/agent-workflows/cross-review.md`; nothing to repeat here.

## 3. Less code, fresh eyes

A fresh context from the other model family. From Claude:
`codex exec --ephemeral -s read-only -C <lane> "<brief>" < /dev/null`. The brief:

> Review the diff between <base> and HEAD for size, not correctness. List code that can
> be deleted, existing helpers it duplicates, the smallest version that meets the
> acceptance criteria, tests that restate the same matrix. For each item give lines
> saved and behaviour risk. No other commentary.

Apply or reject each item with one line of reason, recorded in the task list.
