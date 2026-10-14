"""Offline, no-network acceptance contracts for independent preflight gates."""
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

SCRIPT = Path(__file__).with_name("preflight.py")
SHA = "a" * 40
DEPLOYMENT = "0ac737cf-5b1f-4709-8450-ba3e5631182b"
FLAGS = (
    "marketplace.ltfrb_enforcement_enabled",
    "payments.live_protected_funds_enabled",
    "payments.licensed_provider_active",
    "insurance.provider_active",
    "delivery.messaging_provider_active",
)


class PreflightSeparationContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.evidence_path = Path(self.tmp.name) / "preflight.json"
        environment = {
            "BASE_URL": "https://www.lifthaul.com.ph",
            "EXPECTED_SHA": SHA,
            "EXPECTED_DEPLOYMENT_ID": DEPLOYMENT,
            "LH_PREFLIGHT_EVIDENCE_PATH": str(self.evidence_path),
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            self.module = runpy.run_path(str(SCRIPT), run_name="offline_contract")
        self.calls = []
        self.admin_email = "authorized-admin@example.invalid"
        self.config_rows = [{"key": key, "scope": "platform", "scope_ref": "",
                             "value": "false"} for key in FLAGS]

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, method, path, body=None, token=None, origin=None):
        self.calls.append((method, path))
        if path == "/readyz":
            return 200, {"status": "ready", "schema_version": 27,
                         "release_sha": SHA, "deployment_id": DEPLOYMENT}, {}
        if path == "/healthz":
            return 200, {"status": "ok"}, {}
        if path == "/admin/marketplace/public-booking-queue":
            return 401, {}, {}
        if path == "/login":
            return 200, {"token": "offline-token"}, {}
        if path == "/me/permissions":
            return 200, {"role": "admin"}, {}
        if path == "/admin/config":
            return 200, {"config": self.config_rows}, {}
        if path == "/logout":
            return 200, {"ok": True}, {}
        self.fail("Unexpected endpoint or mutating request: " + method + " " + path)

    def run_scope(self, scope, admin=False):
        globals_ = self.module["main"].__globals__
        with mock.patch.dict(globals_, {"_req": self.request,
                                        "ADMIN_EMAIL": self.admin_email if admin else "",
                                        "ADMIN_PW": "offline-only-password" if admin else ""}):
            with redirect_stdout(io.StringIO()):
                code = self.module["main"](["--scope", scope])
        return code, json.loads(self.evidence_path.read_text(encoding="utf-8"))

    def test_public_without_secrets_gathers_evidence_without_login(self):
        code, evidence = self.run_scope("public")
        self.assertEqual(code, 0)
        self.assertEqual(evidence["result"], "PUBLIC_PASS_ADMIN_UNVERIFIED")
        self.assertFalse(evidence["acceptance_complete"])
        self.assertNotIn(("POST", "/login"), self.calls)
        self.assertTrue(all(m == "GET" for m, _ in self.calls))

    def test_all_missing_admin_blocks_but_runs_public_first(self):
        code, evidence = self.run_scope("all")
        self.assertEqual(code, 2)
        self.assertEqual(evidence["result"], "ADMIN_CREDENTIALS_MISSING")
        self.assertTrue(any(c["name"] == "healthz reports ok" for c in evidence["checks"]))
        self.assertFalse(evidence["acceptance_complete"])

    def test_admin_is_distinct_and_requires_exact_flags(self):
        code, evidence = self.run_scope("admin", admin=True)
        self.assertEqual(code, 0)
        self.assertEqual(evidence["result"], "ADMIN_PASS_PUBLIC_UNVERIFIED")
        self.assertIn(("GET", "/admin/config"), self.calls)
        self.assertIn(("POST", "/logout"), self.calls)
        self.assertNotIn(("POST", "/public/bookings"), self.calls)

    def test_missing_flag_cannot_be_treated_as_off(self):
        self.config_rows.pop()
        code, evidence = self.run_scope("all", admin=True)
        self.assertEqual(code, 1)
        self.assertFalse(evidence["acceptance_complete"])

    def test_elevated_qa_cannot_replace_admin(self):
        original = self.request
        def qa(method, path, body=None, token=None, origin=None):
            if path == "/me/permissions":
                self.calls.append((method, path))
                return 200, {"role": "public_intake_qa"}, {}
            return original(method, path, body, token, origin)
        self.request = qa
        code, evidence = self.run_scope("admin", admin=True)
        self.assertEqual(code, 1)
        self.assertNotIn(("GET", "/admin/config"), self.calls)
        self.assertFalse(evidence["acceptance_complete"])

    def test_full_local_preflight_is_not_mvp_approval(self):
        code, evidence = self.run_scope("all", admin=True)
        self.assertEqual(code, 0)
        self.assertEqual(evidence["result"], "PREFLIGHT_COMPLETE")
        self.assertTrue(evidence["acceptance_complete"])


class WorkflowContract(unittest.TestCase):
    def test_identity_and_credentials_stay_separate(self):
        workflow = (SCRIPT.parent.parent.parent / ".github/workflows/production-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("Bind acceptance to the exact live deployment identity", workflow)
        self.assertIn("steps.identity.outputs.deployment_id", workflow)
        self.assertIn("LH_PROD_AUDIT_ADMIN_EMAIL", workflow)
        self.assertIn("LH_PROD_AUDIT_ADMIN_PASSWORD", workflow)
        self.assertIn("LH_PROD_TEST_ADMIN_EMAIL", workflow)
        self.assertIn("LH_PROD_TEST_ADMIN_PASSWORD", workflow)
        self.assertIn("Enforce complete acceptance (public, admin, regional)", workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn("steps.admin_preflight.outcome == 'success'", workflow)
        self.assertIn("if-no-files-found: warn", workflow)


if __name__ == "__main__":
    unittest.main()
