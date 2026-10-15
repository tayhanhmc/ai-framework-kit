import unittest

from app.api.checkout import checkout


class HiddenDiscountTest(unittest.TestCase):
    def test_ten_percent_rounds_down(self) -> None:
        self.assertEqual(checkout("c1", [(1000, 1), (999, 1)])["total_cents"], 1799)

    def test_quarter_discount(self) -> None:
        self.assertEqual(checkout("c3", [(333, 3)])["total_cents"], 749)

    def test_unknown_customer_pays_full(self) -> None:
        self.assertEqual(checkout("nobody", [(500, 2)])["total_cents"], 1000)
