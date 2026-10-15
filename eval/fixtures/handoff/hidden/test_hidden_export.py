import datetime
import unittest

from app.export import export_rows

JOINED = datetime.datetime(2026, 3, 4, 9, 30)


class HiddenExportTest(unittest.TestCase):
    def test_crlf_everywhere(self) -> None:
        text = export_rows([{"id": 1, "name": "Ann", "joined": JOINED}])
        self.assertEqual(text, "ID,Name,Joined\r\n1,Ann,2026-03-04\r\n")

    def test_quoting(self) -> None:
        text = export_rows([{"id": 2, "name": 'Smith, "Jo"', "joined": JOINED}])
        self.assertIn('2,"Smith, ""Jo""",2026-03-04\r\n', text)

    def test_none_is_empty(self) -> None:
        text = export_rows([{"id": 3, "name": None, "joined": None}])
        self.assertTrue(text.endswith("3,,\r\n"))
