# User-level Claude Code files

Personal `~/.claude` files that every machine should have. `install.py` never writes
user-level config, so copy these by hand on each new machine.

| File | Installs to | What it does |
|---|---|---|
| `claude/commands/verify.md` | `~/.claude/commands/` | `/verify`: three adversarial agents (breaker, correctness, integration) that must show evidence |
| `claude/commands/challenge.md` | `~/.claude/commands/` | `/challenge`: one agent tries to disprove a specific claim |
| `claude/commands/cross-verify.md` | `~/.claude/commands/` | `/cross-verify`: two agents solve the same problem independently; disagreements are compared |
| `claude/hooks/verify-gate.sh` | `~/.claude/hooks/` | Stop hook: once per 10 minutes, blocks ending a turn while code files are modified, asking for `/verify`-style checks |

## Install

From the kit root:

```sh
mkdir -p ~/.claude/commands ~/.claude/hooks
cp user/claude/commands/*.md ~/.claude/commands/
cp user/claude/hooks/verify-gate.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/verify-gate.sh
```

The hook needs `jq`. Then merge this into `~/.claude/settings.json` (keep any other
`Stop` hooks in the same list):

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/verify-gate.sh",
            "timeout": 10,
            "statusMessage": "Checking for unverified changes..."
          }
        ]
      }
    ]
  }
}
```

Restart Claude Code; `/verify`, `/challenge` and `/cross-verify` then appear in the
slash-command list.
