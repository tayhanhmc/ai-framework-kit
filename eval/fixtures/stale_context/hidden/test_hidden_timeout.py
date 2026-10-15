import tempfile
import unittest
from pathlib import Path

from app.config import load


class HiddenTimeoutTest(unittest.TestCase):
    def test_default(self) -> None:
        self.assertEqual(load(Path("/nonexistent/config.toml"))["timeout_seconds"], 30)

    def test_default_with_file_lacking_key(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.toml"
            path.write_text('[server]\nhost = "h"\n')
            self.assertEqual(load(path)["timeout_seconds"], 30)

    def test_override_from_toml(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.toml"
            path.write_text("[server]\ntimeout_seconds = 5\n")
            value = load(path)["timeout_seconds"]
            self.assertEqual(value, 5)
            self.assertIsInstance(value, int)
