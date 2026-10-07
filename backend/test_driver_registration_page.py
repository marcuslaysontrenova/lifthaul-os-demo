"""Regression guards for the public driver and partner registration experience."""
from pathlib import Path
import json
import unittest


ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "driver-register.html").read_text(encoding="utf-8")
CATALOGUE = json.loads((ROOT / "vehicle-catalogue.json").read_text(encoding="utf-8"))


class DriverRegistrationPage(unittest.TestCase):
    def test_professional_hero_and_human_centered_asset(self):
        self.assertIn("Drive with LiftHaul.", HTML)
        self.assertIn("Expand Your Opportunities.", HTML)
        self.assertIn("lifthaul-driver-partners-hero-v1.webp", HTML)
        self.assertIn("Start Driver Registration", HTML)
        self.assertIn("View Vehicle Requirements", HTML)

    def test_partner_paths_keep_accountability_separate(self):
        for label in (
            "Motorcycle Rider", "Light Vehicle Driver", "Truck Driver",
            "Vehicle Owner", "Fleet Operator", "Heavy Equipment Operator",
        ):
            self.assertIn(label, HTML)
        self.assertIn("Driver approval and vehicle approval are separate", HTML)
        self.assertIn("provider.html?type=CRANE_COMPANY", HTML)
        self.assertIn("provider.html?type=FLEET_OPERATOR", HTML)
        self.assertIn("choosePartner('HEAVY_EQUIPMENT_OPERATOR')", HTML)
        self.assertIn("Own the equipment? Register the provider", HTML)

    def test_complete_catalogue_is_grouped(self):
        groups = {v["group"] for v in CATALOGUE["vehicles"]}
        self.assertEqual(groups, {"Motorcycle Delivery", "Light Vehicles", "Commercial Trucks", "Heavy Hauling & Trailers"})
        for vehicle in (
            "Standard Delivery Motorcycle", "Sedan", "Hatchback", "SUV", "Mini Van",
            "Light Commercial Van", "6-Wheel Closed Van", "12-Wheel Dropside",
            "Wing Van", "Refrigerated Van", "Tanker", "Low-Bed Trailer", "Prime Mover with Trailer",
        ):
            self.assertIn(vehicle, {v["display_name"] for v in CATALOGUE["vehicles"]})
        self.assertIn("fetch('vehicle-catalogue.json'", HTML)
        self.assertIn("catalogCategories=data.ui_categories", HTML)
        self.assertIn("activeGroup='ALL'", HTML)
        self.assertIn("activeGroup='ALL';renderCatalog()", HTML)
        self.assertNotIn("activeGroup=restoredCategory", HTML)
        self.assertIn("All Vehicles", HTML)
        self.assertNotIn("items.slice(0,4)", HTML)
        self.assertNotIn("items:[[", HTML)

    def test_form_is_selection_gated_and_resume_is_session_scoped(self):
        self.assertIn("body:not(.driver-started) .driver-layout{display:none}", HTML)
        self.assertIn("lifthaul_driver_partner_track", HTML)
        self.assertIn("sessionStorage", HTML)
        self.assertIn("choosePartner", HTML)
        self.assertIn("resetPartner", HTML)
        self.assertIn("paths.insertAdjacentElement('afterend',app)", HTML)
        self.assertIn("lifthaul_driver_form_draft", HTML)
        self.assertIn("attachDraftAutosave", HTML)
        self.assertIn("verification codes are never stored", HTML)
        self.assertNotIn("localStorage.setItem('lifthaul_driver", HTML)

    def test_driver_and_vehicle_owner_actions_are_unambiguous(self):
        self.assertIn("Select This Vehicle", HTML)
        self.assertIn("Own this vehicle? Register the unit", HTML)
        self.assertIn("from=driver", HTML)
        self.assertIn("← Back to LiftHaul Home", HTML)

    def test_catalogue_uses_optimized_media(self):
        self.assertEqual(len(CATALOGUE["vehicles"]), 34)
        for vehicle in CATALOGUE["vehicles"]:
            image = ROOT / vehicle["image"]
            self.assertTrue(image.is_file(), vehicle["code"])
            self.assertEqual(image.suffix, ".webp")
            self.assertLess(image.stat().st_size, 500_000)
        self.assertIn("loading=\"lazy\"", HTML)
        self.assertIn("vehicle-wordmark", HTML)
        self.assertIn(">LiftHaul</span>", HTML)

    def test_no_income_or_approval_promise(self):
        self.assertIn("Opportunities—not guaranteed income.", HTML)
        self.assertIn("Application is not approval.", HTML)
        self.assertIn("Passenger operations require a separate", HTML)
        self.assertIn("escrow without an approved legal", HTML)

    def test_expiry_and_accessibility_controls(self):
        self.assertIn("licence_expiry<=new Date", HTML)
        self.assertIn("prefers-reduced-motion:reduce", HTML)
        self.assertIn('aria-live="polite"', HTML)
        self.assertIn("#9be33d", HTML.lower())
        self.assertIn("#d1d5db", HTML.lower())
        self.assertIn("--driver-font:var(--lh-font", HTML)


if __name__ == "__main__":
    unittest.main(verbosity=2)
