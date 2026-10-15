"""Settings loader."""

import tomllib
from pathlib import Path

DEFAULTS: dict[str, object] = {"host": "localhost", "port": 8080}
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


def load(path: Path | None = None) -> dict[str, object]:
    """Return settings from the ``[server]`` table of ``path``, over DEFAULTS."""
    path = path or CONFIG_PATH
    settings = dict(DEFAULTS)
    if path.exists():
        with path.open("rb") as f:
            settings.update(tomllib.load(f).get("server", {}))
    return settings
