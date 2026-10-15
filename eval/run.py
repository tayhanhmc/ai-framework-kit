"""Context evaluation runner. Implements docs/guidelines/context_evaluation.md.

Deterministic mode (default, no model calls; suitable for CI) validates every fixture:
the unmodified fixture fails its hidden tests, the reference solution passes with no
regressions or check findings, any seeded violation is caught, the runtime fixture's
service is stopped and its port freed, and each treatment lays out the expected context
without the hidden tests.

Live mode (``--live``) runs bounded ``claude -p`` trials and appends one JSONL row per
trial, including failed and blocked trials. Summarize with ``summarize.py``.

Run from the kit root:
    uv run --isolated --no-project --python ">=3.11" python eval/run.py
    uv run --isolated --no-project --python ">=3.11" python eval/run.py --live \\
        --tasks ordinary_bug,boundary --treatments minimal,framework --trials 1 \\
        --model haiku --total-budget-usd 2 --allow-unsandboxed-bash

Exit codes: 0 pass, 1 a validation failed, 2 could not run.
"""

from __future__ import annotations

import argparse
import datetime
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from grader import Grade, grade  # noqa: E402
from harness import EVAL_DIR, Fixture, all_fixtures, load_fixture, overlay, treatment  # noqa: E402
from live import LiveConfig, claude_version, run_agent  # noqa: E402

KIT_ROOT = EVAL_DIR.parent


def kit_revision() -> str:
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=KIT_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=KIT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as err:
        return f"unknown ({err})"
    return rev + ("-dirty" if dirty else "")


# --- deterministic validation -----------------------------------------------------


def _graded_variant(fixture: Fixture, overlay_dir: str | None) -> Grade:
    with tempfile.TemporaryDirectory(prefix="eval-variant-") as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(fixture.repo, repo)
        if overlay_dir:
            overlay(fixture.root / overlay_dir, repo)
        return grade(fixture, repo)


def validate_fixture(fixture: Fixture) -> list[tuple[bool, str]]:
    """Return (ok, message) for each deterministic check of one fixture."""
    results: list[tuple[bool, str]] = []

    unmodified = _graded_variant(fixture, None)
    results.append(
        (
            not unmodified.success
            and unmodified.hidden_total > 0
            and unmodified.hidden_passed < unmodified.hidden_total,
            f"unmodified fails hidden tests ({unmodified.hidden_passed}/{unmodified.hidden_total}"
            " passed)",
        )
    )
    results.append(
        (
            not unmodified.boundary_violations,
            f"unmodified has no boundary findings {unmodified.boundary_violations}",
        )
    )

    ref = _graded_variant(fixture, "solution")
    results.append(
        (
            ref.success and ref.hidden_passed == ref.hidden_total > 0,
            f"reference solution passes ({ref.hidden_passed}/{ref.hidden_total}; "
            f"failures={ref.failures} regressions={ref.regressions} "
            f"boundary={ref.boundary_violations})",
        )
    )
    results.append(
        (
            not ref.stale_context_mistakes,
            f"reference has no stale-context marks {ref.stale_context_mistakes}",
        )
    )
    if fixture.runtime:
        results.append(
            (
                ref.runtime_cleanup_ok is True and not ref.grader_errors,
                f"service stopped and port freed after tests {ref.grader_errors}",
            )
        )

    if fixture.seeded_expect:
        seeded = _graded_variant(fixture, "seeded")
        found = (
            seeded.boundary_violations
            if fixture.seeded_expect == "boundary"
            else seeded.stale_context_mistakes
        )
        results.append((bool(found), f"seeded {fixture.seeded_expect} violation caught: {found}"))

    for name in ("minimal", "framework", "proposed:example"):
        with tempfile.TemporaryDirectory(prefix="eval-layout-") as tmp:
            dest = Path(tmp)
            treatment(name)(fixture, dest)
            hidden_names = {p.name for p in fixture.hidden.glob("*.py")}
            leaked = [str(p) for p in dest.rglob("*.py") if p.name in hidden_names]
            ok = not leaked and (dest / "repo" / ".git").is_dir()
            msg = f"{name} layout: repo with baseline commit, hidden tests absent {leaked}"
            if name == "minimal":
                extra = sorted(p.name for p in dest.iterdir() if p.name != "repo")
                ok = ok and not extra
                msg += f", nothing beside repo/ {extra}"
            else:
                agents = (dest / "AGENTS.md").read_text(encoding="utf-8")
                unrouted = [
                    c["path"]
                    for c in fixture.context
                    if f"`{c['path']}`" not in agents or not (dest / c["path"]).is_file()
                ]
                ok = ok and not unrouted
                msg += f", AGENTS.md routes every context doc {unrouted}"
            results.append((ok, msg))
    return results


def deterministic(fixtures: list[Fixture]) -> int:
    failed = 0
    for fixture in fixtures:
        print(f"== {fixture.name}: {fixture.kind}")
        for ok, msg in validate_fixture(fixture):
            failed += not ok
            print(f"  {'PASS' if ok else 'FAIL'} {msg}")
    print(
        f"deterministic: {len(fixtures)} fixtures, "
        f"{'all checks passed' if not failed else f'{failed} check(s) failed'}"
    )
    return 1 if failed else 0


# --- live trials ------------------------------------------------------------------


def live(fixtures: list[Fixture], args: argparse.Namespace) -> int:
    if shutil.which("claude") is None:
        print("live: claude CLI not found; UNTESTED", file=sys.stderr)
        return 2
    cfg = LiveConfig(args.model, args.max_turns, args.max_budget_usd, args.timeout)
    run_id = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out or EVAL_DIR / "results" / run_id).resolve()
    out.mkdir(parents=True, exist_ok=True)
    jsonl = out / "results.jsonl"
    common = {
        "run_id": run_id,
        "label": args.label,
        "model_requested": args.model,
        "claude_version": claude_version(),
        "kit_revision": kit_revision(),
        "max_turns": args.max_turns,
        "max_budget_usd": args.max_budget_usd,
        "review_false_positives": None,  # manual field: fill after reading the trace
    }
    spent = 0.0
    for trial in range(1, args.trials + 1):
        for fixture in fixtures:
            names = args.treatments.split(",")
            # Alternate the order between trials so no treatment always runs first.
            for tname in names if trial % 2 else names[::-1]:
                suffix = uuid.uuid4().hex[:6]
                trial_id = f"{fixture.name}.{tname.replace(':', '-')}.{trial}.{suffix}"
                row: dict[str, object] = {
                    **common,
                    "trial_id": trial_id,
                    "task": fixture.name,
                    "treatment": tname,
                    "trial": trial,
                }
                if spent >= args.total_budget_usd:
                    row.update(status="skipped", error=f"total budget ${spent:.2f} reached")
                    _append(jsonl, row)
                    continue
                evidence = out / "evidence" / trial_id
                evidence.mkdir(parents=True)
                row["evidence"] = str(evidence)
                parent = Path(tempfile.mkdtemp(prefix="eval-trial-", dir=args.trial_dir))
                trial_root = parent / "w"
                trial_root.mkdir()
                try:
                    treatment(tname)(fixture, trial_root)
                    print(f"[{trial_id}] running in {trial_root}", flush=True)
                    row.update(run_agent(fixture.full_prompt, trial_root, cfg, evidence))
                    spent += float(str(row.get("cost_usd") or 0.0))
                    subprocess.run(
                        ["git", "add", "-A", "-N"], cwd=trial_root / "repo", capture_output=True
                    )
                    diff = subprocess.run(
                        ["git", "diff", "HEAD"],
                        cwd=trial_root / "repo",
                        capture_output=True,
                        text=True,
                    )
                    (evidence / "diff.patch").write_text(diff.stdout, encoding="utf-8")
                    g = grade(fixture, trial_root / "repo")
                    row.update(g.as_dict())
                    (evidence / "grade.json").write_text(json.dumps(g.as_dict(), indent=1))
                except Exception as err:  # noqa: BLE001 - recorded as a failed trial row
                    row["status"] = "error"
                    row["error"] = f"{type(err).__name__}: {err}"
                    row.setdefault("success", False)
                finally:
                    if args.keep:
                        row["trial_dir"] = str(trial_root)
                    else:
                        shutil.rmtree(parent, ignore_errors=False)
                _append(jsonl, row)
                print(
                    f"[{trial_id}] status={row.get('status')} success={row.get('success')} "
                    f"cost={row.get('cost_usd')} elapsed={row.get('elapsed_s')}s",
                    flush=True,
                )
    print(f"live: wrote {jsonl} (spent ${spent:.4f})")
    return 0


def _append(path: Path, row: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--live", action="store_true", help="run claude -p trials (costs money)")
    p.add_argument(
        "--allow-unsandboxed-bash",
        action="store_true",
        help="required with --live: trial agents run Bash as you, unsandboxed (see live.py)",
    )
    p.add_argument("--tasks", help="comma-separated fixture names (default: all)")
    p.add_argument(
        "--treatments",
        default="minimal,framework",
        help="comma-separated: minimal, framework, proposed:<module>",
    )
    p.add_argument("--trials", type=int, default=1)
    p.add_argument("--model", default="haiku")
    p.add_argument("--max-turns", type=int, default=30)
    p.add_argument("--max-budget-usd", type=float, default=0.50, help="per trial")
    p.add_argument(
        "--total-budget-usd",
        type=float,
        default=2.00,
        help="stop starting trials once this much is spent",
    )
    p.add_argument("--timeout", type=float, default=600.0, help="per-trial wall clock, s")
    p.add_argument("--label", default="", help="e.g. 'smoke'; stored on every row")
    p.add_argument("--out", help="results folder (default eval/results/<run id>)")
    p.add_argument("--trial-dir", help="parent for temporary trial folders")
    p.add_argument("--keep", action="store_true", help="keep trial folders for inspection")
    args = p.parse_args(argv)
    if args.live and not args.allow_unsandboxed_bash:
        print(
            "run: --live gives each trial agent unsandboxed Bash as you; pass"
            " --allow-unsandboxed-bash to accept that (see eval/live.py)",
            file=sys.stderr,
        )
        return 2
    try:
        fixtures = (
            [load_fixture(n) for n in args.tasks.split(",")] if args.tasks else all_fixtures()
        )
        for name in args.treatments.split(","):
            treatment(name)
    except (OSError, ValueError, KeyError) as err:
        print(f"run: could not load fixtures or treatments: {err}", file=sys.stderr)
        return 2
    return live(fixtures, args) if args.live else deterministic(fixtures)


if __name__ == "__main__":
    sys.exit(main())
