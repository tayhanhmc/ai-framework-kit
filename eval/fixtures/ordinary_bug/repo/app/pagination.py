"""Pagination helpers for list endpoints."""


def paginate(items: list[object], page: int, per_page: int) -> list[object]:
    """Return the items on 1-based ``page``."""
    if page < 1 or per_page < 1:
        raise ValueError("page and per_page must be >= 1")
    start = page * per_page
    return items[start : start + per_page]


def page_count(n_items: int, per_page: int) -> int:
    """Number of pages needed for ``n_items``."""
    return -(-n_items // per_page)
