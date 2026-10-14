"""Ensure isolated CI evidence never implies hosted MVP acceptance."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).with_name("isolated_acceptance_evidence.py")
spec = importlib.util.spec_from_file_location("isolated_acceptance_evidence", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SHA = "a" * 40
SUCCESS = dict.fromkeys(module.SUITES, "success")


class IsolatedEvidenceTests(unittest.TestCase):
    def test_all_isolated_suites_pass_without_hosted_go(self):
        result = module.evidence_for(SUCCESS, SHA)
        self.assertEqual(result["result"], "PASS_ISOLATED_SUITES_ONLY")
        self.assertFalse(result["mvp_no_live_funds_go"])
        self.assertFalse(result["production_go"])
        self.assertFalse(result["hosted_production_qa_verified"])
        self.assertFalse(result["hosted_database_recovery_verified"])
        self.assertFalse(result["external_payment_certification"])

    def test_single_skipped_suite_fails_closed(self):
        for suite in module.SUITES:
            with self.subTest(suite=suite):
                r = module.evidence_for({**SUCCESS, suite: "skipped"}, SHA)
                self.assertEqual(r["result"], "FAILED_OR_INCOMPLETE")
                self.assertFalse(r["mvp_no_live_funds_go"])

    def test_cancelled_and_failed_suites_fail_closed(self):
        for outcome in ("cancelled", "failure", "timed_out", "neutral"):
            r = module.evidence_for({**SUCCESS, module.SUITES[0]: outcome}, SHA)
            self.assertEqual(r["result"], "FAILED_OR_INCOMPLETE")

    def test_missing_or_extra_suites_rejected(self):
        with self.assertRaises(ValueError):
            module.evidence_for({"client_identity_and_workspace": "success"}, SHA)
        with self.assertRaises(ValueError):
            module.evidence_for({**SUCCESS, "fake": "success"}, SHA)

    def test_unpinned_candidate_rejected(self):
        for candidate in ("main", "a" * 39, "z" * 40, ""):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    module.evidence_for(SUCCESS, candidate)


if __name__ == "__main__":
    unittest.main()
