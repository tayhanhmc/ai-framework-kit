# Maintenance

Run a focused review when garden flags changed dependencies or a task uncovers a
contradiction. Review tool compatibility when versions change or probes are over 30
days old. After a model change, evaluate whether existing guidance still earns its cost.

1. Run garden's full report (`uv run --locked poe garden`) and the verifier.
2. For freshness findings, read the affected document and its evidence in the target
   checkout. Distinguish an implementation regression from an obsolete description.
   Propose a small correction, or mark the unresolved claim as needing review. Update
   `verified_commit` only after checking the sources, as described in
   `docs/guidelines/agent_docs.md`.
3. When tool compatibility is due, rerun the probes in `docs/dev/tool-probes.md` and
   record each result. Update `Last run:` only after the recorded suite completes;
   retain failing results. A failing row is a design question for the user, never
   something to patch around.
4. Ask the user to run `/doctor prompt-audit` in Claude Code from the framework root,
   and to check skill usage and context costs in the Stats tab (`/stats`). These are
   user commands.
5. Read each rule's provenance comment in every AGENTS.md and propose pruning rules that
   no longer prevent a failure. A rule with no comment is a pruning candidate.
6. Spot-check consequential facts without dependency metadata, including external
   constraints. Add metadata where it would prevent a repeated miss.
7. Compare user-level instructions (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, auto
   memory) with the framework's rules, and list contradictions.

**Kit updates.** To take kit fixes, run the kit's `install.py --framework <root>
--dry-run` from an up-to-date kit clone (the recorded `kit_revision` must be in it),
review the diff with the user, run it, and resolve each `*.kit-conflict-*` sidecar with
them. Then run the verifier, `poe lint` and `poe test`, and commit with the review.
Roll back with Git: revert that commit.

Propose one diff. The user approves it. Nothing is applied automatically, and nothing
is deleted without evidence that the knowledge is obsolete; an old date or low usage
alone is not enough. Never weaken enforcement to clear a finding.

After material routing or model changes, follow `docs/guidelines/context_evaluation.md`.
