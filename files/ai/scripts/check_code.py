"""Run a workspace's lint or test gates against its clone or a lane.

Usage:
    check_code.py lint|test [--profile P] [--target T] [--skip NAME]...

Contract:
    - The profile defaults to the workspace that contains the current folder (found through
      the framework root, else the nearest `workspace.toml` above it).
    - `--target` resolves relative to the current folder, then to the workspace; the default
      is the profile's clone. A lane is a checkout under `<workspace>/.worktrees/<lane>`.
      The target must share the clone's repository, or be a clone whose `origin` is the
      profile's remote (none when `local_only`) or the clone (the setup's scratch clone).
    - The first output line names the target, its commit, branch and dirty state.
    - Gates run in profile order, from the target's checkout root, in the environment of
      `workspace_profile.product_env()`, so the framework's Python environment (its
      `VIRTUAL_ENV`, its bin folder on PATH) does not leak into product tools. Each gate's
      tool is resolved once on that PATH (or under the target, for a path) and run as found.
    - Each gate runs in its own session (process group) and is bounded by its `timeout`.
      On a timeout, or when this script gets SIGINT, SIGTERM or SIGHUP, the whole group is
      terminated (SIGTERM, then SIGKILL after a grace period); whatever a gate leaves
      running in its group when it exits is terminated the same way. A daemon that
      starts its own session (`setsid`) is out of reach. On Windows (best effort; agent
      tooling supports macOS and Linux) a timeout or
      interruption stops the gate's process tree with `taskkill /T /F` (no grace period),
      and children a gate leaves behind after exiting normally are not reached.
    - `--skip NAME` is the only way to leave a gate out; every skip is printed. An unknown
      name is an error, so a typo cannot pass silently.
    - A gate listed in the profile's `baseline_failing` that fails with exit 1 makes the run
      DEGRADED, never PASS: its own new failures are hidden, so prefer quarantining the known
      failures in the gate's argv. Any other exit code, or a timeout, fails.
    - Each gate prints its name, result and run time; a summary closes the run with
      PASS, FAIL, DEGRADED, INTERRUPTED or COULD NOT RUN.

Exit codes:
    0  PASS: every gate that ran passed
    1  FAIL: a gate failed or timed out (a baseline gate too, unless it exited 1)
    2  COULD NOT RUN: no or invalid profile, target missing, not a git checkout or not this
       product's repository, a gate's tool not found, an empty plan (no gates, or all
       skipped), or a gate failed to start; or INTERRUPTED by SIGINT, SIGTERM or SIGHUP.
       Exit 2 is never a pass.
    3  DEGRADED: no gate failed, but a `baseline_failing` gate exited 1. Never a pass.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any

from workspace_profile import (
    PROFILE_NAME,
    Gate,
    Profile,
    ProfileError,
    find_framework_root,
    load_profile,
    product_env,
    workspace_for,
)

WINDOWS = sys.platform == "win32"
STOP_SIGNALS = tuple(
    getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name)
)
EXIT_PASS, EXIT_FAIL, EXIT_CANNOT_RUN, EXIT_DEGRADED = 0, 1, 2, 3
GRACE_SECONDS = 5.0


class CannotRun(Exception):
    """The run cannot start or a gate could not run; maps to exit 2."""


class Interrupted(Exception):
    """SIGINT, SIGTERM or SIGHUP arrived during the run; maps to exit 2."""


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
    returncode: int | None  # None: timed out
    seconds: float
    baseline: bool

    @property
    def passed(self) -> bool:
        return self.returncode == 0

    @property
    def degraded(self) -> bool:
        return self.baseline and self.returncode == 1


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


def common_dir(root: Path) -> Path | None:
    found = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(found.stdout.strip()).resolve() if found.returncode == 0 else None


def describe_target(path: Path, profile: Profile) -> Target:
    top = _git(path, "rev-parse", "--show-toplevel")
    root = Path(top.stdout.strip()).resolve()
    if top.returncode != 0 or root != path.resolve():
        raise CannotRun(f"target is not a git checkout: {path}")
    origin = _git(root, "config", "--get", "remote.origin.url").stdout.strip()
    clone = profile.clone.resolve()
    same = common_dir(root) == common_dir(clone) if clone.is_dir() else False
    # A relative origin is a path from the target; no origin is no claim at all.
    cloned = bool(origin) and (root / origin).resolve() == clone
    if not same and origin != profile.remote and not cloned:
        raise CannotRun(f"target is not a checkout of {profile.name}: {root}")
    sha = _git(root, "rev-parse", "--short", "HEAD")
    commit = sha.stdout.strip() if sha.returncode == 0 else "(no commits)"
    ref = _git(root, "symbolic-ref", "--short", "-q", "HEAD")
    branch = ref.stdout.strip() if ref.returncode == 0 else "(detached)"
    status = _git(root, "status", "--porcelain")
    if status.returncode != 0:
        raise CannotRun(f"git status failed in {root}: {status.stderr.strip()}")
    return Target(root=root, commit=commit, branch=branch, dirty=bool(status.stdout.strip()))


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


def resolve_tools(plan: list[Gate], root: Path, env: dict[str, str]) -> dict[str, str]:
    """Each gate's name -> the executable its argv[0] names, on the gates' PATH."""
    found: dict[str, str] = {}
    missing = []
    for gate in plan:
        tool = gate.argv[0]
        if os.sep in tool or (os.altsep and os.altsep in tool):
            path = shutil.which(str(root / tool), path="")
        else:
            path = shutil.which(tool, path=env.get("PATH", os.defpath))
        if path is None:
            missing.append(f"{gate.name}: `{tool}` not found")
        else:
            found[gate.name] = path
    if missing:
        raise CannotRun("missing tool(s): " + "; ".join(missing))
    return found


def interrupt(signum: int, frame: FrameType | None) -> None:
    raise Interrupted(signal.Signals(signum).name)


def group_alive(proc: subprocess.Popen[bytes]) -> bool:
    """Whether any process is left in the gate's group; reaps the leader first."""
    proc.poll()
    try:
        os.killpg(proc.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # Members exist but belong to another user.
    return True


def stop_tree(proc: subprocess.Popen[bytes]) -> None:
    """Windows: stop the gate and every process it started (`taskkill /T /F`), then reap it."""
    subprocess.run(
        ["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False
    )
    if proc.poll() is None:
        proc.kill()
    proc.wait()


def stop_group(proc: subprocess.Popen[bytes]) -> None:
    """Terminate what is left of the gate's process group, then reap the gate."""
    if WINDOWS:
        stop_tree(proc)
        return
    blocked = set(STOP_SIGNALS)
    signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
    try:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            # Wait for the whole group, not just the leader, so leftover children get
            # the grace period to clean up before SIGKILL.
            deadline = time.monotonic() + GRACE_SECONDS
            while group_alive(proc) and time.monotonic() < deadline:
                time.sleep(0.05)
            if not group_alive(proc):
                break
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    finally:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, blocked)


def run_gate(gate: Gate, root: Path, env: dict[str, str], tool: str, baseline: bool) -> Result:
    print(f"==> {gate.name}: {' '.join(gate.argv)}", flush=True)
    start = time.monotonic()
    try:
        group: dict[str, Any] = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
            if WINDOWS
            else {"start_new_session": True}
        )
        proc = subprocess.Popen(list(gate.argv), executable=tool, cwd=root, env=env, **group)
    except OSError as exc:
        raise CannotRun(f"gate {gate.name} did not run: {exc}") from exc
    code: int | None = None
    try:
        code = proc.wait(timeout=gate.timeout)
    except subprocess.TimeoutExpired:
        pass
    except Interrupted:
        print(f"<-- {gate.name}: INTERRUPTED after {time.monotonic() - start:.1f}s", flush=True)
        raise
    finally:
        stop_group(proc)
    seconds = time.monotonic() - start
    result = Result(gate, code, seconds, baseline)
    if code is None:
        verdict = f"TIMEOUT (killed after {gate.timeout}s)"
    elif result.passed:
        verdict = "PASS"
        if baseline:
            verdict += " (listed in baseline_failing; remove it from the profile)"
    elif result.degraded:
        verdict = f"FAIL (exit {code}), in baseline_failing: DEGRADED"
    elif baseline:
        verdict = f"FAIL (exit {code}), not the baseline failure (exit 1)"
    else:
        verdict = f"FAIL (exit {code})"
    print(f"<-- {gate.name}: {verdict} in {seconds:.1f}s", flush=True)
    return result


def summarize(kind: str, results: list[Result], skipped: list[str]) -> int:
    failed = [r.gate.name for r in results if not r.passed and not r.degraded]
    degraded = [r.gate.name for r in results if r.degraded]
    timed_out = [r.gate.name for r in results if r.returncode is None]
    total = sum(r.seconds for r in results)
    parts = [f"{len(results)} ran", f"{len(results) - len(failed) - len(degraded)} passed"]
    if failed:
        parts.append(f"failed: {', '.join(failed)}")
    if timed_out:
        parts.append(f"timed out: {', '.join(timed_out)}")
    if degraded:
        parts.append(f"baseline failures (degraded): {', '.join(degraded)}")
    if skipped:
        parts.append(f"skipped: {', '.join(skipped)}")
    if failed:
        status, code = "FAIL", EXIT_FAIL
    elif degraded:
        status, code = "DEGRADED", EXIT_DEGRADED
    else:
        status, code = "PASS", EXIT_PASS
    print(f"{kind}: {status} ({'; '.join(parts)}) in {total:.1f}s")
    return code


def run(argv: list[str], cwd: Path | None = None) -> int:
    args = parse_args(argv)
    cwd = (cwd or Path.cwd()).resolve()
    handlers = {sig: signal.signal(sig, interrupt) for sig in STOP_SIGNALS}
    try:
        profile_path = args.profile if args.profile else find_profile(cwd)
        if not profile_path.is_absolute():
            profile_path = cwd / profile_path
        profile = load_profile(profile_path)
        target = describe_target(resolve_target(args.target, profile, cwd), profile)
        print(target.header(), flush=True)
        print(f"profile: {profile.path}  gates: {args.kind}")
        plan = plan_gates(profile, args.kind, args.skip)
        env = product_env()
        tools = resolve_tools(plan, target.root, env)
        results = [
            run_gate(g, target.root, env, tools[g.name], g.name in profile.baseline_failing)
            for g in plan
        ]
    except (CannotRun, ProfileError) as exc:
        sys.stdout.flush()
        print(f"error: {exc}", file=sys.stderr)
        print(f"{args.kind}: COULD NOT RUN (exit 2)")
        return EXIT_CANNOT_RUN
    except Interrupted as exc:
        print(f"{args.kind}: INTERRUPTED by {exc} (exit 2)", flush=True)
        return EXIT_CANNOT_RUN
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    return summarize(args.kind, results, list(dict.fromkeys(args.skip)))


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
