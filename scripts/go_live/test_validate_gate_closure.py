import unittest
from scripts.go_live.validate_gate_closure import validate, G5, G6

SHA = "a" * 40

class GateClosureTests(unittest.TestCase):
    def test_missing_evidence_blocks(self):
        out = validate({"identity": {"release_sha": SHA, "deployment_id": "dep"}, "criteria": {}}, SHA)
        self.assertEqual(out["gates"]["gate5"]["result"], "BLOCKED")
        self.assertEqual(out["gates"]["gate6"]["result"], "BLOCKED")
    def test_wrong_release_blocks(self):
        self.assertEqual(validate({"identity":{"release_sha":"b"*40,"deployment_id":"dep"}}, SHA)["result"], "BLOCKED")
    def test_editable_approval_cannot_self_certify(self):
        criteria = {k: {"status":"PASS","evidence_ref":"self-entered:approved","release_sha": SHA} for k in G5 + G6}
        out = validate({"identity":{"release_sha":SHA,"deployment_id":"dep"},"criteria":criteria},SHA)
        self.assertEqual(out["gates"]["gate5"]["criteria"]["independent_human_review"],"BLOCKED")
        self.assertEqual(out["gates"]["gate6"]["criteria"]["independent_recovery_witness_signoff"],"BLOCKED")
    def test_explicit_failure_fails_gate(self):
        out = validate({"identity":{"release_sha":SHA,"deployment_id":"dep"},"criteria":{"rbac_cross_role":{"status":"FAIL"}}},SHA)
        self.assertEqual(out["gates"]["gate5"]["result"],"FAIL")

if __name__ == "__main__":
    unittest.main()
