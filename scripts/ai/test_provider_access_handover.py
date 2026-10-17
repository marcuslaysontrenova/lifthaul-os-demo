import unittest
from unittest import mock
import provider_access_check as access
import handover_runner as runner

RECORD = {
    "issue": "#20", "base_sha": "a" * 40, "branch": "ai/handover-20",
    "last_provider": "codex", "failure": "quota_exhausted",
    "completed": ["regression check"], "remaining": ["review implementation"],
    "evidence_urls": ["https://github.com/marcuslaysontrenova/lifthaul-os-demo/issues/20"],
    "no_production_writes": True,
}

class ProviderAccessTests(unittest.TestCase):
    def test_offline_all_missing(self):
        for p in access.PROVIDERS:
            self.assertEqual(access.inventory(p, {})["status"], "BLOCKED_NO_CREDENTIAL")

    def test_secret_presence_is_not_verified_access(self):
        self.assertEqual(access.inventory("gemini", {"GEMINI_API_KEY": "fake"})["status"],
                         "CREDENTIAL_PRESENT_UNVERIFIED")

    def test_key_does_not_allow_unapproved_network(self):
        with mock.patch.object(access.urllib.request, "build_opener", side_effect=AssertionError("network")):
            result = access.probe("claude", {"ANTHROPIC_API_KEY": "fake"})
        self.assertEqual(result["status"], "BLOCKED_OWNER_APPROVAL_OR_KILL_SWITCH")

    def test_auth_and_quota_failures_not_misreported(self):
        self.assertEqual(access._status(401), "BLOCKED_AUTH")
        self.assertEqual(access._status(429), "BLOCKED_RATE_LIMIT")
        self.assertEqual(access._status(402), "BLOCKED_BILLING")

class HandoverTests(unittest.TestCase):
    def test_missing_provider_stays_blocked(self):
        self.assertEqual(runner.evaluate(RECORD)["status"], "NO_VERIFIED_PROVIDER")

    def test_approval_missing_prevents_live_execution(self):
        result = runner.evaluate(RECORD, enabled={"claude"}, runner_verified=True)
        self.assertEqual(result["status"], "BLOCKED_GOVERNANCE")
        self.assertFalse(result["provider_invoked"])

    def test_explicit_approvals_still_only_generate_handover(self):
        result = runner.evaluate(RECORD, enabled={"claude"}, runner_verified=True,
                                 budget_approved=True, owner_approved=True, kill_switch_off=True)
        self.assertEqual(result["status"], "READY_FOR_SEPARATE_AUTHORIZED_RUN")
        self.assertFalse(result["provider_invoked"])

    def test_no_untrusted_external_evidence(self):
        record = {**RECORD, "evidence_urls": ["https://example.com/"]}
        with self.assertRaises(ValueError):
            runner.evaluate(record)

    def test_no_unauthorized_production_write(self):
        record = {**RECORD, "no_production_writes": False}
        with self.assertRaises(ValueError):
            runner.evaluate(record)

    def test_no_bypass_for_nonquota_errors(self):
        with self.assertRaises(ValueError):
            runner.evaluate({**RECORD, "failure": "test_failed"})

if __name__ == "__main__":
    unittest.main()
