"""Grade a produced ``repo/`` against a fixture, independently of the agent's report.

The grader copies the artifact into a fresh temporary folder, adds the hidden
acceptance tests and the fixture's original visible tests, runs both, and applies the
fixture's static checks. It never reads the agent's transcript or claims.

``success`` means: every hidden test passes, no visible test that passed on the
unmodified fixture now fails (a regression), and no boundary check fails. Stale-context
mistakes are recorded separately; a stale mistake usually also fails a hidden test.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from harness import IGNORE, Fixture, run_bounded

HELPER = Path(__file__).resolve().parent / "unittest_json.py"
TEST_TIMEOUT = 120.0
_baseline_cache: dict[str, dict[str, str]] = {}


@dataclass
class Grade:
    success: bool
    hidden_passed: int
    hidden_total: int
    failures: list[str] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)
    boundary_violations: list[str] = field(default_factory=list)
    stale_context_mistakes: list[str] = field(default_factory=list)
    tests_modified: list[str] = field(default_factory=list)
    runtime_cleanup_ok: bool | None = None
    grader_errors: list[str] = field(default_factory=list)
    failure_details: list[str] = field(default_factory=list)  # traceback tails

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _ignore_artifact(directory: str, names: list[str]) -> set[str]:
    skip = set(IGNORE(directory, names))
    return skip | {n for n in names if n in {".git", "_hidden", "_visible"}}


def _run_tests(
    fixture: Fixture, root: Path, start: str, env_extra: dict[str, str]
) -> dict[str, Any]:
    (root / start / "__init__.py").touch()  # discovery needs an importable start dir
    out = root / f".{start}_results.json"
    env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "PYTHONPATH"}}
    env.update(env_extra)
    py = (
        [sys.executable]
        if fixture.runner == "python"
        else [
            "uv",
            "run",
            "--frozen",
            "--quiet",
            "--python",
            sys.executable,
            "--project",
            str(root),
            "python",
        ]
    )
    done = run_bounded(
        [*py, str(HELPER), str(root.resolve() / start), str(root.resolve()), str(out)],
        cwd=root,
        timeout=TEST_TIMEOUT,
        env=env,
    )
    if out.is_file():
        data: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
        return data
    reason = "timed out" if done.timed_out else f"exit {done.returncode}"
    return {
        "outcomes": {},
        "details": [f"test run produced no results ({reason}): {done.stderr[-2000:]}"],
    }


def _hash_tree(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def check_no_import(repo: Path, scope: str, module: str) -> list[str]:
    """Imports of ``module`` (absolute, relative or by string) from files under ``scope``."""
    found = []
    for path in sorted((repo / scope).rglob("*.py")):
        rel = path.relative_to(repo)
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError as err:
            found.append(f"{rel}: unparseable ({err.msg})")
            continue
        package = ".".join(rel.with_suffix("").parts[:-1])
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".")
                    parts = parts[: len(parts) - node.level + 1]
                    base = ".".join([*parts, base] if base else parts)
                names = [base] + [f"{base}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names = [node.value]  # importlib.import_module("app.storage")
            if any(n == module or n.startswith(module + ".") for n in names):
                found.append(
                    f"{rel}:{getattr(node, 'lineno', 0)}: imports {module} (forbidden from {scope})"
                )
    return found


def check_unchanged(repo: Path, original: Path, paths: list[str]) -> list[str]:
    found = []
    for rel in paths:
        a, b = repo / rel, original / rel
        if not a.is_file() or a.read_bytes() != b.read_bytes():
            found.append(f"{rel}: changed (must stay as pinned)")
    return found


def check_stale(repo: Path, markers: list[dict[str, str]]) -> list[str]:
    found = []
    for marker in markers:
        if marker["type"] == "path_exists" and (repo / marker["path"]).exists():
            found.append(f"{marker['path']} exists (stale doc followed)")
        elif marker["type"] == "regex":
            pattern = re.compile(marker["pattern"])
            for path in sorted(repo.glob(marker["glob"])):
                if path.is_file() and pattern.search(path.read_text(encoding="utf-8")):
                    found.append(f"{path.relative_to(repo)}: matches {marker['pattern']!r}")
    return found


def port_is_free(port: int) -> bool:
    """No listener on ``port``: connecting is refused and a fresh server could bind it.

    SO_REUSEADDR lets the bind ignore TIME_WAIT entries left by closed connections,
    which are not a leftover server.
    """
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _runtime_cleanup(record: Path) -> tuple[bool | None, list[str]]:
    if not record.is_file():
        return None, ["runtime record missing: the hidden service test did not start"]
    data = json.loads(record.read_text(encoding="utf-8"))
    errors = []
    if pid_alive(data["pid"]):
        errors.append(f"service pid {data['pid']} still alive after tests")
    if not port_is_free(data["port"]):
        errors.append(f"port {data['port']} still bound after tests")
    if errors:  # recorded above; now stop the leftover so it cannot outlive grading
        with contextlib.suppress(ProcessLookupError):
            os.killpg(data["pid"], signal.SIGKILL)
    return not errors, errors


def visible_baseline(fixture: Fixture) -> dict[str, str]:
    """Outcomes of the fixture's visible tests on the unmodified fixture (cached)."""
    if fixture.name not in _baseline_cache:
        with tempfile.TemporaryDirectory(prefix="eval-baseline-") as tmp:
            root = Path(tmp) / "repo"
            shutil.copytree(fixture.repo, root, ignore=IGNORE)
            shutil.copytree(fixture.repo / "tests", root / "_visible", ignore=IGNORE)
            _baseline_cache[fixture.name] = _run_tests(fixture, root, "_visible", {})["outcomes"]
    return _baseline_cache[fixture.name]


def grade(fixture: Fixture, artifact: Path) -> Grade:
    """Grade the produced checkout ``artifact`` (a copy of the fixture's ``repo/``)."""
    baseline = visible_baseline(fixture)
    with tempfile.TemporaryDirectory(prefix="eval-grade-") as tmp:
        root = Path(tmp) / "repo"
        shutil.copytree(artifact, root, ignore=_ignore_artifact)
        shutil.copytree(fixture.hidden, root / "_hidden", ignore=IGNORE)
        shutil.copytree(fixture.repo / "tests", root / "_visible", ignore=IGNORE)
        record = Path(tmp) / "runtime.json"
        env = {"EVAL_RUNTIME_RECORD": str(record)} if fixture.runtime else {}

        hidden = _run_tests(fixture, root, "_hidden", env)
        runtime_ok: bool | None = None
        errors: list[str] = []
        if fixture.runtime:
            runtime_ok, errors = _runtime_cleanup(record)
        visible = _run_tests(fixture, root, "_visible", {})["outcomes"]

        boundary: list[str] = []
        for check in fixture.checks:
            if check["type"] == "no_import":
                boundary += check_no_import(root, check["scope"], check["module"])
            elif check["type"] == "unchanged":
                boundary += check_unchanged(root, fixture.repo, check["paths"])
            else:
                errors.append(f"unknown check type {check['type']!r}")
        stale = check_stale(root, fixture.stale)

    outcomes: dict[str, str] = hidden["outcomes"]
    if not outcomes:
        errors += [d[-3000:] for d in hidden["details"]]
    failures = [t for t, o in outcomes.items() if o != "pass"] or (
        [] if outcomes else ["no hidden tests ran"]
    )
    failures += [d.splitlines()[0] for d in hidden["details"] if d.splitlines()[0] not in outcomes]
    regressions = [
        t.replace("_visible.", "tests.", 1)
        for t, o in baseline.items()
        if o == "pass" and visible.get(t) != "pass"
    ]
    produced_tests = _hash_tree(artifact / "tests")
    modified = sorted(
        k for k, v in _hash_tree(fixture.repo / "tests").items() if produced_tests.get(k) != v
    )
    passed = sum(1 for o in outcomes.values() if o == "pass")
    return Grade(
        success=bool(outcomes) and not failures and not regressions and not boundary,
        hidden_passed=passed,
        hidden_total=len(outcomes),
        failures=sorted(set(failures)),
        regressions=regressions,
        boundary_violations=boundary,
        stale_context_mistakes=stale,
        tests_modified=modified,
        runtime_cleanup_ok=runtime_ok,
        grader_errors=errors,
        failure_details=[d[-1500:] for d in hidden["details"]],
    )
