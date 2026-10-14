"""Tests for garden.py: one per check, plus each mode, over a tiny framework in tmp_path."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

import garden

TODAY = date.fromtimestamp(time.time()).isoformat()
VERSIONS = {"claude": "1.0.0", "codex": "2.0.0"}

ROOT_AGENTS = """# fw

## Commands
- `uv run --locked poe lint|test`
- Read `ai/project.md`.

## Context map
| When the task touches | Read |
|---|---|
| maintenance | `docs/agent-workflows/maintenance.md` |
"""

WS_AGENTS = """# shipyard

- Gates: `uv run --locked poe check`
- Deploy is `scripts/deploy.sh`; the app starts in `repo/src/app.py`.
- Docs follow `../docs/agent-workflows/maintenance.md`.

## Context map
| When the task touches | Read |
|---|---|
| orders | `docs/orders.md` |
"""

RULES = """prefix_rule(
    pattern=[["bash", "sh"], ["scripts/deploy.sh", "./scripts/deploy.sh", "scripts/*"]],
    decision="forbidden",
    justification="deploys production",
)
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def git(cwd: Path, *args: str, days_ago: float = 0) -> str:
    env = dict(os.environ)
    if days_ago:
        stamp = f"@{int(time.time() - days_ago * 86400)} +0000"
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = stamp
    done = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env, check=True
    )
    return done.stdout


def commit(cwd: Path, message: str, days_ago: float = 0) -> None:
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", message, days_ago=days_ago)


@pytest.fixture
def fw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = write(tmp_path / "gitconfig", "")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "Test")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "test@example.com")
    monkeypatch.setattr(garden, "tool_version", VERSIONS.get)
    root = tmp_path / "fw"
    write(root / ".gitignore", "*/repo/\n*/.worktrees/\n")
    write(root / "AGENTS.md", ROOT_AGENTS)
    write(root / "README.md", "Setup: see [the rules](AGENTS.md) and `pyproject.toml`.\n")
    write(root / "ai/project.md", "# Facts\n")
    write(root / "ai/scripts/.gitkeep", "")
    write(root / "ai/tasks/.gitkeep", "")
    write(root / "pyproject.toml", '[tool.poe.tasks]\nlint = "ruff"\ntest = "pytest"\n')
    write(root / "docs/agent-workflows/maintenance.md", "Rerun `docs/dev/tool-probes.md`.\n")
    write(root / "docs/dev/tool-probes.md", f"Last run: {TODAY}, claude 1.0.0, codex-cli 2.0.0\n")
    skill = "Read `../../../docs/agent-workflows/maintenance.md` relative to this `SKILL.md`.\n"
    write(root / ".claude/skills/__P__-maintenance/SKILL.md", skill)
    write(root / ".agents/skills/__P__-maintenance/SKILL.md", skill)
    ws = root / "shipyard"
    write(ws / "workspace.toml", 'name = "shipyard"\nremote = "x"\n')
    write(ws / "AGENTS.md", WS_AGENTS)
    write(ws / "ai/project.md", "Orders live in `src/app.py`.\n")
    write(ws / "docs/orders.md", "Timestamps are UTC; see `src/app.py:3`.\n")
    deny = ["Bash(*scripts/deploy.sh*)", "Bash(./repo/scripts/deploy.sh:*)", "Bash(gh pr merge*)"]
    deny += ["Bash(*bin/*)", "Read(./**/.env)"]
    write(ws / ".claude/settings.json", json.dumps({"permissions": {"deny": deny}}))
    write(ws / ".codex/rules/shipyard.rules", RULES)
    clone = ws / "repo"
    clone.mkdir()
    git(clone, "init", "-q", "-b", "main")
    write(clone / "pyproject.toml", '[tool.poe.tasks]\ncheck = "ruff"\n')
    write(clone / "scripts/deploy.sh", "echo deploy\n")
    write(clone / "src/app.py", "print('hi')\n")
    commit(clone, "Initial")
    git(root, "init", "-q", "-b", "main")
    commit(root, "Framework")
    return root


def run(root: Path, capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = garden.main(["--root", str(root), *args])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def kinds(out: str, kind: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(f"{kind}: ")]


def test_clean_framework_has_no_findings(fw: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(fw, capsys)
    assert (code, out.strip()) == (0, "garden: no findings")


def test_path_flags_missing_citations_and_skips_non_paths(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(
        fw / "docs/agent-workflows/maintenance.md",
        "Run `docs/dev/missing.md:12`, [gone](../docs/gone.md), and\n"
        "`ai/old.py` garden: ignore\n"
        "Skip `~/x`, `/etc/hosts`, `https://x.io/docs/a`, `docs/<name>.md`, `docs/*.md`.\n"
        "```\nuv run python ai/scripts/nope.py --flag\n```\n"
        "Link [ok](../dev/tool-probes.md) and `docs/dev/` exist.\n",
    )
    write(fw / "ai/tasks/plan.md", "History cites `docs/never.md`.\n")
    code, out = run(fw, capsys)
    found = kinds(out, "path")
    assert code == 1
    assert len(found) == 3, out
    assert "docs/agent-workflows/maintenance.md:1: `docs/dev/missing.md`" in found[0]
    assert any("`ai/scripts/nope.py`" in f and ":5:" in f for f in found)
    assert all("never.md" not in f and "ai/old.py" not in f for f in found)


def test_path_in_workspace_rejects_other_branches_but_resolves_workspace_and_framework(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = fw / "shipyard/repo"
    git(clone, "checkout", "-q", "-b", "feature")
    write(clone / "src/feature.py", "x = 1\n")
    commit(clone, "Feature")
    git(clone, "checkout", "-q", "main")
    write(
        fw / "shipyard/docs/orders.md",
        "See `src/feature.py`, `src/gone.py`, `ai/project.md`, `README.md`,\n"
        "`../../docs/agent-workflows/maintenance.md` and `../../docs/lost.md`.\n",
    )
    code, out = run(fw, capsys)
    found = kinds(out, "path")
    assert code == 1
    assert [f.split("`")[1] for f in found] == [
        "src/feature.py",
        "src/gone.py",
        "../../docs/lost.md",
    ]


def test_path_ignores_slash_tokens_outside_known_trees(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(
        fw / "docs/agent-workflows/maintenance.md",
        "Rerun `docs/dev/tool-probes.md`.\n"
        "A product's `scripts/deploy.sh`; import `lodash/fp.js`; GET `v1/orders.json`.\n",
    )
    write(fw / "shipyard/docs/orders.md", "The image ships `library/nginx.conf`.\n")
    code, out = run(fw, capsys)
    assert (code, out.strip()) == (0, "garden: no findings")


def test_path_counts_live_lanes_but_not_remote_refs(
    fw: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = fw / "shipyard/repo"
    for name in ("a", "b"):
        git(clone, "worktree", "add", "-q", "-b", name, f"../.worktrees/{name}")
        write(fw / f"shipyard/.worktrees/{name}/src/{name}_feature.py", "x = 1\n")
        commit(fw / f"shipyard/.worktrees/{name}", name)
        write(fw / f"shipyard/docs/{name}.md", f"Lives in `src/{name}_feature.py`.\n")
    git(clone, "checkout", "-q", "-b", "remote-only")
    write(clone / "src/remote.py", "x = 1\n")
    commit(clone, "Remote only")
    git(clone, "update-ref", "refs/remotes/origin/remote-only", "HEAD")
    git(clone, "checkout", "-q", "main")
    git(clone, "branch", "-D", "remote-only")
    routes = "| a | `docs/a.md` |\n| b | `docs/b.md` |\n"
    write(fw / "shipyard/AGENTS.md", WS_AGENTS + routes + "Remote: `src/remote.py`.\n")
    code, out = run(fw, capsys)
    assert [f.split("`")[1] for f in kinds(out, "path")] == ["src/remote.py"], out
    code, out = run(fw, capsys, "--target", str(fw / "shipyard/.worktrees/a"))
    assert [f.split("`")[1] for f in kinds(out, "path")] == ["src/remote.py"], out
    monkeypatch.chdir(fw / "shipyard")
    code, out = run(fw, capsys, "--session")
    assert "path 1" in out, out


def test_command_flags_unknown_poe_tasks_and_package_scripts(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(fw / "AGENTS.md", ROOT_AGENTS + "- `uv run poe garden` and `npm run build`\n")
    agents = WS_AGENTS + "- `uv run poe lint`, `pnpm install`, `pnpm typecheck`, `pnpm dev`\n"
    agents += "- `uv run --project .. poe test`\n"
    write(fw / "shipyard/AGENTS.md", agents)
    write(fw / "shipyard/repo/package.json", json.dumps({"scripts": {"typecheck": "tsc"}}))
    code, out = run(fw, capsys)
    found = kinds(out, "command")
    assert code == 1
    assert len(found) == 4, out
    assert "`poe garden` is not a task in pyproject.toml;" in found[0]
    assert "package.json (the file is missing)" in found[1]
    assert "`poe lint` is not a task in shipyard/repo/pyproject.toml" in found[2]
    assert "`pnpm dev` names no script in shipyard/repo/package.json;" in found[3]


def test_command_skips_npm_and_pnpm_flags(fw: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(fw / "AGENTS.md", ROOT_AGENTS + "- `npm run -s lint`\n")
    write(fw / "package.json", json.dumps({"scripts": {"lint": "eslint"}}))
    # A package selector names another package.json, which garden does not read.
    agents = WS_AGENTS + "- `pnpm -C web build`, `pnpm --filter app dev`, `pnpm -F app -r ship`\n"
    agents += "- `pnpm -C web run build`, `npm --prefix web run x`, `npm -w app run y`\n"
    agents += "- `pnpm -r ship`\n"
    write(fw / "shipyard/AGENTS.md", agents)
    write(fw / "shipyard/repo/package.json", json.dumps({"scripts": {"build": "b", "dev": "d"}}))
    code, out = run(fw, capsys)
    found = kinds(out, "command")
    assert code == 1
    assert len(found) == 1, out
    assert "`pnpm -r ship` names no script in shipyard/repo/package.json;" in found[0]


def test_finished_task_list(fw: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(fw / "ai/tasks/done.md", "Target: repo\n- [x] one\n- [-] two (dropped: moot)\n")
    write(fw / "shipyard/ai/tasks/notes.md", "No checkboxes here.\n")
    code, out = run(fw, capsys)
    assert code == 1
    assert kinds(out, "finished") == [
        "finished: ai/tasks/done.md: every item is done or dropped; run the work-management"
        " conclusion and move it to ai/archive/<feature>/"
    ]


def test_idle_task_list_dated_by_commit_or_file_time(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = write(fw / "ai/tasks/old.md", "Target: repo\n- [ ] open\n")
    edited = write(fw / "shipyard/ai/tasks/edited.md", "- [ ] open\n")
    commit(fw, "Tasks", days_ago=30)
    edited.write_text("- [ ] open\n- [ ] more\n", encoding="utf-8")
    stale = write(fw / "ai/tasks/untracked.md", "- [ ] open\n")
    past = time.time() - 20 * 86400
    os.utime(stale, (past, past))
    code, out = run(fw, capsys)
    found = kinds(out, "idle")
    assert code == 1
    assert [f.split(":")[1].strip() for f in found] == ["ai/tasks/old.md", "ai/tasks/untracked.md"]
    assert "1 open items, unchanged for 30 days" in found[0]
    assert old.exists()


def test_handoff_older_than_14_days(fw: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(fw / "ai/tasks/handoff_20200101_0930.md", "- [ ] next\n")
    recent = (date.today() - timedelta(days=2)).strftime("%Y%m%d")
    write(fw / "shipyard/ai/tasks" / f"handoff_{recent}_0900.md", "state\n")
    write(fw / "ai/tasks/handoff_20201399_0000.md", "bad date, ignored\n")
    code, out = run(fw, capsys)
    found = kinds(out, "handoff")
    assert code == 1
    assert len(found) == 1 and found[0].startswith("handoff: ai/tasks/handoff_20200101_0930.md")


def test_worktree_merged_idle_fresh_and_broken(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = fw / "shipyard/repo"
    git(clone, "worktree", "add", "-q", "-b", "done", "../.worktrees/done")
    lane = fw / "shipyard/.worktrees/done"
    write(lane / "src/new.py", "y = 2\n")
    commit(lane, "Done")
    git(clone, "merge", "-q", "--ff-only", "done")
    git(clone, "worktree", "add", "-q", "-b", "fresh", "../.worktrees/fresh")
    git(clone, "worktree", "add", "-q", "-b", "slow", "../.worktrees/slow")
    write(fw / "shipyard/.worktrees/slow/src/slow.py", "z = 3\n")
    commit(fw / "shipyard/.worktrees/slow", "Slow", days_ago=20)
    (fw / "shipyard/.worktrees/junk").mkdir()
    code, out = run(fw, capsys)
    found = kinds(out, "worktree")
    assert code == 1
    assert len(found) == 3, out
    assert "worktree: shipyard/.worktrees/done: done is merged into main;" in found[0]
    assert "git -C shipyard/repo worktree remove ../.worktrees/done" in found[0]
    assert found[1].startswith("worktree: shipyard/.worktrees/junk: not a git checkout")
    assert "slow has had no commit for 20 days" in found[2]


def test_one_unreadable_lane_does_not_stop_the_others(
    fw: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = fw / "shipyard/repo"
    for name in ("a-broken", "b-slow"):
        git(clone, "worktree", "add", "-q", "-b", name, f"../.worktrees/{name}")
        write(fw / f"shipyard/.worktrees/{name}/src/{name}.py", "x = 1\n")
        commit(fw / f"shipyard/.worktrees/{name}", name, days_ago=20)
    real = garden.git_out

    def flaky(cwd: Path, *args: str, index: bool = False) -> str:
        if cwd.name == "a-broken" and args[0] == "log":
            raise garden.GardenError("git log in a-broken: bad object")
        return real(cwd, *args, index=index)

    monkeypatch.setattr(garden, "git_out", flaky)
    code, out = run(fw, capsys)
    found = kinds(out, "worktree")
    assert code == 2
    assert "error: shipyard/.worktrees/a-broken: git log in a-broken: bad object" in out
    assert len(found) == 1, out
    assert "b-slow has had no commit for 20 days" in found[0]


def test_contents_for_long_docs(fw: Path, capsys: pytest.CaptureFixture[str]) -> None:
    body = "\n".join(f"line {i}" for i in range(120))
    write(fw / "docs/agent-workflows/long.md", body)
    write(fw / "docs/agent-workflows/listed.md", "# T\n\n## Contents\n" + body)
    links = "- [A](#a)\n- [B](#b)\n"
    write(fw / "shipyard/docs/linked.md", "# T\n" + links + body)
    write(
        fw / "docs/agent-workflows/maintenance.md",
        "`long.md` `listed.md` `../dev/tool-probes.md`\n",
    )
    write(fw / "shipyard/docs/orders.md", "`docs/linked.md`\n")
    code, out = run(fw, capsys)
    assert code == 1
    assert [f.split(":")[1].strip() for f in kinds(out, "contents")] == [
        "docs/agent-workflows/long.md"
    ]
    assert kinds(out, "unrouted") == []


def test_unrouted_docs_follow_chains_not_folders(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(
        fw / "docs/agent-workflows/maintenance.md",
        "Then [b](../guidelines/b.md) and [probes](../dev/tool-probes.md).\n",
    )
    write(fw / "docs/guidelines/b.md", "Leads to `docs/guidelines/c.md`.\n")
    write(fw / "docs/guidelines/c.md", "End.\n")
    write(fw / "docs/orphan/x.md", "Nobody cites me.\n")
    write(fw / "README.md", "Folders route nothing: `docs/orphan/`.\n")
    write(fw / "shipyard/docs/lonely.md", "Alone.\n")
    write(fw / "shipyard/repo/docs/team.md", "Clone docs are never scanned.\n")
    code, out = run(fw, capsys)
    found = kinds(out, "unrouted")
    assert code == 1
    assert [f.split(":")[1].strip() for f in found] == [
        "docs/orphan/x.md",
        "shipyard/docs/lonely.md",
    ]
    assert "route from shipyard/AGENTS.md or a reachable domain index" in found[1]


def test_map_rows_pointing_at_folders(fw: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(fw / "AGENTS.md", ROOT_AGENTS + "| guides | `docs/guidelines/` |\n\n## After\n`docs/`\n")
    rows = "| code | `repo/src` |\n| team | `repo` |\n| more orders | `docs/` |\n"
    write(fw / "shipyard/AGENTS.md", WS_AGENTS + rows)
    code, out = run(fw, capsys)
    found = kinds(out, "map")
    assert code == 1
    assert len(found) == 2, out
    assert found[0].startswith("map: AGENTS.md:11: the row points at the folder `docs/guidelines/`")
    assert found[1].startswith("map: shipyard/AGENTS.md:13: the row points at the folder `docs/`")


def test_deny_targets_missing_from_default_branch(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = fw / "shipyard/repo"
    git(clone, "mv", "scripts/deploy.sh", "scripts/ship.sh")
    commit(clone, "Rename deploy")
    write(fw / "shipyard/AGENTS.md", WS_AGENTS.replace("scripts/deploy.sh", "scripts/ship.sh"))
    code, out = run(fw, capsys)
    found = kinds(out, "deny")
    assert code == 1
    assert [f.split(": ")[1] for f in found] == [
        "shipyard/.claude/settings.json",
        "shipyard/.codex/rules/shipyard.rules:2",
    ]
    assert "denies `scripts/deploy.sh`, which main no longer has" in found[0]


def test_deny_target_parsing() -> None:
    assert garden.deny_target("*scripts/deploy.sh*", "repo/") == "scripts/deploy.sh"
    assert garden.deny_target("./repo/scripts/deploy.sh:*", "repo/") == "scripts/deploy.sh"
    assert garden.deny_target("merge", "repo/") is None
    assert garden.deny_target("scripts/*.sh", "repo/") is None
    assert garden.deny_target("scripts/", "repo/") is None


def test_clean_normalises_words() -> None:
    assert garden.clean("(docs/a.md:12),", False) == "docs/a.md"
    assert garden.clean("./ai/x.py", False) == "ai/x.py"
    assert garden.clean("docs/a.md#part", True) == "docs/a.md"
    assert garden.clean("#part", True) is None
    assert garden.clean("..", False) is None
    assert garden.clean("mailto:a@b.c", True) is None


@pytest.mark.parametrize(
    ("probes", "versions", "expect"),
    [
        (f"Last run: {TODAY}, claude 1.0.1, codex-cli 2.0.0\n", VERSIONS, "claude is 1.0.0"),
        ("Last run: 2020-01-01, claude 1.0.0, codex-cli 2.0.0\n", VERSIONS, "days ago"),
        ("Last run: 2020-13-01, claude 1.0.0, codex-cli 2.0.0\n", VERSIONS, "is invalid"),
        ("| claim | result |\n", VERSIONS, "has no `Last run:"),
        (None, VERSIONS, "is missing"),
    ],
)
def test_tools_flags_version_age_and_missing_line(
    fw: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    probes: str | None,
    versions: dict[str, str],
    expect: str,
) -> None:
    path = fw / "docs/dev/tool-probes.md"
    if probes is None:
        path.unlink()
        write(fw / "docs/agent-workflows/maintenance.md", "Probes are rerun by hand.\n")
    else:
        path.write_text(probes, encoding="utf-8")
    monkeypatch.setattr(garden, "tool_version", versions.get)
    code, out = run(fw, capsys)
    found = kinds(out, "tools")
    assert code == 1
    assert len(found) == 1 and expect in found[0], out
    assert "docs/agent-workflows/maintenance.md" in found[0]


def test_tools_missing_cli_is_a_note(
    fw: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(garden, "tool_version", {"claude": "1.0.0"}.get)
    code, out = run(fw, capsys)
    assert code == 0
    assert "note: tools: `codex --version` gave no version; comparison skipped" in out


def test_tool_version_reads_real_cli() -> None:
    assert garden.tool_version("git") is not None
    assert garden.tool_version("no-such-cli-garden-test") is None


def test_session_summarises_and_lists_active_task_lists(
    fw: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    write(fw / "ai/tasks/setup.md", "# Setup\nTarget: repo (branch main)\n- [ ] step\n")
    write(fw / "shipyard/ai/tasks/x.md", "- [ ] no target\n")
    write(fw / "ai/tasks/done.md", "- [x] all\n")
    write(fw / "README.md", "`docs/missing.md`\n")
    monkeypatch.setattr(garden, "tool_version", {"claude": "9.9.9"}.get)
    code, out = run(fw, capsys, "--session")
    lines = out.splitlines()
    assert code == 0
    assert lines[0] == (
        "garden: 3 findings (path 1, finished 1, tools 1); run `uv run poe garden` for"
        " details and offer the fix"
    )
    assert lines[1].startswith("garden: maintenance due (docs/dev/tool-probes.md): claude is 9.9.9")
    assert lines[2:] == [
        "active: ai/tasks/setup.md (Target: repo (branch main))",
        "active: shipyard/ai/tasks/x.md (no Target: line; add one naming the checkout and branch)",
    ]


def test_session_inside_a_workspace_covers_only_it(
    fw: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    write(fw / "other/workspace.toml", 'name = "other"\nremote = "y"\n')
    write(fw / "other/ai/tasks/t.md", "- [ ] other work\n")
    write(fw / "other/AGENTS.md", "Code is in `src/x.py`.\n")
    monkeypatch.chdir(fw / "shipyard")
    code, out = run(fw, capsys, "--session")
    assert (code, out) == (0, "")
    monkeypatch.chdir(fw)
    code, out = run(fw, capsys, "--session")
    assert code == 0
    assert "could not check: other/repo: clone missing" in out
    assert "active: other/ai/tasks/t.md" in out


def test_session_always_exits_zero(
    fw: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: garden.Garden, checks: object) -> None:
        raise RuntimeError("internal")

    monkeypatch.setattr(garden.Garden, "run", boom)
    code, out = run(fw, capsys, "--session")
    assert code == 0
    assert out == "garden: could not run (internal); run `uv run poe garden` for details\n"
    assert garden.main(["--session", "--bogus"]) == 0
    capsys.readouterr()


def test_static_checks_only_tracked_framework_paths(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(fw / "docs/agent-workflows/new.md", "`docs/nothing.md`\n")
    write(fw / "README.md", "`docs/untracked.md` `ai/project.md`\n")
    write(fw / "docs/untracked.md", "on disk only\n")
    write(fw / "AGENTS.md", ROOT_AGENTS + "- `uv run poe gone`\n")
    write(fw / "shipyard/AGENTS.md", WS_AGENTS + "- `poe missing` `repo/none.py`\n")
    git(fw, "add", "README.md", "AGENTS.md", "shipyard/AGENTS.md")
    shutil_clone = fw / "shipyard/repo"
    subprocess.run(["rm", "-rf", str(shutil_clone)], check=True)
    code, out = run(fw, capsys, "--static")
    assert code == 1
    assert [f.split("`")[1] for f in kinds(out, "path")] == ["docs/untracked.md"]
    assert [f.split("`")[1] for f in kinds(out, "command")] == ["poe gone"]
    assert "garden: 2 findings" in out


def test_static_does_not_resolve_workspace_citations_against_framework_only(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Without a clone, `tests/...` in a workspace doc may name the product's tests,
    # even though the framework also has a top-level `tests/`.
    write(fw / "shipyard/repo/tests/test_orders.py", "def test_x() -> None: ...\n")
    commit(fw / "shipyard/repo", "Add tests")
    write(fw / "tests/test_garden.py", "")
    doc = "UTC; see `tests/test_orders.py`, `docs/gone.md` and [notes](sub/gone.md).\n"
    write(fw / "shipyard/docs/orders.md", doc)
    git(fw, "add", "tests/test_garden.py", "shipyard/docs/orders.md")
    expected = ["docs/gone.md", "sub/gone.md"]
    code, out = run(fw, capsys)
    assert [f.split("`")[1] for f in kinds(out, "path")] == expected, out
    subprocess.run(["rm", "-rf", str(fw / "shipyard/repo")], check=True)
    code, out = run(fw, capsys, "--static")
    assert [f.split("`")[1] for f in kinds(out, "path")] == expected, out


def test_static_needs_a_git_repo(fw: Path, capsys: pytest.CaptureFixture[str]) -> None:
    subprocess.run(["rm", "-rf", str(fw / ".git")], check=True)
    code, out = run(fw, capsys, "--static")
    assert code == 2
    assert "error: paths: git ls-files" in out


def test_changes_flags_paths_the_lane_removed(fw: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clone = fw / "shipyard/repo"
    git(clone, "worktree", "add", "-q", "-b", "move", "../.worktrees/move")
    lane = fw / "shipyard/.worktrees/move"
    git(lane, "mv", "src/app.py", "src/main.py")
    commit(lane, "Move app")
    write(fw / "README.md", "Framework docs are not this lane's: `src/app.py`.\n")
    code, out = run(fw, capsys, "--changes", "main", "--target", str(lane))
    found = kinds(out, "changes")
    assert code == 1
    assert len(found) == 3, out
    assert found[0].startswith("changes: shipyard/AGENTS.md:4: cites `repo/src/app.py`")
    code, out = run(fw, capsys, "--changes", "main", "--target", str(clone))
    assert (code, out.strip()) == (0, "garden: no findings")
    code, out = run(fw, capsys, "--changes", "nope", "--target", str(lane))
    assert code == 2 and "nope" in out
    code, out = run(fw, capsys, "--changes", "main", "--target", str(fw))
    assert code == 2 and "not inside a workspace" in out


def context_doc(revision: str, *paths: str) -> str:
    return (
        "# Orders\nRead when changing timestamps.\n\n```context\n"
        f"depends_on = {json.dumps(paths or ('src/app.py',))}\n"
        f'verified_commit = "{revision}"\n```\n\nTimestamps are UTC.\n'
    )


def test_deleted_path_is_not_rescued_by_old_branch(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = fw / "shipyard/repo"
    git(clone, "branch", "old-release")
    git(clone, "rm", "src/app.py")
    commit(clone, "Remove entrypoint and its directory")
    code, out = run(fw, capsys)
    assert code == 1
    assert len(kinds(out, "path")) == 3, out


def test_full_report_selects_lane_for_paths_and_commands(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = fw / "shipyard/repo"
    git(clone, "worktree", "add", "-q", "-b", "move", "../.worktrees/move")
    lane = fw / "shipyard/.worktrees/move"
    (lane / "src/app.py").unlink()
    write(lane / "pyproject.toml", '[tool.poe.tasks]\nreplacement = "ruff"\n')
    code, out = run(fw, capsys, "--target", str(lane))
    assert code == 1
    assert len(kinds(out, "path")) == 3, out
    assert len(kinds(out, "command")) == 1, out
    code, out = run(fw, capsys)
    assert (code, out.strip()) == (0, "garden: no findings")


def test_target_rejects_unrelated_repository(fw: Path, capsys: pytest.CaptureFixture[str]) -> None:
    impostor = fw / "shipyard/.worktrees/unrelated"
    impostor.mkdir(parents=True)
    git(impostor, "init", "-q", "-b", "main")
    code, out = run(fw, capsys, "--target", str(impostor))
    assert code == 2 and "not a worktree of" in out


def test_static_routing_cannot_follow_untracked_bridge(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(fw / "docs/domain/index.md", "Read [policy](policy.md).\n")
    write(fw / "docs/domain/policy.md", "A consequential constraint.\n")
    write(fw / "AGENTS.md", ROOT_AGENTS + "\nRead `docs/domain/index.md`.\n")
    git(fw, "add", "AGENTS.md", "docs/domain/policy.md")
    code, out = run(fw, capsys, "--static")
    assert code == 1 and "docs/domain/policy.md" in "\n".join(kinds(out, "unrouted"))
    git(fw, "add", "docs/domain/index.md")
    code, out = run(fw, capsys, "--static")
    assert (code, out.strip()) == (0, "garden: no findings")


@pytest.mark.parametrize(
    "state", ["committed", "staged", "staged-only", "unstaged", "untracked", "deleted"]
)
def test_dependency_changes_request_review(
    fw: Path, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    clone = fw / "shipyard/repo"
    revision = git(clone, "rev-parse", "HEAD").strip()
    write(fw / "shipyard/docs/orders.md", context_doc(revision, "src"))
    code, out = run(fw, capsys)
    assert (code, out.strip()) == (0, "garden: no findings")
    original = (clone / "src/app.py").read_text()
    if state == "deleted":
        (clone / "src/app.py").unlink()
    elif state == "untracked":
        write(clone / "src/new.py", "TIMEZONE = 'local'\n")
    else:
        write(clone / "src/app.py", "TIMEZONE = 'local'\n")
        if state == "committed":
            commit(clone, "Change timestamp behavior")
        elif state.startswith("staged"):
            git(clone, "add", "src/app.py")
        if state == "staged-only":
            # The index changes the dependency; the working tree matches the verified commit.
            write(clone / "src/app.py", original)
    code, out = run(fw, capsys, "--changes", revision, "--target", str(clone))
    assert code == 1 and len(kinds(out, "freshness")) == 1, out
    assert "recheck claims" in out


def test_freshness_ignores_unrelated_changes_and_accepts_verified_update(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = fw / "shipyard/repo"
    revision = git(clone, "rev-parse", "HEAD").strip()
    doc = fw / "shipyard/docs/orders.md"
    write(doc, context_doc(revision))
    write(clone / "unrelated.txt", "Unrelated new file.\n")
    code, out = run(fw, capsys)
    assert (code, out.strip()) == (0, "garden: no findings")
    write(clone / "src/app.py", "TIMEZONE = 'UTC'\n")
    commit(clone, "Make timezone explicit")
    write(doc, context_doc(git(clone, "rev-parse", "HEAD").strip()))
    code, out = run(fw, capsys)
    assert (code, out.strip()) == (0, "garden: no findings")


def test_freshness_uses_selected_lane_and_session_checkout(
    fw: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = fw / "shipyard/repo"
    revision = git(clone, "rev-parse", "HEAD").strip()
    write(fw / "shipyard/docs/orders.md", context_doc(revision))
    git(clone, "worktree", "add", "-q", "-b", "change", "../.worktrees/change")
    lane = fw / "shipyard/.worktrees/change"
    write(lane / "src/app.py", "TIMEZONE = 'local'\n")
    code, out = run(fw, capsys, "--target", str(lane))
    assert code == 1 and len(kinds(out, "freshness")) == 1, out
    monkeypatch.chdir(lane / "src")
    code, out = run(fw, capsys, "--session")
    assert code == 0 and "freshness 1" in out


def test_missing_verification_commit_is_not_a_pass(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(fw / "shipyard/docs/orders.md", context_doc("f" * 40))
    code, out = run(fw, capsys)
    assert code == 1 and len(kinds(out, "freshness")) == 1, out
    assert "not in" in out and "update verified_commit" in out


def test_squash_merged_and_collected_commit_requests_reverification(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = fw / "shipyard/repo"
    git(clone, "worktree", "add", "-q", "-b", "feat", "../.worktrees/feat")
    lane = fw / "shipyard/.worktrees/feat"
    write(lane / "src/app.py", "print('utc')\n")
    commit(lane, "Lane work")
    write(fw / "shipyard/docs/orders.md", context_doc(git(lane, "rev-parse", "HEAD").strip()))
    git(clone, "merge", "--squash", "feat")
    git(clone, "commit", "-q", "-m", "Squash")
    git(clone, "worktree", "remove", "../.worktrees/feat")
    git(clone, "branch", "-D", "feat")
    git(clone, "reflog", "expire", "--expire=now", "--all")
    git(clone, "gc", "-q", "--prune=now")
    code, out = run(fw, capsys)
    assert code == 1 and len(kinds(out, "freshness")) == 1, out
    assert "re-verify" in out


def test_ignored_dependency_is_a_metadata_error(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = fw / "shipyard/repo"
    write(clone / ".gitignore", "dist/\n")
    commit(clone, "Ignore build output")
    write(clone / "dist/out.js", "x\n")
    revision = git(clone, "rev-parse", "HEAD").strip()
    write(fw / "shipyard/docs/orders.md", context_doc(revision, "src/app.py", "dist"))
    code, out = run(fw, capsys)
    assert code == 1 and kinds(out, "freshness") == [], out
    assert kinds(out, "context") == [
        "context: shipyard/docs/orders.md: depends_on path is ignored by git; cannot track:"
        " dist; drop it from depends_on"
    ], out


def test_unverified_context_and_missing_dependency_need_review(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = fw / "shipyard/docs/orders.md"
    write(doc, context_doc("unverified"))
    code, out = run(fw, capsys)
    assert code == 1 and "not verified" in out
    revision = git(fw / "shipyard/repo", "rev-parse", "HEAD").strip()
    write(doc, context_doc(revision, "missing/file.py"))
    code, out = run(fw, capsys)
    assert code == 1 and "missing/file.py" in "\n".join(kinds(out, "freshness"))


def test_static_validates_context_syntax_without_product_history(
    fw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = fw / "shipyard/docs/orders.md"
    write(doc, context_doc("f" * 40))
    git(fw, "add", "shipyard/docs/orders.md")
    code, out = run(fw, capsys, "--static")
    assert (code, out.strip()) == (0, "garden: no findings")
    write(doc, context_doc("main"))
    code, out = run(fw, capsys, "--static")
    assert code == 1 and "full commit hash" in out


@pytest.mark.parametrize(
    "bad",
    [
        "```context\ndepends_on = [\n```",
        '```context\ndepends_on = []\nverified_commit = "unverified"\n```',
        context_doc("unverified", "../outside"),
        context_doc("unverified", "src/*.py"),
        context_doc("unverified") + context_doc("unverified"),
        '```context\ndepends_on = ["src/app.py"]',
    ],
)
def test_invalid_context_metadata(bad: str) -> None:
    with pytest.raises(ValueError):
        garden.context_metadata(bad)


def test_context_examples_inside_outer_fence_are_not_metadata() -> None:
    assert garden.context_metadata("````markdown\n" + context_doc("unverified") + "````\n") is None


def test_could_not_run_exits_2(fw: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(fw, capsys, "--workspace", "nope")
    assert code == 2 and "no loadable workspace named 'nope'" in out
    write(fw / "broken/workspace.toml", 'name = "wrong"\n')
    code, out = run(fw, capsys, "--workspace", "shipyard")
    assert code == 2 and "fix the profile" in out
    with pytest.raises(SystemExit):
        garden.main(["--static", "--target", "x"])
    capsys.readouterr()


def test_missing_clone_is_could_not_run(fw: Path, capsys: pytest.CaptureFixture[str]) -> None:
    subprocess.run(["rm", "-rf", str(fw / "shipyard/repo")], check=True)
    code, out = run(fw, capsys)
    assert code == 2
    assert "shipyard/repo: clone missing; restore it with the workspace-setup skill" in out


def test_invalid_settings_json_is_reported(fw: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(fw / "shipyard/.claude/settings.json", "{")
    code, out = run(fw, capsys)
    assert code == 2 and "error: deny:" in out and "invalid JSON" in out


def test_runs_as_a_script(fw: Path) -> None:
    script = Path(garden.__file__)
    done = subprocess.run(
        [sys.executable, str(script), "--root", str(fw), "--static"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (done.returncode, done.stdout.strip()) == (0, "garden: no findings"), done.stderr
