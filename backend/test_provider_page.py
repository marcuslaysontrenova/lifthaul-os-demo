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

    def test_heavy_equipment_route_reuses_approved_visual_library(self):
        for asset in (
            "assets/visual/lifthaul-cinematic-hero-v1.webp",
            "assets/visual/lifthaul-heavy-lift-v1.webp",
            "assets/visual/lifthaul-fleet-handling-v1.webp",
        ):
            self.assertIn(asset, PROVIDER_HTML)
        self.assertGreaterEqual(PROVIDER_HTML.count('loading="lazy"'), 3)
        self.assertGreaterEqual(PROVIDER_HTML.count('data-src="assets/visual/'), 3)
        self.assertIn("function hydrateTrackMedia(track)", PROVIDER_HTML)
        self.assertIn("hydrateTrackMedia('light')", PROVIDER_HTML)
        self.assertIn("hydrateTrackMedia('equipment')", PROVIDER_HTML)
        self.assertNotIn(".equipment-scene{position:absolute;inset:0;background:url(", PROVIDER_HTML)
        self.assertIn("Choose capability,", PROVIDER_HTML)
        self.assertIn("not guesswork.", PROVIDER_HTML)
        self.assertIn("equipment-registration", PROVIDER_HTML)

    def test_equipment_choices_flow_into_provider_application(self):
        for capability in ("heavy_haulage", "crane_heavy_lift", "material_handling"):
            self.assertIn(f'value="{capability}"', PROVIDER_HTML)
        self.assertIn("capabilities:capabilities", PROVIDER_HTML)
        self.assertIn("selectedCapabilities()", PROVIDER_HTML)
        self.assertIn("Select at least one heavy-equipment capability", PROVIDER_HTML)

    def test_equipment_visuals_do_not_claim_automatic_approval(self):
        self.assertIn("not to grant automatic approval", PROVIDER_HTML)
        self.assertIn("Declaration only.", PROVIDER_HTML)
        self.assertIn("Paid-job eligibility remains off", PROVIDER_HTML)

    def test_light_vehicle_route_has_clean_selection_first_experience(self):
        self.assertIn("Drive. Deliver.", PROVIDER_HTML)
        self.assertIn("Select the exact unit before registration.", PROVIDER_HTML)
        self.assertIn("One canonical vehicle catalogue", PROVIDER_HTML)
        self.assertNotIn("data-light-card=", PROVIDER_HTML)
        self.assertNotIn("var lightVehicles=", PROVIDER_HTML)
        self.assertIn("light-registration:not(.light-category-selected)", PROVIDER_HTML)
        self.assertIn("Open Full Vehicle Catalogue", PROVIDER_HTML)

    def test_light_vehicle_assets_and_saved_selection_are_wired(self):
        self.assertIn("fetch('vehicle-catalogue.json'", PROVIDER_HTML)
        self.assertIn("_lightVehicleData", PROVIDER_HTML)
        self.assertIn("lifthaul_light_vehicle_draft", PROVIDER_HTML)
        self.assertIn("initial_vehicle_category", PROVIDER_HTML)
        self.assertIn("initial_vehicle_variant", PROVIDER_HTML)

    def test_passenger_service_is_not_implied_by_cargo_registration(self):
        self.assertIn("Cargo registration does not authorize passenger transport.", PROVIDER_HTML)
        self.assertIn("are not activated here", PROVIDER_HTML)
        self.assertIn("Final eligibility and load capacity", PROVIDER_HTML)

    def test_light_flow_explains_multi_unit_and_governed_approval(self):
        self.assertIn("one governed catalogue across booking, registration, pricing, and administration", PROVIDER_HTML)
        self.assertIn("Approval is never automatic", PROVIDER_HTML)
        for step in ("Select vehicle type", "Create or sign in", "Payout information", "Approval &amp; access"):
            self.assertIn(step, PROVIDER_HTML)

    def test_secondary_page_has_safe_exit_and_contextual_navigation(self):
        self.assertIn("← Back to LiftHaul Home", PROVIDER_HTML)
        self.assertIn("← Back to Vehicle Selection", PROVIDER_HTML)
        self.assertNotIn("Book a Service", PROVIDER_HTML)


if __name__ == "__main__":
    unittest.main(verbosity=2)
