"""Order pricing rules."""


def order_total(lines: list[tuple[int, int]], discount_percent: int = 0) -> int:
    """Total in cents for ``lines`` of (unit price in cents, quantity), discounted."""
    gross = sum(price * qty for price, qty in lines)
    return gross * (100 - discount_percent) // 100
