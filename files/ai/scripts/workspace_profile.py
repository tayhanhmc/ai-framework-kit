"""Load a workspace profile (`<workspace>/workspace.toml`): the one parser every script shares.

Contract:
    load_profile(path) -> Profile, or raises ProfileError naming the file and the bad key.
    find_framework_root(start) -> the nearest folder at or above `start` holding AGENTS.md
        and ai/scripts/, or raises ProfileError.
    find_workspaces(root) -> the folders directly under the framework root that hold a
        workspace.toml, sorted by name.
    workspace_for(path, root) -> the workspace folder containing `path`, or None.
    product_env() -> os.environ without the framework's own Python environment, for running
        product tools: no `VIRTUAL_ENV`, no bin folder of the running virtual environment or
        of `$VIRTUAL_ENV` on PATH, and no `UV_PROJECT_ENVIRONMENT` that names either.

Schema (unknown keys are errors, so a typo cannot silently drop a gate):
    name = "<product>"            required, equal to the workspace folder name
    remote = "<canonical URL>"    required, unless the repository has no remote:
    local_only = true             then this instead (never both)
    clone = "repo"                optional, default "repo", relative to the workspace
    default_branch = "main"       optional, default "main"
    formatters = ["ruff"]         optional, only formatters the repo has its own config for;
                                  "ruff" is `uv run --frozen ruff format {file}` on *.py and
                                  *.pyi, so only for a uv-managed checkout (uv.lock)
    baseline_failing = []         optional, gate names already failing on the default branch
    [[lint]] / [[test]]           name = "...", argv = ["..."], optional timeout = <seconds>
                                  (default 1800); names are unique across both kinds
    [[format]]                    argv = ["...", "{file}"], suffixes = [".ts", ...]: a
                                  project-native formatter for files with those suffixes
    `{workspace}` in an argv word expands to the workspace folder; `{file}` in a [[format]]
    word expands to the edited file.

Run as a script: `workspace_profile.py <workspace.toml>...` prints each profile.
Exit codes: 0 every profile loads, 1 a profile is invalid, 2 a file could not be read.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROFILE_NAME = "workspace.toml"
DEFAULT_GATE_TIMEOUT = 1800
# Where a virtual environment keeps its executables; product_env drops this directory.
VENV_BIN = "Scripts" if sys.platform == "win32" else "bin"
_KEYS = {
    "name",
    "remote",
    "local_only",
    "clone",
    "default_branch",
    "formatters",
    "baseline_failing",
    "format",
}
_GATE_KINDS = ("lint", "test")


class ProfileError(Exception):
    """A profile is missing, unreadable or invalid."""


@dataclass(frozen=True)
class Gate:
    name: str
    argv: tuple[str, ...]
    timeout: int = DEFAULT_GATE_TIMEOUT


@dataclass(frozen=True)
class Formatter:
    argv: tuple[str, ...]
    suffixes: tuple[str, ...]
    requires: str = ""  # a file the checkout must hold for this formatter to run


# `uv run --frozen` would create an environment in a checkout without uv.lock.
RUFF = Formatter(("uv", "run", "--frozen", "ruff", "format", "{file}"), (".py", ".pyi"), "uv.lock")
KNOWN_FORMATTERS = {"ruff": RUFF}


@dataclass(frozen=True)
class Profile:
    path: Path
    workspace: Path
    name: str
    remote: str | None
    clone: Path
    default_branch: str
    formatters: tuple[Formatter, ...]
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


def _tables(data: dict[str, object], kind: str, where: str) -> list[dict[str, object]]:
    raw = data.get(kind, [])
    if not isinstance(raw, list) or not all(isinstance(entry, dict) for entry in raw):
        raise ProfileError(f"{where}: `{kind}` must be an array of tables ([[{kind}]])")
    return raw


def _argv(entry: dict[str, object], at: str, workspace: Path) -> tuple[str, ...]:
    argv = _str_list(entry, "argv", at)
    if not argv:
        raise ProfileError(f"{at}: `argv` is empty")
    return tuple(w.replace("{workspace}", str(workspace)) for w in argv)


def _gates(
    data: dict[str, object], kind: str, where: str, workspace: Path, taken: set[str]
) -> tuple[Gate, ...]:
    """The [[kind]] gates; `taken` holds the names used so far, across kinds."""
    gates: list[Gate] = []
    for i, entry in enumerate(_tables(data, kind, where)):
        at = f"{where}: [[{kind}]] #{i + 1}"
        if not {"name", "argv"} <= set(entry) <= {"name", "argv", "timeout"}:
            raise ProfileError(f"{at} must have `name` and `argv`, and optionally `timeout`")
        argv = _argv(entry, at, workspace)
        name = _str(entry, "name", at)
        if name in taken:
            raise ProfileError(f"{at}: duplicate gate name {name!r} (unique across lint and test)")
        taken.add(name)
        timeout = entry.get("timeout", DEFAULT_GATE_TIMEOUT)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise ProfileError(f"{at}: `timeout` must be a positive integer (seconds)")
        gates.append(Gate(name, argv, timeout))
    return tuple(gates)


def _formatters(data: dict[str, object], where: str, workspace: Path) -> tuple[Formatter, ...]:
    named = _str_list(data, "formatters", where)
    for fmt in named:
        if fmt not in KNOWN_FORMATTERS:
            known = tuple(KNOWN_FORMATTERS)
            raise ProfileError(f"{where}: unknown formatter {fmt!r}; known: {known}")
    formatters = [KNOWN_FORMATTERS[fmt] for fmt in dict.fromkeys(named)]
    for i, entry in enumerate(_tables(data, "format", where)):
        at = f"{where}: [[format]] #{i + 1}"
        if set(entry) != {"argv", "suffixes"}:
            raise ProfileError(f"{at} must have exactly `argv` and `suffixes`")
        argv = _argv(entry, at, workspace)
        if not any("{file}" in w for w in argv):
            raise ProfileError(f"{at}: `argv` must contain `{{file}}`")
        suffixes = _str_list(entry, "suffixes", at)
        if not suffixes or not all(s.startswith(".") for s in suffixes):
            raise ProfileError(f'{at}: `suffixes` must be a non-empty list like [".py"]')
        formatters.append(Formatter(argv, suffixes))
    return tuple(formatters)


def _remote(data: dict[str, object], where: str) -> str | None:
    local_only = data.get("local_only", False)
    if not isinstance(local_only, bool):
        raise ProfileError(f"{where}: `local_only` must be true or false")
    if not local_only:
        hint = " (or set `local_only = true` for a repository without a remote)"
        if not isinstance(data.get("remote"), str) or not data["remote"]:
            raise ProfileError(f"{where}: `remote` must be a non-empty string{hint}")
        return str(data["remote"])
    if "remote" in data:
        raise ProfileError(f"{where}: `local_only = true` and `remote` exclude each other")
    return None


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
    formatters = _formatters(data, where, workspace)
    taken: set[str] = set()
    lint = _gates(data, "lint", where, workspace, taken)
    test = _gates(data, "test", where, workspace, taken)
    baseline = _str_list(data, "baseline_failing", where)
    clone = Path(_str(data, "clone", where, "repo"))
    if clone.anchor or ".." in clone.parts:  # anchor: also Windows-rooted `/x` and `C:x`
        raise ProfileError(f"{where}: `clone` must be a relative path without `..`: {clone}")
    for gate in baseline:
        if gate not in taken:
            raise ProfileError(f"{where}: baseline_failing names unknown gate {gate!r}")
    return Profile(
        path=path,
        workspace=workspace,
        name=name,
        remote=_remote(data, where),
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


def product_env() -> dict[str, str]:
    env = dict(os.environ)
    virtual_env = env.pop("VIRTUAL_ENV", "")
    envs = {Path(virtual_env).resolve()} if virtual_env else set()
    if sys.prefix != sys.base_prefix:
        envs.add(Path(sys.prefix).resolve())
    if "PATH" in env:
        bins = {os.path.normcase(e / VENV_BIN) for e in envs}
        entries = env["PATH"].split(os.pathsep)
        kept = [p for p in entries if not p or os.path.normcase(Path(p).resolve()) not in bins]
        env["PATH"] = os.pathsep.join(kept)
    project_env = env.get("UV_PROJECT_ENVIRONMENT", "")
    if os.path.isabs(project_env) and Path(project_env).resolve() in envs:
        del env["UV_PROJECT_ENVIRONMENT"]
    return env


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
