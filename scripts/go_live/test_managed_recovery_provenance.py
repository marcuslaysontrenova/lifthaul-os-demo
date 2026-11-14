import os
import unittest
from unittest.mock import patch
from scripts.go_live import managed_postgres_recovery as recovery

BASE = {
    "SOURCE_DATABASE_URL":"postgresql://localhost/source",
    "RESTORE_DATABASE_URL":"postgresql://otherhost/restore",
    "PITR_ENABLED":"true", "BACKUP_RETENTION_DAYS":"3",
    "RECOVERY_OWNER":"approved-operator",
    "PROVIDER_BACKUP_REFERENCE":"backup-reference",
    "RESTORE_TARGET_REFERENCE":"isolated-target",
    "RECOVERY_ISOLATION_APPROVAL_REF":"approved-recovery-change",
    "RECOVERY_TIMESTAMP_PROVENANCE":"provider-exported",
    "RECOVERY_APPROVED_BASELINE_COUNTS_JSON":"{\"customers\":0,\"bookings\":0,\"mkt_bookings\":0,\"audit_logs\":0,\"schema_version\":1}",
    "RECOVERY_APPROVED_SCHEMA_DIGEST":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "RECOVERY_BASELINE_APPROVAL_REF":"BACKUP-BASELINE-APPROVED",
    "RECOVERY_APPROVED_RPO_SECONDS":"600",
    "RECOVERY_APPROVED_RTO_SECONDS":"1800",
    "RECOVERY_OBJECTIVES_APPROVAL_REF":"RTO-RPO-APPROVED",
    "RECOVERY_POINT_AT":"2026-10-25T10:00:00Z",
    "INCIDENT_AT":"2026-10-25T10:01:00Z",
    "RESTORE_STARTED_AT":"2026-10-25T10:02:00Z",
    "RESTORE_FINISHED_AT":"2026-10-25T10:05:00Z",
}

class RecoveryIsolationContractTests(unittest.TestCase):
    def test_separate_host_is_not_proof_without_approval(self):
        env = dict(BASE)
        env.pop("RECOVERY_ISOLATION_APPROVAL_REF")
        with patch.dict(os.environ,env,clear=True), self.assertRaises(SystemExit):
            recovery.validate_metadata()
    def test_identical_database_refused(self):
        env = dict(BASE, RESTORE_DATABASE_URL=BASE["SOURCE_DATABASE_URL"])
        with patch.dict(os.environ,env,clear=True), self.assertRaises(SystemExit):
            recovery.validate_metadata()
    def test_missing_timestamp_provenance_refused(self):
        env = dict(BASE, RECOVERY_TIMESTAMP_PROVENANCE="not-verified")
        with patch.dict(os.environ,env,clear=True), self.assertRaises(SystemExit):
            recovery.validate_metadata()
    def test_missing_recovery_point_baseline_blocks(self):
        env=dict(BASE)
        env.pop("RECOVERY_APPROVED_BASELINE_COUNTS_JSON")
        with patch.dict(os.environ,env,clear=True), self.assertRaises(SystemExit):
            recovery.validate_metadata()
    def test_wrong_baseline_structure_blocks(self):
        env=dict(BASE, RECOVERY_APPROVED_BASELINE_COUNTS_JSON='{"customers":0}')
        with patch.dict(os.environ,env,clear=True), self.assertRaises(SystemExit):
            recovery.validate_metadata()
    def test_invalid_catalog_digest_blocks(self):
        env=dict(BASE, RECOVERY_APPROVED_SCHEMA_DIGEST="not-a-real-digest")
        with patch.dict(os.environ,env,clear=True), self.assertRaises(SystemExit):
            recovery.validate_metadata()
    def test_missing_objective_approval_refused(self):
        env=dict(BASE)
        env.pop("RECOVERY_OBJECTIVES_APPROVAL_REF")
        with patch.dict(os.environ,env,clear=True), self.assertRaises(SystemExit):
            recovery.validate_metadata()
    def test_invalid_rto_target_refused(self):
        env=dict(BASE, RECOVERY_APPROVED_RTO_SECONDS="0")
        with patch.dict(os.environ,env,clear=True), self.assertRaises(SystemExit):
            recovery.validate_metadata()
    def test_invalid_rpo_target_refused(self):
        env=dict(BASE, RECOVERY_APPROVED_RPO_SECONDS="-1")
        with patch.dict(os.environ,env,clear=True), self.assertRaises(SystemExit):
            recovery.validate_metadata()
    def test_valid_metadata_does_not_claim_restore(self):
        with patch.dict(os.environ,BASE,clear=True):
            result = recovery.validate_metadata()
        self.assertEqual(result["timestamp_provenance"],"provider-exported")
        self.assertNotIn("restoration_complete",result)
if __name__ == "__main__":
    unittest.main()
