import unittest
from scripts.go_live.monitor_gate import evaluate

class MonitorGateTests(unittest.TestCase):
    def test_success_requires_both(self):
        self.assertTrue(evaluate({"OWNERSHIP_OUTCOME":"success","SURFACE_OUTCOME":"success"})["monitor_passed"])
    def test_missing_owner_cannot_mask_good_site(self):
        r=evaluate({"OWNERSHIP_OUTCOME":"failure","SURFACE_OUTCOME":"success"})
        self.assertFalse(r["monitor_passed"])
        self.assertTrue(r["surface_passed"])
    def test_bad_site_cannot_be_masked_by_owner(self):
        self.assertFalse(evaluate({"OWNERSHIP_OUTCOME":"success","SURFACE_OUTCOME":"failure"})["monitor_passed"])
    def test_missing_checks_fail_closed(self):
        self.assertFalse(evaluate({})["monitor_passed"])
