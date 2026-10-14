"""Format the files an agent just edited, so formatting-only CI failures do not follow.

A PostToolUse hook for Claude Code (`Edit|Write`) and Codex (`apply_patch`).

Contract:
    - Reads the hook payload (JSON) on stdin. Claude sends `tool_name` Edit or Write with
      `tool_input.file_path`. Codex sends `apply_patch` with the patch text in
      `tool_input.command` (a string or a list of strings); every `*** Update File: <p>` and
      `*** Add File: <p>` path is collected. Other tools are ignored.
    - Relative paths resolve against the payload's `cwd`. Paths outside the framework root,
      and files that no longer exist, are dropped.
    - A file inside a checkout of a workspace (its clone, or a lane under
      `.worktrees/<lane>`) gets only the formatters the workspace profile lists, run from
      that checkout's root so the repo's own config applies, with `VIRTUAL_ENV` unset:
      "ruff" runs `uv run --frozen ruff format <file>` on *.py and *.pyi.
    - Any other framework *.py/*.pyi file (outside every checkout) gets the framework's ruff:
      `uv run --frozen --project <framework root> ruff format <file>`.
    - Other files get nothing. Messages go to stderr.

It is best-effort and never blocks an edit; pre-commit stays authoritative.

Exit codes:
    0  nothing to do, or every file formatted
    1  bad payload, or a formatter failed or could not run
    It never exits 2: Claude treats 2 as blocking.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from workspace_profile import (
    PROFILE_NAME,
    ProfileError,
    find_framework_root,
    load_profile,
    workspace_for,
)

PYTHON_SUFFIXES = (".py", ".pyi")
FILE_TOOLS = ("Edit", "Write", "MultiEdit")
PATCH_LINE = re.compile(r"^\*\*\* (?:Update File|Add File|Move to): (.+?)\s*$", re.MULTILINE)
TIMEOUT_SECONDS = 120


class BadPayload(Exception):
    """The hook payload cannot be understood."""


def say(message: str) -> None:
    print(f"format_changed: {message}", file=sys.stderr)


def payload_paths(payload: object) -> tuple[list[str], Path]:
    """The raw edited paths and the cwd they are relative to."""
    if not isinstance(payload, dict):
        raise BadPayload("payload is not a JSON object")
    cwd = Path(payload.get("cwd") or os.getcwd())
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if tool == "apply_patch":
        command = tool_input.get("command") if isinstance(tool_input, dict) else tool_input
        if isinstance(command, list) and all(isinstance(part, str) for part in command):
            command = "\n".join(command)
        if not isinstance(command, str):
            raise BadPayload("apply_patch payload has no patch text in tool_input.command")
        return PATCH_LINE.findall(command), cwd
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if tool in FILE_TOOLS and not isinstance(file_path, str):
        raise BadPayload(f"{tool} payload has no tool_input.file_path")
    return ([file_path] if isinstance(file_path, str) else []), cwd


def framework_root(cwd: Path) -> Path:
    for start in (Path(__file__).resolve().parents[2], cwd):
        try:
            return find_framework_root(start)
        except ProfileError:
            continue
    raise ProfileError(f"no framework root above {Path(__file__).resolve()} or {cwd}")


def checkout_root(path: Path, workspace: Path) -> Path | None:
    """The nearest folder holding .git between `path` and the workspace (exclusive)."""
    for folder in path.parents:
        if folder == workspace or workspace not in folder.parents:
            return None
        if (folder / ".git").exists():
            return folder
    return None


def run_formatter(argv: list[str], cwd: Path, path: Path) -> bool:
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    try:
        done = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        say(f"could not run `{' '.join(argv)}` for {path}: {exc}")
        return False
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()
        say(f"`{' '.join(argv)}` failed for {path} (exit {done.returncode}): {detail}")
        return False
    say(f"formatted {path}")
    return True


def format_file(path: Path, root: Path) -> bool:
    """Format one file; False when a formatter failed or could not run."""
    if path.suffix not in PYTHON_SUFFIXES:
        return True
    workspace = workspace_for(path, root)
    checkout = checkout_root(path, workspace) if workspace else None
    if workspace is None or checkout is None:
        argv = ["uv", "run", "--frozen", "--project", str(root), "ruff", "format", str(path)]
        return run_formatter(argv, root, path)
    try:
        profile = load_profile(workspace / PROFILE_NAME)
    except ProfileError as exc:
        say(f"cannot load the profile for {path}: {exc}")
        return False
    if "ruff" not in profile.formatters:
        return True
    return run_formatter(["uv", "run", "--frozen", "ruff", "format", str(path)], checkout, path)


def run(stdin: str, root: Path | None = None) -> int:
    try:
        raw, cwd = payload_paths(json.loads(stdin))
    except (json.JSONDecodeError, BadPayload) as exc:
        say(f"bad payload: {exc}")
        return 1
    if not raw:
        return 0
    try:
        root = (root or framework_root(cwd)).resolve()
    except ProfileError as exc:
        say(str(exc))
        return 1
    status = 0
    for item in dict.fromkeys(raw):
        path = (cwd / item).resolve()
        if path != root and root not in path.parents:
            say(f"skipped {path}: outside the framework")
        elif not path.is_file():
            say(f"skipped {path}: not a file")
        elif not format_file(path, root):
            status = 1
    return status


def main() -> int:
    return run(sys.stdin.read())


if __name__ == "__main__":
    sys.exit(main())
