"""Summarize live-trial JSONL rows as a markdown table.

Usage: python eval/summarize.py <results.jsonl> [...]

Every row counts, including blocked, timed-out and skipped trials (skipped rows are
listed but excluded from rates). A treatment difference is reported only when a
two-sided Fisher exact test on success counts gives p < 0.05 with at least
``MIN_TRIALS`` graded trials per arm; anything else is labelled inconclusive.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from itertools import combinations
from math import comb
from pathlib import Path
from statistics import mean
from typing import Any

MIN_TRIALS = 5


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """p-value for the 2x2 table [[a, b], [c, d]] (successes, failures per arm)."""
    row1, col1, n = a + b, a + c, a + b + c + d
    denom = comb(n, col1)

    def prob(x: int) -> float:
        return comb(row1, x) * comb(n - row1, col1 - x) / denom

    observed = prob(a)
    lo, hi = max(0, col1 - (n - row1)), min(row1, col1)
    return min(1.0, sum(p for x in range(lo, hi + 1) if (p := prob(x)) <= observed * (1 + 1e-9)))


def verdict(s1: int, n1: int, s2: int, n2: int) -> str:
    if min(n1, n2) < MIN_TRIALS:
        return f"inconclusive (n<{MIN_TRIALS} per arm)"
    p = fisher_two_sided(s1, n1 - s1, s2, n2 - s2)
    return f"difference (Fisher p={p:.3f})" if p < 0.05 else f"inconclusive (Fisher p={p:.2f})"


def _fmt(values: list[float], digits: int) -> str:
    return f"{mean(values):.{digits}f}" if values else "-"


def summarize(rows: list[dict[str, Any]]) -> str:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["task"], row["treatment"])].append(row)
    labels = sorted({str(r.get("label") or "") for r in rows} - {""})
    models = sorted({str(r.get("model") or r.get("model_requested")) for r in rows})
    out = [
        f"Runs: {', '.join(sorted({r['run_id'] for r in rows}))}; label: "
        f"{', '.join(labels) or 'none'}; models: {', '.join(models)}; "
        f"kit: {', '.join(sorted({str(r.get('kit_revision')) for r in rows}))}",
        "",
        "| task | treatment | graded | success | regressions | boundary | stale | questions"
        " | ctx docs read | mean time s | mean cost $ | mean out tokens | other |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (task, tname), rs in sorted(groups.items()):
        graded = [r for r in rs if "success" in r and r.get("status") != "skipped"]
        other = [r.get("status") for r in rs if r not in graded]
        succ = sum(1 for r in graded if r["success"])
        out.append(
            f"| {task} | {tname} | {len(graded)} | {succ}/{len(graded)} "
            f"| {sum(1 for r in graded if r.get('regressions'))} "
            f"| {sum(1 for r in graded if r.get('boundary_violations'))} "
            f"| {sum(1 for r in graded if r.get('stale_context_mistakes'))} "
            f"| {sum(int(r.get('unnecessary_questions') or 0) for r in graded)} "
            f"| {_fmt([len(r.get('context_files_read') or []) for r in graded], 1)} "
            f"| {_fmt([r['elapsed_s'] for r in graded if r.get('elapsed_s') is not None], 0)} "
            f"| {_fmt([r['cost_usd'] for r in graded if r.get('cost_usd') is not None], 4)} "
            f"| {_fmt([r['output_tokens'] for r in graded if r.get('output_tokens')], 0)} "
            f"| {', '.join(map(str, other)) or '-'} |"
        )
    out += ["", "| task | comparison | verdict |", "|---|---|---|"]
    tasks = sorted({t for t, _ in groups})
    for task in tasks:
        arms = sorted(t for k, t in groups if k == task)
        for x, y in combinations(arms, 2):
            gx = [r for r in groups[(task, x)] if "success" in r and r.get("status") != "skipped"]
            gy = [r for r in groups[(task, y)] if "success" in r and r.get("status") != "skipped"]
            sx, sy = sum(r["success"] for r in gx), sum(r["success"] for r in gy)
            out.append(
                f"| {task} | {x} {sx}/{len(gx)} vs {y} {sy}/{len(gy)} "
                f"| {verdict(sx, len(gx), sy, len(gy))} |"
            )
    total = sum(float(r.get("cost_usd") or 0) for r in rows)
    out += [
        "",
        f"Total cost ${total:.4f} over {len(rows)} rows. Small differences are "
        "inconclusive; do not generalize a task-specific result without checking scope.",
    ]
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    rows = [
        json.loads(line)
        for path in argv
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        print("summarize: no rows", file=sys.stderr)
        return 2
    print(summarize(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
