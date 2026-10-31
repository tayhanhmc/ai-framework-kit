"""Real Git lifecycle tests; deterministic stand-ins for the metered Cursor and Codex
reviewers."""

from __future__ import annotations

import contextlib
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

ROUND_CAP = cr.ROUND_CAP
KEY = "AKIA" + "Z7QXJ4MNB2KD5WLP"  # Built at runtime so this file holds no key.


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AI_FRAMEWORK_REVIEWER",
        "CROSS_REVIEW_PANEL",
        "CROSS_REVIEW_FAST",
        "CROSS_REVIEW_MODEL",
        "CROSS_REVIEW_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CROSS_REVIEW_PROVIDER", "cursor")
    monkeypatch.setattr(cr, "ROUND_CAP", 99)  # Only the round-cap tests use the real cap.


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
        cr.git(repo, "config", "commit.gpgsign", "false")
        cr.git(repo, "config", "core.hooksPath", str(repo / ".git/hooks"))
    (framework / ".gitignore").write_text("*/repo/\n*/.worktrees/\n")
    (framework / "shop/repo/code.py").write_text("value = 1\n")
    for repo in (framework, framework / "shop/repo"):
        cr.git(repo, "add", ".")
        cr.git(repo, "commit", "-m", "initial")
    return framework


@pytest.fixture
def clone(root: Path) -> Path:
    return root / "shop/repo"


def result(*, issue: bool = False, complete: bool = True, priority: str = "P1") -> dict[str, Any]:
    finding = {
        "priority": priority,
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


def frozen_diff(_workspace: Path, prompt: str) -> str:
    """The change a commit review hands the reviewer under DIFF; the reviewer's workspace is
    a plain export, so the prompt is all it has of the diff."""
    if not re.search(r"`git diff [0-9a-f]{40} [0-9a-f]{40}`", prompt):
        return ""
    return prompt.split("DIFF\n", 1)[1].split("\n\nARTIFACTS\n", 1)[0]


def ballot(prompt: str, vote: str = "agree") -> dict[str, Any]:
    """A stage-2 answer casting `vote` on every finding the prompt numbers."""
    numbers = re.findall(r'"finding": (\d+)', prompt.split("FINDINGS TO VOTE ON", 1)[1])
    return {"votes": [{"finding": int(n), "vote": vote, "reason": "Checked"} for n in numbers]}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stands in for cr.cursor: every model raises a P1 when the frozen change sets value = 3
    or the plan force-pushes, and agrees with the others' findings. Returns the review
    prompts the first panel model was given; it is also the fast reviewer."""
    prompts: list[str] = []
    monkeypatch.setenv("CROSS_REVIEW_FAST", cr.PANEL[0])

    def fake(target: Path, folder: Path, prompt: str, model: str) -> dict[str, Any]:
        if "FINDINGS TO VOTE ON" in prompt:
            return ballot(prompt)
        if model == cr.PANEL[0]:
            prompts.append(prompt)
        return result(issue="value = 3" in frozen_diff(target, prompt) or "push --force" in prompt)

    monkeypatch.setattr(cr, "cursor", fake)
    return prompts


def stage(repo: Path, value: int, name: str = "code.py") -> None:
    (repo / name).write_text(f"value = {value}\n")
    cr.git(repo, "add", name)


def commit(repo: Path, message: str = "x", *extra: str) -> None:
    """A real `git commit`, running the repository's own hooks."""
    cr.git(repo, "commit", "-q", "-m", message, *extra)


def feature(repo: Path, *values: int) -> None:
    """Branch `feat` off main with one commit per value, each setting code.py."""
    cr.git(repo, "checkout", "-q", "-b", "feat")
    for value in values:
        stage(repo, value)
        commit(repo, f"Set {value}")


def decision(output: dict[str, Any]) -> str | None:
    value = output.get("hookSpecificOutput", {}).get("permissionDecision")
    return None if value is None else str(value)


def plan_hook(start: Path, text: str, **extra: str) -> dict[str, Any]:
    """The ExitPlanMode hook's decision on `text`; `extra` adds tool input such as
    `planFilePath`, or `session_id` to the payload."""
    session = {"session_id": extra.pop("session_id")} if "session_id" in extra else {}
    return cr.hook(
        {
            "cwd": str(start),
            "tool_name": "ExitPlanMode",
            "tool_input": {"plan": text, **extra},
            **session,
        }
    )


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def on_path(monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")


def install_agent(bin_dir: Path, envelope: str, code: int = 0) -> Path:
    """A fake `agent` on PATH that records argv, stdin and environment next to itself, prints
    `envelope` and exits with `code`."""
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "envelope.json").write_text(envelope)
    binary = bin_dir / "agent"
    binary.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{bin_dir}/args"\n'
        f'cat > "{bin_dir}/stdin"\n'
        f'printf "%s" "$AI_FRAMEWORK_REVIEWER" > "{bin_dir}/reviewer_flag"\n'
        f'env > "{bin_dir}/env"\n'
        f'cat "{bin_dir}/envelope.json"\n'
        f"exit {code}\n"
    )
    binary.chmod(0o700)
    return bin_dir


def envelope(text: str, *, is_error: bool = False) -> str:
    return json.dumps({"type": "result", "result": text, "is_error": is_error})


# ---------------------------------------------------------------- provider choice


@pytest.mark.parametrize(
    ("setting", "binaries", "chosen"),
    [
        ("cursor", ["codex", "agent"], "cursor"),
        ("codex", ["agent"], "codex"),
        ("", ["codex", "agent"], "codex"),
        ("", ["agent"], "cursor"),
    ],
)
def test_provider_follows_the_setting_then_what_is_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, setting: str, binaries: list[str], chosen: str
) -> None:
    for name in binaries:
        (tmp_path / name).write_text("#!/bin/sh\n")
        (tmp_path / name).chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("CROSS_REVIEW_PROVIDER", setting)
    assert cr.provider() == chosen


def test_provider_refuses_an_unknown_setting_or_no_reviewer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CROSS_REVIEW_PROVIDER", "gemini")
    with pytest.raises(cr.ReviewError, match="CROSS_REVIEW_PROVIDER"):
        cr.provider()
    monkeypatch.setenv("CROSS_REVIEW_PROVIDER", "")
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(cr.ReviewError, match="neither"):
        cr.provider()


# ---------------------------------------------------------------- staged reviews


def test_staged_tree_review_and_receipt_reuse(clone: Path, provider: list[str]) -> None:
    with pytest.raises(cr.ReviewError, match="nothing staged"):
        cr.review_staged(clone, [])
    stage(clone, 2)
    receipt = cr.review_staged(clone, [])
    assert receipt["approved"] and cr.approved(clone) and receipt["provider"] == "cursor"
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


def test_context_files_reach_the_reviewer(clone: Path, provider: list[str], tmp_path: Path) -> None:
    task = tmp_path / "task.md"
    task.write_text("Value must be 2")
    stage(clone, 2)
    cr.review_staged(clone, [task])
    assert "Value must be 2" in provider[-1] and "git diff --cached" in provider[-1]


def test_the_frozen_diff_is_in_the_prompt(clone: Path, provider: list[str]) -> None:
    stage(clone, 2)
    (clone / "code.py").write_text("value = 9\n")
    cr.review_staged(clone, [])
    assert "DIFF\n" in provider[-1] and "+value = 2" in provider[-1]
    assert "value = 9" not in provider[-1]


def test_product_reviews_keep_the_product_s_platforms(clone: Path, provider: list[str]) -> None:
    stage(clone, 2)
    cr.review_staged(clone, [])
    # Only the framework's own scripts are scoped to macOS and Linux.
    prompt = " ".join(provider[-1].split())
    assert "Product code is reviewed for the platforms its own docs name" in prompt


def test_review_covers_the_frozen_snapshot_even_if_the_index_moves(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def moving(target: Path, folder: Path, prompt: str, model: str) -> dict[str, Any]:
        stage(clone, 3)  # A -> B (buggy) while the reviewer reads.
        seen.append(frozen_diff(target, prompt))
        stage(clone, 2)  # ... and back to A.
        stage(clone, 3)
        return result(issue="value = 3" in seen[-1])

    monkeypatch.setattr(cr, "cursor", moving)
    stage(clone, 2)
    assert cr.review_staged(clone, [])["approved"]
    assert "+value = 2" in seen[0] and "value = 3" not in seen[0]  # The reviewer saw A only.
    assert not cr.approved(clone)  # B was never reviewed.
    stage(clone, 2)
    assert cr.approved(clone)


def test_receipt_is_bound_to_the_parent_commit(clone: Path, provider: list[str]) -> None:
    stage(clone, 2)
    assert cr.review_staged(clone, [])["approved"]
    staged_tree = cr.tree(clone)
    cr.git(clone, "stash")
    cr.git(clone, "commit", "--allow-empty", "-m", "elsewhere")
    cr.git(clone, "stash", "pop", "--index")
    assert cr.tree(clone) == staged_tree and not cr.approved(clone)  # New parent only.


def test_failure_never_passes_and_is_retried(
    clone: Path, provider: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stage(clone, 3)
    assert not cr.review_staged(clone, [])["approved"]  # A real blocking finding.
    reviewer = cr.cursor

    def fail(target: Path, folder: Path, prompt: str, model: str) -> dict[str, Any]:
        raise cr.ReviewError("rate limited")

    monkeypatch.setattr(cr, "cursor", fail)
    receipt = cr.review_staged(clone, [], feedback="The caller needs 3")
    text = cr.describe(receipt)
    assert not receipt["approved"] and "Wrong value" in text and "rate limited" in text
    monkeypatch.setattr(cr, "cursor", reviewer)
    stage(clone, 2)
    assert cr.review_staged(clone, [])["approved"] and cr.approved(clone)
    for crash in (OSError("no agent binary"), ValueError("bad JSON")):

        def broken(*_args: Any, crash: Exception = crash) -> dict[str, Any]:
            raise crash

        monkeypatch.setattr(cr, "cursor", broken)
        receipt = cr.review_staged(clone, [], feedback="Please recheck")
        assert not receipt["approved"] and not cr.approved(clone)  # Old approval revoked.
    assert cr.main(["review", "--target", str(clone)]) == 2  # Could not run, not blocked.
    monkeypatch.setattr(cr, "cursor", reviewer)
    stage(clone, 3)
    calls = len(provider)
    assert not cr.review_staged(clone, [])["approved"]  # A failed attempt is retried.
    assert len(provider) == calls + 1 and "Wrong value" in provider[-1]


def test_lock_prevents_duplicate_review(clone: Path, provider: list[str]) -> None:
    stage(clone, 2)
    key = cr.commit_key(*cr.candidate(clone))
    with (
        cr.locked(cr.key_lock(cr.store(clone), key)),
        pytest.raises(cr.ReviewError, match="already"),
    ):
        cr.review_staged(clone, [])


def test_another_keys_lock_does_not_block(clone: Path, provider: list[str]) -> None:
    stage(clone, 2)
    with cr.locked(cr.key_lock(cr.store(clone), "commit-another")):
        assert cr.review_staged(clone, [])["approved"]


def test_lineage_lock_blocks_a_review_under_another_key(clone: Path, provider: list[str]) -> None:
    """A second review in a running lineage would dodge the round cap, whatever its key."""
    stage(clone, 2)
    lineage = f"main: staged on {cr.head(clone)[:12]}"
    with (
        cr.locked(cr.lineage_lock(cr.store(clone), lineage)),
        pytest.raises(cr.ReviewError, match="already"),
    ):
        cr.review_staged(clone, [])


def test_reviews_of_different_plans_run_at_once(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plans = ("Add a test", "Fix a bug")
    spans: dict[str, list[float]] = {plan: [] for plan in plans}

    def slow(target: Path, folder: Path, prompt: str, model: str) -> dict[str, Any]:
        if "FINDINGS TO VOTE ON" in prompt:
            return ballot(prompt)
        plan = next(plan for plan in plans if plan in prompt)
        spans[plan].append(time.monotonic())
        time.sleep(0.5)
        spans[plan].append(time.monotonic())
        return result()

    monkeypatch.setattr(cr, "cursor", slow)
    # Concurrent reviews are separate processes; threads stand in, and only the main thread
    # may install signal handlers.
    monkeypatch.setattr(cr, "interruptible", contextlib.nullcontext)
    receipts: dict[str, dict[str, Any]] = {}

    def review(plan: str) -> None:
        receipts[plan] = cr.review_plan(root / "shop", plan)

    threads = [threading.Thread(target=review, args=(plan,)) for plan in plans]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(receipts[plan]["approved"] for plan in plans)
    first, second = (spans[plan] for plan in plans)
    assert min(first) < max(second) and min(second) < max(first)


def test_rounds_from_a_concurrent_review_survive(
    root: Path, provider: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Another lineage's review that finishes mid-review keeps its rounds entry."""
    outer, inner = tmp_path / "outer.md", tmp_path / "inner.md"
    outer.write_text("Add a test")
    inner.write_text("Fix a bug")
    fake = cr.cursor
    nested: list[dict[str, Any]] = []

    def midway(target: Path, folder: Path, prompt: str, model: str) -> dict[str, Any]:
        if model == cr.PANEL[0] and "Add a test" in prompt and "FINDINGS TO VOTE ON" not in prompt:
            nested.append(cr.review_plan(root / "shop", "Fix a bug", source=inner))
        return fake(target, folder, prompt, model)

    monkeypatch.setattr(cr, "cursor", midway)
    # Concurrent reviews are separate processes; threads stand in, and only the main thread
    # may install signal handlers.
    monkeypatch.setattr(cr, "interruptible", contextlib.nullcontext)
    assert cr.review_plan(root / "shop", "Add a test", source=outer)["approved"]
    assert [receipt["approved"] for receipt in nested] == [True]
    runs = cr.read_json(cr.store(root) / "rounds.json")["runs"]
    lineages = {entry["lineage"] for entry in runs}
    assert lineages == {f"plan {outer.resolve()}", f"plan {inner.resolve()}"}


def test_changed_context_or_repository_is_reviewed_again(
    root: Path, clone: Path, provider: list[str], tmp_path: Path
) -> None:
    task = tmp_path / "task.md"
    task.write_text("Value must be 2")
    stage(clone, 2)
    cr.review_staged(clone, [task])
    cr.review_staged(clone, [task])
    assert len(provider) == 1  # Same content and context: reused.
    task.write_text("Value must be 5")
    cr.review_staged(clone, [task])
    assert len(provider) == 2 and "Value must be 5" in provider[-1]
    cr.review_plan(root, "Add a test")
    cr.review_plan(root, "Add a test")
    assert len(provider) == 3
    cr.git(clone, "commit", "-m", "advance")
    cr.review_plan(root, "Add a test")
    assert len(provider) == 4  # The code the plan was checked against moved.


def test_changed_context_revokes_the_commit_approval(
    clone: Path, provider: list[str], tmp_path: Path
) -> None:
    task = tmp_path / "task.md"
    task.write_text("Value must be 2")
    stage(clone, 2)
    receipt = cr.review_staged(clone, [task])
    assert receipt["context"][0]["path"] == str(task.resolve()) and cr.approved(clone)
    task.write_text("Value must be 5")
    assert not cr.approved(clone)
    assert "task.md" in str(cr.approval_problem(clone, cr.commit_key(*cr.candidate(clone))))
    assert cr.main(["check", "--target", str(clone)]) == 1
    cr.review_staged(clone, [task])
    assert cr.approved(clone) and len(provider) == 2
    task.unlink()
    assert not cr.approved(clone)  # A deleted context file is a change too.


def test_older_policy_receipts_do_not_count(
    clone: Path, provider: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stage(clone, 2)
    cr.review_staged(clone, [])
    monkeypatch.setattr(cr, "POLICY", "next")
    assert not cr.approved(clone)
    assert "older review policy" in str(
        cr.approval_problem(clone, cr.commit_key(*cr.candidate(clone)))
    )
    cr.review_staged(clone, [])
    assert cr.approved(clone) and len(provider) == 2


def test_new_reviewer_or_force_reviews_again(
    root: Path, clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models: list[str] = []

    def fake(target: Path, folder: Path, prompt: str, model: str) -> dict[str, Any]:
        models.append(model)
        return result()

    monkeypatch.setattr(cr, "cursor", fake)
    stage(clone, 2)
    cr.review_staged(clone, [])
    cr.review_staged(clone, [])
    assert models == [cr.FAST_REVIEWER]
    monkeypatch.setenv("CROSS_REVIEW_FAST", "model-a")
    receipt = cr.review_staged(clone, [])
    assert models[1:] == ["model-a"] and receipt["panel"] == ["model-a"]
    assert receipt["policy"] == cr.POLICY and receipt["reviewed_at"] and receipt["run"]
    assert cr.main(["review", "--target", str(clone), "--force"]) == 0
    assert len(models) == 3
    cr.review_plan(root, "Add a test")
    monkeypatch.setenv("CROSS_REVIEW_PANEL", "model-a,model-b,model-c")
    receipt = cr.review_plan(root, "Add a test")
    assert sorted(models[3 + len(cr.PANEL) :]) == ["model-a", "model-b", "model-c"]
    assert receipt["panel"] == ["model-a", "model-b", "model-c"]


def test_interrupted_re_review_revokes_the_old_approval(
    clone: Path, provider: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stage(clone, 2)
    assert cr.review_staged(clone, [])["approved"] and cr.approved(clone)

    def interrupted(*_args: Any) -> dict[str, Any]:
        raise cr.Interrupted(signal.SIGTERM)

    monkeypatch.setattr(cr, "cursor", interrupted)
    with pytest.raises(cr.Interrupted):
        cr.review_staged(clone, [], force=True)
    assert not cr.approved(clone)


def test_context_is_read_once_for_prompt_and_receipt(
    clone: Path, provider: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    stage(clone, 2)
    receipt = cr.review_staged(clone, [task])
    monkeypatch.setattr(Path, "read_bytes", original)
    assert len(reads) == 1 and "Value must be 2" in provider[-1]
    assert receipt["context"][0]["sha256"] == cr.sha256(b"Value must be 2")


# ---------------------------------------------------------------- result contracts


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


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"votes": [{"finding": 1, "vote": "maybe", "reason": "r"}]},
        {"votes": []},
        {"votes": [{"finding": n, "vote": "agree", "reason": "r"} for n in (1, 1)]},
        {"votes": [{"finding": 1, "vote": ["agree"], "reason": "r"}]},
        {"votes": [{"finding": [1], "vote": "agree", "reason": "r"}]},
    ],
)
def test_malformed_votes_never_count(bad: dict[str, Any]) -> None:
    with pytest.raises(cr.ReviewError):
        cr.validate_votes(bad, {1})


def test_a_vote_with_an_extra_field_counts() -> None:
    cr.validate_votes(
        {"votes": [{"finding": 1, "priority": "P2", "vote": "agree", "reason": "r"}]}, {1}
    )


def finding(number: int, priority: str = "P1") -> dict[str, Any]:
    return {
        "finding": number,
        "model": "m0",
        **result(issue=True)["findings"][0],
        "priority": priority,
    }


@pytest.mark.parametrize(
    ("priority", "votes", "blocks"),
    [
        ("P1", ["agree"], True),
        ("P0", ["agree", "agree", "disagree"], True),
        ("P1", [], False),
        ("P1", ["disagree", "disagree", "agree"], False),
        ("P2", ["not_actionable"] * 3, False),
        ("P3", ["agree"] * 3, False),
    ],
)
def test_verdict_blocks_only_on_a_p0_to_p2_majority(
    priority: str, votes: list[str], blocks: bool
) -> None:
    ballots = {f"m{i}": [{"finding": 1, "vote": v, "reason": "r"}] for i, v in enumerate(votes, 1)}
    outcome = cr.verdict([finding(1, priority)], ballots)
    assert len(outcome["blocking"]) == int(blocks)
    assert len(outcome["advisory"]) == int(not blocks)
    tally = (outcome["blocking"] or outcome["advisory"])[0]
    assert tally["agree"] == 1 + votes.count("agree")
    assert tally["disagree"] == votes.count("disagree")


def test_verdict_counts_each_finding_s_own_votes() -> None:
    ballots = {
        "m1": [
            {"finding": 1, "vote": "agree", "reason": "r"},
            {"finding": 2, "vote": "disagree", "reason": "r"},
        ]
    }
    outcome = cr.verdict([finding(1), finding(2)], ballots)
    assert [f["finding"] for f in outcome["blocking"]] == [1]
    assert [f["finding"] for f in outcome["advisory"]] == [2]


# ---------------------------------------------------------------- panel


def panel_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    votes: dict[str, str] | None = None,
    priority: str = "P1",
    failing: tuple[str, ...] = (),
    incomplete: tuple[str, ...] = (),
) -> list[str]:
    """Stands in for cr.cursor: the first panel model raises one finding at `priority`, the
    others vote `votes.get(model, "agree")` on it; models in `failing` fail and models in
    `incomplete` return an incomplete review. Returns each model asked, in call order."""
    asked: list[str] = []

    def fake(target: Path, folder: Path, prompt: str, model: str) -> dict[str, Any]:
        asked.append(model)
        if model in failing:
            raise cr.ReviewError(f"{model} rate limited")
        if "FINDINGS TO VOTE ON" in prompt:
            return ballot(prompt, (votes or {}).get(model, "agree"))
        return result(
            issue=model == cr.PANEL[0], complete=model not in incomplete, priority=priority
        )

    monkeypatch.setattr(cr, "cursor", fake)
    return asked


def test_panel_is_asked_in_parallel(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Barrier(len(cr.PANEL), timeout=5)

    def fake(target: Path, folder: Path, prompt: str, model: str) -> dict[str, Any]:
        gate.wait()  # Returns only once every model is being asked at the same time.
        return result()

    monkeypatch.setattr(cr, "cursor", fake)
    receipt = cr.review_plan(root, "Add a test")
    assert receipt["approved"] and set(receipt["results"]) == set(cr.PANEL)
    assert receipt["panel"] == list(cr.PANEL) and receipt["votes"] == {}


def test_panel_blocks_on_a_finding_the_majority_backs(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    votes = {cr.PANEL[1]: "agree", cr.PANEL[2]: "disagree", cr.PANEL[3]: "not_actionable"}
    asked = panel_fake(monkeypatch, votes=votes)
    receipt = cr.review_plan(root, "Add a test")
    output = cr.describe(receipt)
    assert not receipt["approved"] and output.startswith("Cross-model review BLOCKED.")
    assert "P1 code.py:1: Wrong value" in output and "2 agree, 1 disagree" in output
    assert sorted(asked) == sorted([*cr.PANEL, *cr.PANEL[1:]])  # The author does not vote.
    assert set(receipt["votes"]) == set(cr.PANEL[1:])
    assert receipt["verdict"]["blocking"][0]["model"] == cr.PANEL[0]


@pytest.mark.parametrize(
    ("votes", "priority"),
    [(("disagree", "disagree", "agree"), "P1"), (("disagree",) * 3, "P1"), (("agree",) * 3, "P3")],
)
def test_panel_approves_with_advisory_findings_the_majority_does_not_back(
    root: Path, monkeypatch: pytest.MonkeyPatch, votes: tuple[str, ...], priority: str
) -> None:
    panel_fake(monkeypatch, votes=dict(zip(cr.PANEL[1:], votes, strict=True)), priority=priority)
    receipt = cr.review_plan(root, "Add a test")
    output = cr.describe(receipt)
    assert receipt["approved"] and "1 advisory finding" in output and "Wrong value" not in output


@pytest.mark.parametrize(
    ("broken", "finishes"), [("failing", 2), ("incomplete", 2), ("failing", 3)]
)
def test_panel_needs_three_models_to_finish_both_stages(
    root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, broken: str, finishes: int
) -> None:
    if broken == "failing":
        panel_fake(monkeypatch, failing=cr.PANEL[finishes:])
    else:
        panel_fake(monkeypatch, incomplete=cr.PANEL[finishes:])
    plan = tmp_path / "plan.md"
    plan.write_text("Add a test")
    receipt = cr.review_plan(root, "Add a test")
    if finishes < cr.QUORUM:
        assert not receipt["approved"] and f"{finishes} of 4 models" in receipt["error"]
        assert cr.main(["review", "--target", str(root), "--plan", str(plan)]) == 2
    else:
        assert "error" not in receipt


def test_panel_skips_the_votes_when_nothing_could_block(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = panel_fake(monkeypatch, priority="P3")
    receipt = cr.review_plan(root, "Add a test")
    assert receipt["approved"] and receipt["votes"] == {}
    assert sorted(asked) == sorted(cr.PANEL)  # Stage 1 only.
    assert len(receipt["verdict"]["advisory"]) == 1


def test_panel_counts_malformed_votes_as_unfinished(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel_fake(monkeypatch, votes={cr.PANEL[1]: "maybe", cr.PANEL[2]: "maybe"})
    receipt = cr.review_plan(root, "Add a test")
    assert not receipt["approved"] and "2 of 4 models" in receipt["error"]


@pytest.mark.parametrize(
    "models",
    ["claude-opus-4-8,b,c", "a,sonnet-4-thinking,c", "a,b,auto", "a,b,Auto"],
)
def test_panel_refuses_anthropic_or_routed_models(
    monkeypatch: pytest.MonkeyPatch, models: str
) -> None:
    monkeypatch.setenv("CROSS_REVIEW_PANEL", models)
    with pytest.raises(cr.ReviewError, match="another family"):
        cr.panel()


@pytest.mark.parametrize("models", ["a,b", "a,b,a"])
def test_panel_refuses_fewer_than_the_quorum(monkeypatch: pytest.MonkeyPatch, models: str) -> None:
    monkeypatch.setenv("CROSS_REVIEW_PANEL", models)
    with pytest.raises(cr.ReviewError, match="at least 3"):
        cr.panel()


def test_panel_honours_the_setting_then_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CROSS_REVIEW_PANEL", "a, b ,c")
    chosen = cr.panel()
    monkeypatch.delenv("CROSS_REVIEW_PANEL")
    assert chosen == ("a", "b", "c") and cr.panel() == cr.PANEL


# ---------------------------------------------------------------- fast tier


@pytest.mark.parametrize(("priority", "code"), [("P0", 1), ("P1", 1), ("P2", 0), ("P3", 0)])
def test_fast_review_blocks_only_on_p0_or_p1(
    clone: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    priority: str,
    code: int,
) -> None:
    monkeypatch.setenv("CROSS_REVIEW_FAST", cr.PANEL[0])  # panel_fake's finding raiser.
    panel_fake(monkeypatch, priority=priority)
    stage(clone, 2)
    assert cr.main(["review", "--target", str(clone)]) == code
    output = capsys.readouterr().out
    assert ("Wrong value" in output) == bool(code)
    assert ("1 advisory finding" in output) != bool(code)


def test_fast_reviewer_refuses_an_anthropic_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CROSS_REVIEW_FAST", "claude-opus-4-8")
    with pytest.raises(cr.ReviewError, match="another family"):
        cr.fast_reviewer()


# ---------------------------------------------------------------- Cursor backend


def test_agent_runs_read_only_with_the_prompt_on_stdin(
    clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fenced = f"Here is the review:\n```json\n{json.dumps(result())}\n```\n"
    on_path(monkeypatch, install_agent(tmp_path / "bin", envelope(fenced)))
    bin_dir = tmp_path / "bin"
    stage(clone, 2)
    receipt = cr.review_staged(clone, [])
    args = (bin_dir / "args").read_text().splitlines()
    pairs = set(zip(args, args[1:], strict=False))
    assert receipt["approved"] and receipt["panel"] == [cr.FAST_REVIEWER]
    assert "-p" in args and "--trust" in args
    assert {
        ("--mode", "ask"),
        ("--output-format", "json"),
    } <= pairs
    workspace = Path(args[args.index("--workspace") + 1])
    assert workspace != cr.checkout(clone) and not workspace.exists()  # A removed export.
    assert args[args.index("--model") + 1] == cr.FAST_REVIEWER
    stdin = (bin_dir / "stdin").read_text()
    assert "git diff" in stdin and json.dumps(cr.SCHEMA) in stdin
    assert (bin_dir / "reviewer_flag").read_text() == "1"


def test_parse_takes_the_last_fenced_json_after_prose_with_braces() -> None:
    text = (
        "The {value} key and a stale draft:\n```json\n{}\n```\n"
        f"Final answer:\n```json\n{json.dumps(result())}\n```\n"
    )
    assert cr.parse_result(text) == result()


def test_parse_accepts_a_repeated_object_and_refuses_two_different_ones() -> None:
    assert cr.parse_result(json.dumps(result()) * 2) == result()
    with pytest.raises(cr.ReviewError, match="ambiguous"):
        cr.parse_result(json.dumps(result()) + "\n" + json.dumps(result(issue=True)))


def test_parse_reads_a_fenced_json_whose_strings_quote_a_fence() -> None:
    review = {**result(), "summary": "parse_result now prefers the last ```json fence."}
    assert cr.parse_result(f"Done.\n\n```json\n{json.dumps(review, indent=2)}\n```") == review


def test_bad_timeout_is_refused_before_the_reviewer_starts(
    clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = install_agent(tmp_path / "bin", envelope(json.dumps(result())))
    on_path(monkeypatch, bin_dir)
    monkeypatch.setenv("CROSS_REVIEW_TIMEOUT", "ten")
    stage(clone, 2)
    with contextlib.suppress(cr.ReviewError):
        assert not cr.review_staged(clone, [])["approved"]
    assert not (bin_dir / "args").exists()  # No reviewer was left running unsupervised.


def test_timeout_defaults_and_honours_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    default = cr.review_timeout()
    monkeypatch.setenv("CROSS_REVIEW_TIMEOUT", "900")
    assert default == cr.TIMEOUT and cr.review_timeout() == 900
    for value in ("0", "-5", "ten"):
        monkeypatch.setenv("CROSS_REVIEW_TIMEOUT", value)
        with pytest.raises(cr.ReviewError, match="CROSS_REVIEW_TIMEOUT"):
            cr.review_timeout()


def test_agent_gets_only_an_allowlisted_environment(
    clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = install_agent(tmp_path / "bin", envelope(json.dumps(result())))
    on_path(monkeypatch, bin_dir)
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "do-not-forward")
    monkeypatch.setenv("https_proxy", "http://proxy.invalid:3128")
    stage(clone, 2)
    cr.review_staged(clone, [])
    env = (bin_dir / "env").read_text()
    assert "SOME_SERVICE_TOKEN" not in env and "do-not-forward" not in env
    assert "https_proxy=http://proxy.invalid:3128" in env and "\nHOME=" in f"\n{env}"


@pytest.mark.parametrize(
    ("output", "code", "message"),
    [
        (envelope(json.dumps(result())), 3, "exit 3"),
        (envelope(json.dumps(result()), is_error=True), 0, "reported an error"),
        (envelope("I could not finish the review."), 0, "no JSON object"),
    ],
)
def test_failed_agent_is_recorded_as_an_error(
    clone: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    code: int,
    message: str,
) -> None:
    on_path(monkeypatch, install_agent(tmp_path / "bin", output, code))
    stage(clone, 5)
    receipt = cr.review_staged(clone, [])
    assert not receipt["approved"] and message in receipt["error"]


def test_interrupted_agent_review_stops_the_process_group(
    clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pids = tmp_path / "pids"
    (bin_dir / "agent").write_text(
        f"#!/bin/sh\nsleep 60 &\necho $! > '{pids}.tmp'\nmv '{pids}.tmp' '{pids}'\nwait\n"
    )
    (bin_dir / "agent").chmod(0o700)
    on_path(monkeypatch, bin_dir)

    def interrupt() -> None:
        while not pids.exists():
            time.sleep(0.01)
        os.kill(os.getpid(), signal.SIGTERM)

    stage(clone, 2)
    threading.Thread(target=interrupt, daemon=True).start()
    with pytest.raises(cr.Interrupted):
        cr.review_staged(clone, [])
    sleeper = int(pids.read_text())
    deadline = time.monotonic() + 5
    while alive(sleeper) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(sleeper)


def listing(folder: Path) -> dict[str, str]:
    return {
        str(path.relative_to(folder)): path.read_text()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def workspaces(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, dict[str, str]]]:
    """Stands in for cr.cursor and cr.codex: approves and records each reviewer's workspace
    and every file in it at call time."""
    seen: list[tuple[Path, dict[str, str]]] = []

    def fake(target: Path, folder: Path, prompt: str, model: str) -> dict[str, Any]:
        seen.append((target, listing(target)))
        return result()

    monkeypatch.setattr(cr, "cursor", fake)
    monkeypatch.setattr(cr, "codex", fake)
    return seen


@pytest.mark.parametrize("name", ["cursor", "codex"])
def test_commit_reviewer_sees_only_the_reviewed_tree(
    clone: Path,
    workspaces: list[tuple[Path, dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    monkeypatch.setenv("CROSS_REVIEW_PROVIDER", name)
    (clone / ".gitignore").write_text(".env\n")
    (clone / ".env").write_text(f"KEY={KEY}\n")  # Ignored: never committed, never screened.
    (clone / "draft.py").write_text("value = 8\n")  # Untracked: not in the commit.
    stage(clone, 2)
    cr.git(clone, "add", ".gitignore")
    (clone / "code.py").write_text("value = 9\n")  # Unstaged: not in the commit.
    cr.review_staged(clone, [])
    workspace, files = workspaces[-1]
    assert files == {".gitignore": ".env\n", "code.py": "value = 2\n"}
    assert not workspace.is_relative_to(clone) and not workspace.exists()


def test_range_reviewer_sees_the_head_tree(
    clone: Path, workspaces: list[tuple[Path, dict[str, str]]]
) -> None:
    feature(clone, 4, 2)
    (clone / "server.pem").write_text("synthetic\n")
    cr.git(clone, "add", "-f", "server.pem")
    commit(clone, "Track a pem")  # Even tracked secret-like files stay out of the export.
    assert cr.main(["review", "--target", str(clone), "--range", "main..feat"]) == 0
    assert workspaces[-1][1] == {"code.py": "value = 2\n"}


def test_plan_reviewer_sees_the_snapshot_of_each_checkout(
    root: Path, clone: Path, workspaces: list[tuple[Path, dict[str, str]]]
) -> None:
    (root / "shop/.env").write_text(f"KEY={KEY}\n")
    (clone / "code.py").write_text("value = 7\n")  # Unstaged work is part of the plan's base.
    (clone / "new.py").write_text("x = 1\n")
    cr.review_plan(root / "shop", "Add a test")
    files = workspaces[-1][1]
    assert files["shop/repo/code.py"] == "value = 7\n" and files["shop/repo/new.py"] == "x = 1\n"
    assert files["AGENTS.md"] == "# Test framework\n"
    assert not any(path.endswith(".env") for path in files)


# ---------------------------------------------------------------- secret screen


def test_the_audited_baseline_stays_out_of_the_upload(clone: Path, provider: list[str]) -> None:
    digest = "9f" * 32
    entry = {"hashed_secret": digest, "type": "Secret Keyword", "is_secret": False}
    (clone / ".secrets.baseline").write_text(json.dumps({"results": {"x.py": [entry]}}))
    stage(clone, 2)
    cr.git(clone, "add", ".secrets.baseline")
    assert cr.review_staged(clone, [])["approved"] and digest not in provider[-1]


@pytest.mark.parametrize("staged_baseline", [True, False])
def test_the_change_s_own_baseline_never_whitelists_a_secret(
    clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, staged_baseline: bool
) -> None:
    bin_dir = install_agent(tmp_path / "bin", envelope(json.dumps(result())))
    on_path(monkeypatch, bin_dir)
    (clone / "settings.py").write_text(f'aws_key = "{KEY}"\n')
    scanner = Path(sys.executable).parent / "detect-secrets"
    scan = subprocess.run(
        [str(scanner), "scan", "settings.py"], cwd=clone, capture_output=True, text=True, check=True
    )
    audited = json.loads(scan.stdout)
    for spots in audited["results"].values():
        for spot in spots:
            spot["is_secret"] = False
    (clone / ".secrets.baseline").write_text(json.dumps(audited))
    cr.git(clone, "add", "settings.py", *([".secrets.baseline"] if staged_baseline else []))
    with pytest.raises(cr.ReviewError, match="secret"):
        cr.review_staged(clone, [])
    assert not (bin_dir / "stdin").exists()


@pytest.mark.parametrize("change", ["added", "removed line", "deleted file"])
def test_a_change_holding_a_secret_is_never_uploaded(
    clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    bin_dir = install_agent(tmp_path / "bin", envelope(json.dumps(result())))
    on_path(monkeypatch, bin_dir)
    settings = clone / "settings.py"
    settings.write_text(f'aws_key = "{KEY}"\nmode = 1\n')
    if change != "added":
        cr.git(clone, "add", "settings.py")
        cr.git(clone, "commit", "-m", "leak")
        if change == "removed line":
            settings.write_text("mode = 1\n")
        else:
            settings.unlink()
    cr.git(clone, "add", "-A")
    with pytest.raises(cr.ReviewError, match="secret"):
        cr.review_staged(clone, [])
    assert not (bin_dir / "stdin").exists()


@pytest.mark.parametrize(
    "template", ['sha256 = "{digest}"; aws_key = "{key}"', "{key}.sha256={digest}"]
)
def test_a_secret_sharing_a_line_with_a_digest_is_screened(
    clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, template: str
) -> None:
    bin_dir = install_agent(tmp_path / "bin", envelope(json.dumps(result())))
    on_path(monkeypatch, bin_dir)
    (clone / "settings.py").write_text(template.format(digest="ab" * 32, key=KEY) + "\n")
    cr.git(clone, "add", "settings.py")
    with pytest.raises(cr.ReviewError, match="secret"):
        cr.review_staged(clone, [])
    assert not (bin_dir / "stdin").exists()


def test_a_re_review_failing_the_screen_revokes_the_approval(
    clone: Path, provider: list[str]
) -> None:
    stage(clone, 2)
    assert cr.review_staged(clone, [])["approved"] and cr.approved(clone)
    with pytest.raises(cr.ReviewError, match="secret"):
        cr.review_staged(clone, [], feedback=f'Recheck with aws_key = "{KEY}"')
    assert not cr.approved(clone) and len(provider) == 1


@pytest.mark.parametrize(
    "crash",
    [subprocess.CalledProcessError(1, "detect-secrets"), OSError("gone"), KeyError("results")],
)
def test_a_crashed_screen_revokes_the_approval(
    clone: Path, provider: list[str], monkeypatch: pytest.MonkeyPatch, crash: Exception
) -> None:
    stage(clone, 2)
    assert cr.review_staged(clone, [])["approved"]

    def broken(*_args: Any) -> None:
        raise crash

    monkeypatch.setattr(cr, "screen_secrets", broken)
    with pytest.raises(cr.ReviewError, match="secret screen"):
        cr.review_staged(clone, [], force=True)
    assert not cr.approved(clone) and len(provider) == 1


def test_findings_are_screened_before_other_models_see_them(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []

    def leaky(target: Path, folder: Path, prompt: str, model: str) -> dict[str, Any]:
        prompts.append(prompt)
        answer = result(issue=True)
        answer["findings"][0]["evidence"] = f'aws_key = "{KEY}"'
        return answer

    monkeypatch.setattr(cr, "cursor", leaky)
    receipt = cr.review_plan(root, "Add a test")
    assert not receipt["approved"] and "secret" in receipt["error"]
    assert len(prompts) == len(cr.PANEL)  # No votes were asked for.


# ---------------------------------------------------------------- Codex provider


@pytest.fixture
def codex(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Stands in for cr.codex: a P2 finding when the frozen change sets value = 3 or the
    plan force-pushes. Returns (model, prompt) per call."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setenv("CROSS_REVIEW_PROVIDER", "codex")

    def fake(target: Path, folder: Path, prompt: str, model: str) -> dict[str, Any]:
        calls.append((model, prompt))
        bad = "value = 3" in frozen_diff(target, prompt) or "push --force" in prompt
        return result(issue=bad, priority="P2")

    monkeypatch.setattr(cr, "codex", fake)
    return calls


def test_codex_reviews_every_tier_alone(
    root: Path, clone: Path, codex: list[tuple[str, str]]
) -> None:
    stage(clone, 3)
    receipt = cr.review_staged(clone, [])
    assert receipt["approved"] and receipt["provider"] == "codex"  # P2 is advisory on a commit.
    assert receipt["panel"] == [cr.CODEX_DEFAULT] and receipt["votes"] == {}
    plan = cr.review_plan(root, "1. git push --force origin main")
    assert not plan["approved"]  # P2 blocks a plan.
    commit(clone, "Set 3")
    cr.git(clone, "branch", "feat")
    cr.git(clone, "reset", "-q", "--hard", "HEAD~1")
    assert cr.main(["review", "--target", str(clone), "--range", "main..feat"]) == 1
    assert [model for model, _ in codex] == [cr.CODEX_DEFAULT] * 3


def test_codex_model_follows_the_setting(
    clone: Path, codex: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CROSS_REVIEW_MODEL", "gpt-reviewer")
    stage(clone, 2)
    assert cr.review_staged(clone, [])["panel"] == ["gpt-reviewer"]
    monkeypatch.setenv("CROSS_REVIEW_MODEL", "claude-opus-4-8")
    with pytest.raises(cr.ReviewError, match="another family"):
        cr.review_staged(clone, [], force=True)


def test_codex_command_and_output(
    clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CROSS_REVIEW_PROVIDER", "codex")
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
    on_path(monkeypatch, binary.parent)
    monkeypatch.setenv("CROSS_REVIEW_MODEL", "reviewer-model")
    stage(clone, 2)
    assert cr.review_staged(clone, [])["approved"]
    args = args_file.read_text().split("\n")
    assert {"read-only", "--ephemeral", "--skip-git-repo-check", "reviewer-model"} <= set(args)
    assert Path(args[args.index("--cd") + 1]) != cr.checkout(clone)  # An export, not the clone.
    monkeypatch.delenv("CROSS_REVIEW_MODEL")
    stage(clone, 4)
    assert cr.review_staged(clone, [])["approved"]
    assert "--model" not in args_file.read_text().split("\n")  # Codex's configured model.
    binary.write_text("#!/bin/sh\nexit 3\n")
    stage(clone, 5)
    assert "exit 3" in cr.review_staged(clone, [])["error"]


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_interrupted_codex_review_stops_codex_and_records_nothing(
    clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signum: int
) -> None:
    monkeypatch.setenv("CROSS_REVIEW_PROVIDER", "codex")
    binary = tmp_path / "codex"
    pids = tmp_path / "pids"
    binary.write_text(
        f"#!/bin/sh\nsleep 60 &\necho $! > '{pids}.tmp'\nmv '{pids}.tmp' '{pids}'\nwait\n"
    )
    binary.chmod(0o700)
    on_path(monkeypatch, tmp_path)

    def interrupt() -> None:
        while not pids.exists():
            time.sleep(0.01)
        os.kill(os.getpid(), signum)

    stage(clone, 2)
    threading.Thread(target=interrupt, daemon=True).start()
    with pytest.raises(cr.Interrupted):
        cr.review_staged(clone, [])
    sleeper = int(pids.read_text())
    deadline = time.monotonic() + 5
    while alive(sleeper) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(sleeper)
    assert not cr.approved(clone)


def test_timeout_counts_time_the_machine_slept(
    clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A review the machine slept through times out on wake, stops the reviewer and its
    children, records no approval and frees the lock."""
    monkeypatch.setenv("CROSS_REVIEW_PROVIDER", "codex")
    monkeypatch.setenv("CROSS_REVIEW_TIMEOUT", "1")
    binary = tmp_path / "codex"
    pids = tmp_path / "pids"
    binary.write_text(
        f"#!/bin/sh\nsleep 60 &\necho $$ $! > '{pids}.tmp'\nmv '{pids}.tmp' '{pids}'\nwait\n"
    )
    binary.chmod(0o700)
    on_path(monkeypatch, tmp_path)
    # macOS's time.monotonic (which subprocess binds as _time) stops while the machine
    # sleeps; a frozen one stands in for a review that slept through its deadline.
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(subprocess, "_time", lambda: 0.0)
    done = threading.Event()

    def guard() -> None:
        # Bounds the test if the timeout never fires: stopping the reviewer unblocks it.
        if not done.wait(5):
            with contextlib.suppress(FileNotFoundError, ProcessLookupError):
                os.killpg(int(pids.read_text().split()[0]), signal.SIGKILL)

    stage(clone, 2)
    threading.Thread(target=guard, daemon=True).start()
    try:
        receipt = cr.review_staged(clone, [])
    finally:
        done.set()
    assert not receipt["approved"]
    assert "timed out" in receipt["error"]
    assert not alive(int(pids.read_text().split()[1]))
    with cr.locked(cr.key_lock(cr.store(clone), receipt["key"])):
        pass


# ---------------------------------------------------------------- workspaces and plans


def test_checkouts_skip_deleted_lanes(root: Path, clone: Path) -> None:
    lane = root / "shop/.worktrees/feature"
    cr.git(clone, "worktree", "add", "-b", "feature", str(lane))
    gone = root / "shop/.worktrees/gone"
    cr.git(clone, "worktree", "add", "-b", "gone", str(gone))
    for path in sorted(gone.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    gone.rmdir()  # Listed by Git but deleted: skipped.
    assert set(cr.checkouts(root)) == {root, clone, lane}


@pytest.mark.parametrize("change", ["staged", "unstaged", "untracked", "framework"])
def test_plan_review_covers_uncommitted_work(
    root: Path, clone: Path, provider: list[str], change: str
) -> None:
    cr.review_plan(root / "shop", "Add a test")
    cr.review_plan(root / "shop", "Add a test")
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
    cr.review_plan(root / "shop", "Add a test")
    assert len(provider) == 2  # The snapshot the plan was checked against changed.
    assert cr.out(clone, "status", "--porcelain") == status  # The real index is untouched.


def test_plan_review_sees_index_only_changes(root: Path, clone: Path, provider: list[str]) -> None:
    cr.review_plan(root / "shop", "Add a test")
    stage(clone, 7)
    (clone / "code.py").write_text("value = 1\n")  # Working copy back to HEAD, index changed.
    cr.review_plan(root / "shop", "Add a test")
    assert len(provider) == 2
    assert "+value = 7" in provider[-1]  # The reviewer sees the staged-only change.


def test_plan_in_a_workspace_ignores_other_products(root: Path, provider: list[str]) -> None:
    other = root / "other"
    other.mkdir()
    (other / "workspace.toml").write_text('name = "other"\n')
    cr.git(other, "init", "repo")
    cr.review_plan(root / "shop", "Add a test")
    (other / "repo/file.txt").write_text("unrelated\n")
    cr.review_plan(root / "shop", "Add a test")
    assert len(provider) == 1  # Another product's work does not invalidate this plan.
    cr.review_plan(root, "Add a test")
    (other / "repo/file.txt").write_text("changed\n")
    cr.review_plan(root, "Add a test")
    assert len(provider) == 3  # From the root, every checkout counts.


def test_plan_snapshot_never_writes_secrets(root: Path) -> None:
    secret = b"API_KEY=synthetic-not-a-real-key\n"
    (root / "shop/.env").write_bytes(secret)
    (root / "id_rsa_backup").write_bytes(secret + b"x")
    (root / "server.pem").write_bytes(b"synthetic\n")
    cr.git(root, "add", "-f", "server.pem")
    tree_id = cr.snapshot(root)
    listed = cr.out(root, "ls-tree", "-r", "--name-only", tree_id)
    assert ".env" not in listed and "id_rsa" not in listed and "server.pem" not in listed
    for data in (secret, secret + b"x"):  # Untracked ones are not in the object store at all.
        blob = (
            subprocess.run(
                ["git", "-C", str(root), "hash-object", "--stdin"], input=data, capture_output=True
            )
            .stdout.decode()
            .strip()
        )
        assert cr.git(root, "cat-file", "-e", blob, ok=(0, 1, 128)).returncode != 0


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


# ---------------------------------------------------------------- hook


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
    unsafe = "1. Rewrite history\n2. git push --force origin main"
    assert decision(plan_hook(root, unsafe)) == "deny" and len(provider) == 1
    asked = plan_hook(root, unsafe)
    assert decision(asked) == "ask" and "already blocked" in json.dumps(asked)
    assert len(provider) == 1  # The user decides; the reviewer is not asked again.
    assert plan_hook(root, "1. Open a branch\n2. Push it for review") == {}
    assert len(provider) == 2
    assert cr.hook({"cwd": str(root), "tool_name": "Edit", "tool_input": {}}) == {}


def test_blocked_plan_is_reviewed_again_when_the_code_moved(
    root: Path, clone: Path, provider: list[str]
) -> None:
    unsafe = "1. git push --force origin main"
    assert decision(plan_hook(root, unsafe)) == "deny" and len(provider) == 1
    stage(clone, 2)
    assert decision(plan_hook(root, unsafe)) == "deny" and len(provider) == 2  # Not "ask".
    assert decision(plan_hook(root, unsafe)) == "ask" and len(provider) == 2


@pytest.mark.parametrize(
    "command",
    [
        "uv run python ai/scripts/cross_review.py review --owner-override 'ok'",
        "cd ai/scripts && python cross_review.py review --owner-override=ok",
    ],
)
def test_hook_asks_the_owner_before_the_agent_s_override(command: str) -> None:
    output = cr.hook({"cwd": "/", "tool_name": "Bash", "tool_input": {"command": command}})
    assert decision(output) == "ask" and "owner" in json.dumps(output)


@pytest.mark.parametrize(
    "command", ["uv run python ai/scripts/cross_review.py review --commit HEAD", "ls"]
)
def test_hook_lets_other_bash_calls_through(command: str) -> None:
    assert cr.hook({"cwd": "/", "tool_name": "Bash", "tool_input": {"command": command}}) == {}


# ---------------------------------------------------------------- command line


def test_cli_plan_check_and_errors(
    root: Path,
    clone: Path,
    provider: list[str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cr.main(["check", "--target", str(clone)]) == 0
    assert capsys.readouterr().out == "Nothing staged; no index review required.\n"
    stage(clone, 3)
    assert cr.main(["check", "--target", str(clone)]) == 1
    response = tmp_path / "response.txt"
    response.write_text("The caller was updated")
    assert cr.main(["review", "--target", str(clone), "--feedback", str(response)]) == 1
    assert "Cross-model review BLOCKED" in capsys.readouterr().out
    plan = tmp_path / "plan.md"
    plan.write_text("Small safe plan")
    assert cr.main(["review", "--target", str(root), "--plan", str(plan)]) == 0
    assert cr.main(["review", "--target", str(tmp_path / "missing")]) == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["check", "--commit", "HEAD"],
        ["hook", "--range", "a..b"],
        ["check", "--plan", "p.md"],
        ["check", "--owner-override", "because"],
        ["review", "--owner", "because"],
    ],
)
def test_subjects_must_match_the_action(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cr.main(argv)
    assert exc.value.code == 2


# ---------------------------------------------------------------- ranges


def test_range_review_covers_the_combined_change(
    clone: Path, provider: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    feature(clone, 4, 2)
    check = ["check", "--target", str(clone), "--range", "main..feat"]
    assert cr.main(check) == 1 and "not reviewed" in capsys.readouterr().out
    assert cr.main(["review", "--target", str(clone), "--range", "main..feat"]) == 0
    assert "-value = 1\n+value = 2" in provider[-1] and "value = 4" not in provider[-1]
    assert cr.main(check) == 0 and len(provider) == 1  # No per-commit receipts needed.
    stage(clone, 5)
    commit(clone, "Set 2", "--amend")  # A new head tree needs a new range review.
    assert cr.main(check) == 1


def test_range_the_panel_blocks_fails_the_check(clone: Path, provider: list[str]) -> None:
    feature(clone, 3)
    assert cr.main(["review", "--target", str(clone), "--range", "main..feat"]) == 1
    assert cr.main(["check", "--target", str(clone), "--range", "main..feat"]) == 1


def test_range_review_starts_at_the_merge_base(clone: Path, provider: list[str]) -> None:
    feature(clone, 2)
    assert cr.main(["review", "--target", str(clone), "--range", "main..feat"]) == 0
    cr.git(clone, "checkout", "-q", "main")
    stage(clone, 7, "main.py")
    commit(clone, "Main moves on")
    assert cr.main(["check", "--target", str(clone), "--range", "main..feat"]) == 0
    assert cr.main(["review", "--target", str(clone), "--range", "main..feat", "--force"]) == 0
    assert "main.py" not in provider[-1]  # Only what the change adds.


def test_docs_only_range_needs_no_review(clone: Path, provider: list[str]) -> None:
    (clone / "notes.md").write_text("plain words\n")
    cr.git(clone, "add", "notes.md")
    commit(clone, "docs")
    assert cr.main(["check", "--target", str(clone), "--range", "HEAD~1..HEAD"]) == 0
    stage(clone, 2)
    commit(clone, "code")
    assert cr.main(["check", "--target", str(clone), "--range", "HEAD~2..HEAD"]) == 1
    assert provider == []


def test_range_whose_commits_held_a_secret_is_refused(clone: Path, provider: list[str]) -> None:
    cr.git(clone, "checkout", "-q", "-b", "feat")
    (clone / "settings.py").write_text(f'aws_key = "{KEY}"\n')
    cr.git(clone, "add", "settings.py")
    commit(clone, "leak")
    cr.git(clone, "rm", "-q", "settings.py")
    commit(clone, "unleak")  # The combined change is empty.
    assert cr.main(["review", "--target", str(clone), "--range", "main..feat"]) == 2
    assert cr.main(["check", "--target", str(clone), "--range", "main..feat"]) == 1
    assert provider == []


def test_range_whose_merges_held_a_secret_is_refused(clone: Path, provider: list[str]) -> None:
    for side in ("one", "two"):
        cr.git(clone, "checkout", "-q", "-b", side, "main")
        stage(clone, 2, f"{side}.py")
        commit(clone, side)
    cr.git(clone, "checkout", "-q", "-b", "feat", "main")
    cr.git(clone, "merge", "-q", "--no-ff", "--no-commit", "one")
    (clone / "settings.py").write_text(f'aws_key = "{KEY}"\n')
    cr.git(clone, "add", "settings.py")
    commit(clone, "Merge one")  # The secret is in this merge alone.
    cr.git(clone, "merge", "-q", "--no-ff", "--no-commit", "two")
    cr.git(clone, "rm", "-q", "settings.py")
    commit(clone, "Merge two")
    assert cr.main(["review", "--target", str(clone), "--range", "main..feat"]) == 2
    assert provider == []


def test_renaming_code_to_markdown_still_needs_a_range_review(
    clone: Path, provider: list[str]
) -> None:
    cr.git(clone, "mv", "code.py", "code.md")
    commit(clone, "Rename")
    assert cr.main(["check", "--target", str(clone), "--range", "HEAD~1..HEAD"]) == 1


def test_non_utf8_files_can_be_reviewed(clone: Path, provider: list[str]) -> None:
    (clone / "table.csv").write_bytes("caf\xe9,1\n".encode("latin-1"))
    cr.git(clone, "add", "table.csv")
    commit(clone, "Add a Latin-1 table")
    assert cr.main(["review", "--target", str(clone), "--commit", "HEAD"]) == 0
    assert "caf" in provider[-1]
    cr.git(clone, "checkout", "-q", "-b", "feat")
    stage(clone, 2)
    commit(clone, "Set 2")
    assert cr.main(["review", "--target", str(clone), "--range", "HEAD~2..feat"]) == 0


def test_prompts_name_no_local_path(root: Path, clone: Path, provider: list[str]) -> None:
    stage(clone, 2)
    cr.review_staged(clone, [])
    cr.review_plan(root / "shop", "Add a test")
    assert all(str(root) not in prompt for prompt in provider)


def test_range_needs_two_dots(clone: Path, provider: list[str]) -> None:
    assert cr.main(["review", "--target", str(clone), "--range", "HEAD"]) == 2
    assert provider == []


# ---------------------------------------------------------------- round cap


@pytest.fixture
def real_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cr, "ROUND_CAP", ROUND_CAP)


@pytest.mark.usefixtures("real_cap")
def test_third_round_on_a_commit_is_refused_through_amends_and_fixups(
    clone: Path, provider: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    response = tmp_path / "response.txt"
    response.write_text("Value 3 is required")
    review = ["review", "--target", str(clone), "--commit", "HEAD"]
    stage(clone, 3)
    commit(clone, "Add feature")
    assert cr.main(review) == 1
    assert cr.main(review) == 1 and len(provider) == 1  # A reused receipt is no round.
    assert cr.main([*review, "--feedback", str(response)]) == 1  # Round 2.
    stage(clone, 4)
    commit(clone, "Add feature", "--amend")  # Squashed fix: new SHA, same lineage.
    assert cr.main(review) == 3 and len(provider) == 2
    assert "round cap reached" in capsys.readouterr().err
    stage(clone, 5)
    commit(clone, "fixup! Add feature")
    assert cr.main(review) == 3 and len(provider) == 2
    assert cr.main([*review, "--owner-override", "Checked with the owner"]) == 0
    receipt = cr.read_json(cr.store(clone) / f"{cr.commit_key(*cr.committed(clone, 'HEAD'))}.json")
    assert receipt["owner_override"] == "Checked with the owner" and len(provider) == 3


@pytest.mark.usefixtures("real_cap")
def test_lineages_stay_apart_and_failed_runs_are_no_round(
    clone: Path, provider: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stage(clone, 3)
    cr.review_staged(clone, [])
    reviewer = cr.cursor

    def fail(*_args: Any) -> dict[str, Any]:
        raise cr.ReviewError("rate limited")

    monkeypatch.setattr(cr, "cursor", fail)
    assert "error" in cr.review_staged(clone, [], feedback="again")  # Could not run: no round.
    monkeypatch.setattr(cr, "cursor", reviewer)
    cr.review_staged(clone, [], feedback="again")
    with pytest.raises(cr.RoundCapError):
        cr.review_staged(clone, [], feedback="again")
    commit(clone, "First")
    stage(clone, 2)
    cr.review_staged(clone, [])  # A new HEAD starts a new lineage.
    cr.git(clone, "checkout", "-q", "-b", "other")
    stage(clone, 5)
    cr.review_staged(clone, [])  # So does another branch.


@pytest.mark.usefixtures("real_cap")
def test_plan_reviews_are_capped_by_path(root: Path, provider: list[str], tmp_path: Path) -> None:
    plan = tmp_path / "plan.md"
    review = ["review", "--target", str(root), "--plan", str(plan)]
    for version in ("one", "two"):
        plan.write_text(f"Plan {version}: git push --force")
        assert cr.main(review) == 1
    plan.write_text("Plan three")
    assert cr.main(review) == 3 and len(provider) == 2
    # The hook counts the same plan file's rounds.
    assert decision(plan_hook(root, "Plan three", planFilePath=str(plan))) == "ask"
    assert len(provider) == 2


@pytest.mark.usefixtures("real_cap")
def test_the_third_blocked_plan_in_the_hook_goes_to_the_user(
    root: Path, provider: list[str], tmp_path: Path
) -> None:
    path = str(tmp_path / "plans" / "slug.md")
    for version in ("one", "two"):
        blocked = plan_hook(root, f"{version}: git push --force", planFilePath=path)
        assert decision(blocked) == "deny"
    asked = plan_hook(root, "three: git push --force", planFilePath=path)
    reason = json.dumps(asked)
    assert decision(asked) == "ask" and len(provider) == 2  # No model was asked.
    assert "2 of 2 rounds" in reason and "owner decides" in reason and path in reason
    other = str(tmp_path / "plans" / "other.md")
    assert decision(plan_hook(root, "four: git push --force", planFilePath=other)) == "deny"
    assert len(provider) == 3


@pytest.mark.usefixtures("real_cap")
def test_hook_plan_rounds_restart_after_an_approval(root: Path, provider: list[str]) -> None:
    session = {"session_id": "s1"}  # Without a plan file path, the session is the lineage.
    assert decision(plan_hook(root, "one: git push --force", **session)) == "deny"
    assert plan_hook(root, "two: open a branch", **session) == {}
    assert decision(plan_hook(root, "three: git push --force", **session)) == "deny"
    assert decision(plan_hook(root, "four: git push --force", **session)) == "deny"
    assert decision(plan_hook(root, "five: git push --force", **session)) == "ask"
    assert len(provider) == 4


@pytest.mark.usefixtures("real_cap")
def test_range_rounds_count_per_head_branch_however_spelled(
    clone: Path, provider: list[str]
) -> None:
    feature(clone, 3)
    spans = ["main..feat", "main..refs/heads/feat"]
    codes = [cr.main(["review", "--target", str(clone), "--range", s, "--force"]) for s in spans]
    cr.git(clone, "checkout", "-q", "main")  # Rounds go to the range head, not the checkout.
    codes.append(cr.main(["review", "--target", str(clone), "--range", "main..feat", "--force"]))
    assert codes == [1, 1, 3] and len(provider) == 2


@pytest.mark.usefixtures("real_cap")
def test_an_approval_ends_the_lineage_s_rounds(clone: Path, provider: list[str]) -> None:
    feature(clone, 2)  # Landing main again and again must not run into the cap.
    review = ["review", "--target", str(clone), "--range", "main..feat", "--force"]
    assert [cr.main(review) for _ in range(3)] == [0, 0, 0] and len(provider) == 3
    stage(clone, 3)
    commit(clone, "Set 3")
    assert [cr.main(review) for _ in range(3)] == [1, 1, 3]  # Blocked rounds still count.


@pytest.mark.usefixtures("real_cap")
def test_range_head_must_be_a_branch(clone: Path, provider: list[str]) -> None:
    feature(clone, 2)
    tip = cr.head(clone)
    assert cr.main(["review", "--target", str(clone), "--range", f"main..{tip}"]) == 2
    assert provider == []


@pytest.mark.usefixtures("real_cap")
def test_check_reports_rounds_and_branch_runs(
    clone: Path, provider: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    stage(clone, 3)
    commit(clone, "Add feature")
    cr.main(["review", "--target", str(clone), "--commit", "HEAD"])
    cr.main(["review", "--target", str(clone), "--commit", "HEAD", "--force"])
    capsys.readouterr()
    cr.main(["check", "--target", str(clone), "--range", "HEAD~1..HEAD"])
    output = capsys.readouterr().out
    assert "main: Add feature: 2 of 2 rounds" in output
    assert "Review runs on main: 2, for 1 commit(s) in HEAD~1..HEAD." in output
