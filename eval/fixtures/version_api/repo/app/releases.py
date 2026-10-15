"""Release tag selection."""

from packaging.version import LegacyVersion, parse


def latest_stable(tags: list[str]) -> str | None:
    """Return the tag of the highest final release, skipping non-version tags."""
    best = None
    for tag in tags:
        version = parse(tag.removeprefix("v"))
        if isinstance(version, LegacyVersion) or version.is_prerelease:
            continue
        if best is None or version > best[0]:
            best = (version, tag)
    return best[1] if best else None
