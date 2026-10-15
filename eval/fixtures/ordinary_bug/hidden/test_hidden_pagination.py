import unittest

from app.pagination import paginate


class HiddenPaginationTest(unittest.TestCase):
    def test_first_page(self) -> None:
        self.assertEqual(paginate(list(range(10)), 1, 3), [0, 1, 2])

    def test_last_partial_page(self) -> None:
        self.assertEqual(paginate(list(range(10)), 4, 3), [9])

    def test_past_end_is_empty(self) -> None:
        self.assertEqual(paginate(list(range(10)), 5, 3), [])
