"""Checkout endpoint handler."""

from app.domain.pricing import order_total


def checkout(customer_id: str, lines: list[tuple[int, int]]) -> dict[str, object]:
    """Return the charge for an order."""
    return {"customer": customer_id, "total_cents": order_total(customer_id, lines)}
