from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import check_code

SCRIPT = Path(__file__).resolve().parents[1] / "ai" / "scripts" / "check_code.py"
PASS = [sys.executable, "-c", "print('ok')"]
FAIL = [sys.executable, "-c", "import sys; sys.exit(3)"]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def gate(name: str, argv: list[str], kind: str = "lint") -> str:
    return f"[[{kind}]]\nname = {json.dumps(name)}\nargv = {json.dumps(argv)}\n"


def make_workspace(tmp_path: Path, gates: str, extra: str = "", commit: bool = True) -> Path:
    """A framework root with workspace `shop` and a git clone at shop/repo."""
    (tmp_path / "AGENTS.md").write_text("# fw\n", encoding="utf-8")
    (tmp_path / "ai" / "scripts").mkdir(parents=True)
    workspace = tmp_path / "shop"
    clone = workspace / "repo"
    clone.mkdir(parents=True)
    (workspace / "workspace.toml").write_text(
        f'name = "shop"\nremote = "x"\n{extra}{gates}', encoding="utf-8"
    )
    git(clone, "init", "-q", "-b", "main")
    if commit:
        (clone / "a.txt").write_text("a\n", encoding="utf-8")
        git(clone, "add", "a.txt")
        git(clone, "commit", "-q", "-m", "init")
    return workspace


def test_all_pass_header_and_timings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = make_workspace(tmp_path, gate("one", PASS) + gate("two", PASS))
    assert check_code.run(["lint"], cwd=ws) == 0
    out = capsys.readouterr().out.splitlines()
    sha = git(ws / "repo", "rev-parse", "--short", "HEAD")
    assert out[0] == f"target: {(ws / 'repo').resolve()}  commit: {sha}  branch: main  (clean)"
    assert any(line.startswith("<-- one: PASS in ") for line in out)
    assert any(line.startswith("<-- two: PASS in ") for line in out)
    assert out[-1].startswith("lint: PASS (2 ran; 2 passed)")


def test_failure_exits_1_and_runs_every_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = make_workspace(tmp_path, gate("bad", FAIL) + gate("good", PASS))
    assert check_code.run(["lint"], cwd=ws / "repo") == 1
    out = capsys.readouterr().out
    assert "<-- bad: FAIL (exit 3)" in out
    assert "<-- good: PASS" in out
    assert "lint: FAIL (2 ran; 1 passed; failed: bad)" in out


def test_baseline_failure_only_warns(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    failing = [sys.executable, "-c", "import sys; sys.exit(1)"]
    gates = gate("old", failing) + gate("fixed", PASS)
    ws = make_workspace(tmp_path, gates, 'baseline_failing = ["old", "fixed"]\n')
    assert check_code.run(["lint"], cwd=ws) == 0
    out = capsys.readouterr().out
    assert "<-- old: FAIL (exit 1), baseline: warning only" in out
    assert "fixed: PASS (listed in baseline_failing; remove it from the profile)" in out
    assert "baseline warnings: old" in out


def test_skip_is_printed_and_unknown_skip_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = make_workspace(tmp_path, gate("slow", FAIL) + gate("fast", PASS))
    assert check_code.run(["lint", "--skip", "slow"], cwd=ws) == 0
    out = capsys.readouterr().out
    assert "skip: slow (--skip)" in out
    assert "skipped: slow" in out
    assert "==> slow" not in out
    assert check_code.run(["lint", "--skip", "slwo"], cwd=ws) == 2
    assert "unknown lint gate(s) ['slwo']" in capsys.readouterr().err


def test_empty_plan_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = make_workspace(tmp_path, gate("only", PASS))
    assert check_code.run(["test"], cwd=ws) == 2
    assert "empty plan: the profile has no [[test]] gates" in capsys.readouterr().err
    assert check_code.run(["lint", "--skip", "only"], cwd=ws) == 2
    captured = capsys.readouterr()
    assert "empty plan: all gates skipped" in captured.err
    assert "lint: COULD NOT RUN (exit 2)" in captured.out


def test_missing_tool_exits_2_before_running(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = gate("ok", PASS) + gate("ghost", ["no-such-tool-xyz", "run"])
    gates += gate("rel", ["./bin/missing"])
    ws = make_workspace(tmp_path, gates)
    assert check_code.run(["lint"], cwd=ws) == 2
    captured = capsys.readouterr()
    assert "ghost: `no-such-tool-xyz` not found" in captured.err
    assert "rel: `./bin/missing` not found" in captured.err
    assert "==> ok" not in captured.out


def test_relative_tool_in_target(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = make_workspace(tmp_path, gate("local", ["./run.sh"]))
    script = ws / "repo" / "run.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    assert check_code.run(["lint"], cwd=ws) == 0
    assert "(dirty)" in capsys.readouterr().out.splitlines()[0]


def test_gate_that_cannot_start_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = make_workspace(tmp_path, gate("one", PASS))
    real_run = subprocess.run

    def fake_run(argv: list[str], **kwargs: object) -> object:
        if argv[0] == sys.executable:
            raise PermissionError("denied")
        return real_run(argv, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert check_code.run(["lint"], cwd=ws) == 2
    assert "gate one did not run: denied" in capsys.readouterr().err


def test_virtual_env_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    probe = [sys.executable, "-c", "import os, sys; sys.exit('VIRTUAL_ENV' in os.environ)"]
    ws = make_workspace(tmp_path, gate("env", probe))
    monkeypatch.setenv("VIRTUAL_ENV", "/framework/.venv")
    assert check_code.run(["lint"], cwd=ws) == 0
    capsys.readouterr()


def test_target_lane_detached_and_relative(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    probe = [sys.executable, "-c", "import os; print('cwd=' + os.getcwd())"]
    ws = make_workspace(tmp_path, gate("where", probe))
    lane = ws / ".worktrees" / "lane"
    git(ws / "repo", "worktree", "add", "-q", "--detach", str(lane))
    assert check_code.run(["lint", "--target", ".worktrees/lane"], cwd=ws / "repo") == 0
    out = capfd.readouterr().out
    assert out.startswith(f"target: {lane.resolve()}  commit: ")
    assert "branch: (detached)" in out
    assert f"cwd={lane.resolve()}" in out
    assert check_code.run(["lint", "--target", str(lane / "sub")], cwd=ws) == 2
    assert "target does not exist" in capfd.readouterr().err


def test_target_not_git_and_no_commits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = make_workspace(tmp_path, gate("one", PASS), commit=False)
    assert check_code.run(["lint"], cwd=ws) == 0
    assert "commit: (no commits)  branch: main" in capsys.readouterr().out
    outside = tmp_path.parent / f"{tmp_path.name}-plain"
    outside.mkdir()
    assert check_code.run(["lint", "--target", str(outside)], cwd=ws) == 2
    assert "not a git checkout" in capsys.readouterr().err


def test_target_inside_checkout_is_not_a_checkout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = make_workspace(tmp_path, gate("one", PASS))
    (ws / "repo" / "sub").mkdir()
    assert check_code.run(["lint", "--target", str(ws / "repo" / "sub")], cwd=ws) == 2
    assert "not a git checkout" in capsys.readouterr().err


def test_profile_option_and_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = make_workspace(tmp_path, gate("one", PASS))
    elsewhere = tmp_path.parent / f"{tmp_path.name}-else"
    elsewhere.mkdir()
    rel = Path("..") / tmp_path.name / "shop" / "workspace.toml"
    assert check_code.run(["lint", "--profile", str(rel)], cwd=elsewhere) == 0
    capsys.readouterr()
    assert check_code.run(["lint"], cwd=elsewhere) == 2
    assert "no workspace contains" in capsys.readouterr().err
    (ws / "workspace.toml").write_text('name = "shop"\nremote = "x"\nbogus = 1\n')
    assert check_code.run(["lint"], cwd=ws) == 2
    assert "unknown keys" in capsys.readouterr().err


def test_profile_found_without_framework_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = make_workspace(tmp_path, gate("one", PASS))
    (tmp_path / "AGENTS.md").unlink()
    assert check_code.run(["lint"], cwd=ws / "repo") == 0
    capsys.readouterr()


def test_git_status_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = make_workspace(tmp_path, gate("one", PASS))
    real_git = check_code._git

    def fake_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        if args[0] == "status":
            return subprocess.CompletedProcess(args, 128, "", "broken index")
        return real_git(root, *args)

    monkeypatch.setattr(check_code, "_git", fake_git)
    assert check_code.run(["lint"], cwd=ws) == 2
    assert "git status failed" in capsys.readouterr().err


def test_git_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = make_workspace(tmp_path, gate("one", PASS))

    def no_git(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", no_git)
    assert check_code.run(["lint"], cwd=ws) == 2
    assert "cannot run git" in capsys.readouterr().err


def test_script_runs_standalone(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path, gate("one", PASS) + gate("two", FAIL, "test"))
    lint = subprocess.run(
        [sys.executable, str(SCRIPT), "lint"], cwd=ws, capture_output=True, text=True
    )
    assert lint.returncode == 0, lint.stderr
    assert lint.stdout.startswith("target: ")
    test = subprocess.run(
        [sys.executable, str(SCRIPT), "test", "--profile", str(ws / "workspace.toml")],
        cwd=tmp_path.parent,
        capture_output=True,
        text=True,
    )
    assert test.returncode == 1
    bad = subprocess.run([sys.executable, str(SCRIPT), "deploy"], capture_output=True, text=True)
    assert bad.returncode == 2


def test_baseline_tolerates_only_ordinary_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = [sys.executable, "-c", "import sys; sys.exit(2)"]
    ws = make_workspace(tmp_path, gate("old", broken), 'baseline_failing = ["old"]\n')
    assert check_code.run(["lint"], cwd=ws) == 1
    assert "<-- old: FAIL (exit 2), not the baseline failure (exit 1)" in capsys.readouterr().out


def test_target_from_another_repository_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = make_workspace(tmp_path, gate("one", PASS))
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    assert check_code.run(["lint", "--target", str(other)], cwd=ws) == 2
    assert "not a checkout of shop" in capsys.readouterr().err
    scratch = tmp_path / "scratch"
    git(tmp_path, "clone", "-q", str(ws / "repo"), str(scratch))
    assert check_code.run(["lint", "--target", str(scratch)], cwd=ws) == 0
