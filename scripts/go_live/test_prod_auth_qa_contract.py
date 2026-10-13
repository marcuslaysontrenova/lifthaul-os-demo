"""Offline contract tests for fail-closed, no-live-funds production QA checks.

Never makes network requests or reads production credentials.
"""
import io
import os
from pathlib import Path
import runpy
import unittest
from unittest import mock
from contextlib import redirect_stdout


SCRIPT = Path(__file__).with_name("prod_auth_qa.py")
SHA = "301d002a7482cb51ebffa59b8733ad31fde7b3bc"


class ReadOnlyAuthenticatedQAContract(unittest.TestCase):
    def setUp(self):
        env = {
            "BASE_URL": "https://www.lifthaul.com.ph",
            "EXPECTED_SHA": SHA,
            "LH_TEST_BOOKING_REF": "LH-XDMVQO",
            "LH_ADMIN_EMAIL": "unprivileged-uat@example.invalid",
            "LH_ADMIN_PASSWORD": "offline-mock-password-never-deploy",
        }
        with mock.patch.dict(os.environ, env):
            self.main = runpy.run_path(str(SCRIPT), run_name="test_contract")["main"]
        self.calls = []
        self.expected_role = "public_intake_qa"
        self.permissions = ["marketplace.booking.view", "marketplace.public_intake.view"]
        self.queue_token = "pbk_offline-synthetic-XDMVQO"
        self.privileged_status = 403
        self.queue_requests = [
            {"id": 99, "tracking_token": self.queue_token, "inter_island": False}
        ]

    def fake_call(self, method, path, payload=None, bearer=None):
        self.calls.append((method, path))
        if path == "/readyz":
            return 200, {"status": "ready", "release_sha": SHA, "schema_version": 27}
        if path == "/login":
            self.assertEqual(payload["email"], "unprivileged-uat@example.invalid")
            return 200, {"token": "only-an-offline-test-token"}
        self.assertEqual(bearer, "only-an-offline-test-token")
        if path == "/me/permissions":
            return 200, {
                "role": self.expected_role,
                "permissions": self.permissions,
                "is_super": False,
                "financial": {},
            }
        if path == "/admin/marketplace/public-booking-queue":
            return 200, {
                "source": "PUBLIC_MARKETPLACE",
                "requests": self.queue_requests,
            }
        if path == "/logout":
            return 200, {"ok": True}
        if path in (
            "/admin/marketplace/public-bookings/0/review",
            "/admin/users",
            "/founder/dashboard/summary",
        ):
            return self.privileged_status, {}
        self.fail("Production QA unexpectedly called an unapproved endpoint: " + path)

    def run_check(self, should_pass):
        with mock.patch.dict(self.main.__globals__, {"call": self.fake_call}):
            with redirect_stdout(io.StringIO()):
                if should_pass:
                    self.main()
                else:
                    with self.assertRaises(AssertionError):
                        self.main()
        self.assertNotIn(("POST", "/public/bookings"), self.calls)
        self.assertNotIn(("POST", "/public/bookings/bulk"), self.calls)
        self.assertNotIn(("POST", "/payments/charge"), self.calls)
        self.assertIn(("POST", "/logout"), self.calls)

    def test_valid_read_only_qa_session_passes_without_booking_creation(self):
        self.run_check(should_pass=True)

    def test_nonqa_role_is_rejected(self):
        self.expected_role = "admin"
        self.run_check(should_pass=False)

    def test_financial_or_management_permission_is_rejected(self):
        self.permissions.append("payment.verify")
        self.run_check(should_pass=False)

    def test_qa_must_see_the_exact_preexisting_synthetic_booking(self):
        self.queue_requests = [{"id": 99, "tracking_token": "pbk_another-reference-ABCDEF",
                                "inter_island": False}]
        self.run_check(should_pass=False)

    def test_qa_cannot_gain_booking_review_permission(self):
        self.permissions.append("marketplace.public_intake.manage")
        self.run_check(should_pass=False)

    def test_unlisted_privileges_and_wildcard_are_rejected(self):
        for permission in ("*", "role_admin.view", "new.unreviewed.permission"):
            with self.subTest(permission=permission):
                self.permissions = ["marketplace.booking.view", "marketplace.public_intake.view", permission]
                self.run_check(should_pass=False)

    def test_rejects_authorized_review_or_admin_user_or_founder_access(self):
        self.privileged_status = 200
        self.run_check(should_pass=False)


if __name__ == "__main__":
    unittest.main()
