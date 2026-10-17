"""Readiness gate must never mistake synthetic CI for client MVP GO."""
import copy
import importlib.util
import json
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location("readiness", HERE / "codex_mvp_readiness.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
LEDGER = json.loads((ROOT / "docs/readiness/MVP_CODEX_CLIENT_CONFIDENCE_LEDGER.json").read_text())


class AcceptanceLedgerTests(unittest.TestCase):
    def test_current_candidate_remains_no_go(self):
        r = module.evaluate(LEDGER)
        self.assertEqual(r["historical_gates"], 3)
        self.assertEqual(r["current_candidate_gates_verified"], 0)
        self.assertEqual(r["pilot_target_tasks_remaining"], 24)
        self.assertEqual(r["high_confidence_tasks_remaining"], 30)
        self.assertFalse(r["mvp_go"])

    def test_task_cannot_be_finished_without_review_evidence(self):
        x = copy.deepcopy(LEDGER)
        x["tasks"][0]["status"] = "HOSTED_PASS"
        with self.assertRaises(ValueError):
            module.evaluate(x)

    def test_gate_cannot_be_finished_with_stale_sha(self):
        x = copy.deepcopy(LEDGER)
        x["gates"][3].update(status="COMPLETE", evidence_urls=["https://example.invalid/test"],
                              verified_candidate_sha="f"*40, verified_deployment_id="id",
                              reviewed_by="reviewer", reviewed_at="2026-10-17")
        with self.assertRaises(ValueError):
            module.evaluate(x)

    def test_historical_gate_cannot_claim_current_candidate(self):
        x = copy.deepcopy(LEDGER)
        x["gates"][0]["verified_candidate_sha"] = x["source"]["candidate_sha"]
        with self.assertRaises(ValueError):
            module.evaluate(x)

    def test_go_with_missing_approval_fails_closed(self):
        x = copy.deepcopy(LEDGER)
        x["release"]["decision"] = "GO"
        with self.assertRaises(ValueError):
            module.evaluate(x)

    def test_fake_numbers_or_statuses_rejected(self):
        x = copy.deepcopy(LEDGER)
        x["tasks"].pop()
        with self.assertRaises(ValueError):
            module.evaluate(x)
        x = copy.deepcopy(LEDGER)
        x["gates"][5]["status"] = "ALMOST_DONE"
        with self.assertRaises(ValueError):
            module.evaluate(x)

    def test_source_sha_must_be_exact(self):
        x = copy.deepcopy(LEDGER)
        x["source"]["candidate_sha"] = "main"
        with self.assertRaises(ValueError):
            module.evaluate(x)


if __name__ == "__main__":
    unittest.main()
