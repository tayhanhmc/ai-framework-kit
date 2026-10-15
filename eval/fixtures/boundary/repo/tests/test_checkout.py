import unittest

from app.api.checkout import checkout


class CheckoutTest(unittest.TestCase):
    def test_returns_customer(self) -> None:
        self.assertEqual(checkout("c2", [(100, 1)])["customer"], "c2")

    def test_no_discount_customer(self) -> None:
        self.assertEqual(checkout("c2", [(250, 2)])["total_cents"], 500)
