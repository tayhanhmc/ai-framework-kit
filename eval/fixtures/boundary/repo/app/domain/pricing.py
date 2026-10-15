"""Order pricing rules."""


def order_total(customer_id: str, lines: list[tuple[int, int]]) -> int:
    """Total in cents for ``lines`` of (unit price in cents, quantity)."""
    # TODO: apply the customer's discount
    return sum(price * qty for price, qty in lines)
