import unittest
from scripts.go_live.validate_persona_acceptance import valid, PERSONAS, REVOCATION_CONTROL

SHA = "a" * 40

def sample():
    return {
        "schema": "lifthaul.production-persona-uat.v1",
        "result": "PASS", "expected_release_sha": SHA, "observed_release_sha": SHA,
        "deployment_id": "a-production-deployment", "schema_version": 27,
        "personas_required": 11, "personas_passed": 11, "sessions_revoked": 10,
        "revocation_control": REVOCATION_CONTROL,
        "external_persona_fixture_isolation": "PASS",
        "live_funds_enabled": False,
        "results": [{"persona": p, "result": "PASS",
                     "control": "login_pending" if p == "driver_applicant" else "login_role_scope_fixture"}
                    for p in PERSONAS],
        "accounts": [{"persona": p, "account_fingerprint": f"{i:012x}"}
                     for i, p in enumerate(PERSONAS)],
    }

class Gate5EvidenceContract(unittest.TestCase):
    def test_complete_exact_sha_passes(self):
        self.assertTrue(valid(sample(), SHA))

    def test_mismatched_release_fails(self):
        self.assertFalse(valid(sample(), "b" * 40))

    def test_old_unhardened_proof_fails(self):
        p = sample()
        p.pop("sessions_revoked")
        self.assertFalse(valid(p, SHA))

    def test_missing_denial_or_persona_fails(self):
        p = sample()
        p["results"] = p["results"][:-1]
        self.assertFalse(valid(p, SHA))

    def test_reusable_session_fails(self):
        p = sample()
        p["sessions_revoked"] = 9
        self.assertFalse(valid(p, SHA))

    def test_duplicate_account_fails(self):
        p = sample()
        p["accounts"][1]["account_fingerprint"] = p["accounts"][0]["account_fingerprint"]
        self.assertFalse(valid(p, SHA))

    def test_live_money_fails(self):
        p = sample()
        p["live_funds_enabled"] = True
        self.assertFalse(valid(p, SHA))

if __name__ == "__main__":
    unittest.main()
