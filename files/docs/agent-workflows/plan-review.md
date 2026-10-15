# Plan review

Codex reviews plans through the plan checkpoint in `docs/agent-workflows/cross-review.md`:
automatically when Claude leaves plan mode, or with `--plan` for a written plan. Give it
the plan and the requirements as written, not your assessment or preferred answer.

## Depth follows risk

<!-- added 2026-10-14: fixed line-count thresholds sent large mechanical edits to review and let one-line authorization changes through -->

Scale planning and review by what could break, not by size. A one-line change to
authorization, money, data deletion, migrations or a public contract can merit a full
plan and review; a large mechanical edit (rename, formatting, generated code) may need
none. Obvious, local, easily reverted changes skip the plan: make the change, run the
gates, commit through the commit review.

## A consequential plan holds

<!-- added 2026-10-14: plans omitted boundaries, alternatives and rollback, so review had nothing to check them against -->

Write "not applicable" for a section that does not apply; never pad one.

1. **Objective and non-goals;** the repository evidence read (`file:line`, command
   output); open questions, and which of them change behaviour, APIs, data or scope.
2. **Reuse and alternatives:** existing code to reuse, and the meaningful options
   considered, including doing less or nothing.
3. **Boundaries:** affected interfaces, module boundaries, data ownership and invariants
   the change must keep.
4. **Concerns:** compatibility, migration, security, performance and operations, where
   they apply.
5. **Experiments:** small checks that settle an uncertain assumption (an API's real
   behaviour, a version's signature) before building on it.
6. **Steps and acceptance:** ordered steps, each acceptance criterion tied to a command
   or test that checks it, and how to recover or roll back.

For consequential or unfamiliar work, explore read-only first (a subagent keeps the
search out of the main context), and ask for an independent critique that gets the
requirements and evidence, not the conclusion.

## Runtime acceptance (web and service products)

<!-- added 2026-10-14: acceptance for services was left to manual checks with no start, readiness or cleanup step -->

When acceptance depends on a running application, the plan states how to start it,
detect readiness (a health URL or log line, with a timeout), isolate ports and test
data, run the relevant user or API flows, and clean up. Exploratory browser checks
find problems; behaviour that matters again becomes a deterministic regression test.

## The reviewer checks

1. Unverified paths, versions, permissions and quotas.
2. Version mismatches.
3. Values that look copied rather than checked.
4. Steps that depend on outputs no earlier step produces.
5. Partial failure and recovery.
6. Criteria a machine cannot check.
7. Simpler alternatives not evaluated.
8. Shared helpers: anything two parallel lanes need is built once, first.
9. Proportion: each component names the failure it prevents; a component with no named
   failure is cut.

## Output

Structured findings with evidence: P0/P1/P2 block, P3 is advisory. Verify findings,
repair or rebut with evidence, and re-review within the bounded loop. A materially
changed plan needs a fresh review before implementation.
