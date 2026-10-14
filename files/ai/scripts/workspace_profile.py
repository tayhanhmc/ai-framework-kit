"""Load a workspace profile (`<workspace>/workspace.toml`): the one parser every script shares.

Contract:
    load_profile(path) -> Profile, or raises ProfileError naming the file and the bad key.
    find_framework_root(start) -> the nearest folder at or above `start` holding AGENTS.md
        and ai/scripts/, or raises ProfileError.
    find_workspaces(root) -> the folders directly under the framework root that hold a
        workspace.toml, sorted by name.
    workspace_for(path, root) -> the workspace folder containing `path`, or None.

Schema (unknown keys are errors, so a typo cannot silently drop a gate):
    name = "<product>"            required, equal to the workspace folder name
    remote = "<canonical URL>"    required
    clone = "repo"                optional, default "repo", relative to the workspace
    default_branch = "main"       optional, default "main"
    formatters = ["ruff"]         optional, only formatters the repo has its own config for
    baseline_failing = []         optional, gate names already failing on the default branch
    [[lint]] / [[test]]           name = "...", argv = ["..."]; `{workspace}` in a word
                                  expands to the workspace folder

Run as a script: `workspace_profile.py <workspace.toml>...` prints each profile.
Exit codes: 0 every profile loads, 1 a profile is invalid, 2 a file could not be read.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROFILE_NAME = "workspace.toml"
KNOWN_FORMATTERS = ("ruff",)
_KEYS = {"name", "remote", "clone", "default_branch", "formatters", "baseline_failing"}
_GATE_KINDS = ("lint", "test")


class ProfileError(Exception):
    """A profile is missing, unreadable or invalid."""


@dataclass(frozen=True)
class Gate:
    name: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class Profile:
    path: Path
    workspace: Path
    name: str
    remote: str
    clone: Path
    default_branch: str
    formatters: tuple[str, ...]
    baseline_failing: tuple[str, ...]
    lint: tuple[Gate, ...]
    test: tuple[Gate, ...]

    def gates(self, kind: str) -> tuple[Gate, ...]:
        if kind not in _GATE_KINDS:
            raise ProfileError(f"unknown gate kind {kind!r}; use lint or test")
        return self.lint if kind == "lint" else self.test


def _str(data: dict[str, object], key: str, where: str, default: str | None = None) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value:
        raise ProfileError(f"{where}: `{key}` must be a non-empty string")
    return value


def _str_list(data: dict[str, object], key: str, where: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ProfileError(f"{where}: `{key}` must be a list of non-empty strings")
    return tuple(value)


def _gates(data: dict[str, object], kind: str, where: str, workspace: Path) -> tuple[Gate, ...]:
    raw = data.get(kind, [])
    if not isinstance(raw, list):
        raise ProfileError(f"{where}: `{kind}` must be an array of tables ([[{kind}]])")
    gates: list[Gate] = []
    for i, entry in enumerate(raw):
        at = f"{where}: [[{kind}]] #{i + 1}"
        if not isinstance(entry, dict) or set(entry) != {"name", "argv"}:
            raise ProfileError(f"{at} must have exactly `name` and `argv`")
        argv = _str_list(entry, "argv", at)
        if not argv:
            raise ProfileError(f"{at}: `argv` is empty")
        name = _str(entry, "name", at)
        if name in {g.name for g in gates}:
            raise ProfileError(f"{at}: duplicate gate name {name!r}")
        expanded = tuple(w.replace("{workspace}", str(workspace)) for w in argv)
        gates.append(Gate(name, expanded))
    return tuple(gates)


def load_profile(path: Path) -> Profile:
    path = path.resolve()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileError(f"{path}: cannot read: {exc.strerror}") from exc
    except UnicodeDecodeError as exc:
        raise ProfileError(f"{path}: invalid encoding: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{path}: invalid TOML: {exc}") from exc
    where = str(path)
    unknown = set(data) - _KEYS - set(_GATE_KINDS)
    if unknown:
        raise ProfileError(f"{where}: unknown keys {sorted(unknown)}")
    workspace = path.parent
    name = _str(data, "name", where)
    if name != workspace.name:
        raise ProfileError(f"{where}: name {name!r} differs from folder {workspace.name!r}")
    formatters = _str_list(data, "formatters", where)
    for fmt in formatters:
        if fmt not in KNOWN_FORMATTERS:
            raise ProfileError(f"{where}: unknown formatter {fmt!r}; known: {KNOWN_FORMATTERS}")
    lint = _gates(data, "lint", where, workspace)
    test = _gates(data, "test", where, workspace)
    baseline = _str_list(data, "baseline_failing", where)
    clone = Path(_str(data, "clone", where, "repo"))
    if clone.is_absolute() or ".." in clone.parts:
        raise ProfileError(f"{where}: `clone` must be a relative path without `..`: {clone}")
    names = {g.name for g in lint + test}
    for gate in baseline:
        if gate not in names:
            raise ProfileError(f"{where}: baseline_failing names unknown gate {gate!r}")
    return Profile(
        path=path,
        workspace=workspace,
        name=name,
        remote=_str(data, "remote", where),
        clone=workspace / clone,
        default_branch=_str(data, "default_branch", where, "main"),
        formatters=formatters,
        baseline_failing=baseline,
        lint=lint,
        test=test,
    )


def find_framework_root(start: Path) -> Path:
    start = start.resolve()
    for folder in (start, *start.parents):
        if (folder / "AGENTS.md").is_file() and (folder / "ai" / "scripts").is_dir():
            return folder
    raise ProfileError(f"no framework root (AGENTS.md and ai/scripts/) at or above {start}")


def find_workspaces(root: Path) -> list[Path]:
    return sorted(p.parent for p in root.glob(f"*/{PROFILE_NAME}") if p.is_file())


def workspace_for(path: Path, root: Path) -> Path | None:
    path = path.resolve()
    for workspace in find_workspaces(root.resolve()):
        if path == workspace or workspace in path.parents:
            return workspace
    return None


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: workspace_profile.py <workspace.toml>...", file=sys.stderr)
        return 2
    status = 0
    for arg in argv:
        try:
            profile = load_profile(Path(arg))
        except ProfileError as exc:
            print(f"error: {exc}", file=sys.stderr)
            status = max(status, 2 if "cannot read" in str(exc) else 1)
            continue
        gates = ", ".join(g.name for g in profile.lint + profile.test)
        print(f"{profile.name}: clone={profile.clone} gates=[{gates}]")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
