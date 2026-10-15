"""Order pricing rules (seeded boundary violation for the grader self-check)."""

from app.storage.customers import discount_percent


def order_total(customer_id: str, lines: list[tuple[int, int]]) -> int:
    gross = sum(price * qty for price, qty in lines)
    return gross * (100 - discount_percent(customer_id)) // 100
