"""Tests for the evaluation runner, grader, live parsing and summary."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL))

import grader  # noqa: E402
import live  # noqa: E402
import run  # noqa: E402
import summarize  # noqa: E402
from harness import all_fixtures, load_fixture, run_bounded, treatment  # noqa: E402

FIXTURES = all_fixtures()


def _variant(tmp_path: Path, name: str, overlay: str | None = None) -> Path:
    fixture = load_fixture(name)
    repo = tmp_path / "repo"
    shutil.copytree(fixture.repo, repo)
    if overlay:
        shutil.copytree(fixture.root / overlay, repo, dirs_exist_ok=True)
    return repo


def test_suite_covers_required_kinds() -> None:
    assert {f.name for f in FIXTURES} == {
        "ordinary_bug",
        "boundary",
        "version_api",
        "stale_context",
        "handoff",
        "runtime_http",
    }


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_deterministic_validation(fixture: object) -> None:
    results = run.validate_fixture(fixture)  # type: ignore[arg-type]
    assert [m for ok, m in results if not ok] == []


def test_deterministic_main_exit_code() -> None:
    assert run.main(["--tasks", "ordinary_bug"]) == 0


def test_unknown_treatment_exits_2() -> None:
    assert run.main(["--treatments", "nope"]) == 2


def test_regression_and_test_tampering_detected(tmp_path: Path) -> None:
    repo = _variant(tmp_path, "ordinary_bug", "solution")
    src = repo / "app" / "pagination.py"
    src.write_text(src.read_text().replace("return -(-n_items // per_page)", "return 0"))
    (repo / "tests" / "test_pagination.py").write_text("")
    g = grader.grade(load_fixture("ordinary_bug"), repo)
    assert g.hidden_passed == 3
    assert g.regressions == ["tests.test_pagination.PaginationTest.test_page_count"]
    assert g.tests_modified == ["test_pagination.py"]
    assert not g.success


def test_relative_and_string_imports_count_as_boundary_violations(tmp_path: Path) -> None:
    domain = tmp_path / "app" / "domain"
    domain.mkdir(parents=True)
    (domain / "a.py").write_text("from ..storage import customers\n")
    (domain / "b.py").write_text("import importlib\nimportlib.import_module('app.storage.x')\n")
    (domain / "c.py").write_text("from app.domain import a\nimport app.storageless\n")
    found = grader.check_no_import(tmp_path, "app/domain", "app.storage")
    assert [f.split(":")[0] for f in found] == ["app/domain/a.py", "app/domain/b.py"]


def test_pin_change_is_a_violation(tmp_path: Path) -> None:
    repo = _variant(tmp_path, "version_api", "solution")
    (repo / "pyproject.toml").write_text((repo / "pyproject.toml").read_text() + "\n")
    g = grader.grade(load_fixture("version_api"), repo)
    assert g.boundary_violations == ["pyproject.toml: changed (must stay as pinned)"]
    assert not g.success


def test_stale_marker_from_created_ini(tmp_path: Path) -> None:
    repo = _variant(tmp_path, "stale_context", "solution")
    (repo / "settings.ini").write_text("[server]\ntimeout_seconds = 30\n")
    g = grader.grade(load_fixture("stale_context"), repo)
    assert g.stale_context_mistakes == ["settings.ini exists (stale doc followed)"]
    assert g.success  # recorded separately; the hidden tests still pass here


def test_grader_ignores_agent_copies_of_hidden_dirs(tmp_path: Path) -> None:
    repo = _variant(tmp_path, "ordinary_bug")
    fake = repo / "_hidden"
    fake.mkdir()
    (fake / "test_fake.py").write_text(
        "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\n"
    )
    g = grader.grade(load_fixture("ordinary_bug"), repo)
    assert g.hidden_total == 3 and not g.success


def test_port_check_sees_listener() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert not grader.port_is_free(port)
    assert grader.port_is_free(port)


def test_leftover_service_is_reported_then_killed(tmp_path: Path) -> None:
    server = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import socket,time\ns=socket.socket()\n"
            "s.bind(('127.0.0.1',0)); s.listen(); print(s.getsockname()[1], flush=True)\n"
            "time.sleep(60)",
        ],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert server.stdout is not None
    port = int(server.stdout.readline())
    record = tmp_path / "runtime.json"
    record.write_text(json.dumps({"pid": server.pid, "port": port}))
    ok, errors = grader._runtime_cleanup(record)
    assert ok is False and len(errors) == 2
    assert server.wait(timeout=5) == -9
    assert grader.port_is_free(port)


def test_run_bounded_kills_process_group_on_timeout(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    start = time.monotonic()
    done = run_bounded(
        ["sh", "-c", f"sleep 60 & echo $! > {pidfile}; wait"], cwd=tmp_path, timeout=1
    )
    assert done.timed_out and time.monotonic() - start < 10
    child = int(pidfile.read_text())
    time.sleep(0.2)
    assert not grader.pid_alive(child) or _is_zombie(child)


def test_run_bounded_kills_leftover_background_process(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    done = run_bounded(
        ["sh", "-c", f"sleep 60 > /dev/null 2>&1 & echo $! > {pidfile}"], cwd=tmp_path, timeout=10
    )
    assert done.returncode == 0 and not done.timed_out
    time.sleep(0.2)
    child = int(pidfile.read_text())
    assert not grader.pid_alive(child) or _is_zombie(child)


def _is_zombie(pid: int) -> bool:
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return out.stdout.strip().startswith("Z") or not out.stdout.strip()


def test_trial_diff_excludes_virtualenv(tmp_path: Path) -> None:
    treatment("minimal")(load_fixture("version_api"), tmp_path)
    repo = tmp_path / "repo"
    (repo / ".venv").mkdir()
    (repo / ".venv" / "x").write_text("x")
    (repo / "app" / "new.py").write_text("x = 1\n")
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert status.split() == ["??", "app/new.py"]


def test_proposed_hook_runs_after_framework(tmp_path: Path) -> None:
    proposed = EVAL / "proposed" / "_test_marker.py"
    proposed.write_text(
        "def apply(root, fixture):\n"
        "    (root / 'docs' / 'extra.md').parent.mkdir(exist_ok=True)\n"
        "    (root / 'docs' / 'extra.md').write_text('x')\n"
    )
    try:
        treatment("proposed:_test_marker")(load_fixture("boundary"), tmp_path)
    finally:
        proposed.unlink()
    assert (tmp_path / "AGENTS.md").is_file() and (tmp_path / "docs" / "extra.md").is_file()


def test_claude_command_is_bounded() -> None:
    cmd = live.build_claude_cmd("do it", live.LiveConfig("haiku", 7, 0.25, 60))
    joined = " ".join(cmd)
    for flag in (
        "--max-turns 7",
        "--max-budget-usd 0.25",
        "--no-session-persistence",
        "--model haiku",
        "--setting-sources project",
        "--strict-mcp-config",
    ):
        assert flag in joined
    assert live.claude_env()["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"


def test_parse_stream_extracts_result_and_context_reads(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    lines = [
        {"type": "system", "subtype": "init"},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": f"{root}/AGENTS.md"},
                    },
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "cat docs/architecture.md"},
                    },
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": f"{root}/repo/app/x.py"},
                    },
                ]
            },
        },
        {
            "type": "result",
            "total_cost_usd": 0.01,
            "num_turns": 3,
            "result": "Done.",
            "usage": {"input_tokens": 5, "output_tokens": 9},
            "modelUsage": {"claude-x": {}},
        },
    ]
    text = "\n".join(json.dumps(x) for x in lines) + "\nnot json\n"
    trace = live.parse_stream(text, root)
    assert trace.result and trace.result["total_cost_usd"] == 0.01
    assert trace.context_files_read == ["AGENTS.md", "docs/architecture.md"]
    assert trace.tool_calls == 3 and trace.parse_errors == 1


def test_question_heuristic() -> None:
    assert live.question_count("Fixed it. Should I also add a test? Thanks.") == 1
    assert live.question_count("Why did it fail? Because of the off-by-one.") == 0


def test_run_agent_records_blocked_when_cli_missing(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(live, "build_claude_cmd", lambda p, c: ["/nonexistent/claude"])
    row = live.run_agent("x", tmp_path, live.LiveConfig("haiku", 1, 0.1, 5), tmp_path)
    assert row["status"] == "blocked"


def test_fisher_matches_known_values() -> None:
    assert summarize.fisher_two_sided(5, 0, 0, 5) == pytest.approx(0.007937, abs=1e-5)
    assert summarize.fisher_two_sided(3, 2, 2, 3) == pytest.approx(1.0)


def test_summary_labels_small_samples_inconclusive() -> None:
    base = {"run_id": "r", "label": "smoke", "model": "m", "kit_revision": "k"}
    rows = [
        {
            **base,
            "task": "t",
            "treatment": "minimal",
            "success": False,
            "cost_usd": 0.1,
            "elapsed_s": 10,
            "status": "completed",
        },
        {
            **base,
            "task": "t",
            "treatment": "framework",
            "success": True,
            "cost_usd": 0.2,
            "elapsed_s": 20,
            "status": "completed",
        },
        {**base, "task": "t", "treatment": "framework", "status": "skipped"},
    ]
    text = summarize.summarize(rows)
    assert "inconclusive (n<5 per arm)" in text
    assert "| t | framework | 1 | 1/1 |" in text and "skipped" in text
    assert "label: smoke" in text
