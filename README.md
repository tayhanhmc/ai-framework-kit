# ai-framework-kit

- `RECREATE_CODE_ENV_PROMPT.md`: the prompt that wraps a codebase in the framework;
  `files/` is what `install.py` installs, and `check_kit.sh` checks both.
- `MIGRATION.md`: upgrading an existing framework.
- Cross-model review runs through Codex or Cursor's `agent` CLI, whichever the machine
  has; set `CROSS_REVIEW_PROVIDER=codex|cursor` to choose when both are installed
  (`files/docs/agent-workflows/cross-review.md`).
- `user/`: personal `~/.claude` commands and hooks to copy onto each machine
  (`user/README.md`).
