"""Customer records (an in-memory stand-in for the database)."""

_DISCOUNT_PERCENT = {"c1": 10, "c2": 0, "c3": 25}


def discount_percent(customer_id: str) -> int:
    """Stored discount percentage for a customer; 0 when unknown."""
    return _DISCOUNT_PERCENT.get(customer_id, 0)
