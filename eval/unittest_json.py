"""Run unittest discovery and write each test's outcome as JSON.

Usage: python unittest_json.py <start dir> <top-level dir> <output json>

The grader runs this inside the graded copy, so it uses only the standard library.
Import errors surface as tests whose id starts with ``unittest.loader._FailedTest``.
"""

from __future__ import annotations

import json
import sys
import unittest


class _Result(unittest.TestResult):
    def __init__(self) -> None:
        super().__init__()
        self.outcomes: dict[str, str] = {}

    def addSuccess(self, test: unittest.TestCase) -> None:  # noqa: N802
        self.outcomes[test.id()] = "pass"

    def addFailure(self, test: unittest.TestCase, err: object) -> None:  # noqa: N802
        super().addFailure(test, err)  # type: ignore[arg-type]
        self.outcomes[test.id()] = "fail"

    def addError(self, test: unittest.TestCase, err: object) -> None:  # noqa: N802
        super().addError(test, err)  # type: ignore[arg-type]
        self.outcomes[test.id()] = "error"

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:  # noqa: N802
        self.outcomes[test.id()] = "skip"


def main() -> int:
    start, top, out = sys.argv[1:4]
    sys.path.insert(0, top)
    suite = unittest.defaultTestLoader.discover(start, top_level_dir=top)
    result = _Result()
    suite.run(result)
    details = [f"{t.id()}\n{tb}" for t, tb in result.failures + result.errors]
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"outcomes": result.outcomes, "details": details}, f, indent=1)
    return 0 if result.wasSuccessful() and result.outcomes else 1


if __name__ == "__main__":
    sys.exit(main())
