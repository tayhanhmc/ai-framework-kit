# Plan review

Codex reviews plans through the plan checkpoint in `docs/agent-workflows/cross-review.md`:
automatically when Claude leaves plan mode, or with `--plan` for a written plan. Give it
the plan, not your assessment. A small task need not create a plan just to get a review.

## The reviewer checks

1. Unverified paths, versions, permissions and quotas.
2. Version mismatches.
3. Values that look copied rather than checked.
4. Steps that depend on outputs no earlier step produces.
5. Partial failure and recovery.
6. Criteria a machine cannot check.
7. Simpler alternatives not evaluated.
8. Shared helpers: anything two parallel lanes need is built once, first.
9. Proportion: each component names the failure it prevents and its size in lines; a
   component with no named failure is cut.

## Output

Structured findings with evidence: P0/P1/P2 block, P3 is advisory. Verify findings,
repair or rebut with evidence, and re-review within the bounded loop. A materially
changed plan needs a fresh review before implementation.
