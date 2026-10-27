"""Real Git lifecycle tests; deterministic stand-in for the metered Codex service."""

from __future__ import annotations

import io
import json
import os
import re
import signal
import subprocess
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
    (framework / ".gitignore").write_text("*/repo/\n*/.worktrees/\n")
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


@pytest.fixture
def add_test(tmp_path: Path) -> Path:
    """A plan file outside the framework, so writing it changes no snapshot."""
    plan = tmp_path / "add-test.md"
    plan.write_text("Add a test")
    return plan


def frozen_diff(target: Path, prompt: str) -> str:
    """The change a commit review names, read the way the prompt tells Codex to."""
    match = re.search(r"`git diff ([0-9a-f]{40}) ([0-9a-f]{40})`", prompt)
    return cr.git(target, "diff", *match.groups()).stdout.decode() if match else ""


def stage(repo: Path, value: int, name: str = "code.py") -> None:
    (repo / name).write_text(f"value = {value}\n")
    cr.git(repo, "add", name)


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


def test_product_reviews_keep_the_product_s_platforms(root: Path, provider: list[str]) -> None:
    stage(root / "shop/repo", 2)
    cr.review_staged(root / "shop/repo", [])
    # Only the framework's own scripts are scoped to macOS and Linux.
    prompt = " ".join(provider[-1].split())
    assert "Product code is reviewed for the platforms its own docs name" in prompt


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
    assert cr.main(["review", "--target", str(clone)]) == 2  # Could not run, not blocked.
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


def test_checkouts_skip_deleted_lanes(root: Path) -> None:
    clone = root / "shop/repo"
    lane = root / "shop/.worktrees/feature"
    cr.git(clone, "worktree", "add", "-b", "feature", str(lane))
    gone = root / "shop/.worktrees/gone"
    cr.git(clone, "worktree", "add", "-b", "gone", str(gone))
    for path in sorted(gone.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    gone.rmdir()  # Listed by Git but deleted: skipped.
    assert set(cr.checkouts(root)) == {root, clone, lane}


def test_hook_failure_denies_and_reviewer_is_exempt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {"cwd": "/", "tool_name": "ExitPlanMode", "tool_input": {"plan": "x"}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert cr.main(["hook"]) == 0
    assert "could not run" in capsys.readouterr().out  # No framework root: fail closed.
    monkeypatch.setenv("AI_FRAMEWORK_REVIEWER", "1")
    assert cr.hook(payload) == {}


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


def test_changed_context_or_repository_is_reviewed_again(
    root: Path, provider: list[str], tmp_path: Path, add_test: Path
) -> None:
    clone = root / "shop/repo"
    task = tmp_path / "task.md"
    task.write_text("Value must be 2")
    stage(clone, 2)
    cr.review_staged(clone, [task])
    cr.review_staged(clone, [task])
    assert len(provider) == 1  # Same content and context: reused.
    task.write_text("Value must be 5")
    cr.review_staged(clone, [task])
    assert len(provider) == 2 and "Value must be 5" in provider[-1]
    cr.review_plan(root, add_test)
    cr.review_plan(root, add_test)
    assert len(provider) == 3
    cr.git(clone, "commit", "-m", "advance")
    cr.review_plan(root, add_test)
    assert len(provider) == 4  # The code the plan was checked against moved.


def commit(repo: Path, message: str = "x", *extra: str) -> None:
    """A real `git commit`, running the repository's own hooks."""
    cr.git(repo, "commit", "-q", "-m", message, *extra)


def test_range_check_lists_commits_without_a_review(
    root: Path, provider: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    clone = root / "shop/repo"
    assert cr.main(["check", "--target", str(clone), "--range", "HEAD"]) == 1
    assert "not reviewed" in capsys.readouterr().out  # The initial commit had no review.
    stage(clone, 2)
    assert cr.main(["review", "--target", str(clone)]) == 0
    commit(clone)
    assert cr.main(["check", "--target", str(clone), "--range", "HEAD~1..HEAD"]) == 0
    stage(clone, 4)  # Amend: the staged review covers only the increment on the old commit.
    assert cr.main(["review", "--target", str(clone)]) == 0
    commit(clone, "x", "--amend")
    assert cr.main(["check", "--target", str(clone), "--range", "HEAD~1..HEAD"]) == 1
    assert cr.main(["review", "--target", str(clone), "--commit", "HEAD"]) == 0
    assert cr.main(["check", "--target", str(clone), "--range", "HEAD~1..HEAD"]) == 0


def test_changed_context_revokes_the_commit_approval(
    root: Path, provider: list[str], tmp_path: Path
) -> None:
    clone = root / "shop/repo"
    task = tmp_path / "task.md"
    task.write_text("Value must be 2")
    stage(clone, 2)
    receipt = cr.review_staged(clone, [task])
    assert receipt["context"][0]["path"] == str(task.resolve()) and cr.approved(clone)
    task.write_text("Value must be 5")
    assert not cr.approved(clone)
    assert "task.md" in str(cr.approval_problem(clone, *cr.candidate(clone)))
    assert cr.main(["check", "--target", str(clone)]) == 1
    cr.review_staged(clone, [task])
    assert cr.approved(clone) and len(provider) == 2
    task.unlink()
    assert not cr.approved(clone)  # A deleted context file is a change too.


def test_older_policy_receipts_do_not_count(
    root: Path, provider: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = root / "shop/repo"
    stage(clone, 2)
    cr.review_staged(clone, [])
    monkeypatch.setattr(cr, "POLICY", "next")
    assert not cr.approved(clone)
    assert "older review policy" in str(cr.approval_problem(clone, *cr.candidate(clone)))
    cr.review_staged(clone, [])
    assert cr.approved(clone) and len(provider) == 2


def test_model_choice_and_force_review_again(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    models: list[str | None] = []

    def fake(target: Path, folder: Path, prompt: str, model: str | None) -> dict[str, Any]:
        models.append(model)
        return result()

    monkeypatch.setattr(cr, "codex", fake)
    clone = root / "shop/repo"
    stage(clone, 2)
    cr.review_staged(clone, [], model="first")
    cr.review_staged(clone, [], model="first")
    receipt = cr.review_staged(clone, [], model="second")
    assert models == ["first", "second"]
    assert receipt["model"] == "second" and receipt["policy"] == cr.POLICY
    assert receipt["reviewed_at"] and receipt["run"]
    assert cr.main(["review", "--target", str(clone), "--model", "second", "--force"]) == 0
    assert models == ["first", "second", "second"]


@pytest.mark.parametrize("change", ["staged", "unstaged", "untracked", "framework"])
def test_plan_review_covers_uncommitted_work(
    root: Path, provider: list[str], change: str, add_test: Path
) -> None:
    clone = root / "shop/repo"
    cr.review_plan(root / "shop", add_test)
    cr.review_plan(root / "shop", add_test)
    assert len(provider) == 1
    if change == "staged":
        stage(clone, 7)
    elif change == "unstaged":
        (clone / "code.py").write_text("value = 7\n")
    elif change == "untracked":
        (clone / "new.py").write_text("x = 1\n")
    else:
        (root / "notes.md").write_text("framework note\n")
    status = cr.out(clone, "status", "--porcelain")
    cr.review_plan(root / "shop", add_test)
    assert len(provider) == 2  # The snapshot the plan was checked against changed.
    assert cr.out(clone, "status", "--porcelain") == status  # The real index is untouched.


def test_plan_in_a_workspace_ignores_other_products(
    root: Path, provider: list[str], add_test: Path
) -> None:
    other = root / "other"
    other.mkdir()
    (other / "workspace.toml").write_text('name = "other"\n')
    cr.git(other, "init", "repo")
    cr.review_plan(root / "shop", add_test)
    (other / "repo/file.txt").write_text("unrelated\n")
    cr.review_plan(root / "shop", add_test)
    assert len(provider) == 1  # Another product's work does not invalidate this plan.
    cr.review_plan(root, add_test)
    (other / "repo/file.txt").write_text("changed\n")
    cr.review_plan(root, add_test)
    assert len(provider) == 3  # From the root, every checkout counts.


def test_blocked_plan_is_reviewed_again_when_the_code_moved(
    root: Path, provider: list[str]
) -> None:
    def plan(text: str) -> dict[str, Any]:
        return cr.hook(
            {"cwd": str(root), "tool_name": "ExitPlanMode", "tool_input": {"plan": text}}
        )

    unsafe = "1. git push --force origin main"
    assert decision(plan(unsafe)) == "deny" and len(provider) == 1
    stage(root / "shop/repo", 2)
    assert decision(plan(unsafe)) == "deny" and len(provider) == 2  # Fresh review, not "ask".
    assert decision(plan(unsafe)) == "ask" and len(provider) == 2


def test_amending_an_unreviewed_commit_does_not_launder_it(root: Path, provider: list[str]) -> None:
    clone = root / "shop/repo"
    stage(clone, 2)
    commit(clone, "made outside Claude")  # Never reviewed.
    stage(clone, 4)
    assert cr.main(["review", "--target", str(clone)]) == 0  # Only the increment.
    commit(clone, "x", "--amend")
    assert cr.main(["check", "--target", str(clone), "--range", "HEAD~1..HEAD"]) == 1


def test_landed_commits_keep_their_review_when_context_changes(
    root: Path, provider: list[str], tmp_path: Path
) -> None:
    clone = root / "shop/repo"
    task = tmp_path / "task.md"
    task.write_text("Value must be 2")
    stage(clone, 2)
    cr.review_staged(clone, [task])
    commit(clone)
    task.write_text("Value must be 2\n- [x] done")  # Progress recorded after landing.
    assert cr.main(["check", "--target", str(clone), "--range", "HEAD~1..HEAD"]) == 0


@pytest.mark.parametrize(
    "argv",
    [["check", "--commit", "HEAD"], ["review", "--range", "HEAD"], ["check", "--plan", "p.md"]],
)
def test_subjects_must_match_the_action(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cr.main(argv)
    assert exc.value.code == 2


def test_message_only_amend_keeps_the_reviewed_commit(root: Path, provider: list[str]) -> None:
    clone = root / "shop/repo"
    stage(clone, 2)
    assert cr.main(["review", "--target", str(clone)]) == 0
    commit(clone)
    commit(clone, "reworded", "--amend")  # Same parent and tree: the same reviewed content.
    assert cr.main(["check", "--target", str(clone), "--range", "HEAD~1..HEAD"]) == 0


def test_merge_rewritten_by_a_hook_fails_the_range_check(root: Path, provider: list[str]) -> None:
    clone = root / "shop/repo"
    base = cr.out(clone, "rev-parse", "HEAD")
    cr.git(clone, "checkout", "-q", "-b", "feat")
    stage(clone, 2, "feat.py")
    cr.review_staged(clone, [])
    commit(clone, "feat")
    cr.git(clone, "checkout", "-q", "main")
    stage(clone, 2, "main.py")
    cr.review_staged(clone, [])
    commit(clone, "main")
    cr.git(clone, "merge", "-q", "--no-ff", "--no-commit", "feat")
    assert cr.main(["review", "--target", str(clone)]) == 0
    hooks = clone / ".git/hooks"
    (hooks / "pre-commit").write_text("#!/bin/sh\necho 'value = 3' > evil.py\ngit add evil.py\n")
    (hooks / "pre-commit").chmod(0o755)
    commit(clone, "merge")
    (hooks / "pre-commit").unlink()
    assert cr.main(["check", "--target", str(clone), "--range", f"{base}..HEAD"]) == 1
    assert cr.main(["review", "--target", str(clone), "--commit", "HEAD"]) == 1  # Sees value 3.


def test_plan_snapshot_never_writes_untracked_secrets(root: Path, provider: list[str]) -> None:
    secret = b"API_KEY=synthetic-not-a-real-key\n"
    (root / "shop/.env").write_bytes(secret)
    (root / "id_rsa_backup").write_bytes(secret + b"x")
    tree_id = cr.snapshot(root)
    listed = cr.out(root, "ls-tree", "-r", "--name-only", tree_id)
    assert ".env" not in listed and "id_rsa" not in listed
    for data in (secret, secret + b"x"):  # Not in the object store at all.
        blob = (
            subprocess.run(
                ["git", "-C", str(root), "hash-object", "--stdin"], input=data, capture_output=True
            )
            .stdout.decode()
            .strip()
        )
        assert cr.git(root, "cat-file", "-e", blob, ok=(0, 1, 128)).returncode != 0


def test_plan_review_sees_index_only_changes(
    root: Path, provider: list[str], add_test: Path
) -> None:
    clone = root / "shop/repo"
    cr.review_plan(root / "shop", add_test)
    stage(clone, 7)
    (clone / "code.py").write_text("value = 1\n")  # Working copy back to HEAD, index changed.
    cr.review_plan(root / "shop", add_test)
    assert len(provider) == 2


def test_interrupted_re_review_revokes_the_old_approval(
    root: Path, provider: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = root / "shop/repo"
    stage(clone, 2)
    assert cr.review_staged(clone, [])["approved"] and cr.approved(clone)

    def interrupted(*_args: Any) -> dict[str, Any]:
        raise cr.Interrupted(signal.SIGTERM)

    monkeypatch.setattr(cr, "codex", interrupted)
    with pytest.raises(cr.Interrupted):
        cr.review_staged(clone, [], force=True)
    assert not cr.approved(clone)


def test_context_is_read_once_for_prompt_and_receipt(
    root: Path, provider: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = tmp_path / "task.md"
    task.write_text("Value must be 2")
    reads: list[Path] = []
    original = Path.read_bytes

    def counting(self: Path) -> bytes:
        data = original(self)
        if self == task:
            reads.append(self)
            self.write_text("Value must be 5")  # Changes right after this read.
        return data

    monkeypatch.setattr(Path, "read_bytes", counting)
    stage(root / "shop/repo", 2)
    receipt = cr.review_staged(root / "shop/repo", [task])
    monkeypatch.setattr(Path, "read_bytes", original)
    assert len(reads) == 1 and "Value must be 2" in provider[-1]
    assert receipt["context"][0]["sha256"] == cr.sha256(b"Value must be 2")


def test_plan_review_uses_and_binds_context(
    root: Path, provider: list[str], tmp_path: Path
) -> None:
    task = tmp_path / "requirements.md"
    task.write_text("Plans must keep value at 2")
    plan = tmp_path / "plan.md"
    plan.write_text("Add a test")
    args = ["review", "--target", str(root), "--plan", str(plan), "--context", str(task)]
    assert cr.main(args) == 0 and "Plans must keep value at 2" in provider[-1]
    assert cr.main(args) == 0 and len(provider) == 1  # Same plan and context: reused.
    task.write_text("Plans must keep value at 3")
    assert cr.main(args) == 0 and len(provider) == 2  # Changed requirements: reviewed again.


def answer(tmp_path: Path, text: str = "The caller needs 3") -> str:
    response = tmp_path / "response.txt"
    response.write_text(text)
    return str(response)


def staged_receipt(clone: Path) -> dict[str, Any]:
    return cr.read_json(cr.store(clone) / f"{cr.commit_key(*cr.candidate(clone))}.json")


def test_two_blocking_rounds_then_the_owner_decides(
    root: Path, provider: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = root / "shop/repo"
    stage(clone, 2)
    assert cr.main(["review", "--target", str(clone)]) == 0
    stage(clone, 3)
    review = ["review", "--target", str(clone)]
    assert cr.main(review) == 1
    assert cr.main([*review, "--feedback", answer(tmp_path)]) == 1
    capsys.readouterr()
    calls = len(provider)
    for extra in ([], ["--force"], ["--feedback", answer(tmp_path)]):
        assert cr.main([*review, *extra]) == 4
    stage(clone, 5)  # New content in the same unit is refused too.
    assert cr.main(review) == 4 and len(provider) == calls
    out = capsys.readouterr().out
    assert "Wrong value" in out and "go to the owner" in out
    stage(clone, 2)
    assert cr.main(review) == 0 and len(provider) == calls  # A current approval still counts.
    stage(clone, 3)
    assert cr.main([*review, "--owner-approved", "--force"]) == 1 and len(provider) == calls + 1
    assert staged_receipt(clone)["owner_approved"] is True
    assert cr.main([*review, "--force"]) == 4  # The owner allowed exactly one round.
    assert cr.main([*review, "--owner-approved", "--force"]) == 1 and len(provider) == calls + 2


def test_owner_approval_below_the_limit_grants_nothing(
    root: Path, provider: list[str], tmp_path: Path
) -> None:
    clone = root / "shop/repo"
    stage(clone, 3)
    review = ["review", "--target", str(clone)]
    assert cr.main([*review, "--owner-approved"]) == 1
    assert "owner_approved" not in staged_receipt(clone)
    assert cr.main([*review, "--feedback", answer(tmp_path)]) == 1
    assert cr.main([*review, "--force"]) == 4


def test_amending_keeps_the_round_count(root: Path, provider: list[str]) -> None:
    clone = root / "shop/repo"
    review = ["review", "--target", str(clone), "--commit", "HEAD"]
    for round_ in (1, 2, 3):
        stage(clone, 3, f"fix{round_}.py")
        commit(clone, "fix", *(["--amend"] if round_ > 1 else []))
        assert cr.main(review) == (1 if round_ < 3 else 4)  # Every amend is a new sha.
    assert len(provider) == 2
    stage(clone, 3, "next.py")  # The next commit on the branch is a new unit.
    assert cr.main(["review", "--target", str(clone)]) == 1 and len(provider) == 3


def test_new_branch_or_rebase_starts_fresh(root: Path, provider: list[str], tmp_path: Path) -> None:
    clone = root / "shop/repo"
    lane = root / "shop/.worktrees/feature"
    cr.git(clone, "worktree", "add", "-q", "-b", "feature", str(lane))
    stage(lane, 3)
    commit(lane, "feature")
    review = ["review", "--target", str(lane), "--commit", "HEAD"]
    assert cr.main(review) == 1
    assert cr.main([*review, "--feedback", answer(tmp_path)]) == 1
    assert cr.main([*review, "--force"]) == 4
    cr.git(lane, "checkout", "-q", "-b", "retry")
    assert cr.main([*review, "--force"]) == 1  # Another branch: fresh rounds.
    cr.git(lane, "checkout", "-q", "feature")
    assert cr.main([*review, "--force"]) == 4
    stage(clone, 2, "main.py")
    commit(clone, "main moved")
    cr.git(lane, "rebase", "-q", "main")
    assert cr.main(review) == 1 and len(provider) == 4  # Rebased onto new main: fresh.


def test_failures_incomplete_reviews_and_approvals_do_not_count(
    root: Path, provider: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = root / "shop/repo"
    review = ["review", "--target", str(clone)]
    stage(clone, 3)
    assert cr.main(review) == 1
    reviewer = cr.codex

    def fail(*_args: Any) -> dict[str, Any]:
        raise cr.ReviewError("rate limited")

    def incomplete(*_args: Any) -> dict[str, Any]:
        return result(issue=True, complete=False)

    monkeypatch.setattr(cr, "codex", fail)
    assert cr.main([*review, "--force"]) == 2
    assert cr.main([*review, "--force"]) == 2
    monkeypatch.setattr(cr, "codex", incomplete)
    assert cr.main([*review, "--force"]) == 1
    monkeypatch.setattr(cr, "codex", reviewer)
    stage(clone, 2)
    assert cr.main([*review, "--force"]) == 0
    stage(clone, 3)
    assert cr.main([*review, "--force"]) == 1  # The second blocking round.
    assert cr.main([*review, "--force"]) == 4


def test_plan_rounds_count_per_plan_file_and_in_the_hook(
    root: Path, provider: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = tmp_path / "plan.md"
    review = ["review", "--target", str(root), "--plan", str(plan)]
    for version in (1, 2):
        plan.write_text(f"{version}. git push --force origin main")
        assert cr.main(review) == 1
    plan.write_text("3. git push --force origin main")
    assert cr.main(review) == 4 and len(provider) == 2
    assert "go to the owner" in capsys.readouterr().out

    def hook(text: str, **extra: str) -> dict[str, Any]:
        tool_input = {"plan": text, **extra}
        return cr.hook({"cwd": str(root), "tool_name": "ExitPlanMode", "tool_input": tool_input})

    denied = hook("4. git push --force origin main", planFilePath=str(plan))
    assert decision(denied) == "deny" and "go to the owner" in json.dumps(denied)
    assert len(provider) == 2  # Codex is not asked again.
    other = tmp_path / "other.md"
    assert decision(hook("5. git push --force", planFilePath=str(other))) == "deny"
    assert len(provider) == 3  # Another plan file has rounds of its own.
    assert cr.main([*review, "--owner-approved"]) == 1 and len(provider) == 4
    plan.write_text("Small safe plan")
    assert cr.main(review) == 4  # The owner's round is used up.


def test_plan_hook_without_a_plan_file_counts_per_session(
    root: Path, provider: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def hook(text: str, session: str) -> dict[str, Any]:
        payload = {"cwd": str(root), "session_id": session, "tool_name": "ExitPlanMode"}
        return cr.hook({**payload, "tool_input": {"plan": text}})

    for version in (1, 2):
        assert decision(hook(f"{version}. git push --force", "s1")) == "deny"
    denied = hook("3. git push --force", "s1")
    assert "go to the owner" in json.dumps(denied) and len(provider) == 2
    assert decision(hook("3. git push --force", "s2")) == "deny" and len(provider) == 3
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({})))
    assert cr.main(["hook"]) == 0  # Hooks never exit with the round-limit code.
