"""Regression coverage for the shared LiftHaul visual identity."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_PAGES = (
    "index.html",
    "book.html",
    "track.html",
    "provider.html",
    "portal.html",
    "driver.html",
    "client.html",
    "console.html",
    "admin-console.html",
    "admin_commercial.html",
    "admin_referral.html",
    "samantha.html",
    "policies.html",
    "support.html",
)


class ThemeConsistency(unittest.TestCase):
    def test_shared_theme_declares_approved_brand_pair(self):
        css = (ROOT / "theme.css").read_text(encoding="utf-8").lower()
        self.assertIn("--lh-lasalle: #006b3c", css)
        self.assertIn("--lh-apple: #70c247", css)
        self.assertIn("--primary: var(--lh-lasalle)", css)
        self.assertIn("--accent: var(--lh-apple)", css)
        self.assertIn("background-color: var(--lh-apple) !important", css)
        self.assertIn("remove legacy pale page bands", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)

    def test_every_customer_and_operations_page_loads_shared_theme(self):
        for relative in PUBLIC_PAGES:
            with self.subTest(page=relative):
                markup = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn('href="theme.css?v=4"', markup)

    def test_bundled_frontend_loads_shared_theme(self):
        markup = (ROOT / "backend" / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="../../theme.css?v=4"', markup)


if __name__ == "__main__":
    unittest.main()
