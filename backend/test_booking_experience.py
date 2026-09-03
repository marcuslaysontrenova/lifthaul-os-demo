"""Focused regression coverage for the public booking experience upgrade."""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
import unittest

import core
import db
import public_booking as pb


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "psgc"


class _Ids(HTMLParser):
    def __init__(self):
        super().__init__(); self.tags = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.tags[attrs["id"]] = tag


class GeographicSnapshot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
        cls.islands = [
            json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
            for name in ("luzon", "visayas", "mindanao")
        ]

    def test_official_q2_2026_totals_and_release(self):
        meta = self.manifest["meta"]
        self.assertEqual(meta["as_of"], "2026-06-30")
        self.assertEqual(meta["totals"], {
            "regions": 18, "provinces": 82, "localities": 1642, "barangays": 42010,
        })

    def test_all_codes_are_unique_and_parented(self):
        codes = set(); totals = {"regions": 0, "provinces": 0, "localities": 0, "barangays": 0}
        for island in self.islands:
            for region in island["regions"]:
                totals["regions"] += 1
                nodes = [region]
                localities = list(region["localities"])
                for province in region["provinces"]:
                    totals["provinces"] += 1; nodes.append(province); localities.extend(province["localities"])
                for locality in localities:
                    totals["localities"] += 1; nodes.append(locality)
                    totals["barangays"] += len(locality["barangays"]); nodes.extend(locality["barangays"])
                for node in nodes:
                    code = node["psgc_code"]
                    self.assertRegex(code, r"^\d{10}$")
                    self.assertNotIn(code, codes)
                    codes.add(code)
        self.assertEqual(totals, self.manifest["meta"]["totals"])

    def test_current_structural_changes_are_present(self):
        by_region = {r["psgc_code"]: r for island in self.islands for r in island["regions"]}
        self.assertEqual(by_region["1800000000"]["name"], "Negros Island Region (NIR)")
        region_ix_provinces = {p["name"] for p in by_region["0900000000"]["provinces"]}
        self.assertIn("Sulu", region_ix_provinces)
        davao_del_norte = next(p for p in by_region["1100000000"]["provinces"] if p["name"] == "Davao del Norte")
        self.assertIn("Sawata", {l["name"] for l in davao_del_norte["localities"]})


class BookingMarkup(unittest.TestCase):
    def test_cascades_map_payment_review_and_accessibility_are_wired(self):
        html = (ROOT / "book.html").read_text(encoding="utf-8")
        parser = _Ids(); parser.feed(html)
        for prefix in ("o", "d"):
            for suffix in ("Island", "Region", "Province", "City", "Barangay"):
                self.assertEqual(parser.tags.get(prefix + suffix), "select")
        self.assertEqual(parser.tags.get("routeMap"), "div")
        self.assertEqual(parser.tags.get("gatewayStatus"), "span")
        self.assertEqual(parser.tags.get("payAmountValue"), "dd")
        self.assertEqual(parser.tags.get("cargoCategory"), "select")
        self.assertEqual(parser.tags.get("cargoWeight"), "input")
        self.assertEqual(parser.tags.get("packageLength"), "input")
        self.assertEqual(parser.tags.get("excludedAck"), "input")
        self.assertEqual(parser.tags.get("vehicleStage"), "section")
        self.assertIn('id="km" class="auto-distance"', html)
        self.assertIn("required readonly", html)
        self.assertIn("Haulift 10% administration fee", html)
        self.assertIn("expenses excluded from the booking total will be shouldered separately", html)
        self.assertIn("Vehicle matching is unavailable", html)  # approved module fails closed
        self.assertNotIn('name="pm"', html)
        self.assertIn("signed provider notification", html)
        self.assertIn("prefers-reduced-motion", (ROOT / "booking-experience.css").read_text(encoding="utf-8"))
        self.assertIn('src="booking-experience.js"', html)
        self.assertIn('src="cargo-booking.js"', html)
        self.assertNotIn("lifthaul_bookings", html)
        self.assertIn("does not create local or fake bookings", html)
        experience = (ROOT / "booking-experience.js").read_text(encoding="utf-8")
        self.assertIn("lifthaul:distancechange", experience)
        self.assertIn("getDistance", experience)
        cargo = (ROOT / "cargo-booking.js").read_text(encoding="utf-8")
        self.assertIn("Total Protected-Payment amount", cargo)
        self.assertIn("vehicle-recommendations", cargo)
        self.assertIn("excluded_charges_ack", cargo)
        self.assertIn("transport=Math.round", cargo)
        self.assertIn("fee=Math.round(transport*.10)", cargo)
        self.assertNotIn("fee=Math.round((transport+tax)", cargo)

    def test_staff_login_is_direct_real_and_permission_filtered(self):
        home = (ROOT / "index.html").read_text(encoding="utf-8")
        console = (ROOT / "console.html").read_text(encoding="utf-8")
        self.assertIn('href="console.html?staff=1"', home)
        self.assertIn('id="loginForm"', console)
        self.assertIn('id="showStaffPassword"', console)
        self.assertIn('id="staffForgotPassword"', console)
        self.assertNotIn('id="demoRole"', console)
        self.assertNotIn("any values sign in", console.lower())
        self.assertIn("Incorrect email, employee ID or password.", console)
        self.assertIn('data-perms="platform.settings.view,role_admin.view"', console)
        self.assertIn("applyNavigationAccess", console)

    def test_client_and_provider_workspaces_have_unambiguous_purpose(self):
        client = (ROOT / "client.html").read_text(encoding="utf-8")
        client_js = (ROOT / "client-workspace.js").read_text(encoding="utf-8")
        portal = (ROOT / "portal.html").read_text(encoding="utf-8")
        self.assertIn("This workspace is for customers booking transport", client)
        self.assertIn('href="portal.html"', client)
        self.assertIn('id="demoBtn"', client)
        self.assertIn("Explore demo workspace", client)
        self.assertIn("READ-ONLY DEMO · SYNTHETIC DATA", client_js)
        self.assertIn("No real transaction or provider verification has occurred", client_js)
        self.assertIn("demoMode=!base", client_js)
        self.assertIn("Book a Service", client_js)
        self.assertNotIn("Create booking", client_js)
        self.assertIn("data-booking-filter", client_js)
        self.assertIn("View protected payments", client_js)
        self.assertIn("This workspace is for truckers and fleet owners", portal)
        self.assertIn('href="client.html"', portal)

    def test_customer_maps_and_payment_checkout_are_functional_not_decorative(self):
        home = (ROOT / "index.html").read_text(encoding="utf-8")
        track = (ROOT / "track.html").read_text(encoding="utf-8")
        map_js = (ROOT / "network-map.js").read_text(encoding="utf-8")
        checkout_js = (ROOT / "payment-checkout.js").read_text(encoding="utf-8")
        self.assertIn('src="network-map.js?v=1"', home)
        self.assertIn("L.tileLayer", map_js)
        self.assertIn("data-area", map_js)
        self.assertNotIn("cloneNode(true)", home)
        self.assertIn('src="payment-checkout.js?v=1"', track)
        self.assertIn("/public/payments/channels", checkout_js)
        self.assertIn("/payments/refresh", checkout_js)
        self.assertNotIn("Operator-confirmed payment", track)


class StructuredPersistence(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def payload(self):
        return {
            "contact_name": "Ana Cruz", "contact_phone": "09171234567",
            "origin_island": "Luzon", "dest_island": "Visayas", "vehicle": "6w", "km": 680,
            "payment": "protected", "payment_method": "gcash",
            "origin_location": {
                "island_group": "Luzon", "region_code": "1300000000",
                "region_name": "National Capital Region (NCR)", "province_code": None,
                "administrative_area_kind": "region_direct", "locality_code": "1380300000",
                "locality_name": "City of Makati", "locality_type": "city",
                "barangay_code": "1380300002", "barangay_name": "Bel-Air",
                "address_detail": "Warehouse A", "full_address": "Warehouse A, Bel-Air, City of Makati, NCR",
                "latitude": 14.5601, "longitude": 121.026,
                "coordinate_source": "user-pin",
            },
            "destination_location": {
                "island_group": "Visayas", "region_code": "0700000000",
                "region_name": "Region VII (Central Visayas)", "province_code": "0702200000",
                "province_name": "Cebu", "administrative_area_kind": "province",
                "locality_code": "0702250000", "locality_name": "City of Talisay",
                "locality_type": "city", "barangay_code": "0702250007",
                "barangay_name": "Lawaan I", "full_address": "Lawaan I, City of Talisay, Cebu",
                "latitude": 10.337, "longitude": 123.93, "coordinate_source": "planning",
            },
        }

    def test_structured_locations_and_payment_preference_persist(self):
        result = pb.submit(self.conn, self.payload())
        row = self.conn.execute(
            "SELECT pickup_address,delivery_address,pickup_lat,pickup_lng,origin_psgc,destination_psgc,"
            "payment_method,payment_provider,payment_channel FROM mkt_bookings WHERE id=?",
            (result["booking_id"],),
        ).fetchone()
        self.assertIn("Bel-Air", row["pickup_address"])
        self.assertAlmostEqual(row["pickup_lat"], 14.5601)
        self.assertEqual(json.loads(row["origin_psgc"])["barangay_code"], "1380300002")
        self.assertEqual(json.loads(row["destination_psgc"])["province_name"], "Cebu")
        self.assertEqual(row["payment_method"], "gcash")
        self.assertEqual(row["payment_channel"], "E-wallet")
        self.assertFalse(result["payment_preference"]["charged"])
        tracked = pb.track(self.conn, result["tracking_token"])
        self.assertEqual(tracked["payment_preference"]["method"], "gcash")

    def test_invalid_psgc_or_cross_island_location_is_rejected(self):
        bad = self.payload(); bad["origin_location"]["region_code"] = "NCR"
        with self.assertRaises(core.ValidationError): pb.submit(self.conn, bad)
        mismatch = self.payload(); mismatch["origin_location"]["island_group"] = "Mindanao"
        with self.assertRaises(core.ValidationError): pb.submit(self.conn, mismatch)


if __name__ == "__main__":
    unittest.main()
