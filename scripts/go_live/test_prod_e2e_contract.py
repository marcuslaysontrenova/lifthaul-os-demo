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
            return 200, {"status": "ready", "release_sha": SHA, "schema_version": 27}
        if path == "/healthz":
            return 200, {"status": "ok", "release_sha": SHA}
        if path == "/admin/marketplace/public-booking-queue" and not token:
            return 401, {}
        if path == "/login":
            return 200, {"token": "offline-admin-token"}
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

    def run_main(self):
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

    def test_release_identity_mismatch_fails_before_first_write(self):
        def mismatch(method, path, body=None, token=None):
            self.calls.append((method, path))
            self.assertEqual((method, path), ("GET", "/readyz"))
            return 200, {"status": "ready", "release_sha": "0" * 40, "schema_version": 27}

        with mock.patch.dict(self.module["main"].__globals__, {"_req": mismatch}):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(self.module["main"](), 1)
        self.assertEqual(self.calls, [("GET", "/readyz")])


if __name__ == "__main__":
    unittest.main()
