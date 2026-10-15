import unittest

from app.pagination import page_count, paginate


class PaginationTest(unittest.TestCase):
    def test_page_count(self) -> None:
        self.assertEqual(page_count(10, 3), 4)
        self.assertEqual(page_count(9, 3), 3)

    def test_rejects_bad_page(self) -> None:
        with self.assertRaises(ValueError):
            paginate([1, 2], 0, 1)
