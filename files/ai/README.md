# ai/

| Folder | Holds |
|---|---|
| `ai/project.md` | non-obvious framework facts, at most 150 lines |
| `ai/analysis/` | investigations before a spec |
| `ai/specs/` | approved specs |
| `ai/plans/` | approved plans |
| `ai/tasks/` | open task lists and handoffs |
| `ai/archive/` | finished work, by feature |
| `ai/scripts/` | verifier, garden, profile loader, check runner, format hook, cross-model review |

Each work-state folder keeps a `.gitkeep`, so it survives when its last file is archived.

Cross-model review receipts live in Git metadata, outside this tree; see
`docs/agent-workflows/cross-review.md`.
