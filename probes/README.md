# Native probes

Opt-in checks of how the installed Claude Code and Codex CLIs really behave, for the
claims the kit relies on (hooks, plan review, AGENTS.md loading, launch folders, the
Codex review call). They call the real CLIs, cost a little money and need the user's
logins, so they never run in CI or `check_kit.sh`. Run them when a CLI version changes,
before trusting a claim in `RECREATE_CODE_ENV_PROMPT.md`, or when a probe row is stale.

```sh
# list the probes; runs nothing
uv run --isolated --no-project --python ">=3.11" -- python probes/run_probes.py
# run them all (about $0.07 of Claude haiku and ~50k mostly cached Codex tokens, ~3 min)
uv run --isolated --no-project --python ">=3.11" -- python probes/run_probes.py --live
# a subset, a framework's own scripts, no Codex
... run_probes.py --live --only claude.hook p_compaction --framework ~/fw --no-codex
```

Each probe builds a disposable framework fixture in a temp folder (`--workdir`,
`--keep` to keep the fixtures), runs the CLI with a hard timeout that kills the whole
process group, and reports one status: PASS, FAIL, UNSUPPORTED or UNTESTED. Results go
to `results.json` and `results.md` in the work folder, with the raw event streams in
`raw/`. Exit 0 means no FAIL; 1 at least one FAIL; 2 could not run.

Evidence comes from the harness, not the model's word: hook stubs log their stdin
payload, argv and `CLAUDE_PROJECT_DIR` beside themselves; file and commit side effects are
checked on disk; `init.memory_paths`, `compact_boundary` and hook events come from
Claude's `stream-json`. Where Claude does not echo injected text in the stream, the probe
plants a random codeword and requires the model to repeat it, which it cannot guess.

| Probe | Shows |
|---|---|
| `claude.hook.*`, `claude.pretooluse.deny_honoured` | exec-form `args` (no shell, `${CLAUDE_PROJECT_DIR}` expanded), the `if` filter, a PreToolUse deny honoured and its reason delivered |
| `claude.hook.onfailure_block` | a crashing hook blocks with `onFailure: block`; a control run without it does not |
| `claude.exitplanmode.plan_in_payload` | the ExitPlanMode payload carries `tool_input.plan`; `-p` offers the tool only with a permission host, so the probe adds a stub MCP host |
| `claude.launch.*`, `claude.agents_md.*` | which hooks and AGENTS.md files load from the root, a workspace, a workspace subfolder, the nested clone and a git worktree; an ancestor CLAUDE.md turns AGENTS.md off |
| `claude.sessionstart.compact_reinjects` | `/compact` in a stream-json session fires SessionStart `compact` and its output reaches the model |
| `codex.agents_md.*` | `codex debug prompt-input` (no model call) from a workspace and from inside the clone |
| `codex.exec.output_schema` | the kit's `cross_review.review_staged` end to end on a seeded bug: Codex output matches the kit schema |
| `codex.hooks.session_start_workspace` | the kit's Codex SessionStart hook form fires from a workspace launch |

Side effects outside the work folder: none in config. Claude runs use
`--no-session-persistence`; the plan probe sets `plansDirectory` so its plan stays in the
fixture. Hook probes use `--setting-sources project,local` so user hooks cannot interfere;
the launch probes use the user's normal sources, since loading must be checked as it
really runs. Codex trust is given per invocation (`-c 'projects={"<root>"={trust_level="trusted"}}'`
and `--dangerously-bypass-hook-trust`) on a throwaway fixture; the dotted form
`-c projects."<root>".trust_level=...` is silently ignored. Nothing is written to
`~/.codex/config.toml`; Codex may still write its usual logs. Review input goes to the
configured Codex service, so the fixtures hold only generated code.
