import tempfile
import unittest
from pathlib import Path

from app.config import load


class ConfigTest(unittest.TestCase):
    def test_defaults_without_file(self) -> None:
        self.assertEqual(load(Path("/nonexistent/config.toml"))["port"], 8080)

    def test_file_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.toml"
            path.write_text('[server]\nport = 9000\n')
            self.assertEqual(load(path)["port"], 9000)
