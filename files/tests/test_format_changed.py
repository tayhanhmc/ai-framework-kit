from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import format_changed
from workspace_profile import ProfileError

FW = Path(__file__).resolve().parents[1]
SCRIPTS = FW / "ai" / "scripts"
UGLY = "x=[1,\n2]\ndef f( a ):\n    return a\n"
PRETTY = "x = [1, 2]\n\n\ndef f(a):\n    return a\n"
LONG = "value = some_function_name(argument_one, argument_two)\n"


def make_framework(root: Path, real_tools: bool = False) -> Path:
    """A framework root with workspace `shop`, its clone and one lane."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text("# fw\n", encoding="utf-8")
    (root / "ai" / "scripts").mkdir(parents=True)
    workspace = root / "shop"
    workspace.mkdir()
    (workspace / "workspace.toml").write_text(
        'name = "shop"\nremote = "x"\nformatters = ["ruff"]\n', encoding="utf-8"
    )
    for checkout in (workspace / "repo", workspace / ".worktrees" / "lane"):
        checkout.mkdir(parents=True)
        (checkout / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    if real_tools:
        for name in ("pyproject.toml", "uv.lock"):
            shutil.copy(FW / name, root / name)
        pyproject = (FW / "pyproject.toml").read_text(encoding="utf-8")
        narrow = pyproject.replace("line-length = 100", "line-length = 40")
        (workspace / "repo" / "pyproject.toml").write_text(narrow, encoding="utf-8")
        shutil.copy(FW / "uv.lock", workspace / "repo" / "uv.lock")
    return root


def fake_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int = 0) -> Path:
    """Put a `uv` on PATH that logs its cwd, VIRTUAL_ENV and argv."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "uv.log"
    uv = bin_dir / "uv"
    uv.write_text(
        f'#!/bin/sh\necho "$PWD|${{VIRTUAL_ENV:-unset}}|$*" >> "{log}"\n'
        f'echo "uv says no" >&2\nexit {exit_code}\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("VIRTUAL_ENV", "/framework/.venv")
    return log


def edit(path: Path | str, cwd: Path, tool: str = "Edit") -> str:
    return json.dumps({"tool_name": tool, "tool_input": {"file_path": str(path)}, "cwd": str(cwd)})


def patch(command: object, cwd: Path) -> str:
    return json.dumps(
        {"tool_name": "apply_patch", "tool_input": {"command": command}, "cwd": str(cwd)}
    )


def test_framework_file_formatted_with_framework_ruff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_framework(tmp_path / "fw", real_tools=True)
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(FW / ".venv"))
    target = root / "ai" / "scripts" / "tool.py"
    target.write_text(UGLY, encoding="utf-8")
    assert format_changed.run(edit("ai/scripts/tool.py", root), root) == 0
    assert target.read_text(encoding="utf-8") == PRETTY


def test_clone_file_uses_repo_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_framework(tmp_path / "fw", real_tools=True)
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(FW / ".venv"))
    target = root / "shop" / "repo" / "long.py"
    target.write_text(LONG, encoding="utf-8")
    assert format_changed.run(edit(target, root, "Write"), root) == 0
    assert target.read_text(encoding="utf-8") != LONG
    assert target.read_text(encoding="utf-8").startswith("value = some_function_name(\n")


def test_checkout_roots_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_framework(tmp_path / "fw").resolve()
    log = fake_uv(tmp_path, monkeypatch)
    lane_file = root / "shop" / ".worktrees" / "lane" / "pkg" / "m.py"
    lane_file.parent.mkdir()
    clone_file = root / "shop" / "repo" / "s.pyi"
    loose = root / "shop" / "notes.py"
    for path in (lane_file, clone_file, loose):
        path.write_text("x=1\n", encoding="utf-8")
    text = (
        "*** Begin Patch\n*** Update File: shop/.worktrees/lane/pkg/m.py\n@@\n-a\n+b\n"
        "*** Add File: shop/repo/s.pyi\n+x=1\n*** Delete File: shop/gone.py\n"
        "*** Update File: shop/notes.py\n*** Update File: README.md\n*** End Patch\n"
    )
    assert format_changed.run(patch(text, root), root) == 0
    assert log.read_text(encoding="utf-8").splitlines() == [
        f"{lane_file.parent.parent}|unset|run --frozen ruff format {lane_file}",
        f"{clone_file.parent}|unset|run --frozen ruff format {clone_file}",
        f"{root}|unset|run --frozen --project {root} ruff format {loose}",
    ]


def test_patch_move_formats_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_framework(tmp_path / "fw").resolve()
    log = fake_uv(tmp_path, monkeypatch)
    moved = root / "shop" / "repo" / "new.py"
    moved.write_text("x=1\n", encoding="utf-8")
    text = "*** Begin Patch\n*** Update File: shop/repo/old.py\n*** Move to: shop/repo/new.py\n"
    assert format_changed.run(patch(text + "@@\n-a\n+b\n*** End Patch\n", root), root) == 0
    assert log.read_text(encoding="utf-8").splitlines() == [
        f"{moved.parent}|unset|run --frozen ruff format {moved}",
    ]


def test_patch_as_list_and_profile_without_formatters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_framework(tmp_path / "fw")
    log = fake_uv(tmp_path, monkeypatch)
    (root / "shop" / "workspace.toml").write_text('name = "shop"\nremote = "x"\n')
    (root / "shop" / "repo" / "m.py").write_text("x=1\n", encoding="utf-8")
    command = ["apply_patch", "*** Begin Patch\n*** Update File: m.py\n*** End Patch"]
    assert format_changed.run(patch(command, root / "shop" / "repo"), root) == 0
    assert not log.exists()


def test_formatter_failure_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_framework(tmp_path / "fw")
    fake_uv(tmp_path, monkeypatch, exit_code=2)
    (root / "a.py").write_text("x=1\n", encoding="utf-8")
    assert format_changed.run(edit("a.py", root), root) == 1
    assert "failed for" in capsys.readouterr().err


def test_uv_missing_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_framework(tmp_path / "fw")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    (root / "shop" / "repo" / "a.py").write_text("x=1\n", encoding="utf-8")
    assert format_changed.run(edit("shop/repo/a.py", root), root) == 1
    assert "could not run" in capsys.readouterr().err


def test_broken_profile_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_framework(tmp_path / "fw")
    (root / "shop" / "workspace.toml").write_text('name = "shop"\n', encoding="utf-8")
    (root / "shop" / "repo" / "a.py").write_text("x=1\n", encoding="utf-8")
    assert format_changed.run(edit("shop/repo/a.py", root), root) == 1
    assert "cannot load the profile" in capsys.readouterr().err


def test_dropped_and_ignored_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_framework(tmp_path / "fw")
    log = fake_uv(tmp_path, monkeypatch)
    outside = tmp_path / "outside.py"
    outside.write_text("x=1\n", encoding="utf-8")
    (root / "notes.md").write_text("# x\n", encoding="utf-8")
    assert format_changed.run(edit(outside, root), root) == 0
    assert "outside the framework" in capsys.readouterr().err
    assert format_changed.run(edit("missing.py", root), root) == 0
    assert "not a file" in capsys.readouterr().err
    assert format_changed.run(edit("notes.md", root), root) == 0
    assert format_changed.run(json.dumps({"tool_name": "Bash", "tool_input": {}}), root) == 0
    assert not log.exists()


@pytest.mark.parametrize(
    "stdin",
    [
        "not json",
        "[1, 2]",
        json.dumps({"tool_name": "Edit", "tool_input": {}}),
        json.dumps({"tool_name": "Write", "tool_input": "x"}),
        json.dumps({"tool_name": "apply_patch", "tool_input": {"command": 3}}),
        json.dumps({"tool_name": "apply_patch", "tool_input": {"command": ["a", 1]}}),
    ],
)
def test_bad_payload_exits_1(stdin: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert format_changed.run(stdin) == 1
    assert "bad payload" in capsys.readouterr().err


def test_framework_root_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_framework(tmp_path / "fw")
    monkeypatch.setattr(format_changed, "__file__", str(tmp_path / "a" / "b" / "c" / "x.py"))
    assert format_changed.framework_root(root / "shop") == root.resolve()
    with pytest.raises(ProfileError):
        format_changed.framework_root(tmp_path)
    assert format_changed.run(edit("a.py", tmp_path)) == 1


def test_script_runs_standalone(tmp_path: Path) -> None:
    root = make_framework(tmp_path / "fw")
    for name in ("format_changed.py", "workspace_profile.py"):
        shutil.copy(SCRIPTS / name, root / "ai" / "scripts" / name)
    script = root / "ai" / "scripts" / "format_changed.py"
    done = subprocess.run(
        [sys.executable, str(script)],
        input=edit(tmp_path / "elsewhere.py", tmp_path),
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert done.returncode == 0
    (root / "doc.md").write_text("x\n", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(script)], input=edit("doc.md", root), capture_output=True, text=True
    )
    assert (done.returncode, done.stderr) == (0, "")
    done = subprocess.run([sys.executable, str(script)], input="{", capture_output=True, text=True)
    assert done.returncode == 1
