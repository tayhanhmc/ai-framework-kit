"""Real Git lifecycle tests; deterministic stand-in for the metered Codex service."""

from __future__ import annotations

import io
import json
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

if sys.platform == "win32":
    pytest.skip("The review coordinator supports macOS/Linux only", allow_module_level=True)

import cross_review as cr  # noqa: E402 - skip unsupported platforms before importing


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A framework repo with one workspace whose product clone has one commit."""
    framework = tmp_path / "framework"
    (framework / "ai/scripts").mkdir(parents=True)
    (framework / "AGENTS.md").write_text("# Test framework\n")
    (framework / "shop").mkdir()
    (framework / "shop/workspace.toml").write_text('name = "shop"\n')
    for repo in (framework, framework / "shop/repo"):
        repo.mkdir(exist_ok=True)
        cr.git(repo, "init", "-b", "main")
        cr.git(repo, "config", "user.name", "Review Test")
        cr.git(repo, "config", "user.email", "review@example.invalid")
    (framework / ".gitignore").write_text("shop/repo/\n")
    (framework / "shop/repo/code.py").write_text("value = 1\n")
    for repo in (framework, framework / "shop/repo"):
        cr.git(repo, "add", ".")
        cr.git(repo, "commit", "-m", "initial")
    return framework


def result(*, issue: bool = False, complete: bool = True) -> dict[str, Any]:
    finding = {
        "priority": "P1",
        "location": "code.py:1",
        "issue": "Wrong value",
        "evidence": "Caller requires 2",
        "suggestion": "Use 2",
    }
    return {
        "complete": complete,
        "summary": "Checked the change",
        "limitations": [],
        "findings": [finding] if issue else [],
    }


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Approves unless the prompt's staged change sets value = 3."""
    prompts: list[str] = []

    def fake(target: Path, folder: Path, prompt: str, model: str | None) -> dict[str, Any]:
        prompts.append(prompt)
        return result(issue="value = 3" in frozen_diff(target, prompt) or "push --force" in prompt)

    monkeypatch.setattr(cr, "codex", fake)
    return prompts


def frozen_diff(target: Path, prompt: str) -> str:
    """The change a commit review names, read the way the prompt tells Codex to."""
    match = re.search(r"`git diff (\S+) (\S+)`", prompt)
    return cr.git(target, "diff", *match.groups()).stdout.decode() if match else ""


def stage(repo: Path, value: int, name: str = "code.py") -> None:
    (repo / name).write_text(f"value = {value}\n")
    cr.git(repo, "add", name)


def bash(root: Path, command: str, cwd: Path | None = None) -> dict[str, Any]:
    return cr.hook(
        {"cwd": str(cwd or root), "tool_name": "Bash", "tool_input": {"command": command}}
    )


def decision(output: dict[str, Any]) -> str | None:
    value = output.get("hookSpecificOutput", {}).get("permissionDecision")
    return None if value is None else str(value)


def test_staged_tree_review_and_receipt_reuse(root: Path, provider: list[str]) -> None:
    clone = root / "shop/repo"
    with pytest.raises(cr.ReviewError, match="nothing staged"):
        cr.review_staged(clone, [])
    stage(clone, 2)
    receipt = cr.review_staged(clone, [])
    assert receipt["approved"] and cr.approved(clone)
    assert cr.review_staged(clone, []) == receipt and len(provider) == 1  # Same tree.
    (clone / "code.py").write_text("value = 9\n")  # Unstaged edits are not committed.
    assert cr.approved(clone)
    stage(clone, 3)
    blocked = cr.review_staged(clone, [])
    assert not blocked["approved"] and not cr.approved(clone)
    assert cr.review_staged(clone, []) == blocked and len(provider) == 2  # No re-rolling.
    answer = cr.review_staged(clone, [], feedback="Value 3 is required by the caller")
    assert "IMPLEMENTER RESPONSE" in provider[-1] and "Wrong value" in provider[-1]
    assert not answer["approved"] and len(provider) == 3
    stage(clone, 2)
    assert cr.approved(clone)  # Back to the reviewed tree.


def test_context_files_reach_the_reviewer(root: Path, provider: list[str], tmp_path: Path) -> None:
    task = tmp_path / "task.md"
    task.write_text("Value must be 2")
    stage(root / "shop/repo", 2)
    cr.review_staged(root / "shop/repo", [task])
    assert "Value must be 2" in provider[-1] and "git diff --cached" in provider[-1]


def test_review_covers_the_frozen_snapshot_even_if_the_index_moves(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = root / "shop/repo"
    seen: list[str] = []

    def moving(target: Path, folder: Path, prompt: str, model: str | None) -> dict[str, Any]:
        stage(clone, 3)  # A -> B (buggy) while Codex reads.
        seen.append(frozen_diff(target, prompt))
        stage(clone, 2)  # ... and back to A.
        stage(clone, 3)
        return result(issue="value = 3" in seen[-1])

    monkeypatch.setattr(cr, "codex", moving)
    stage(clone, 2)
    assert cr.review_staged(clone, [])["approved"]
    assert "+value = 2" in seen[0] and "value = 3" not in seen[0]  # Codex saw A only.
    assert not cr.approved(clone)  # B was never reviewed.
    stage(clone, 2)
    assert cr.approved(clone)


def test_receipt_is_bound_to_the_parent_commit(root: Path, provider: list[str]) -> None:
    clone = root / "shop/repo"
    stage(clone, 2)
    assert cr.review_staged(clone, [])["approved"]
    staged_tree = cr.tree(clone)
    cr.git(clone, "stash")
    cr.git(clone, "commit", "--allow-empty", "-m", "elsewhere")
    cr.git(clone, "stash", "pop", "--index")
    assert cr.tree(clone) == staged_tree and not cr.approved(clone)  # New parent only.


def test_codex_failure_never_passes_and_is_retried(
    root: Path, provider: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = root / "shop/repo"
    stage(clone, 3)
    assert not cr.review_staged(clone, [])["approved"]  # A real blocking finding.
    reviewer = cr.codex

    def fail(target: Path, folder: Path, prompt: str, model: str | None) -> dict[str, Any]:
        raise cr.ReviewError("rate limited")

    monkeypatch.setattr(cr, "codex", fail)
    receipt = cr.review_staged(clone, [], feedback="The caller needs 3")
    text = cr.describe(receipt)
    assert not receipt["approved"] and "Wrong value" in text and "rate limited" in text
    monkeypatch.setattr(cr, "codex", reviewer)
    stage(clone, 2)
    assert cr.review_staged(clone, [])["approved"] and cr.approved(clone)
    for crash in (OSError("no codex binary"), ValueError("bad JSON")):

        def broken(*_args: Any, crash: Exception = crash) -> dict[str, Any]:
            raise crash

        monkeypatch.setattr(cr, "codex", broken)
        receipt = cr.review_staged(clone, [], feedback="Please recheck")
        assert not receipt["approved"] and not cr.approved(clone)  # Old approval revoked.
    monkeypatch.setattr(cr, "codex", reviewer)
    stage(clone, 3)
    calls = len(provider)
    assert not cr.review_staged(clone, [])["approved"]  # A failed attempt is retried.
    assert len(provider) == calls + 1 and "Wrong value" in provider[-1]


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {**result(), "complete": "yes"},
        {**result(), "limitations": "none"},
        {**result(), "findings": {}},
        {**result(), "findings": [{"priority": "P1"}]},
    ],
)
def test_malformed_output_never_passes(bad: dict[str, Any]) -> None:
    with pytest.raises(cr.ReviewError):
        cr.validate_result(bad)


def test_incomplete_and_advisory() -> None:
    assert not cr.validate_result(result(complete=False))
    advisory = result(issue=True)
    advisory["findings"][0]["priority"] = "P3"
    assert cr.validate_result(advisory)


def test_lock_prevents_duplicate_review(root: Path, provider: list[str]) -> None:
    clone = root / "shop/repo"
    stage(clone, 2)
    with cr.locked(cr.store(clone) / "review.lock"), pytest.raises(cr.ReviewError, match="already"):
        cr.review_staged(clone, [])


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'fix: add tests'",
        "cd shop/repo && git commit -qm 'msg -a'",
        "git -C shop/repo commit -m 'x; y'",
        "git commit --amend --no-edit --author='A <a@example.com>'",
        "git commit -F msg.txt | tee log",
        "git commit --message=done -s 2>&1",
        "git --no-pager commit -m x > /dev/null 2>&1 && git push && git log -1",
    ],
)
def test_plain_commits_are_checked(root: Path, provider: list[str], command: str) -> None:
    clone = root / "shop/repo"
    assert bash(root, command) == {}  # Nothing staged anywhere.
    stage(clone, 2)
    denied = bash(root, command)
    assert decision(denied) == "deny"
    reason = denied["hookSpecificOutput"]["permissionDecisionReason"]
    assert "cross_review.py review --target shop/repo" in reason
    assert cr.main(["review", "--target", str(clone)]) == 0
    assert bash(root, command, cwd=clone) == {}


@pytest.mark.parametrize(
    ("command", "why"),
    [
        ("git commit -am x", "outside the reviewed index"),
        ("git commit --all -m x", "outside the reviewed index"),
        ("git commit -p", "outside the reviewed index"),
        ("git commit --pathspec-from=list.txt -m x", "outside the reviewed index"),
        ("git commit code.py -m x", "outside the reviewed index"),
        ("git commit -m x -- code.py", "outside the reviewed index"),
        ("git add code.py && git commit -m x", "other Git commands"),
        ("git stash pop; git commit -m x", "other Git commands"),
        ("export GIT_INDEX_FILE=/tmp/i; git commit -m x", "other Git commands"),
        ('git commit -m "$(git add -A)msg"', "substitution"),
        ("git commit -m `date`", "substitution"),
        ("git commit -m a && git commit -m b", "once per command"),
        ("bash -c 'git commit -m x'", "wrapper"),
        ("git commit -m ok && bash -c 'git add o.py; git commit -m x'", "wrapper"),
        ("echo `git commit -m x`", "wrapper"),
        ("time git commit -m x", "wrapper"),
        ("command git commit -m x", "wrapper"),
        ("{ git commit -m x; }", "wrapper"),
        ("if true; then git commit -m x; fi", "wrapper"),
        ("GIT_INDEX_FILE=/tmp/other git commit -m x", "wrapper"),
        ("env GIT_DIR=/tmp/x git commit -m x", "wrapper"),
        ("git --git-dir=/tmp/x commit -m x", "wrapper"),
        ("git -c core.hooksPath=/dev/null commit -m x", "wrapper"),
        ("git --no-pager -c core.hooksPath=/dev/null commit -m x", "wrapper"),
        ("git commit -m 'unterminated", "cannot be parsed"),
    ],
)
def test_commit_forms_the_hook_cannot_see_are_denied(
    root: Path, provider: list[str], command: str, why: str
) -> None:
    stage(root / "shop/repo", 2)
    assert cr.main(["review", "--target", str(root / "shop/repo")]) == 0
    denied = bash(root, command)
    assert decision(denied) == "deny" and why in json.dumps(denied)


@pytest.mark.parametrize(
    "command", ["git log --grep commit", "git status && echo commit", "git commit-tree HEAD^{tree}"]
)
def test_other_commands_pass(root: Path, provider: list[str], command: str) -> None:
    stage(root / "shop/repo", 2)
    assert bash(root, command) == {}


def test_every_checkout_with_staged_work_is_checked(root: Path, provider: list[str]) -> None:
    clone = root / "shop/repo"
    lane = root / "shop/.worktrees/feature"
    cr.git(clone, "worktree", "add", "-b", "feature", str(lane))
    gone = root / "shop/.worktrees/gone"
    cr.git(clone, "worktree", "add", "-b", "gone", str(gone))
    for path in sorted(gone.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    gone.rmdir()  # Listed by Git but deleted: skipped.
    assert set(cr.checkouts(root)) == {root, clone, lane}
    stage(lane, 2)
    (root / "notes.md").write_text("framework change\n")
    cr.git(root, "add", "notes.md")
    reason = json.dumps(bash(root, "git commit -m x"))
    assert "shop/.worktrees/feature" in reason and "--target ." in reason
    assert cr.main(["review", "--target", str(lane)]) == 0
    assert cr.main(["review", "--target", str(root)]) == 0
    assert bash(root, "git commit -m x") == {}


def test_hook_failure_denies_and_reviewer_is_exempt(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {"cwd": "/", "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert cr.main(["hook"]) == 0
    assert "could not run" in capsys.readouterr().out  # No framework root: fail closed.
    monkeypatch.setenv("AI_FRAMEWORK_REVIEWER", "1")
    assert cr.hook(payload) == {}
    monkeypatch.delenv("AI_FRAMEWORK_REVIEWER")
    assert bash(root, "ls") == {}  # Only commits are checked.


def test_plan_hook_reviews_then_asks_on_the_same_plan(root: Path, provider: list[str]) -> None:
    def plan(text: str) -> dict[str, Any]:
        return cr.hook(
            {"cwd": str(root), "tool_name": "ExitPlanMode", "tool_input": {"plan": text}}
        )

    unsafe = "1. Rewrite history\n2. git push --force origin main"
    assert decision(plan(unsafe)) == "deny" and len(provider) == 1
    asked = plan(unsafe)
    assert decision(asked) == "ask" and "already blocked" in json.dumps(asked)
    assert len(provider) == 1  # The user decides; Codex is not asked again.
    assert plan("1. Open a branch\n2. Push it for review") == {} and len(provider) == 2
    assert cr.hook({"cwd": str(root), "tool_name": "Edit", "tool_input": {}}) == {}


def test_cli_plan_check_and_errors(
    root: Path, provider: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = root / "shop/repo"
    assert cr.main(["check", "--target", str(clone)]) == 0  # Nothing staged.
    stage(clone, 3)
    assert cr.main(["check", "--target", str(clone)]) == 1
    response = tmp_path / "response.txt"
    response.write_text("The caller was updated")
    assert cr.main(["review", "--target", str(clone), "--feedback", str(response)]) == 1
    assert "Codex review BLOCKED" in capsys.readouterr().out
    plan = tmp_path / "plan.md"
    plan.write_text("Small safe plan")
    assert cr.main(["review", "--target", str(root), "--plan", str(plan)]) == 0
    assert cr.main(["review", "--target", str(tmp_path / "missing")]) == 2


def test_codex_command_and_output(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "bin/codex"
    binary.parent.mkdir()
    args_file = tmp_path / "args"
    binary.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{args_file}"\n'
        'while [ "$1" != "--output-last-message" ]; do shift; done\n'
        f"echo '{json.dumps(result())}' > \"$2\"\n"
    )
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", f"{binary.parent}{os.pathsep}{os.environ['PATH']}")
    stage(root / "shop/repo", 2)
    assert cr.review_staged(root / "shop/repo", [], model="reviewer-model")["approved"]
    args = args_file.read_text().split("\n")
    assert {"read-only", "--ephemeral", "reviewer-model"} <= set(args)
    binary.write_text("#!/bin/sh\nexit 3\n")
    stage(root / "shop/repo", 5)
    assert "exit 3" in cr.review_staged(root / "shop/repo", [])["error"]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_interrupted_review_stops_codex_and_records_nothing(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signum: int
) -> None:
    binary = tmp_path / "codex"
    pids = tmp_path / "pids"
    binary.write_text(
        f"#!/bin/sh\nsleep 60 &\necho $! > '{pids}.tmp'\nmv '{pids}.tmp' '{pids}'\nwait\n"
    )
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])

    def interrupt() -> None:
        while not pids.exists():
            time.sleep(0.01)
        os.kill(os.getpid(), signum)

    clone = root / "shop/repo"
    stage(clone, 2)
    threading.Thread(target=interrupt, daemon=True).start()
    with pytest.raises(cr.Interrupted):
        cr.review_staged(clone, [])
    sleeper = int(pids.read_text())
    deadline = time.monotonic() + 5
    while alive(sleeper) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(sleeper)
    assert not (cr.store(clone) / f"{cr.tree(clone)}.json").exists()
