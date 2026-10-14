"""Offline contract tests for regional booking acceptance; no network or money."""
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import runpy
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("prod_e2e.py")
SHA = "ea1e19430046d3640454ba4baa8f2ec9e1b7516b"


class RegionalBookingAcceptanceContract(unittest.TestCase):
    def setUp(self):
        self.evidence_path = SCRIPT.parent / ".regional-acceptance-test.json"
        env = {
            "BASE_URL": "https://www.lifthaul.com.ph",
            "EXPECTED_SHA": SHA,
            "LH_ADMIN_EMAIL": "regional-qa@example.invalid",
            "LH_ADMIN_PASSWORD": "offline-only-password",
            "EXPECTED_DEPLOYMENT_ID": "offline-deploy-123",
            "LH_PRODUCTION_SYNTHETIC_BOOKINGS_APPROVED": "true",
            "LH_MVP_EVIDENCE_PATH": str(self.evidence_path),
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.module = runpy.run_path(str(SCRIPT), run_name="test_contract")
        self.calls = []
        self.created = {}

    def tearDown(self):
        self.evidence_path.unlink(missing_ok=True)

    def fake_request(self, method, path, body=None, token=None):
        self.calls.append((method, path))
        if path == "/readyz":
            return 200, {"status": "ready", "release_sha": SHA, "schema_version": 27, "deployment_id": "offline-deploy-123"}
        if path == "/healthz":
            return 200, {"status": "ok", "release_sha": SHA}
        if path == "/admin/marketplace/public-booking-queue" and not token:
            return 401, {}
        if path == "/login":
            return 200, {"token": "offline-admin-token"}
        if path == "/me/permissions":
            return 200, {"role": "public_intake_qa", "permissions": ["marketplace.booking.view", "marketplace.public_intake.view"], "is_super": False, "financial": {}}
        if path == "/public/bookings" and method == "POST":
            island = body["origin_island"]
            token_value = self.created.setdefault(island, f"pbk_offline_{island.lower()}_token")
            return 200, {
                "tracking_token": token_value,
                "ref": f"LH-{island[:3]}",
                "status": "REQUEST_RECEIVED",
                "protected_payment": {"live_funds_enabled": False},
                "payment_preference": {"charged": False},
            }
        if path.startswith("/public/bookings/track/"):
            token_value = path.rsplit("/", 1)[-1]
            island = next(name for name, value in self.created.items() if value == token_value)
            return 200, {
                "ref": f"LH-{island[:3]}",
                "customer_status": "Request Received",
                "protected_payment": {"live_funds_enabled": False},
            }
        if path == "/admin/marketplace/public-booking-queue" and token:
            self.assertEqual(token, "offline-admin-token")
            return 200, {"source": "PUBLIC_MARKETPLACE", "requests": [
                {"tracking_token": value} for value in self.created.values()
            ]}
        self.fail(f"unexpected request: {method} {path}")

    def run_main(self, approved=True):
        with mock.patch.dict(os.environ, {
            "LH_PRODUCTION_SYNTHETIC_BOOKINGS_APPROVED": "true" if approved else ""
        }):
            with mock.patch.dict(self.module["main"].__globals__, {"_req": self.fake_request}):
                with mock.patch.object(self.module["time"], "time", return_value=1791943200):
                    with redirect_stdout(io.StringIO()):
                        return self.module["main"]()

    def test_three_regions_exact_queue_and_no_payment_endpoint(self):
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(set(self.created), {"LUZON", "VISAYAS", "MINDANAO"})
        booking_posts = [call for call in self.calls if call == ("POST", "/public/bookings")]
        self.assertEqual(len(booking_posts), 6)
        self.assertFalse(any("payment" in path or "payout" in path for _, path in self.calls))
        evidence = self.evidence_path.read_text(encoding="utf-8")
        self.assertIn('"result": "PASS"', evidence)
        self.assertNotIn("pbk_offline", evidence)

    def test_missing_hosted_approval_blocks_all_bookings(self):
        self.assertEqual(self.run_main(approved=False), 2)
        self.assertNotIn(("POST", "/public/bookings"), self.calls)

    def test_missing_hosted_deployment_pin_blocks_all_bookings(self):
        with mock.patch.dict(self.module["main"].__globals__, {"EXPECTED_DEPLOYMENT_ID": ""}):
            self.assertEqual(self.run_main(), 2)
        self.assertNotIn(("POST", "/public/bookings"), self.calls)

    def test_wrong_role_never_creates_a_booking(self):
        original = self.fake_request
        def wrong_role(method, path, body=None, token=None):
            if path == "/me/permissions":
                self.calls.append((method, path))
                return 200, {"role": "admin", "permissions": ["*"], "is_super": True}
            return original(method, path, body, token)
        self.fake_request = wrong_role
        self.assertEqual(self.run_main(), 1)
        self.assertNotIn(("POST", "/public/bookings"), self.calls)

    def test_failed_queue_access_never_creates_a_booking(self):
        original = self.fake_request
        def denied(method, path, body=None, token=None):
            if path == "/admin/marketplace/public-booking-queue" and token:
                self.calls.append((method, path))
                return 403, {}
            return original(method, path, body, token)
        self.fake_request = denied
        self.assertEqual(self.run_main(), 1)
        self.assertNotIn(("POST", "/public/bookings"), self.calls)

    def test_release_identity_mismatch_fails_before_first_write(self):
        def mismatch(method, path, body=None, token=None):
            self.calls.append((method, path))
            self.assertEqual((method, path), ("GET", "/readyz"))
            return 200, {"status": "ready", "release_sha": "0" * 40, "schema_version": 27}

        with mock.patch.dict(self.module["main"].__globals__, {"_req": mismatch}):
            with mock.patch.dict(os.environ, {"LH_PRODUCTION_SYNTHETIC_BOOKINGS_APPROVED": "true"}):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(self.module["main"](), 1)
        self.assertEqual(self.calls, [("GET", "/readyz")])


if __name__ == "__main__":
    unittest.main()
