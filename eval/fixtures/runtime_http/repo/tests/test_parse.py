import unittest

from app.server import parse_note_id


class ParseTest(unittest.TestCase):
    def test_parse(self) -> None:
        self.assertEqual(parse_note_id("/notes/12"), 12)
        self.assertIsNone(parse_note_id("/notes/x"))
