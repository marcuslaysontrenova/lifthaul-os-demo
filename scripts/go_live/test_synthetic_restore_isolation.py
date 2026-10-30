"""No-network contracts for synthetic PostgreSQL restore target safety."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).with_name("validate_synthetic_restore_isolation.py")
spec = importlib.util.spec_from_file_location("restore_guard", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
src = "postgresql://lifthaul:synthetic@localhost:5432/lifthaul_ci_source"
dst = "postgresql://lifthaul:synthetic@localhost:5432/lifthaul_ci_restore"


class SyntheticRecoveryIsolationTests(unittest.TestCase):
    def check(self, source=src, target=dst, ci=True, opt_in=True):
        return module.assess_isolation(source, target, github_actions=ci, synthetic_opt_in=opt_in)

    def test_distinct_ephemeral_ci_shapes_pass_only_metadata_check(self):
        report = self.check()
        self.assertEqual(report["result"], "PASS_OFFLINE_ISOLATION_METADATA_ONLY")
        self.assertFalse(report["hosted_restore_verified"])
        self.assertFalse(report["mvp_recovery_gate_complete"])

    def test_production_or_remote_hosts_are_blocked(self):
        for candidate in ("postgresql://lifthaul:secret@production.db.example:5432/lifthaul_ci_source",
                          "postgresql://lifthaul:secret@10.0.0.2:5432/lifthaul_ci_source",
                          "postgresql://lifthaul:secret@localhost:5432/production"):
            with self.subTest(candidate=candidate.split("@")[-1]):
                self.assertNotEqual(self.check(source=candidate)["result"], "PASS_OFFLINE_ISOLATION_METADATA_ONLY")

    def test_missing_ci_or_operator_opt_in_blocks(self):
        self.assertEqual(self.check(ci=False)["result"], "BLOCKED_ENVIRONMENT_ISOLATION")
        self.assertEqual(self.check(opt_in=False)["result"], "BLOCKED_ENVIRONMENT_ISOLATION")

    def test_source_target_reversed_or_identical_blocks(self):
        self.assertNotEqual(self.check(src, src)["result"], "PASS_OFFLINE_ISOLATION_METADATA_ONLY")
        self.assertNotEqual(self.check(dst, src)["result"], "PASS_OFFLINE_ISOLATION_METADATA_ONLY")

    def test_invalid_url_is_safe(self):
        self.assertNotEqual(self.check("not-a-url", dst)["result"], "PASS_OFFLINE_ISOLATION_METADATA_ONLY")
        self.assertNotEqual(self.check("postgresql://u@localhost:bogus/x", dst)["result"], "PASS_OFFLINE_ISOLATION_METADATA_ONLY")


if __name__ == "__main__":
    unittest.main()
