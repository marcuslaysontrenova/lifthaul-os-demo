"""Automated QA Manager release gates for the canonical vehicle catalogue."""
from pathlib import Path
import hashlib
import json
import unittest

import marketplace


ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "vehicle-catalogue.json").read_text(encoding="utf-8"))
VEHICLES = DATA["vehicles"]


class VehicleCatalogueQAGate(unittest.TestCase):
    def test_canonical_count_codes_names_and_images_are_unique(self):
        self.assertEqual(len(VEHICLES), 34)
        for field in ("code", "display_name", "image"):
            values = [v[field] for v in VEHICLES]
            self.assertEqual(len(values), len(set(values)), f"duplicate {field}")
        hashes = [hashlib.sha256((ROOT / v["image"]).read_bytes()).hexdigest() for v in VEHICLES]
        self.assertEqual(len(hashes), len(set(hashes)), "unrelated categories reuse an image")

    def test_every_vehicle_has_governed_matching_and_registration_fields(self):
        required = {"code", "display_name", "group", "registration_class", "body_type",
                    "wheel_configuration", "payload_kg", "cargo_dimensions_cm", "image",
                    "required_documents", "eligible_services", "booking_rules",
                    "manual_review_rules", "typical_cargo", "active"}
        for vehicle in VEHICLES:
            self.assertFalse(required - set(vehicle), vehicle["code"])
            self.assertTrue(vehicle["required_documents"], vehicle["code"])
            self.assertTrue(vehicle["eligible_services"], vehicle["code"])
            self.assertTrue(vehicle["booking_rules"], vehicle["code"])
            self.assertTrue((ROOT / vehicle["image"]).is_file(), vehicle["image"])

    def test_mini_van_is_distinct_from_mpv_l300_and_small_closed_van(self):
        by_code = {v["code"]: v for v in VEHICLES}
        mini = by_code["mini_van"]
        for other_code in ("mpv", "l300_van", "small_van"):
            other = by_code[other_code]
            self.assertNotEqual(mini["image"], other["image"])
            self.assertNotEqual(mini["body_type"], other["body_type"])
        self.assertLess(mini["payload_kg"]["max"], by_code["l300_van"]["payload_kg"]["max"])
        self.assertLess(mini["cargo_dimensions_cm"]["length"], by_code["l300_van"]["cargo_dimensions_cm"]["length"])

    def test_every_asset_is_optimized_and_has_a_lifthaul_ui_wordmark(self):
        html = (ROOT / "driver-register.html").read_text(encoding="utf-8")
        for vehicle in VEHICLES:
            asset = ROOT / vehicle["image"]
            self.assertLess(asset.stat().st_size, 500_000, vehicle["code"])
        self.assertIn('<span class="vehicle-wordmark" aria-label="LiftHaul branded vehicle">LiftHaul</span>', html)
        self.assertIn("alt=\"LiftHaul ", html)

    def test_backend_seed_is_derived_from_same_catalogue(self):
        self.assertIn("canonical_vehicle_catalogue", marketplace.__dict__)
        seeded = {row[0] for row in marketplace._VEHICLES}
        self.assertEqual(seeded, {v["code"] for v in VEHICLES})

    def test_selection_accessibility_persistence_and_filters_are_release_gated(self):
        html = (ROOT / "driver-register.html").read_text(encoding="utf-8")
        for marker in ("vehicleSearch", "bodyFilter", "payloadFilter", "All Vehicles",
                       "aria-pressed", "selectedVehicleCode", "lifthaul_driver_vehicle_code",
                       "vehicle_category_code", "loading=\"lazy\"", "role=\"tablist\"",
                       "aria-expanded", "aria-controls"):
            self.assertIn(marker, html)
        self.assertNotIn("Show more vehicles", html)
        self.assertNotIn("items.slice(0,4)", html)
        self.assertIn("grid-template-columns:repeat(4,minmax(0,1fr))", html)
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", html)

    def test_ui_categories_are_canonical_complete_and_non_overlapping(self):
        categories = DATA["ui_categories"]
        self.assertEqual(
            [category["display_name"] for category in categories],
            ["Motorcycle", "Cars and Compact Vehicles", "Light Commercial",
             "Medium Trucks", "Heavy Trucks", "Trailers and Heavy Hauling"],
        )
        categorized = [code for category in categories for code in category["vehicle_codes"]]
        self.assertEqual(len(categorized), len(set(categorized)), "vehicle appears in multiple UI categories")
        self.assertEqual(set(categorized), {vehicle["code"] for vehicle in VEHICLES})

    def test_capacity_notice_and_passenger_boundary_are_explicit(self):
        self.assertIn("Final eligibility and load capacity", DATA["capacity_notice"])
        by_code = {v["code"]: v for v in VEHICLES}
        for code in ("sedan", "hatchback", "suv", "mpv"):
            combined = " ".join(by_code[code]["booking_rules"] + by_code[code]["manual_review_rules"])
            self.assertIn("passenger", combined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
