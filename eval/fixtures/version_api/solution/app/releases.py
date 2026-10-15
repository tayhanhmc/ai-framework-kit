"""Release tag selection."""

from packaging.version import InvalidVersion, Version


def latest_stable(tags: list[str]) -> str | None:
    """Return the tag of the highest final release, skipping non-version tags."""
    best: tuple[Version, str] | None = None
    for tag in tags:
        try:
            version = Version(tag.removeprefix("v"))
        except InvalidVersion:
            continue
        if version.is_prerelease:
            continue
        if best is None or version > best[0]:
            best = (version, tag)
    return best[1] if best else None
