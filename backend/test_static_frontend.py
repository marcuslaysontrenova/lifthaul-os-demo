import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server


class StaticFrontendRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.frontend = Path(self.tmp.name).resolve()
        (self.frontend / "index.html").write_text("<h1>LiftHaul</h1>", encoding="utf-8")
        (self.frontend / "theme.css").write_text("body{}", encoding="utf-8")
        (self.frontend / "secret.txt").write_text("no", encoding="utf-8")
        (self.frontend / "assets").mkdir()
        (self.frontend / "assets" / "fleet.webp").write_bytes(b"fleet")
        self.handler = object.__new__(server.Handler)

    def tearDown(self):
        self.tmp.cleanup()

    def test_root_maps_to_landing_page(self):
        with mock.patch.object(server, "PUBLIC_ROOT", self.frontend):
            self.assertEqual(self.handler._static_file("/"), self.frontend / "index.html")

    def test_asset_is_served_from_frontend_root(self):
        with mock.patch.object(server, "PUBLIC_ROOT", self.frontend):
            self.assertEqual(self.handler._static_file("/theme.css"), self.frontend / "theme.css")

    def test_nested_release_assets_are_served(self):
        with mock.patch.object(server, "PUBLIC_ROOT", self.frontend):
            self.assertEqual(
                self.handler._static_file("/assets/fleet.webp"),
                self.frontend / "assets" / "fleet.webp",
            )

    def test_nested_and_unapproved_files_are_rejected(self):
        with mock.patch.object(server, "PUBLIC_ROOT", self.frontend):
            self.assertIsNone(self.handler._static_file("/../index.html"))
            self.assertIsNone(self.handler._static_file("/%2e%2e/index.html"))
            self.assertIsNone(self.handler._static_file("/secret.txt"))


if __name__ == "__main__":
    unittest.main()
