#!/bin/bash
# check_kit.sh: check the kit before using or changing it. Copies files/ into a scratch
# framework, substitutes a prefix, then runs lint, tests, verifier and garden --static.
# Minimal generated docs stand in for setup output. Exit 0 only if all pass.
set -euo pipefail
kit="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap '/bin/rm -r "$work"' EXIT
uv run --isolated --no-config --no-project --python ">=3.11" -- python - "$kit/files" "$work" <<'PY'
import shutil
import sys

shutil.copytree(sys.argv[1], sys.argv[2], dirs_exist_ok=True, ignore=shutil.ignore_patterns(
    "__pycache__", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".coverage",
))
PY
cd "$work"
for d in .claude/skills .agents/skills; do
  for s in "$d"/__P__-*; do mv "$s" "$d/kt-${s##*/__P__-}"; done
done
uv run --isolated --no-config --no-project --python ">=3.11" -- python - <<'PY'
from pathlib import Path

for path in Path(".").rglob("*"):
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "__P__" in text:
            path.write_text(text.replace("__P__", "kt"), encoding="utf-8")
PY
git init -q -b main .
uv lock -q && uv sync -q --locked
uv run --locked poe lint
uv run --locked poe test
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
git add -A
uv run --locked python ai/scripts/verify_agent_env.py
uv run --locked python ai/scripts/garden.py --static
echo "check_kit: all checks passed"
