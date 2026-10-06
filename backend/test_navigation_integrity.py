"""QA-manager regression checks for LiftHaul public navigation and handoffs."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
import unittest


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_JOURNEY_PAGES = (
    "index.html", "book.html", "track.html", "provider.html",
    "portal.html", "driver.html", "driver-register.html", "client.html",
    "support.html", "policies.html",
)


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])


def parse(path):
    parser = LinkParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


class NavigationIntegrity(unittest.TestCase):
    def test_static_internal_links_and_fragments_resolve(self):
        parsed = {path.name: parse(path) for path in ROOT.glob("*.html")}
        failures = []
        for source, info in parsed.items():
            for href in info.links:
                if href.startswith(("http://", "https://", "mailto:", "tel:", "javascript:")):
                    continue
                parts = urlsplit(href)
                target_name = parts.path or source
                target = ROOT / target_name
                if not target.exists():
                    failures.append(f"{source}: missing target {href}")
                    continue
                if parts.fragment and target.suffix.lower() == ".html":
                    target_info = parsed.get(target.name) or parse(target)
                    if parts.fragment not in target_info.ids:
                        failures.append(f"{source}: missing fragment {href}")
        self.assertEqual([], failures, "\n".join(failures))

    def test_every_public_journey_has_a_home_route(self):
        for page in PUBLIC_JOURNEY_PAGES:
            with self.subTest(page=page):
                html = (ROOT / page).read_text(encoding="utf-8")
                if page == "index.html":
                    self.assertIn('href="#home"', html)
                else:
                    self.assertIn('href="index.html', html)

    def test_driver_to_provider_handoff_has_an_explicit_return_route(self):
        driver = (ROOT / "driver-register.html").read_text(encoding="utf-8")
        provider = (ROOT / "provider.html").read_text(encoding="utf-8")
        self.assertIn("from=driver", driver)
        self.assertIn('id="providerBackLink"', provider)
        self.assertIn("Back to driver options", provider)
        self.assertIn("driver-register.html#partnerPaths", provider)

    def test_shared_theme_defines_one_font_stack(self):
        css = (ROOT / "theme.css").read_text(encoding="utf-8")
        self.assertIn("--lh-font:", css)
        self.assertIn("--font: var(--lh-font)", css)
        self.assertIn("font-family: var(--lh-font)", css)

    def test_registration_media_stays_within_the_qa_budget(self):
        for asset in (
            "lifthaul-driver-partners-hero-v1.webp",
            "lifthaul-fleet-lineup-v2.webp",
            "lifthaul-cinematic-hero-v1.webp",
            "lifthaul-heavy-lift-v1.webp",
            "lifthaul-fleet-handling-v1.webp",
        ):
            with self.subTest(asset=asset):
                size = (ROOT / "assets" / "visual" / asset).stat().st_size
                self.assertLess(size, 500_000, f"{asset} exceeds the 500 KB registration-media budget")


if __name__ == "__main__":
    unittest.main(verbosity=2)
