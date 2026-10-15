#!/bin/bash
# check_kit.sh: check the kit before using or changing it. Lints and tests install.py, eval/
# and probes/ (fixtures validated without a model; probes listed, never run), installs
# files/ (uncommitted changes included) into a scratch framework with it, then runs lint,
# tests, verifier and garden --static there. Minimal generated docs stand in for setup
# output. UV_PYTHON picks the Python (default: the newest >=3.11). Exit 0 only if all pass.
set -euo pipefail
kit="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap '/bin/rm -r "$work"' EXIT
py=(uv run --isolated --no-config --no-project --python "${UV_PYTHON:->=3.11}")
tools=(--with pytest==9.1.1 --with ruff==0.17.0 --with mypy==2.4.0)
ruff_args=(--isolated --line-length 100 --target-version py311)
(
  cd "$kit"
  kit_py=(install.py tests eval probes --exclude eval/fixtures)
  "${py[@]}" "${tools[@]}" -- ruff check "${ruff_args[@]}" --select E,F,I,B,UP,SIM "${kit_py[@]}"
  "${py[@]}" "${tools[@]}" -- ruff format "${ruff_args[@]}" --check "${kit_py[@]}"
  MYPYPATH=files/ai/scripts "${py[@]}" "${tools[@]}" -- mypy --strict --python-version 3.11 install.py tests \
    probes/run_probes.py eval/run.py eval/harness.py eval/grader.py eval/live.py \
    eval/summarize.py eval/unittest_json.py
  "${py[@]}" "${tools[@]}" -- pytest -q -p no:cacheprovider tests eval/tests
  "${py[@]}" -- python eval/run.py >/dev/null
  "${py[@]}" -- python probes/run_probes.py >/dev/null
)
"${py[@]}" -- python "$kit/install.py" --framework "$work" --prefix kt \
  --revision "$("$kit/kit_snapshot.sh")" >/dev/null
cd "$work"
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
