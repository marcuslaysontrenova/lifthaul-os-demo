"""Release-gate tests for the versioned public fare matrix and payment boundary."""
import copy
import json
import unittest

import db
import fare_matrix
import public_booking as booking


def payload(**changes):
    value = {
        "contact_name": "Synthetic Fare Test",
        "contact_phone": "09170000000",
        "origin_island": "Luzon",
        "dest_island": "Luzon",
        "vehicle": "sedan",
        "km": 10,
        "excluded_charges_ack": True,
    }
    value.update(changes)
    return value


class FareMatrixRegistryGovernance(unittest.TestCase):
    def test_production_image_packages_the_governed_registry(self):
        dockerfile = (fare_matrix.REGISTRY_PATH.parents[1] / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY data/fare_matrix_registry.json ./data/fare_matrix_registry.json", dockerfile)

    def test_candidate_is_versioned_but_not_fabricated_as_approved(self):
        matrix = fare_matrix.public_estimate_matrix()
        readiness = fare_matrix.approval_readiness(matrix)
        self.assertEqual(matrix["version"], "LH-PH-2026-10-09-BENCHMARK-01")
        self.assertEqual(readiness["status"], "AWAITING_APPROVAL")
        self.assertEqual(readiness["runtime_use"], "INDICATIVE_ESTIMATE_ONLY")
        self.assertFalse(readiness["approved"])
        self.assertIn("commercial_owner", readiness["missing_approval_fields"])
        with self.assertRaises(fare_matrix.FareMatrixError):
            fare_matrix.require_collection_approval(matrix)

    def test_false_approved_status_is_rejected_without_complete_evidence(self):
        registry = fare_matrix.load_registry()
        registry["matrices"][0]["status"] = "APPROVED"
        registry["matrices"][0]["runtime_use"] = "BOOKING_AND_COLLECTION"
        with self.assertRaisesRegex(fare_matrix.FareMatrixError, "missing approval evidence"):
            fare_matrix.validate_registry(registry)

    def test_duplicate_version_is_rejected_to_preserve_history(self):
        registry = fare_matrix.load_registry()
        registry["matrices"].append(copy.deepcopy(registry["matrices"][0]))
        with self.assertRaisesRegex(fare_matrix.FareMatrixError, "versions must be unique"):
            fare_matrix.validate_registry(registry)

    def test_admin_fee_is_exact_ten_percent_with_half_up_cent_rounding(self):
        matrix = fare_matrix.public_estimate_matrix()
        self.assertEqual(fare_matrix.administration_fee(10.05, matrix), 1.01)
        self.assertEqual(fare_matrix.administration_fee(260, matrix), 26.00)


class PublicPricingIntegration(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def test_calculator_booking_and_tracking_share_one_versioned_snapshot(self):
        request = payload(
            cargo_category="BOXES_GENERAL", cargo="Documents", weight_kg=50,
            package_count=1, package_length_cm=50, package_width_cm=40,
            package_height_cm=30, additional_stops=2, waiting_minutes=60,
            helper_count=1,
        )
        preview = booking.preview_estimate(self.conn, request)
        created = booking.submit(self.conn, request)
        tracked = booking.track(self.conn, created["tracking_token"])
        self.assertEqual(preview["estimate"], created["estimate"])
        self.assertEqual(
            preview["quote_breakdown"]["rate_version"],
            created["quote_breakdown"]["rate_version"],
        )
        self.assertEqual(
            created["quote_breakdown"]["components"],
            tracked["quote_breakdown"]["components"],
        )
        self.assertEqual(created["quote_breakdown"]["administration_fee_rate"], 0.10)
        self.assertFalse(created["quote_breakdown"]["fare_collection_enabled"])
        self.assertEqual(
            created["quote_breakdown"]["fare_approval_status"], "AWAITING_APPROVAL")

    def test_confirmed_booking_retains_version_and_components(self):
        created = booking.submit(self.conn, payload())
        row = self.conn.execute(
            "SELECT fare_rate_version,fare_effective_date,fare_components "
            "FROM mkt_bookings WHERE id=?",
            (created["booking_id"],),
        ).fetchone()
        self.assertEqual(row["fare_rate_version"], booking.PUBLIC_FARE_MATRIX_VERSION)
        self.assertEqual(row["fare_effective_date"], booking.PUBLIC_FARE_MATRIX_EFFECTIVE_DATE)
        self.assertEqual(
            json.loads(row["fare_components"]),
            created["quote_breakdown"]["components"],
        )

    def test_tampered_customer_total_cannot_override_server_matrix(self):
        created = booking.submit(self.conn, payload(total=1, estimate=1, amount=1))
        self.assertNotEqual(created["estimate"], 1)


if __name__ == "__main__":
    unittest.main()
