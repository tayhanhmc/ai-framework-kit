import unittest

import packaging
from app.releases import latest_stable


class HiddenReleasesTest(unittest.TestCase):
    def test_pinned_version_in_use(self) -> None:
        self.assertEqual(packaging.__version__, "26.3")

    def test_skips_invalid_and_prereleases(self) -> None:
        tags = ["v1.0.0", "nightly", "v1.2.0rc1", "v1.1.0", "latest", "v1.3.0.dev1"]
        self.assertEqual(latest_stable(tags), "v1.1.0")

    def test_keeps_original_tag(self) -> None:
        self.assertEqual(latest_stable(["2.0", "v1.9"]), "2.0")

    def test_none_when_no_valid(self) -> None:
        self.assertIsNone(latest_stable(["nightly", "latest-build"]))
