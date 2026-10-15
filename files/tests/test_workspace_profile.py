from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import workspace_profile as wp

BASE = 'name = "shop"\nremote = "git@example.com:shop.git"\n'


def write_profile(root: Path, body: str, name: str = "shop") -> Path:
    path = root / name / wp.PROFILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_load_full_profile_expands_workspace(tmp_path: Path) -> None:
    body = BASE + (
        'clone = "src"\ndefault_branch = "trunk"\nformatters = ["ruff"]\n'
        'baseline_failing = ["types"]\n'
        '[[lint]]\nname = "types"\nargv = ["tool", "--config={workspace}/cfg"]\n'
        '[[test]]\nname = "unit"\nargv = ["pytest"]\ntimeout = 60\n'
        '[[format]]\nargv = ["fmt", "--cfg={workspace}/f", "{file}"]\nsuffixes = [".ts"]\n'
    )
    profile = wp.load_profile(write_profile(tmp_path, body))
    workspace = (tmp_path / "shop").resolve()
    assert profile.workspace == workspace
    assert profile.remote == "git@example.com:shop.git"
    assert profile.clone == workspace / "src"
    assert profile.default_branch == "trunk"
    custom = wp.Formatter(("fmt", f"--cfg={workspace}/f", "{file}"), (".ts",))
    assert profile.formatters == (wp.RUFF, custom)
    assert profile.baseline_failing == ("types",)
    assert profile.gates("lint") == (wp.Gate("types", ("tool", f"--config={workspace}/cfg")),)
    assert profile.gates("lint")[0].timeout == wp.DEFAULT_GATE_TIMEOUT
    assert profile.gates("test") == (wp.Gate("unit", ("pytest",), 60),)


def test_local_only_repository(tmp_path: Path) -> None:
    profile = wp.load_profile(write_profile(tmp_path, 'name = "shop"\nlocal_only = true\n'))
    assert profile.remote is None


def test_defaults(tmp_path: Path) -> None:
    profile = wp.load_profile(write_profile(tmp_path, BASE))
    assert profile.clone.name == "repo"
    assert profile.default_branch == "main"
    assert profile.lint == profile.test == ()
    assert profile.formatters == ()
    assert profile.baseline_failing == ()


def test_unknown_gate_kind(tmp_path: Path) -> None:
    profile = wp.load_profile(write_profile(tmp_path, BASE))
    with pytest.raises(wp.ProfileError, match="unknown gate kind"):
        profile.gates("deploy")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (BASE + 'colne = "repo"\n', "unknown keys \\['colne'\\]"),
        ('remote = "x"\n', "`name` must be a non-empty string"),
        ('name = "other"\nremote = "x"\n', "differs from folder"),
        ('name = "shop"\n', "`remote` must be a non-empty string \\(or set `local_only"),
        ('name = "shop"\nlocal_only = false\n', "`remote` must be a non-empty string"),
        (BASE + "local_only = true\n", "`local_only = true` and `remote` exclude each other"),
        ('name = "shop"\nlocal_only = "yes"\n', "`local_only` must be true or false"),
        (BASE + "formatters = [1]\n", "`formatters` must be a list"),
        (BASE + 'formatters = ["black"]\n', "unknown formatter 'black'"),
        (BASE + 'baseline_failing = ["ghost"]\n', "unknown gate 'ghost'"),
        (BASE + 'lint = "ruff"\n', "must be an array of tables"),
        (BASE + '[[lint]]\nname = "a"\n', "must have `name` and `argv`"),
        (BASE + '[[lint]]\nname = "a"\nargv = ["x"]\nnice = 1\n', "optionally `timeout`"),
        (BASE + '[[test]]\nname = "a"\nargv = ["x"]\ntimeout = 0\n', "positive integer"),
        (BASE + '[[test]]\nname = "a"\nargv = ["x"]\ntimeout = true\n', "positive integer"),
        (BASE + '[[test]]\nname = "a"\nargv = ["x"]\ntimeout = 1.5\n', "positive integer"),
        (BASE + '[[format]]\nargv = ["f", "{file}"]\n', "exactly `argv` and `suffixes`"),
        (BASE + '[[format]]\nargv = ["f"]\nsuffixes = [".ts"]\n', "must contain `{file}`"),
        (BASE + '[[format]]\nargv = ["{file}"]\nsuffixes = []\n', "`suffixes` must be"),
        (BASE + '[[format]]\nargv = ["{file}"]\nsuffixes = ["ts"]\n', "`suffixes` must be"),
        (BASE + "format = [1]\n", "must be an array of tables"),
        (BASE + '[[lint]]\nname = "a"\nargv = []\n', "`argv` is empty"),
        (BASE + '[[lint]]\nname = "a"\nargv = [""]\n', "list of non-empty strings"),
        (
            BASE + '[[lint]]\nname = "a"\nargv = ["x"]\n[[lint]]\nname = "a"\nargv = ["y"]\n',
            "duplicate gate name 'a'",
        ),
        (
            BASE + '[[lint]]\nname = "a"\nargv = ["x"]\n[[test]]\nname = "a"\nargv = ["y"]\n',
            "duplicate gate name 'a' \\(unique across lint and test\\)",
        ),
        (BASE + "name = \n", "invalid TOML"),
    ],
)
def test_invalid_profiles(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(wp.ProfileError, match=message):
        wp.load_profile(write_profile(tmp_path, body))


@pytest.mark.parametrize("clone", ["/abs/repo", "../repo", "a/../../b", ".."])
def test_clone_must_stay_inside_workspace(tmp_path: Path, clone: str) -> None:
    with pytest.raises(wp.ProfileError, match="`clone` must be a relative path"):
        wp.load_profile(write_profile(tmp_path, BASE + f"clone = {clone!r}\n"))


def test_invalid_encoding(tmp_path: Path) -> None:
    path = write_profile(tmp_path, BASE)
    path.write_bytes(BASE.encode() + b"# \xff\n")
    with pytest.raises(wp.ProfileError, match="invalid encoding"):
        wp.load_profile(path)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(wp.ProfileError, match="cannot read"):
        wp.load_profile(tmp_path / "nope" / wp.PROFILE_NAME)


def make_framework(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text("# fw\n", encoding="utf-8")
    (root / "ai" / "scripts").mkdir(parents=True)
    return root


def test_find_framework_root(tmp_path: Path) -> None:
    root = make_framework(tmp_path / "fw")
    deep = root / "shop" / "repo" / "src"
    deep.mkdir(parents=True)
    assert wp.find_framework_root(deep) == root.resolve()
    with pytest.raises(wp.ProfileError, match="no framework root"):
        wp.find_framework_root(tmp_path)


def test_find_workspaces_and_workspace_for(tmp_path: Path) -> None:
    root = make_framework(tmp_path / "fw")
    write_profile(root, BASE, "shop")
    write_profile(root, 'name = "app"\nremote = "x"\n', "app")
    (root / "docs").mkdir()
    workspaces = wp.find_workspaces(root)
    assert [w.name for w in workspaces] == ["app", "shop"]
    inner = root / "shop" / ".worktrees" / "lane" / "x.py"
    assert wp.workspace_for(inner, root) == (root / "shop").resolve()
    assert wp.workspace_for(root / "shop", root) == (root / "shop").resolve()
    assert wp.workspace_for(root / "docs" / "a.md", root) is None
    assert wp.workspace_for(root / "shopping" / "a.md", root) is None


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = write_profile(tmp_path, BASE + '[[test]]\nname = "unit"\nargv = ["pytest"]\n')
    bad = write_profile(tmp_path, 'name = "bad"\nremote = "x"\ntypo = 1\n', "bad")
    assert wp.main([str(good)]) == 0
    assert "shop: clone=" in capsys.readouterr().out
    assert wp.main([str(good), str(bad)]) == 1
    assert "unknown keys" in capsys.readouterr().err
    assert wp.main([str(bad), str(tmp_path / "missing.toml")]) == 2
    assert wp.main([]) == 2
    assert "usage" in capsys.readouterr().err


def test_product_env_drops_only_the_framework_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    running, active, user = tmp_path / "fw-venv", tmp_path / "active", tmp_path / "user"
    monkeypatch.setattr(sys, "prefix", str(running))
    path = [str(running / "bin"), str(user / "bin"), str(active / "bin"), "", "/usr/bin"]
    monkeypatch.setenv("PATH", os.pathsep.join(path))
    monkeypatch.setenv("VIRTUAL_ENV", str(active))
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(running))
    env = wp.product_env()
    assert env["PATH"] == os.pathsep.join([str(user / "bin"), "", "/usr/bin"])
    assert "VIRTUAL_ENV" not in env and "UV_PROJECT_ENVIRONMENT" not in env
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", ".venv-product")
    assert wp.product_env()["UV_PROJECT_ENVIRONMENT"] == ".venv-product"


def test_product_env_outside_a_venv_keeps_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join([f"{sys.base_prefix}/bin", "/usr/bin"]))
    assert wp.product_env()["PATH"] == os.pathsep.join([f"{sys.base_prefix}/bin", "/usr/bin"])
