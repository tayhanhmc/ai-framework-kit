"""Checkout endpoint handler."""

from app.domain.pricing import order_total
from app.storage.customers import discount_percent


def checkout(customer_id: str, lines: list[tuple[int, int]]) -> dict[str, object]:
    """Return the charge for an order."""
    total = order_total(lines, discount_percent(customer_id))
    return {"customer": customer_id, "total_cents": total}
