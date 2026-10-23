import json
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
from scripts.ai import cloud_backup_readiness as cloud


class CloudReadinessTests(unittest.TestCase):
    def valid_answer(self):
        return {"mvp_issue": 20, "work_order_issue": 73, "gate5": "OPEN", "gate6": "OPEN",
                "production_changes_allowed": False, "live_funds_allowed": False}

    def stream(self, answer):
        return '\n'.join([json.dumps({"type": "message", "role": "assistant",
                                     "content": json.dumps(answer)}),
                          json.dumps({"type": "result", "status": "success"})])

    def test_shared_alignment_contract(self):
        self.assertTrue(cloud.gemini_answer_ok(self.stream(self.valid_answer())))

    def test_invented_mutation_rejected(self):
        self.assertFalse(cloud.gemini_answer_ok(self.stream({"name": "fictional_mutation"})))

    def test_tool_or_error_events_rejected(self):
        for kind in ("tool_use", "tool_result", "error"):
            self.assertFalse(cloud.gemini_answer_ok(json.dumps({"type": kind}) + '\n' +
                                                  self.stream(self.valid_answer())))

    def test_missing_success_and_malformed_rejected(self):
        for text in ("not json", "", "[]", '{"type":"result","status":"error"}'):
            self.assertFalse(cloud.gemini_answer_ok(text))

    def test_read_only_command(self):
        args = cloud.gemini_command("gemini")
        self.assertEqual(args[args.index("--approval-mode") + 1], "plan")
        self.assertNotIn("yolo", args)

    def test_registry_cannot_claim_activation(self):
        config = json.loads(cloud.CONFIG.read_text())
        with patch.object(cloud.shutil, "which", return_value="installed"):
            result = cloud.readiness(config)
        self.assertFalse(result["automatic_handover_enabled"])
        self.assertFalse(result["gemini"]["authentication_verified"])
        self.assertFalse(result["grok"]["adapter_implemented"])
        self.assertFalse(result["grok"]["paid_calls_allowed"])

    def setup_diagnosis(self, lock=False, binary="gemini", bad_entry=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "lifthaul-ai"
            state.mkdir()
            if lock:
                (state / "runner.lock").write_text("fixture")
            with patch.object(cloud.shared, "check_checkout"), \
                    patch.object(cloud.shared, "git", return_value=str(root)), \
                    patch.object(cloud.shutil, "which", return_value=binary), \
                    patch.object(cloud, "gemini_command", side_effect=ValueError() if bad_entry else None):
                return cloud.diagnose(root)

    def test_diagnosis_distinguishes_lock_from_cli(self):
        with patch.dict(os.environ, {}, clear=True):
            checks = self.setup_diagnosis(lock=True)
        self.assertFalse(checks["NO_ACTIVE_LAUNCHER_LOCK"])
        self.assertTrue(checks["GEMINI_CLI_ON_PATH"])

    def test_diagnosis_distinguishes_missing_cli(self):
        with patch.dict(os.environ, {}, clear=True):
            checks = self.setup_diagnosis(binary=None)
        self.assertFalse(checks["GEMINI_CLI_ON_PATH"])
        self.assertTrue(checks["NO_ACTIVE_LAUNCHER_LOCK"])

    def test_diagnosis_does_not_expose_key(self):
        secret = "PRIVATE_TEST_FIXTURE_NOT_A_REAL_KEY"
        with patch.dict(os.environ, {"GEMINI_API_KEY": secret}, clear=True):
            checks = self.setup_diagnosis()
        self.assertFalse(checks["GOOGLE_SIGN_IN_MODE"])
        self.assertNotIn(secret, json.dumps(checks))

    def test_diagnosis_distinguishes_bad_entrypoint(self):
        with patch.dict(os.environ, {}, clear=True):
            checks = self.setup_diagnosis(bad_entry=True)
        self.assertTrue(checks["GEMINI_CLI_ON_PATH"])
        self.assertFalse(checks["GEMINI_ENTRYPOINT"])


if __name__ == "__main__":
    unittest.main()
