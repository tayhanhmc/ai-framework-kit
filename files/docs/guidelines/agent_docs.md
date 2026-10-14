# Writing docs agents read

Rules for any doc another agent will load: AGENTS.md files, `docs/`, `ai/project.md`.
The pre-land review rereads changed docs against this file.

## Rules

- Write only what the code and general knowledge cannot give: business rules, external
  constraints, incidents, decisions. No descriptions of what code does, no tours.
- State accepted rules directly with their reason. Label observations and proposals;
  finding a statement in code, a log or an external source does not make it policy.
- Give each rule the scope it really has: "any money paid back over $500", not
  "refunds over $500".
- Add one concrete example when a format or value matters.
- No conversation noise. Keep dates only when they establish evidence or applicability.
- Archive superseded rules outside the active reading path; keep a link when useful.
- One topic per file, named for its content. Rules first.
- Over 100 lines: add a contents list at the top, or split the file.
- One term per concept; do not alternate synonyms.
- One home per fact: edit the topic's existing doc rather than adding a second one or
  copying the rule into a history doc.
- Give every new doc a context-map row that points at the doc itself, never a folder.
- Before landing, reread the doc as an agent starting a related task, and delete any
  line whose removal changes nothing.

## Evidence and freshness

When claims depend on repository files, add one fenced `context` TOML block. Start
with `unverified`, then record the full commit hash only after checking the claims:

````markdown
```context
depends_on = ["src/payments", "tests/test_settlement.py"] # garden: ignore
verified_commit = "unverified"
```
````

Paths are literal files or directories relative to the product checkout for workspace
docs, or the framework root for framework docs. No globs, absolute paths or `..`.
Choose the smallest useful dependencies, including tests or config where relevant.
Garden checks these paths against the recorded commit, including staged, unstaged and
untracked changes. Static CI checks the metadata syntax without needing product clones.
Documents without metadata remain supported; add it where drift would matter.

A freshness finding requests review; changed code does not automatically supersede an
accepted decision. Compare the claim and source, then fix the implementation, patch the
doc, or mark the uncertainty. Check external evidence manually; cite its version or
retrieval date. Garden cannot verify external facts or infer semantic correctness.

Repairs follow the maintenance workflow: propose, the user approves. Never refresh a verification marker
merely to silence a finding. If the checked sources are dirty, finish their commit and
recheck before recording its hash. Keep proposals out of accepted guidance until the
authorized decision-maker resolves them.
