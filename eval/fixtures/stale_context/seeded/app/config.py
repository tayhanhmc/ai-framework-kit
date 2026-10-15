"""Settings loader (seeded stale-context mistake for the grader self-check)."""

import configparser
from pathlib import Path

DEFAULTS: dict[str, object] = {"host": "localhost", "port": 8080, "timeout_seconds": 30}


def load(path: Path | None = None) -> dict[str, object]:
    parser = configparser.ConfigParser()
    parser.read(path or "settings.ini")
    settings = dict(DEFAULTS)
    if parser.has_section("server"):
        settings.update(parser["server"])
    return settings
