import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PUBLIC_PAGES = (
    "index.html",
    "book.html",
    "driver-register.html",
    "track.html",
    "policies.html",
    "support.html",
)


class PublicReleaseContentTests(unittest.TestCase):
    def test_public_pages_do_not_advertise_a_demo_or_prototype(self):
        prohibited = (
            "demo prototype",
            "prototype · in-browser",
            "lifthaul (demo)",
            "support@lifthaul.demo",
            "this production-safe demo",
        )
        for name in PUBLIC_PAGES:
            content = (ROOT / name).read_text(encoding="utf-8").lower()
            for phrase in prohibited:
                self.assertNotIn(phrase, content, f"{name} exposes {phrase!r}")

    def test_booking_geographic_data_is_packaged_with_public_ui(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY data/psgc/ ./public/data/psgc/", dockerfile)
        self.assertTrue((ROOT / "data" / "psgc" / "manifest.json").is_file())

    def test_driver_fields_have_bounded_control_heights(self):
        content = (ROOT / "driver-register.html").read_text(encoding="utf-8")
        self.assertIn(".field input,.field select{height:47px}", content)
        self.assertIn(".field select[multiple]{height:auto", content)
        self.assertNotIn("access activates only after sponsorship", content)


if __name__ == "__main__":
    unittest.main()
