# Pre-land review

Run before work lands on the default branch: before opening a PR when the project uses
PRs, or before pushing when commits go straight to the default branch. `<base>` is the
remote default branch (`origin/main`); `<lane>` is the checkout holding the work (a lane,
or `repo/` itself when committing to main), so `<base>..HEAD` is exactly what is about to
land. It reports; the user decides. Step 1 always runs. Steps 2 and 3 scale with
behavioural risk (`docs/agent-workflows/plan-review.md`), not line count: skip them for a
mechanical or low-risk change, and say so in the report.

## 1. Deterministic step

- Gates: `check_code.py lint` and `check_code.py test` with `--target <lane>`. Only
  exit 0 passes: report exit 3 (DEGRADED, a `baseline_failing` gate failed, so new
  failures in it are invisible) as unverified, and exit 2 as not run.
- Every commit reviewed: `cross_review.py check --target <lane> --range <base>..HEAD`
  from the framework root. A commit it lists (a Git hook changed it, or it was made
  outside Claude) gets `cross_review.py review --target <lane> --commit <sha>` first.
- Net lines from `git diff --shortstat <base>...HEAD`; explain a large surprise, but a
  difference from the plan's estimate alone needs no approval.
- `garden.py --changes <base> --target <lane>`: fix mechanical doc drift in this same
  change; list judgement calls.
- `garden.py --workspace <product> --target <lane>`: fix every path and command finding,
  in the framework repo and not the product's history, before the work lands; list the
  rest. Drift the change did not cause otherwise sits unfixed change after change.
  Resolve freshness findings against evidence; never update a marker just to clear one.
- Secrets in the files that land, from the workspace (`detect-secrets scan` exits 0 even
  with findings; the hook fails on them):
  `fw=$(git rev-parse --show-toplevel); (cd <lane> && git diff -z --name-only --diff-filter=d <base>...HEAD | xargs -0 uv run --project "$fw" --locked detect-secrets-hook)`.
  Any nonzero exit is a finding: remove the secret from the change. A false positive
  goes to the user; never add a `pragma: allowlist secret` to product code without
  their go-ahead. Framework files are scanned by the pre-commit hook at every commit.
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

Every commit already passed the Codex review in `docs/agent-workflows/cross-review.md`,
one change at a time. For risky changes, run the built-in `/code-review` once over the
whole `<base>..HEAD` diff, which catches interactions between commits. Record findings
that duplicate the commit reviews, and false positives, in the task file: they decide
whether this layer keeps earning its time.

## 3. Less code, fresh eyes

A fresh context from the other model family. From Claude:
`codex exec --ephemeral -s read-only -C <lane> "<brief>" < /dev/null`. The brief:

> Review the diff between <base> and HEAD for size, not correctness. List code that can
> be deleted, existing helpers it duplicates, the smallest version that meets the
> acceptance criteria, tests that restate the same matrix. For each item give lines
> saved and behaviour risk. No other commentary.

Apply or reject each item with one line of reason, recorded in the task list. Run this
step for new modules, new abstractions or a change much larger than its task needs.
