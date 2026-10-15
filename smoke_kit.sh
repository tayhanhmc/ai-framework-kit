#!/bin/bash
# smoke_kit.sh: slow end-to-end check of a real install, run before a kit release. Installs
# the kit with install.py into a scratch framework whose path holds a space, adds a uv Python
# product and (when node and npm exist) a Node product with generated configuration, then
# runs what a user and CI would: uv sync --locked, pre-commit install, the secrets baseline
# procedure, a real git commit through pre-commit, every `run:` step of the shipped CI
# workflow, check_code.py lint|test, the pre-land secrets scan as documented, the verifier
# and garden. Seeded failures (a synthetic secret, a failing test, an unaudited baseline
# entry) must fail. It never touches user-level config: git runs with an empty global
# config. UV_PYTHON picks the Python. Exit 0 only if every check passes; UNTESTED lines
# name what this machine could not exercise.
set -euo pipefail
kit="$(cd "$(dirname "$0")" && pwd)"
root="$(mktemp -d)"
trap '/bin/rm -r "$root"' EXIT
fw="$root/my framework"
remotes="$root/remote store"
py=(uv run --isolated --no-config --no-project --python "${UV_PYTHON:->=3.11}")
export GIT_CONFIG_GLOBAL="$root/gitconfig" GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=smoke GIT_AUTHOR_EMAIL=smoke@example.invalid
export GIT_COMMITTER_NAME=smoke GIT_COMMITTER_EMAIL=smoke@example.invalid
: >"$GIT_CONFIG_GLOBAL"
# A made-up AWS-shaped key, built at run time so this file holds no secret-shaped string.
fake_key="AKIA$(printf 'QZXJ7W2KSYNTHETC')"

say() { echo "smoke_kit: $*"; }
fail() {
  echo "smoke_kit: FAIL: $*" >&2
  exit 1
}
expect() { # expect <exit code> <command>...: run it, fail unless it exits with that code
  local want=$1 got=0
  shift
  "$@" || got=$?
  [ "$got" = "$want" ] || fail "expected exit $want, got $got: $*"
  say "ok (exit $got): $*"
}
nonzero() { # nonzero <command>...: run it, fail if it exits 0
  local got=0
  "$@" || got=$?
  [ "$got" != 0 ] || fail "expected a failure, got exit 0: $*"
  say "ok (failed as seeded, exit $got): $*"
}

say "1. install the kit into '$fw'"
snap="$("$kit/kit_snapshot.sh")" # the kit as it stands, uncommitted work included
mkdir -p "$fw" "$remotes"
git -C "$fw" init -q -b main
install=("${py[@]}" -- python "$kit/install.py" --framework "$fw")
expect 1 "${install[@]}" --prefix kt --revision "$snap" --dry-run >/dev/null
expect 0 "${install[@]}" --prefix kt --revision "$snap"
expect 0 "${install[@]}" --revision "$snap"
cd "$fw"

say "2. framework environment"
uv lock -q
expect 0 uv sync -q --locked
expect 0 uv run --locked pre-commit install
uv run --locked python - <<'PY'
import sys
from pathlib import Path

sys.path[:0] = ["ai/scripts", "tests"]
from test_verify_agent_env import ROOT_AGENTS

agents = ROOT_AGENTS.replace(
    "- `docs/dev/architecture.md`",
    "- `docs/dev/architecture.md`\n- `docs/dev/decisions.md`\n- `docs/dev/tool-probes.md`",
)
Path("AGENTS.md").write_text(agents)
for name in ("README.md", "ai/project.md", "docs/dev/architecture.md",
             "docs/dev/decisions.md", "docs/dev/tool-probes.md"):
    path = Path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Minimal setup fixture\n")
PY
version() { # a missing CLI records 0; garden then notes it and skips the comparison
  local v
  v=$("$1" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9][0-9.]*' | head -1 || true)
  echo "${v:-0}"
}
printf '# Tool probes\n\nLast run: %s, claude %s, codex-cli %s\n' "$(date +%F)" \
  "$(version claude)" "$(version codex)" >docs/dev/tool-probes.md

workspace() { # workspace <name> <lint argv json> <test argv json>: generated configuration
  "${py[@]}" -- python - "$1" "$remotes/$1.git" "$2" "$3" <<'PY'
import json
import sys
from pathlib import Path

name, remote, lint, test = sys.argv[1:]
ws = Path(name)
root_settings = json.loads(Path(".claude/settings.json").read_text())
hooks = json.loads(
    json.dumps(root_settings["hooks"]).replace(
        "${CLAUDE_PROJECT_DIR}/ai/scripts/", "${CLAUDE_PROJECT_DIR}/../ai/scripts/"
    )
)
settings = {
    "permissions": {
        "deny": ["Bash(*deploy*)", "EnterWorktree", *root_settings["permissions"]["deny"]]
    },
    "autoMemoryDirectory": f"~/.claude/workspace-memory/{name}",
    "hooks": hooks,
}
(ws / ".claude").mkdir(parents=True)
(ws / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
(ws / ".codex/rules").mkdir(parents=True)
(ws / f".codex/rules/{name}.rules").write_text(
    'prefix_rule(pattern=["npm", "run", "deploy"], decision="forbidden", '
    'justification="deploys reach production")\n'
)
(ws / "ai/tasks").mkdir(parents=True)
(ws / "ai/tasks/.gitkeep").write_text("")
(ws / "ai/project.md").write_text("# Project facts\n\n## Environment\n\n## Gotchas\n")
(ws / "AGENTS.md").write_text(
    f"# {name}\n\nA smoke-test product.\n\n"
    "- Launch here, never inside `repo/`.\n"
    "- Gates: `uv run --project .. --locked python ../ai/scripts/check_code.py lint`.\n\n"
    "## Docs\n\nRecord rules the code cannot show in this workspace's docs folder.\n\n"
    "## Context map\n\n| When the task touches | Read |\n|---|---|\n"
)
gates = "".join(
    f'\n[[{kind}]]\nname = "{name}-{kind}"\nargv = {json.dumps(json.loads(argv))}\n'
    for kind, argv in (("lint", lint), ("test", test))
)
(ws / "workspace.toml").write_text(f'name = "{name}"\nremote = "{remote}"\n{gates}')
PY
}

publish() { # publish <name>: first commit of the product clone, pushed to a local bare remote
  git -C "$1/repo" add -A
  git -C "$1/repo" commit -q -m "Initial product"
  git init -q --bare "$remotes/$1.git"
  git -C "$1/repo" remote add origin "$remotes/$1.git"
  git -C "$1/repo" push -q origin main
}

say "3. product: pyapp (uv-managed Python)"
git init -q -b main pyapp/repo
cat >pyapp/repo/pyproject.toml <<'EOF'
[project]
name = "pyapp"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []

[dependency-groups]
dev = ["pytest==9.1.1", "ruff==0.17.0"]

[tool.uv]
package = false

[tool.ruff]
line-length = 100

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
EOF
printf '.venv/\n__pycache__/\n' >pyapp/repo/.gitignore
printf 'def add(a: int, b: int) -> int:\n    return a + b\n' >pyapp/repo/pyapp.py
mkdir pyapp/repo/tests
printf 'from pyapp import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n' \
  >pyapp/repo/tests/test_pyapp.py
(cd pyapp/repo && uv lock -q)
publish pyapp
workspace pyapp \
  '["uv", "run", "--locked", "ruff", "check", "--config", "pyproject.toml", "."]' \
  '["uv", "run", "--locked", "pytest", "-q", "-c", "pyproject.toml"]'
expect 0 uv run --locked python ai/scripts/workspace_profile.py pyapp/workspace.toml
products=(pyapp)

if command -v node >/dev/null && command -v npm >/dev/null; then
  say "3b. product: nodeapp (Node $(node --version), npm scripts)"
  git init -q -b main nodeapp/repo
  cat >nodeapp/repo/package.json <<'EOF'
{
  "name": "nodeapp",
  "version": "0.1.0",
  "private": true,
  "scripts": { "lint": "node --check index.js", "test": "node --test" }
}
EOF
  printf 'exports.add = (a, b) => a + b;\n' >nodeapp/repo/index.js
  mkdir nodeapp/repo/test
  printf 'const test = require("node:test");\nconst assert = require("node:assert");\n%s\n' \
    'test("add", () => assert.strictEqual(require("../index.js").add(2, 3), 5));' \
    >nodeapp/repo/test/add.test.js
  printf 'node_modules/\n' >nodeapp/repo/.gitignore
  (cd nodeapp/repo && npm install -q --no-audit --no-fund --ignore-scripts >/dev/null)
  publish nodeapp
  workspace nodeapp '["npm", "run", "lint"]' '["npm", "test"]'
  expect 0 uv run --locked python ai/scripts/workspace_profile.py nodeapp/workspace.toml
  products+=(nodeapp)
  say "UNTESTED: mise toolchain pinning for nodeapp (it would download a node); system node used"
else
  say "UNTESTED: no node and npm on this machine, so no non-Python product was exercised"
fi

say "4. product gates through check_code.py"
for p in "${products[@]}"; do
  expect 0 uv run --locked python ai/scripts/check_code.py lint --profile "$p/workspace.toml"
  expect 0 uv run --locked python ai/scripts/check_code.py test --profile "$p/workspace.toml"
done
git -C pyapp/repo worktree add -q ../.worktrees/broken -b broken
printf '\n\ndef test_seeded_failure() -> None:\n    assert add(2, 2) == 5\n' \
  >>pyapp/.worktrees/broken/tests/test_pyapp.py
expect 1 uv run --locked python ai/scripts/check_code.py test --profile pyapp/workspace.toml \
  --target pyapp/.worktrees/broken

say "5. secrets baseline, by the documented procedure"
git add -N .
uv run --locked detect-secrets scan --exclude-files '(\.venv|uv\.lock)' \
  --exclude-lines 'verified_commit = "[0-9a-f]{40}"' >.secrets.baseline
git add -N .secrets.baseline
entries=$("${py[@]}" -- python -c \
  "import json; print(sum(map(len, json.load(open('.secrets.baseline'))['results'].values())))")
say "a fresh install's baseline holds $entries finding(s)"
if [ "$entries" = 0 ]; then # seed an unaudited entry to show the audited check fails
  cp .secrets.baseline "$root/baseline"
  "${py[@]}" -- python - <<'PY'
import json

b = json.load(open(".secrets.baseline"))
b["results"]["README.md"] = [{"type": "AWS Access Key", "filename": "README.md",
                              "hashed_secret": "0" * 40, "is_verified": False, "line_number": 1}]
json.dump(b, open(".secrets.baseline", "w"), indent=2)
PY
  nonzero uv run --locked pre-commit run detect-secrets-audited --all-files
  cp "$root/baseline" .secrets.baseline
else
  nonzero uv run --locked pre-commit run detect-secrets-audited --all-files
  uv run --locked detect-secrets audit --report .secrets.baseline
  fail "the kit itself produces $entries unaudited baseline finding(s), listed above"
fi
expect 0 uv run --locked pre-commit run detect-secrets --all-files
expect 0 uv run --locked pre-commit run detect-secrets-audited --all-files

say "6. a real commit through the pre-commit hooks"
git add -A
expect 0 git commit -q -m "Set up framework"
head=$(git rev-parse HEAD)
printf 'aws_access_key_id = "%s"\n' "$fake_key" >ai/leak.md
git add ai/leak.md
nonzero git commit -q -m "Seeded secret"
[ "$(git rev-parse HEAD)" = "$head" ] || fail "the seeded secret was committed"
git rm -q --cached ai/leak.md
/bin/rm ai/leak.md

say "7. the shipped CI workflow's run steps, locally"
"${py[@]}" --with pyyaml==6.0.3 -- python - "$root/ci-steps" <<'PY'
import sys
from pathlib import Path

import yaml

workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())
out = Path(sys.argv[1])
out.mkdir()
for n, step in enumerate(s for job in workflow["jobs"].values() for s in job["steps"]):
    if "run" in step:
        (out / f"{n:02d}.sh").write_text(f"# {step.get('name', n)}\n{step['run']}\n")
PY
for step in "$root/ci-steps"/*.sh; do
  expect 0 bash -e "$step"
done

say "8. the pre-land secrets scan, as pre-land-review.md writes it"
"${py[@]}" -- python - >"$root/scan" <<'PY'
import re
from pathlib import Path

text = Path("docs/agent-workflows/pre-land-review.md").read_text()
match = re.search(r"`(fw=\$\(git rev-parse[^`]*detect-secrets-hook\))`", text)
if not match:
    raise SystemExit("smoke_kit: the pre-land secrets command was not found")
print(match.group(1).replace("<base>", "origin/main"))
PY
scan=$(cat "$root/scan")
lane() { # lane <name> <file> <text>: a pyapp lane with one committed change
  git -C pyapp/repo worktree add -q "../.worktrees/$1" -b "$1" origin/main
  printf '%s\n' "$3" >"pyapp/.worktrees/$1/$2"
  git -C "pyapp/.worktrees/$1" rm -q tests/test_pyapp.py # a deleted file must not break it
  git -C "pyapp/.worktrees/$1" add -A
  git -C "pyapp/.worktrees/$1" commit -q -m "$1"
}
lane clean "settings file.py" 'TIMEOUT = 30'
lane leaky "settings file.py" "AWS_KEY = \"$fake_key\""
expect 0 bash -c "cd pyapp && ${scan//<lane>/.worktrees/clean}"
nonzero bash -c "cd pyapp && ${scan//<lane>/.worktrees/leaky}"

say "9. verifier, garden, and an installer rerun over a customised framework"
expect 0 uv run --locked python ai/scripts/verify_agent_env.py
expect 0 uv run --locked python ai/scripts/garden.py --static
for p in "${products[@]}"; do
  expect 0 uv run --locked python ai/scripts/garden.py --workspace "$p"
done
printf '\nLocal note kept across kit updates.\n' >>docs/agent-workflows/maintenance.md
git commit -q -am "Customise a kit doc"
expect 0 "${install[@]}" --revision "$snap"
grep -q "Local note kept" docs/agent-workflows/maintenance.md || fail "customisation lost"
[ -z "$(git status --porcelain)" ] || fail "installer rerun changed the framework"

say "all checks passed (products: ${products[*]})"
