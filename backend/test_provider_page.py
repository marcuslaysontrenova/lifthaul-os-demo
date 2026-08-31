"""Regression guards for the public provider-onboarding experience."""
from pathlib import Path
import unittest


PROVIDER_HTML = (Path(__file__).resolve().parent.parent / "provider.html").read_text(encoding="utf-8")


class TestProviderPage(unittest.TestCase):
    def test_public_page_stays_focused_on_account_creation(self):
        self.assertIn("Put your truck or equipment to work.", PROVIDER_HTML)
        self.assertIn("Only the essentials are required", PROVIDER_HTML)
        self.assertIn("Create account &amp; verify contact", PROVIDER_HTML)
        self.assertNotIn("Classify this unit", PROVIDER_HTML)
        self.assertNotIn("provider (carrier) master record", PROVIDER_HTML)

    def test_provider_choices_are_plain_language_and_backend_supported(self):
        for provider_type in (
            "OWNER_OPERATOR", "FLEET_OPERATOR", "CRANE_COMPANY", "LOGISTICS_PROVIDER"
        ):
            self.assertIn(f'value="{provider_type}"', PROVIDER_HTML)
        self.assertIn("What best describes you?", PROVIDER_HTML)

    def test_driver_deep_link_is_not_misrepresented_as_company_signup(self):
        self.assertIn("Are you registering only as an employed driver?", PROVIDER_HTML)
        self.assertIn("Your verified fleet owner adds drivers", PROVIDER_HTML)
        self.assertIn("if(raw==='DRIVER')", PROVIDER_HTML)

    def test_signup_requires_clear_identity_and_contact_fields(self):
        for field_id in ("legal", "rep", "email", "mobile", "password"):
            self.assertIn(f'id="{field_id}"', PROVIDER_HTML)
        self.assertIn("username:em", PROVIDER_HTML)
        self.assertIn("Account creation is not marketplace approval.", PROVIDER_HTML)

    def test_repeated_click_and_accessibility_controls_remain(self):
        self.assertIn("setSubmitBusy(true)", PROVIDER_HTML)
        self.assertIn("b.disabled=busy", PROVIDER_HTML)
        self.assertIn("prefers-reduced-motion:reduce", PROVIDER_HTML)
        self.assertIn('aria-live="polite"', PROVIDER_HTML)


if __name__ == "__main__":
    unittest.main(verbosity=2)
