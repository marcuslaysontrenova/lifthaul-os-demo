import unittest
from scripts.go_live.validate_browser_evidence import validate, CHECKS
SHA="a"*40
def base():
    return {"release_sha":SHA,"release_sha_after":SHA,
            "deployment_id":"dep","deployment_id_after":"dep"}
class BrowserEvidenceTests(unittest.TestCase):
    def test_missing_blocks(self):
        self.assertEqual(validate(None,SHA,"dep")["result"],"BLOCKED")
    def test_changed_release_blocks(self):
        d=base(); d["deployment_id_after"]="other"
        self.assertEqual(validate(d,SHA,"dep")["reason"],"RELEASE_IDENTITY_UNSTABLE")
    def test_fabricated_check_fields_cannot_pass(self):
        d=base(); d["checks"]={k:{"status":"PASS","artifact_reference":"github-actions-run:123"} for k in CHECKS}
        self.assertEqual(validate(d,SHA,"dep")["result"],"BLOCKED")
    def test_real_failure_is_fail(self):
        d=base();d["checks"]={"direct_route_denied":{"status":"FAIL"}}
        self.assertEqual(validate(d,SHA,"dep")["result"],"FAIL")
if __name__=="__main__":
    unittest.main()
