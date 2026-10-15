# Upgrading an existing framework to this kit revision

Nothing here is applied automatically. Each framework is upgraded on purpose, by its
owner, one at a time, and can be rolled back with Git.

## What changes for an installed framework

| Change | Effect on an existing framework | Action |
|---|---|---|
| Review receipts carry `POLICY`, model and context digests | Every receipt made before the upgrade stops counting; staged work must be reviewed again | Review once after upgrading |
| Commit gate checks only the checkout being committed | Unrelated staged work in another lane no longer blocks a commit | None |
| `PostToolUse`/`PostToolUseFailure` `Bash` hooks run `cross_review.py hook` | Claude is told when a Git hook or concurrent edit changed what it committed | The verifier requires both entries in `.claude/settings.json` and each workspace's |
| `cross_review.py check --range` in the pre-land review | Commits without a current receipt (changed by a hook, or made by hand) are listed before landing | Review listed commits with `--commit <sha>` |
| Plan reviews snapshot uncommitted work | A plan approved before dirty edits is reviewed again | None |
| `check_code.py` exit 3 (DEGRADED) | A failing `baseline_failing` gate no longer passes with exit 0 | Prefer narrow deselection in the gate's argv; scripts that test `== 0` now treat it as not passed |
| Gate names unique across lint and test | A profile reusing a name in both kinds fails to load (exit 2) | Rename one gate |
| Gate `timeout` (default 1800 s) | A gate running longer than 30 minutes is killed and fails | Set `timeout` on long gates |
| Framework venv removed from gate `PATH` | A gate that relied on a framework-installed tool fails with "not found" | Install the tool in the product's toolchain |
| `local_only = true` profiles | A repository without a remote can be set up | None |
| `[[format]]` formatters; built-in `ruff` needs `uv.lock` | A non-uv Ruff product now reports an error instead of using the framework's Ruff | Add a `[[format]]` entry |
| `detect-secrets-audited` pre-commit hook | A baseline with unaudited entries fails pre-commit | Audit each entry with the owner; real secrets are removed and rotated |
| Pre-land secret check uses `detect-secrets-hook` | Findings now fail instead of exiting 0 | None |
| Garden reads only top-level names per ref at session start | Faster session start; same output | None |
| Workflow docs: risk-based depth, authorized adjustments, optional unattended runs, stack-aware integrations | Fewer approval requests for routine changes | Review the changed rules with the team |
| Comment rule in the root AGENTS.md `## Conventions`; root budget 3.5 KB | The commit review checks changed comments and docstrings against it (`POLICY` 2026-10-15: receipts are reviewed again) | Add the `## Conventions` comment bullet from `RECREATE_CODE_ENV_PROMPT.md`, before `## Etiquette` |
| Framework scripts support macOS and Linux | They import on Windows and their tests skip there; the commit review no longer reports Windows-only behaviour in `ai/scripts` (product code is still reviewed for its own platforms) | None |

## Procedure

1. In an up-to-date kit clone, run `check_kit.sh`.
2. From the framework root, on a branch:
   `uv run --isolated --no-config --no-project --python ">=3.11" <kit>/install.py --framework . --prefix <p> --dry-run`.
   A framework set up before the installer has no `.kit-install.toml`, so every kit
   file that differs from this revision is reported as a conflict, with the kit's
   version in a `*.kit-conflict-*` sidecar. Merge each with the owner, keeping local
   rules, then delete the sidecar.
   The installer only knows the kit's files. Files from an older framework generation
   (for example `discover.py`, or a skill the kit renamed such as `<p>-pre-pr-review`) are
   neither updated nor removed: list them with the owner and retire each by hand. A dry
   run against a copy of one real framework (2026-10-14) reported 15 files to create and
   30 conflicts, so plan this first adoption as a reviewed merge, not a quick update.
3. Rerun without `--dry-run` until it exits 0, then `uv lock`, `uv sync --locked`,
   `uv run --locked poe lint`, `uv run --locked poe test`, the verifier and
   `pre-commit run --all-files`.
4. Copy the new `PostToolUse` and `PostToolUseFailure` entries into each workspace's
   `.claude/settings.json` (with `${CLAUDE_PROJECT_DIR}/../`), and recheck each
   `workspace.toml` against the table above.
5. Rerun the step 9 probes in `docs/agent-workflows/workspace-setup.md`, or the kit's
   `probes/run_probes.py --live --framework <root>`.
6. Commit through the commit review. Later updates are step 2 again; the installer then
   three-way merges local edits against the recorded revision.

## Rollback

Revert the upgrade commit (`git revert <sha>`) and run `uv sync --locked`. The old
scripts' commit gate accepts approvals the new ones wrote (same key and `approved`
field), but their `review` command does not reuse those results and calls Codex again. Delete `.kit-install.toml` only if returning to manual copying.
