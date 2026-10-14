"""Run a workspace's lint or test gates against its clone or a lane.

Usage:
    check_code.py lint|test [--profile P] [--target T] [--skip NAME]...

Contract:
    - The profile defaults to the workspace that contains the current folder (found through
      the framework root, else the nearest `workspace.toml` above it).
    - `--target` resolves relative to the current folder, then to the workspace; the default
      is the profile's clone. A lane is a checkout under `<workspace>/.worktrees/<lane>`.
    - The first output line names the target, its commit, branch and dirty state.
    - Gates run in profile order, from the target's checkout root, with `VIRTUAL_ENV` removed
      from the environment so the framework's environment does not leak into product tools.
    - `--skip NAME` is the only way to leave a gate out; every skip is printed. An unknown
      name is an error, so a typo cannot pass silently.
    - A failing gate listed in the profile's `baseline_failing` only warns.
    - Each gate prints its name, result and run time; a summary closes the run.

Exit codes:
    0  every gate that ran passed (baseline failures only warn)
    1  a gate outside `baseline_failing` failed
    2  could not run: no or invalid profile, target missing or not a git checkout, a gate's
       tool not on PATH, an empty plan (no gates, or all skipped), or a gate failed to start.
       Exit 2 is never a pass.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from workspace_profile import (
    PROFILE_NAME,
    Gate,
    Profile,
    ProfileError,
    find_framework_root,
    load_profile,
    workspace_for,
)

EXIT_PASS, EXIT_FAIL, EXIT_CANNOT_RUN = 0, 1, 2


class CannotRun(Exception):
    """The run cannot start or a gate could not run; maps to exit 2."""


@dataclass(frozen=True)
class Target:
    root: Path
    commit: str
    branch: str
    dirty: bool

    def header(self) -> str:
        state = "dirty" if self.dirty else "clean"
        return f"target: {self.root}  commit: {self.commit}  branch: {self.branch}  ({state})"


@dataclass(frozen=True)
class Result:
    gate: Gate
    returncode: int
    seconds: float
    baseline: bool

    @property
    def passed(self) -> bool:
        return self.returncode == 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_code.py", description="Run a workspace's lint or test gates."
    )
    parser.add_argument("kind", choices=("lint", "test"))
    parser.add_argument("--profile", type=Path, help="workspace.toml (default: from cwd)")
    parser.add_argument("--target", type=Path, help="clone or lane folder (default: clone)")
    parser.add_argument(
        "--skip", action="append", default=[], metavar="NAME", help="leave a gate out"
    )
    return parser.parse_args(argv)


def find_profile(cwd: Path) -> Path:
    """The workspace.toml of the workspace containing `cwd`."""
    cwd = cwd.resolve()
    try:
        workspace = workspace_for(cwd, find_framework_root(cwd))
    except ProfileError:
        workspace = None
    if workspace is not None:
        return workspace / PROFILE_NAME
    for folder in (cwd, *cwd.parents):
        if (folder / PROFILE_NAME).is_file():
            return folder / PROFILE_NAME
    raise CannotRun(f"no workspace contains {cwd}; pass --profile <workspace.toml>")


def resolve_target(raw: Path | None, profile: Profile, cwd: Path) -> Path:
    if raw is None:
        candidates = [profile.clone]
    elif raw.is_absolute():
        candidates = [raw]
    else:
        candidates = [cwd / raw, profile.workspace / raw]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    tried = ", ".join(str(c) for c in candidates)
    raise CannotRun(f"target does not exist (tried {tried})")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise CannotRun(f"cannot run git: {exc}") from exc


def describe_target(path: Path) -> Target:
    top = _git(path, "rev-parse", "--show-toplevel")
    root = Path(top.stdout.strip()).resolve()
    if top.returncode != 0 or root != path.resolve():
        raise CannotRun(f"target is not a git checkout: {path}")
    sha = _git(root, "rev-parse", "--short", "HEAD")
    commit = sha.stdout.strip() if sha.returncode == 0 else "(no commits)"
    ref = _git(root, "symbolic-ref", "--short", "-q", "HEAD")
    branch = ref.stdout.strip() if ref.returncode == 0 else "(detached)"
    status = _git(root, "status", "--porcelain")
    if status.returncode != 0:
        raise CannotRun(f"git status failed in {root}: {status.stderr.strip()}")
    return Target(root=root, commit=commit, branch=branch, dirty=bool(status.stdout.strip()))


def gate_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    return env


def plan_gates(profile: Profile, kind: str, skips: list[str]) -> list[Gate]:
    gates = list(profile.gates(kind))
    names = {g.name for g in gates}
    unknown = sorted(set(skips) - names)
    if unknown:
        known = ", ".join(sorted(names)) or "none"
        raise CannotRun(f"--skip names unknown {kind} gate(s) {unknown}; known: {known}")
    plan = []
    for gate in gates:
        if gate.name in skips:
            print(f"skip: {gate.name} (--skip)")
        else:
            plan.append(gate)
    if not plan:
        why = "all gates skipped" if gates else f"the profile has no [[{kind}]] gates"
        raise CannotRun(f"empty plan: {why}")
    return plan


def check_tools(plan: list[Gate], root: Path, env: dict[str, str]) -> None:
    missing = []
    for gate in plan:
        tool = gate.argv[0]
        if os.sep in tool or (os.altsep and os.altsep in tool):
            found = shutil.which(str(root / tool), path="")
        else:
            found = shutil.which(tool, path=env.get("PATH", os.defpath))
        if found is None:
            missing.append(f"{gate.name}: `{tool}` not found")
    if missing:
        raise CannotRun("missing tool(s): " + "; ".join(missing))


def run_gate(gate: Gate, root: Path, env: dict[str, str], baseline: bool) -> Result:
    print(f"==> {gate.name}: {' '.join(gate.argv)}", flush=True)
    start = time.monotonic()
    try:
        code = subprocess.run(list(gate.argv), cwd=root, env=env, check=False).returncode
    except OSError as exc:
        raise CannotRun(f"gate {gate.name} did not run: {exc}") from exc
    seconds = time.monotonic() - start
    result = Result(gate, code, seconds, baseline)
    if result.passed:
        verdict = "PASS"
        if baseline:
            verdict += " (listed in baseline_failing; remove it from the profile)"
    elif baseline:
        verdict = f"FAIL (exit {code}), baseline: warning only"
    else:
        verdict = f"FAIL (exit {code})"
    print(f"<-- {gate.name}: {verdict} in {seconds:.1f}s", flush=True)
    return result


def summarize(kind: str, results: list[Result], skipped: list[str]) -> int:
    failed = [r.gate.name for r in results if not r.passed and not r.baseline]
    warned = [r.gate.name for r in results if not r.passed and r.baseline]
    total = sum(r.seconds for r in results)
    parts = [f"{len(results)} ran", f"{len(results) - len(failed) - len(warned)} passed"]
    if failed:
        parts.append(f"failed: {', '.join(failed)}")
    if warned:
        parts.append(f"baseline warnings: {', '.join(warned)}")
    if skipped:
        parts.append(f"skipped: {', '.join(skipped)}")
    status = "FAIL" if failed else "PASS"
    print(f"{kind}: {status} ({'; '.join(parts)}) in {total:.1f}s")
    return EXIT_FAIL if failed else EXIT_PASS


def run(argv: list[str], cwd: Path | None = None) -> int:
    args = parse_args(argv)
    cwd = (cwd or Path.cwd()).resolve()
    try:
        profile_path = args.profile if args.profile else find_profile(cwd)
        if not profile_path.is_absolute():
            profile_path = cwd / profile_path
        profile = load_profile(profile_path)
        target = describe_target(resolve_target(args.target, profile, cwd))
        print(target.header(), flush=True)
        print(f"profile: {profile.path}  gates: {args.kind}")
        plan = plan_gates(profile, args.kind, args.skip)
        env = gate_env()
        check_tools(plan, target.root, env)
        results = [
            run_gate(gate, target.root, env, gate.name in profile.baseline_failing) for gate in plan
        ]
    except (CannotRun, ProfileError) as exc:
        sys.stdout.flush()
        print(f"error: {exc}", file=sys.stderr)
        print(f"{args.kind}: COULD NOT RUN (exit 2)")
        return EXIT_CANNOT_RUN
    return summarize(args.kind, results, list(dict.fromkeys(args.skip)))


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
