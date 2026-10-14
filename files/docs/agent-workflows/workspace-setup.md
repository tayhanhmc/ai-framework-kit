# Workspace setup: these steps add a workspace for a new product, or restore one on a new machine.

The step numbers follow the setup prompt, whose steps 0-3 built the framework. Adding a
product later starts here: ask the product's name, and whether it exists (its remote
URL) or is new (its language; whether to create a GitHub repository, only on a yes).
Record the answers in `ai/tasks/setup-<product>.md` with a `Target:` line.

## Contents

- [Step 4: discover](#step-4-bring-in-the-codebase-and-discover-read-only)
- [Step 5: ask](#step-5-ask-the-user-one-batch)
- [Step 6: toolchain and gates](#step-6-toolchain-and-gates)
- [Step 7: workspace files](#step-7-workspace-files-and-guardrails)
- [Step 8: user-level entries](#step-8-user-level-entries)
- [Step 9: verify](#step-9-verify)
- [Step 10: second run and commit](#step-10-second-run-and-commit)

## Step 4: Bring in the codebase and discover (read-only)

1. **Existing repository:** `git clone <remote> <product>/repo`. On a restore, clone
   only when `repo/` is missing.

   **New project:** `git init -b main <product>/repo`. Add the remote if one was
   created.
2. Record each answer below in the task file, with evidence (`file:line` or command
   output). For a new project most are empty; note that.
   - **Repository:** its remote, default branch and HEAD. Read
     `git log --format=%B -20` for commit style, trailers, squash merges and version
     bumps. On GitHub, check branch protection.
   - **The team's AI layer:** AGENTS.md, CLAUDE.md, `.claude/`, `.cursor/`, skills, and
     prompt or memory data the product reads at runtime.
   - **Toolchain pins:** CI's versions, `requires-python`, `packageManager`, any version
     files and lockfiles.
   - **Gates** CI and the team run: type check, lint, tests, format and schema checks.
     Include any checker CI skips.
   - **External effects:** commands and scripts that merge, deploy, release, publish,
     migrate, backfill, or reach a production host. Look in `scripts/`, `bin/`, `ops/`,
     CI, package scripts and the README. Each gets a task-file line: denied, or why it
     is safe.
   - **Native, container or hardware needs,** and any gate that cannot run here.
   - **Installed git hooks,** in `.git/hooks` and via `core.hooksPath`.
   - **The assistants:**
     - `claude --version` and `codex --version`;
     - `claude auto-mode config`;
     - user allow rules that cover a whole outside command (an allow rule skips auto
       mode's classifier, and only a deny rule beats it);
     - Codex approval policy, sandbox and trusted projects;
     - user-level instructions and memory that conflict with the planned rules.

## Step 5: Ask the user (one batch)

Ask about all of these at once:

- the gates and formatters;
- the deny-rule list;
- the contribution flow: whether work lands through PRs or straight on the default
  branch; who merges and releases; whether agents push without asking; the commit
  message format; AI co-author trailers;
- how the team's AI layer is used;
- any new machine dependency (uv, mise, a runtime);
- for a new project: the toolchain and scaffold (step 6).

After this, ask only about a gate that fails on the default branch, and at step 8.

## Step 6: Toolchain and gates

- **uv-managed Python** needs nothing more.
- **Anything else:**
  - write `<product>/mise.toml` with CI's versions;
  - run `mise trust` and `mise install` from the workspace;
  - install dependencies from the clone with the lockfile frozen, through
    `mise exec --`. Agent shells do not load mise's shell activation, so prefix every
    gate with `mise exec --`.
- **New project:** in `repo/` (on a branch, or on `main` for a solo flow), scaffold the
  agreed toolchain:
  - the manifest and lockfile;
  - a linter, a formatter and a type checker with their config;
  - a test runner with one passing test;
  - CI running the same gates, with actions pinned by SHA.

  Before committing, pass the Codex review in `docs/agent-workflows/cross-review.md`.
  This is product code, owned by the repo.

Then write `<product>/workspace.toml`:
- Use the team's package scripts over raw tool calls.
- Ruff and pytest search parent folders for config. If the repo has none of its own,
  they read the framework's `pyproject.toml`, so pin them: Ruff
  `--config pyproject.toml` (`--isolated` if the file is absent), pytest
  `-c pyproject.toml`.
- List a formatter only if the repo has its own config for it.

Validate the profile before anything else by loading it through `workspace_profile.py`.
The full verifier needs step 7's files, so it runs in step 9.

Run each gate on the default branch with `check_code.py`. If a gate fails, rerun it in a
clone outside the framework (`mktemp -d`):
- If it passes there, the framework's layout causes the failure: fix the pinning.
- If it fails in both, ask: must pass, or `baseline_failing` (which tolerates only
  exit 1, never a crash or usage error).

Record each gate's result and its run time.

## Step 7: Workspace files and guardrails

Write each file only when it is absent.

- **`<product>/AGENTS.md`, at most 5 KB.** Only what the root does not say and an agent
  cannot infer. It ends with `## Docs` and `## Context map`:
  - one paragraph on what the product is and what it touches in production;
  - where things are: launch here, never inside `repo/`; lanes are made by path as
    `git -C repo worktree add ../.worktrees/<lane>`; the toolchain prefix; <!-- garden: ignore -->
  - rules inside `repo/`: which team files bind code conventions (Codex must read the
    team's CLAUDE.md, since it never loads one), the commit style, commands agents never
    run, and team tools that are not available here;
  - gates: `uv run --project .. --locked python ../ai/scripts/check_code.py lint`, and <!-- garden: ignore -->
    `test`, with `--target .worktrees/<lane>` for a lane; known baseline failures; the
    pre-land review before a PR or a push to the default branch;
  - `## Docs`: "Record rules the code cannot show (business rules, external
    constraints) in `docs/`, written by `../docs/guidelines/agent_docs.md`; read it <!-- garden: ignore -->
    before writing or editing a doc. Give each new doc a context-map row.";
  - `## Context map`: a table, `| When the task touches | Read |`, one row per doc,
    naming the trigger in task words (totals, discounts, customer email, order files)
    and pointing at the doc itself, never a framework folder. Add rows for the team's
    binding docs in `repo/` too; a team folder is fine there. Write a doc in `<product>/docs/` only for rules steps 4 and 5
    actually found; never create one to fill a row. An empty map is fine.
- **`<product>/.claude/settings.json`:**
  - `permissions.deny`:
    - `Bash(*<command>*)` for each merge, deploy, release, publish, migrate and
      production-host command;
    - `EnterWorktree`, because it makes framework worktrees, not product ones;
    - the root's `Read(./**/...)` rules.
  - Never `ask` rules: auto mode still prompts for them.
  - `autoMemoryDirectory` (a top-level key):
    `~/.claude/workspace-memory/<product>`, so other products' memories never load here.
  - `hooks`: the root's, with `${CLAUDE_PROJECT_DIR}/../ai/scripts/`. <!-- garden: ignore -->
  - `enabledPlugins` only for a plugin the user approved.
- **`<product>/.codex/rules/<product>.rules`:** a
  `prefix_rule(pattern=[...], decision="forbidden", justification="...")` for each form
  an agent would type.
  - Cover the bare command; a script through `bash`, `sh` or `zsh`; a bare
    `./scripts/x`; `repo/`-prefixed paths; and each form wrapped in `mise exec --` when
    mise is used.
  - Put alternatives in a list at one position:
    `[["bash","sh","zsh"], ["scripts/deploy.sh","./scripts/deploy.sh"]]`.
  - Codex splits plain-word `bash -lc` chains, but not commands with substitutions or
    redirects. The AGENTS.md rule covers those.
  - No workspace `hooks.json`: the root's hooks run here.
- **`<product>/ai/project.md`:** this product's facts, with the root file's header and
  budget.
- **`<product>/ai/tasks/.gitkeep`:** `garden --static` reads the workspace's task folder,
  and git does not track an empty one.
- **A product skill** only when the team's flow needs one the framework lacks (for
  example, PR preparation to the team's standard when their release tool is not
  installed). Name it `<p>-<product>-<purpose>`.

**Accepted limits:** a deny rule matches the command as written, so a differently
spelled merge (`gh api ...`) gets through. A launch inside `repo/` skips the workspace
rules. Claude's commits need a passing Codex review of the staged tree; human commits,
merges and task-branch pushes are never blocked.

## Step 8: User-level entries

Show each diff, and apply it only on the user's go-ahead.

- **Claude `autoMode.environment`** in `~/.claude/settings.json`. Auto mode trusts only
  the launch repo (the framework) and its remotes, so it blocks pushes to the product
  until the product is named. Add a source-control entry with the product remote, the
  clone path and the lanes path. It should say which pushes are routine (task branches,
  or the default branch for a solo flow), and that merging and deploying are not. Keep `$defaults`. Check it with
  `claude auto-mode config`.
- **User allow rules** that cover a whole outside command (`Bash(gh *)`) skip the
  classifier. Propose narrowing them.
- **Codex trust (the user does this):** run an interactive `codex` at the framework root,
  trust the project, and trust its hooks. Do it again after every `hooks.json` change.

## Step 9: Verify

Record each result in the task file. Skip a probe for a tool that is not installed, and
note it.

1. **Commit review:** from a Claude session in the workspace, stage a scratch change
   on a scratch branch of the product clone and ask for a commit: the hook must deny
   it. After `cross_review.py review` passes, the same commit must go through. Delete
   the branch. The verifier exits 0, and `poe lint` and `poe test` pass.
2. The gates give the results recorded in step 6.
3. **Codex deny rules:** `codex execpolicy check --rules <file> -- <words>` gives
   `forbidden` for every form, and a control (`gh pr view 1`) matches nothing. Pass the
   command as separate words: one quoted string never matches, and zsh does not split
   an unquoted `$c`, so loop in bash or use `${=c}`.
4. **Claude deny rules:**
   - In a scratch folder, write a `.claude/settings.json` holding only the workspace's
     `permissions`.
   - Make stub executables, named for each denied command's first word, that print a
     marker, and put them first on PATH. For a path command (`scripts/deploy.sh`), put
     the stub at that path in the scratch folder.
   - Run `PATH=<stubs>:$PATH claude -p --permission-mode dontAsk --allowedTools "Bash(*)"
     --output-format json "<prompt>" < /dev/null`, asking it to run each denied command
     and one harmless control. No classifier runs in this mode.
   - Each denied command must appear in `permission_denials`, and the control must run.
   - Rerun with an empty deny list: every stub must run, which shows the deny rules did
     the blocking. A run in auto mode may block more; that is the classifier, not a
     failure.
5. **Loading, Claude:** from the workspace, run
   `claude -p "<question>" --model haiku --output-format json`, asking for a rule only
   the root AGENTS.md holds and one only the workspace's holds. Both must come back.
   Run it in the user's normal environment: a probe under another `CLAUDE_CONFIG_DIR` or
   an injected token tests a different setup, and has missed both files.
6. **Loading, Codex:** `codex debug prompt-input` from the workspace shows both AGENTS.md
   files and the root's skills.
7. **Session hook:**
   - With a scratch task list open, a fresh `claude -p` from the workspace must quote
     its `active:` line.
   - Codex is the user's step, since it needs trust: in `codex exec`, ask for the same
     line.
   - Codex re-injection after compaction is unprobed; record that.
8. **Tool probes:** write `docs/dev/tool-probes.md` now, and only now. It is a table of
   claim, command, expected result and last result. Fill it with probes 3-7 and their
   results, the loading claims above, two user-run rows (a `<workspace>/.codex/rules` file
   blocks at runtime; `~` in `autoMemoryDirectory` expands), and
   `Last run: <date>, claude <version>, codex-cli <version>`. A failing row is a design
   question for the user, never something to patch around. Then garden must report
   nothing about this workspace (before this file exists, it flags the missing probes).
9. **Script review:** if this setup changed a framework script, run the built-in
   `/code-review` (Codex: `/review`) and the Codex checkpoint over the change. Fix each
   confirmed finding with a
   regression test; list the rest in the report.

## Step 10: Second run and commit

1. Hash the framework's tracked and new files, excluding `ai/tasks/` and
   `docs/dev/tool-probes.md`:

   ```
   git ls-files -co --exclude-standard | grep -v -e '^ai/tasks/' -e '^docs/dev/tool-probes.md$' | xargs shasum -a 256 > <scratch>/first
   ```

2. Rerun steps 4-7 (on a first setup, the prompt's steps 1-3 too), reusing the task
   file's answers (no new questions). Then hash again into `<scratch>/second`. The two
   must be identical. A file the user edited between the runs keeps their edit.
3. Archive the task file to `ai/archive/setup-<product>/tasks/`.
4. Offer one commit of the framework files, listed by explicit path (never
   `git add -A`). Push only when asked. The Codex checkpoint reviews it like any
   commit; on a first setup that is the whole kit. Fix findings in files this setup
   wrote. Findings in kit files copied unchanged go to the user, who decides whether to
   commit by hand or fix the kit first.
5. Report:
   - what was built;
   - the verification evidence;
   - what was skipped, and why;
   - what the user must do by hand: Codex trust, `/doctor prompt-audit`, plugin
     installs.
