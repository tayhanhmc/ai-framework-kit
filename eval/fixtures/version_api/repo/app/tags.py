"""Tag name helpers."""


def is_release_tag(name: str) -> bool:
    """Release tags start with ``v`` followed by a digit."""
    return len(name) > 1 and name[0] == "v" and name[1].isdigit()
