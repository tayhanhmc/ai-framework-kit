"""Tests for install.py against throwaway kit repos built per test."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "install.py"
SKILL = ".claude/skills/__P__-demo/SKILL.md"
LINES = [f"line {n}\n" for n in range(1, 21)]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout


class Kit:
    """A kit repo holding install.py and a files/ tree, committed one revision at a time."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "files").mkdir(parents=True)
        shutil.copy(INSTALLER, root / "install.py")
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.email", "kit@example.invalid")
        git(root, "config", "user.name", "kit")

    def write(self, path: str, text: str) -> None:
        dest = self.root / "files" / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)

    def delete(self, path: str) -> None:
        (self.root / "files" / path).unlink()

    def commit(self) -> str:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "kit")
        return git(self.root, "rev-parse", "HEAD").strip()


def run(kit: Kit, framework: Path, *args: str, path: str | None = None) -> tuple[int, str]:
    env = dict(os.environ)
    if path is not None:
        env["PATH"] = path
    result = subprocess.run(
        [sys.executable, str(kit.root / "install.py"), "--framework", str(framework), *args],
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout + result.stderr


def load(kit: Kit) -> ModuleType:
    """Import the kit's copy in-process, so a test can make it crash mid-run."""
    spec = importlib.util.spec_from_file_location("kit_install", kit.root / "install.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["kit_install"] = module  # dataclasses resolve annotations through it
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def kit(tmp_path: Path) -> Kit:
    k = Kit(tmp_path / "the kit")
    k.write(SKILL, "---\nname: __P__-demo\n---\nUse __P__ skills.\n")
    k.write("docs/guide.md", "".join(LINES))
    k.write("ai/old.md", "dropped later\n")
    k.write("ai/plans/.gitkeep", "")
    k.commit()
    return k


@pytest.fixture
def framework(tmp_path: Path) -> Path:
    path = tmp_path / "my framework"
    path.mkdir()
    return path


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    }


def test_fresh_install_renders_prefix_and_records_revision(kit: Kit, framework: Path) -> None:
    code, out = run(kit, framework, "--prefix", "kt")
    assert code == 0, out
    skill = framework / ".claude/skills/kt-demo/SKILL.md"
    assert skill.read_text() == "---\nname: kt-demo\n---\nUse kt skills.\n"
    assert (framework / "ai/plans/.gitkeep").exists()
    record = (framework / ".kit-install.toml").read_text()
    assert f'kit_revision = "{git(kit.root, "rev-parse", "HEAD").strip()}"' in record
    assert 'prefix = "kt"' in record


def test_second_install_is_a_no_op(kit: Kit, framework: Path) -> None:
    run(kit, framework, "--prefix", "kt")
    before = snapshot(framework)
    assert run(kit, framework, "--dry-run")[0] == 0
    code, out = run(kit, framework)
    assert code == 0 and "no file changes" in out, out
    assert snapshot(framework) == before


def test_dry_run_shows_diff_and_writes_nothing(kit: Kit, framework: Path) -> None:
    code, out = run(kit, framework, "--prefix", "kt", "--dry-run")
    assert code == 1, out
    assert "+name: kt-demo" in out and "create   docs/guide.md" in out
    assert snapshot(framework) == {}


def test_upgrade_updates_adds_and_removes(kit: Kit, framework: Path) -> None:
    run(kit, framework, "--prefix", "kt")
    kit.write("docs/guide.md", "".join(LINES).replace("line 10", "line ten"))
    kit.write("docs/new.md", "new in rev 2\n")
    kit.delete("ai/old.md")
    rev2 = kit.commit()
    code, out = run(kit, framework)
    assert code == 0, out
    assert "line ten" in (framework / "docs/guide.md").read_text()
    assert (framework / "docs/new.md").exists()
    assert not (framework / "ai/old.md").exists()
    assert rev2 in (framework / ".kit-install.toml").read_text()
    assert run(kit, framework)[0] == 0


def test_user_edit_is_merged_with_kit_change(kit: Kit, framework: Path) -> None:
    run(kit, framework, "--prefix", "kt")
    guide = framework / "docs/guide.md"
    guide.write_text(guide.read_text().replace("line 2\n", "line 2 (ours)\n"))
    kit.write("docs/guide.md", "".join(LINES).replace("line 18", "line 18 (kit)"))
    kit.commit()
    code, out = run(kit, framework)
    assert code == 0 and "merge    docs/guide.md" in out, out
    text = guide.read_text()
    assert "line 2 (ours)" in text and "line 18 (kit)" in text


def test_conflicting_edit_is_reported_and_never_overwritten(kit: Kit, framework: Path) -> None:
    run(kit, framework, "--prefix", "kt")
    guide = framework / "docs/guide.md"
    ours = guide.read_text().replace("line 5\n", "line 5 (ours)\n")
    guide.write_text(ours)
    kit.write("docs/guide.md", "".join(LINES).replace("line 5", "line 5 (kit)"))
    rev2 = kit.commit()
    code, out = run(kit, framework)
    assert code == 1 and "conflict docs/guide.md" in out, out
    assert guide.read_text() == ours
    sidecar = framework / f"docs/guide.md.kit-conflict-{rev2[:12]}"
    marked = sidecar.read_text()
    assert "<<<<<<< framework" in marked and "line 5 (kit)" in marked and ">>>>>>> kit" in marked
    assert rev2 in (framework / ".kit-install.toml").read_text()
    # Unresolved: the next run changes nothing and still fails.
    before = snapshot(framework)
    code, out = run(kit, framework)
    assert code == 1 and "unresolved" in out, out
    assert snapshot(framework) == before
    # Resolved by keeping ours and deleting the sidecar: clean from then on.
    sidecar.unlink()
    assert run(kit, framework)[0] == 0
    assert guide.read_text() == ours


def test_dropped_file_changed_here_is_kept(kit: Kit, framework: Path) -> None:
    run(kit, framework, "--prefix", "kt")
    (framework / "ai/old.md").write_text("mine now\n")
    kit.delete("ai/old.md")
    kit.commit()
    code, out = run(kit, framework)
    assert code == 1 and "keep     ai/old.md" in out, out
    assert (framework / "ai/old.md").read_text() == "mine now\n"


def test_deleted_here_stays_deleted_unless_kit_changed_it(kit: Kit, framework: Path) -> None:
    run(kit, framework, "--prefix", "kt")
    (framework / "ai/old.md").unlink()
    assert run(kit, framework)[0] == 0
    assert not (framework / "ai/old.md").exists()
    kit.write("ai/old.md", "changed in kit\n")
    kit.commit()
    code, out = run(kit, framework)
    assert code == 1 and "deleted here, changed in the kit" in out, out
    assert not (framework / "ai/old.md").exists()


def test_existing_file_without_record_is_a_conflict(kit: Kit, framework: Path) -> None:
    (framework / "docs").mkdir()
    (framework / "docs/guide.md").write_text("hand-copied, edited\n")
    code, out = run(kit, framework, "--prefix", "kt")
    assert code == 1 and "no recorded base" in out, out
    assert (framework / "docs/guide.md").read_text() == "hand-copied, edited\n"
    assert (framework / ".claude/skills/kt-demo/SKILL.md").exists()


@pytest.mark.parametrize("crash_at", [1, 2, 3])
def test_interrupted_upgrade_completes_on_rerun(
    kit: Kit, framework: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_at: int
) -> None:
    run(kit, framework, "--prefix", "kt")
    rev1 = git(kit.root, "rev-parse", "HEAD").strip()
    guide = framework / "docs/guide.md"
    guide.write_text(guide.read_text().replace("line 2\n", "line 2 (ours)\n"))
    kit.write("docs/guide.md", "".join(LINES).replace("line 18", "line 18 (kit)"))
    kit.write(SKILL, "---\nname: __P__-demo\n---\nUse __P__ skills, v2.\n")
    kit.write("docs/new.md", "new\n")
    rev2 = kit.commit()

    # The same upgrade, uninterrupted, in a copy: the expected end state.
    reference = tmp_path / "reference"
    shutil.copytree(framework, reference)
    assert run(kit, reference)[0] == 0

    module = load(kit)
    real_replace, calls = os.replace, list[str]()

    def crashing_replace(src: str | Path, dst: str | Path) -> None:
        calls.append(str(dst))
        if len(calls) == crash_at:
            raise KeyboardInterrupt("simulated crash")
        real_replace(src, dst)

    monkeypatch.setattr(module.os, "replace", crashing_replace)
    with pytest.raises(KeyboardInterrupt):
        module.main(["--framework", str(framework)])
    monkeypatch.undo()
    assert rev1 in (framework / ".kit-install.toml").read_text()  # record not advanced
    assert not list(framework.rglob("*.kit-tmp"))

    code, out = run(kit, framework)
    assert code == 0, out
    assert rev2 in (framework / ".kit-install.toml").read_text()
    assert snapshot(framework) == snapshot(reference)


def test_crash_after_conflict_sidecar_reruns_to_same_conflict(
    kit: Kit, framework: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(kit, framework, "--prefix", "kt")
    guide = framework / "docs/guide.md"
    guide.write_text(guide.read_text().replace("line 5\n", "line 5 (ours)\n"))
    kit.write("docs/guide.md", "".join(LINES).replace("line 5", "line 5 (kit)"))
    rev2 = kit.commit()
    module = load(kit)
    real_replace = os.replace

    def crash_on_record(src: str | Path, dst: str | Path) -> None:
        if str(dst).endswith(".kit-install.toml"):
            raise KeyboardInterrupt("simulated crash")
        real_replace(src, dst)

    monkeypatch.setattr(module.os, "replace", crash_on_record)
    with pytest.raises(KeyboardInterrupt):
        module.main(["--framework", str(framework)])
    monkeypatch.undo()
    assert (framework / f"docs/guide.md.kit-conflict-{rev2[:12]}").exists()
    code, out = run(kit, framework)
    assert code == 1 and "conflict docs/guide.md ->" in out, out  # redone, not "unresolved"
    assert rev2 in (framework / ".kit-install.toml").read_text()


def test_paths_with_spaces(kit: Kit, framework: Path) -> None:
    kit.write("docs/with space/a b.md", "spaced __P__\n")
    kit.commit()
    code, out = run(kit, framework, "--prefix", "kt")
    assert code == 0, out
    assert (framework / "docs/with space/a b.md").read_text() == "spaced kt\n"
    assert " " in str(kit.root) and " " in str(framework)


def tool_dir(tmp_path: Path, *tools: str) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in tools:
        found = shutil.which(tool)
        assert found
        (bin_dir / tool).symlink_to(found)
    return str(bin_dir)


@pytest.mark.parametrize(("present", "missing"), [(("uv",), "git"), (("git",), "uv")])
def test_missing_prerequisite_cannot_run(
    kit: Kit, framework: Path, tmp_path: Path, present: tuple[str, ...], missing: str
) -> None:
    code, out = run(kit, framework, "--prefix", "kt", path=tool_dir(tmp_path, *present))
    assert code == 2 and f"{missing} is not on PATH" in out, out
    assert snapshot(framework) == {}


def test_local_only_framework_repo_without_remote(kit: Kit, framework: Path) -> None:
    git(framework, "init", "-q", "-b", "main")
    assert git(framework, "remote") == "" and git(kit.root, "remote") == ""
    assert run(kit, framework, "--prefix", "kt")[0] == 0
    kit.write("docs/new.md", "new\n")
    kit.commit()
    assert run(kit, framework)[0] == 0
    assert (framework / "docs/new.md").exists()


def snapshot_commit(kit: Kit) -> str:
    """A commit of the kit's working tree made through a temporary index, as check_kit does."""
    env = {**os.environ, "GIT_INDEX_FILE": str(kit.root.parent / "snapshot-index")}
    for args in (["read-tree", "HEAD"], ["add", "-A", "--", "files"]):
        subprocess.run(["git", "-C", str(kit.root), *args], check=True, env=env)
    tree = (
        subprocess.run(
            ["git", "-C", str(kit.root), "write-tree"], check=True, env=env, capture_output=True
        )
        .stdout.decode()
        .strip()
    )
    return git(kit.root, "commit-tree", tree, "-p", "HEAD", "-m", "snapshot").strip()


def test_dirty_kit_is_refused_but_a_snapshot_installs(kit: Kit, framework: Path) -> None:
    kit.write("docs/guide.md", "uncommitted\n")
    code, out = run(kit, framework, "--prefix", "kt")
    assert code == 2 and "uncommitted changes" in out, out
    assert snapshot(framework) == {}
    status = git(kit.root, "status", "--porcelain")
    snap = snapshot_commit(kit)
    assert git(kit.root, "status", "--porcelain") == status  # The kit is untouched.
    code, out = run(kit, framework, "--prefix", "kt", "--revision", snap)
    assert code == 0, out
    assert (framework / "docs/guide.md").read_text() == "uncommitted\n"
    assert snap in (framework / ".kit-install.toml").read_text()
    (framework / "docs/guide.md").write_text("uncommitted\nmine\n")
    kit.commit()  # The work in progress is committed for real.
    code, out = run(kit, framework)  # The snapshot is the base: the local edit is kept.
    assert code == 0, out
    assert (framework / "docs/guide.md").read_text().endswith("mine\n")


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        (lambda fw: (fw / ".kit-install.toml").write_text("kit_revision = 3\n"), "cannot read"),
        (
            lambda fw: (fw / ".kit-install.toml").write_text(
                f'kit_revision = "{"a" * 40}"\nprefix = "kt"\n'
            ),
            "not in",
        ),
    ],
)
def test_bad_or_missing_record_cannot_run(
    kit: Kit, framework: Path, setup: Callable[[Path], object], expected: str
) -> None:
    setup(framework)
    code, out = run(kit, framework)
    assert code == 2 and expected in out, out


def test_prefix_change_and_bad_prefix_cannot_run(kit: Kit, framework: Path) -> None:
    assert run(kit, framework, "--prefix", "Bad Prefix")[0] == 2
    run(kit, framework, "--prefix", "kt")
    code, out = run(kit, framework, "--prefix", "other")
    assert code == 2 and "prefix 'kt'" in out, out
    assert run(kit, framework.parent / "missing", "--prefix", "kt")[0] == 2


def test_record_line_passes_detect_secrets_pragma(kit: Kit, framework: Path) -> None:
    run(kit, framework, "--prefix", "kt")
    line = next(
        x for x in (framework / ".kit-install.toml").read_text().splitlines() if "kit_rev" in x
    )
    assert line.endswith("# pragma: allowlist secret")


@pytest.mark.parametrize("kind", ["binary", "executable"])
def test_unsupported_kit_files_are_refused(kit: Kit, framework: Path, kind: str) -> None:
    if kind == "binary":
        (kit.root / "files/ai/blob.bin").write_bytes(b"\xff\xfe kit\0")
    else:
        kit.write("ai/run.sh", "#!/bin/sh\n")
        (kit.root / "files/ai/run.sh").chmod(0o755)
    kit.commit()
    code, out = run(kit, framework, "--prefix", "kt")
    assert code == 2 and ("not UTF-8 text" in out or "plain files only" in out), out
    assert snapshot(framework) == {}


def test_os_error_is_could_not_run(kit: Kit, framework: Path) -> None:
    run(kit, framework, "--prefix", "kt")
    kit.write("docs/guide.md", "".join(LINES).replace("line 10", "line ten"))
    kit.commit()
    guide = framework / "docs/guide.md"
    guide.chmod(0o000)  # Unreadable: the plan cannot compare it.
    try:
        code, out = run(kit, framework)
    finally:
        guide.chmod(0o644)
    assert code == 2 and "cannot run" in out


def test_dry_run_with_a_non_utf8_local_file_does_not_crash(kit: Kit, framework: Path) -> None:
    run(kit, framework, "--prefix", "kt")
    (framework / "docs/guide.md").write_bytes(b"".join(x.encode() for x in LINES) + b"\xff\n")
    kit.write("docs/guide.md", "".join(LINES).replace("line 1\n", "line one\n"))
    kit.commit()
    code, out = run(kit, framework, "--dry-run")
    assert code == 1 and "Traceback" not in out, out
