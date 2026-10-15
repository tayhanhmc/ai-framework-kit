import unittest

from app.tags import is_release_tag


class TagsTest(unittest.TestCase):
    def test_release_tag(self) -> None:
        self.assertTrue(is_release_tag("v1.0"))
        self.assertFalse(is_release_tag("nightly"))
