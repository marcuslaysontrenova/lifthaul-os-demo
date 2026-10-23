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

    def windows_fixture(self, directory, target, name="@google/gemini-cli", as_string=False):
        package = Path(directory) / "node_modules/@google/gemini-cli"
        package.mkdir(parents=True)
        (package / "package.json").write_text(json.dumps({"name": name, "bin": target if as_string
                                                       else {"gemini": target}}))
        if isinstance(target, str):
            entry = package / target
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text("// fixture")
        return package

    def test_windows_reads_bundled_entry_from_package_bin(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.windows_fixture(directory, "bundle/gemini.js")
            with patch.object(cloud.shutil, "which", return_value="node.exe"):
                args = cloud.gemini_command(str(Path(directory) / "gemini.cmd"))
            self.assertEqual(args[:2], ["node.exe", str((package / "bundle/gemini.js").resolve())])
            self.assertNotIn("cmd.exe", args)

    def test_windows_reads_standard_string_bin(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.windows_fixture(directory, "dist/index.js", as_string=True)
            with patch.object(cloud.shutil, "which", return_value="node.exe"):
                args = cloud.gemini_command(str(Path(directory) / "gemini.cmd"))
            self.assertEqual(args[1], str((package / "dist/index.js").resolve()))

    def test_windows_rejects_package_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            self.windows_fixture(directory, "../outside.js")
            with patch.object(cloud.shutil, "which", return_value="node.exe"):
                with self.assertRaises(ValueError):
                    cloud.gemini_command(str(Path(directory) / "gemini.cmd"))

    def test_windows_rejects_wrong_package_or_missing_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(cloud.shutil, "which", return_value="node.exe"):
                with self.assertRaises(ValueError):
                    cloud.gemini_command(str(Path(directory) / "gemini.cmd"))
            self.windows_fixture(directory, "dist/index.js", name="unrelated-package")
            with patch.object(cloud.shutil, "which", return_value="node.exe"):
                with self.assertRaises(ValueError):
                    cloud.gemini_command(str(Path(directory) / "gemini.cmd"))

    def test_windows_uses_adjacent_node_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            self.windows_fixture(directory, "dist/index.js")
            node = Path(directory) / "node.exe"
            node.write_text("fixture")
            with patch.object(cloud.shutil, "which", return_value=None):
                args = cloud.gemini_command(str(Path(directory) / "gemini.cmd"))
            self.assertEqual(args[0], str(node.resolve()))

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
