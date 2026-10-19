"""Contract tests for production recovery, rollback and monitoring controls."""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_script(name):
    path = ROOT / "scripts" / "go_live" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recovery = load_script("managed_postgres_recovery.py")
rollback = load_script("rollback_probe.py")


class ManagedRecoveryContractTests(unittest.TestCase):
    def base_env(self):
        return {
            "SOURCE_DATABASE_URL": "postgresql://user:secret@source.invalid/lifthaul",
            "RESTORE_DATABASE_URL": "postgresql://user:secret@restore.invalid/lifthaul_restore",
            "RECOVERY_OWNER": "platform-oncall",
            "PROVIDER_BACKUP_REFERENCE": "railway-backup-123",
            "RESTORE_TARGET_REFERENCE": "railway-restore-456",
            "RECOVERY_POINT_AT": "2026-10-16T10:00:00Z",
            "INCIDENT_AT": "2026-10-16T10:05:00Z",
            "RESTORE_STARTED_AT": "2026-10-16T10:06:00Z",
            "RESTORE_FINISHED_AT": "2026-10-16T10:16:00Z",
            "BACKUP_RETENTION_DAYS": "7",
            "PITR_ENABLED": "true",
            "EVIDENCE_PATH": "evidence/test.json",
        }

    def test_metadata_is_fail_closed_and_calculable(self):
        with mock.patch.dict(os.environ, self.base_env(), clear=True):
            data = recovery.validate_metadata()
        self.assertEqual(data["backup_retention_days"], 7)
        self.assertEqual(
            (data["incident_at"] - data["recovery_point_at"]).total_seconds(), 300
        )

    def test_restore_target_must_be_isolated(self):
        env = self.base_env()
        env["RESTORE_DATABASE_URL"] = env["SOURCE_DATABASE_URL"]
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit):
                recovery.validate_metadata()

    def test_unverified_pitr_is_rejected(self):
        env = self.base_env()
        env["PITR_ENABLED"] = "false"
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit):
                recovery.validate_metadata()

    def test_database_credentials_are_not_part_of_identity(self):
        identity = recovery.database_identity(
            "postgresql://sensitive:password@db.example:5432/lifthaul"
        )
        self.assertEqual(identity, ("db.example", 5432, "lifthaul"))
        self.assertNotIn("sensitive", repr(identity))


class OperationsWorkflowContractTests(unittest.TestCase):
    def test_recovery_workflow_requires_isolated_secrets_and_owner(self):
        text = (ROOT / ".github" / "workflows" / "production-recovery-drill.yml").read_text()
        for required in (
            "LH_PROD_RECOVERY_OWNER",
            "LH_PROD_DATABASE_URL",
            "LH_RECOVERY_DATABASE_URL",
            "inputs.pitr_enabled",
            "managed_postgres_recovery.py",
            "retention-days: 90",
        ):
            self.assertIn(required, text)

    def test_health_monitor_fails_closed_and_routes_failure(self):
        text = (ROOT / ".github" / "workflows" / "production-health-monitor.yml").read_text()
        for required in (
            "LH_PROD_OPERATIONS_OWNER",
            "LH_PROD_INCIDENT_OWNER",
            "alert_drill:",
            "Controlled alert-delivery drill",
            '"production_mutated": false',
            "production and live-funds controls were not changed",
            "if: failure()",
            "gh issue create",
            "issues: write",
        ):
            self.assertIn(required, text)

    def test_monitor_probes_are_independent_and_fail_closed(self):
        workflow = (ROOT / ".github" / "workflows" / "production-health-monitor.yml").read_text()
        self.assertIn("id: ownership", workflow)
        self.assertIn("id: surface", workflow)
        self.assertIn("continue-on-error: true", workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn("python scripts/go_live/monitor_gate.py", workflow)
        self.assertIn("steps.alert_drill.outcome == 'failure'", workflow)
        gate = load_script("monitor_gate.py")
        self.assertEqual(gate.evaluate({"OWNERSHIP_OUTCOME": "success", "SURFACE_OUTCOME": "success"}),
                         {"ownership_passed": True, "surface_passed": True, "monitor_passed": True})
        self.assertFalse(gate.evaluate({"OWNERSHIP_OUTCOME": "failure", "SURFACE_OUTCOME": "success"})["monitor_passed"])
        self.assertFalse(gate.evaluate({"OWNERSHIP_OUTCOME": "success", "SURFACE_OUTCOME": "failure"})["monitor_passed"])
        self.assertFalse(gate.evaluate({})["monitor_passed"])

    def test_rollback_workflow_binds_sha_and_deployment_identity(self):
        text = (ROOT / ".github" / "workflows" / "production-rollback-evidence.yml").read_text()
        for required in (
            "expected_sha",
            "expected_deployment_id",
            "rollback_probe.py",
            "retention-days: 90",
        ):
            self.assertIn(required, text)

    def test_runbook_does_not_claim_external_recovery_is_complete(self):
        text = (ROOT / "docs" / "go_live" / "PRODUCTION_RECOVERY_AND_ROLLBACK_RUNBOOK.md").read_text()
        self.assertIn("does **not**", text)
        self.assertIn("If any field is absent, Gate 6 remains `OPEN`", text)
        self.assertIn("EVIDENCE_PHASE=rollback", text)


class RollbackProbeContractTests(unittest.TestCase):
    class Response:
        def __init__(self, payload):
            self.status = 200
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    def test_probe_binds_both_release_identifiers(self):
        sha = "a" * 40
        deployment = "00000000-0000-0000-0000-000000000001"
        responses = [
            self.Response({"status": "ok"}),
            self.Response({
                "status": "ready",
                "release_sha": sha,
                "deployment_id": deployment,
                "schema_version": 27,
            }),
        ]
        env = {
            "BASE_URL": "https://www.lifthaul.com.ph",
            "EXPECTED_SHA": sha,
            "EXPECTED_DEPLOYMENT_ID": deployment,
            "EVIDENCE_PHASE": "rollback",
            "EVIDENCE_PATH": "evidence/rollback.json",
        }
        fake_path = mock.Mock()
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            rollback.urllib.request, "urlopen", side_effect=responses
        ), mock.patch.object(rollback, "Path", return_value=fake_path):
            self.assertEqual(rollback.main(), 0)
        evidence = json.loads(fake_path.write_text.call_args.args[0])
        self.assertEqual(evidence["result"], "PASS")
        self.assertTrue(all(evidence["checks"].values()))

    def test_probe_rejects_an_abbreviated_sha_before_network_access(self):
        env = {
            "BASE_URL": "https://www.lifthaul.com.ph",
            "EXPECTED_SHA": "abc123",
            "EXPECTED_DEPLOYMENT_ID": "deployment-id",
            "EVIDENCE_PHASE": "rollback",
            "EVIDENCE_PATH": "unused.json",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit):
                rollback.main()


if __name__ == "__main__":
    unittest.main()
