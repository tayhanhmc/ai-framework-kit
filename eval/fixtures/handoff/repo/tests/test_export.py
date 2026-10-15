import unittest

from app.export import export_rows


class ExportTest(unittest.TestCase):
    def test_header(self) -> None:
        self.assertEqual(export_rows([]).splitlines()[0], "ID,Name,Joined")
