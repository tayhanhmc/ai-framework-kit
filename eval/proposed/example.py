"""Placeholder proposed-change treatment: copy this file to add one integration.

``apply`` receives the trial root after the framework layout is written (``AGENTS.md``,
``docs/``, ``ai/``, ``repo/``) and may add or change framework files there; it must not
touch ``repo/`` contents, so the code stays constant across treatments. Select it with
``--treatments framework,proposed:<module name>``. This example changes nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def apply(trial_root: Path, fixture: Any) -> None:
    del trial_root, fixture
