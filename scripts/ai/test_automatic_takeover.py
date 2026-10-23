"""Offline takeover tests. Never use real provider sessions or account settings."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from scripts.ai import automatic_takeover as auto


class TakeoverTests(unittest.TestCase):
    def stream(self, response="ok", steps=None, mode="request-review"):
        return "\n".join(json.dumps(e) for e in [
            {"event": "init", "init": {"permission_mode": mode}},
            *(steps or []), {"event": "result", "result": {"status": "SUCCESS", "response": response}}])

    def fixture(self, directory):
        root = Path(directory)
        (root / "sample.txt").write_text("primary partial work\n", encoding="utf-8")
        (root / "AGENTS.md").write_text("contract", encoding="utf-8")
        return root

    def test_path_traversal_controls_and_secrets_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            for name in ("../outside", "/absolute", "C:/file", "a\\b", "AGENTS.md",
                         "scripts/ai/automatic_takeover.py", ".git/config", "secrets.txt", "x//y"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    auto.selected_path(root, name)
            self.assertEqual(auto.selected_path(root, "sample.txt"), root / "sample.txt")

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            try:
                (root / "link.txt").symlink_to(root / "sample.txt")
            except OSError:
                self.skipTest("Symlink permission unavailable")
            with self.assertRaises(ValueError):
                auto.selected_path(root, "link.txt")

    def test_task_schema_and_duplicate_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            task_path = root / "task.json"
            for value in ({"task": "x", "files": ["sample.txt", "sample.txt"]},
                          {"task": "x", "files": []}, {"task": "x", "files": ["sample.txt"], "exec": "bad"}):
                task_path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    auto.load_task(root, task_path)

    def test_context_never_silently_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            (root / "sample.txt").write_bytes(b"x" * (auto.MAX_FILE_BYTES + 1))
            with self.assertRaises(ValueError):
                auto.file_context(root, ["sample.txt"])

    def test_deny_profile_restores_exact_preferences_on_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            original = b'{ "colorScheme": "terminal", "permissions": {"allow": ["command(git)"]} }\n'
            settings.write_bytes(original)
            with self.assertRaises(RuntimeError):
                with auto.deny_all_tools(settings) as policy:
                    policy()
                    active = json.loads(settings.read_bytes())
                    self.assertEqual(active["permissions"]["deny"], auto.DENY)
                    self.assertEqual(active["permissions"]["allow"], [])
                    self.assertEqual(active["colorScheme"], "terminal")
                    raise RuntimeError("fixture")
            self.assertEqual(settings.read_bytes(), original)
            self.assertFalse((settings.parent / "lifthaul-settings-recovery.json").exists())

    def test_new_settings_removed_after_run(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            with auto.deny_all_tools(settings) as policy:
                policy()
            self.assertFalse(settings.exists())

    def test_concurrent_settings_edit_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            settings.write_text("{}")
            with self.assertRaises(ValueError):
                with auto.deny_all_tools(settings):
                    settings.write_text('{"new_preference":true}')
            self.assertEqual(json.loads(settings.read_text()), {"new_preference": True})
            self.assertTrue((settings.parent / "lifthaul-settings-recovery.json").exists())

    def test_cli_reformat_and_rule_order_do_not_look_like_policy_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            original = b'{"colorScheme":"terminal"}\n'
            settings.write_bytes(original)
            with auto.deny_all_tools(settings) as policy:
                config = json.loads(settings.read_bytes())
                config["permissions"]["deny"].reverse()
                settings.write_text(json.dumps(config, sort_keys=True))
                policy()
            self.assertEqual(json.loads(settings.read_bytes()), json.loads(original))
            self.assertFalse((settings.parent / "lifthaul-settings-recovery.json").exists())

    def test_unrelated_cli_preferences_preserved_on_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            settings.write_text('{"colorScheme":"terminal"}')
            with auto.deny_all_tools(settings) as policy:
                config = json.loads(settings.read_bytes())
                config["new_preference"] = True
                settings.write_text(json.dumps(config))
                policy()
            self.assertEqual(json.loads(settings.read_bytes()),
                             {"colorScheme": "terminal", "new_preference": True})

    def test_allow_rule_added_during_run_blocks_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            settings.write_text("{}")
            with self.assertRaises(auto.Blocked):
                with auto.deny_all_tools(settings) as policy:
                    config = json.loads(settings.read_bytes())
                    config["permissions"]["allow"] = ["command(git)"]
                    settings.write_text(json.dumps(config))
                    policy()
            self.assertEqual(json.loads(settings.read_bytes())["permissions"]["allow"], ["command(git)"])
            self.assertTrue((settings.parent / "lifthaul-settings-recovery.json").exists())

    def test_recover_old_byte_guard_record_after_harmless_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            original = '{"colorScheme":"terminal"}'
            record = {"existed": True, "original": original, "restricted_sha256": "old-byte-hash"}
            recovery = settings.parent / "lifthaul-settings-recovery.json"
            recovery.write_text(json.dumps(record))
            settings.write_text(json.dumps({"colorScheme": "terminal", "new_preference": True,
                "permissions": {"deny": list(reversed(auto.DENY)), "allow": [], "ask": []},
                "toolPermission": "request-review", "agentMode": ""}))
            with patch.object(auto, "active_antigravity", return_value=False):
                self.assertEqual(auto.recover_settings(settings),
                                 "SETTINGS_RECOVERED_UNRELATED_PREFERENCES_PRESERVED")
            self.assertEqual(json.loads(settings.read_bytes()),
                             {"colorScheme": "terminal", "new_preference": True})
            self.assertFalse(recovery.exists())

    def test_pending_recovery_blocks_new_session(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            (settings.parent / "lifthaul-settings-recovery.json").write_text("{}")
            with self.assertRaises(ValueError), auto.deny_all_tools(settings):
                self.fail("Must not run")

    def test_stream_rejects_wrong_permissions_and_multiple_results(self):
        for output in (self.stream(mode="always-proceed"), "[]", self.stream() + '\n{"event":"result"}'):
            with self.assertRaises((ValueError, TypeError)):
                auto.stream_result(output)

    def test_stream_prompt_avoids_windows_command_length_and_shell(self):
        prompt = "TASK " + "x" * 40000
        result = SimpleNamespace(stdout=self.stream(), returncode=0)
        with patch.object(auto.subprocess, "run", return_value=result) as run:
            auto.run_antigravity(Path("."), "agy.exe", prompt, lambda: None)
        args, kwargs = run.call_args
        self.assertNotIn(prompt, args[0])
        self.assertEqual(json.loads(kwargs["input"])["message"]["content"], prompt)
        self.assertFalse(kwargs.get("shell", False))
        self.assertNotIn("--dangerously-skip-permissions", args[0])

    def denial_step(self, error_type="PERMISSION_DENIED", output=None):
        return {"event": "step_update", "step_update": {"step_type": "tool", "state": "DONE",
                "tool_name": "run_command", "tool_info": {"error": {"type": error_type}, "output": output}}}

    def test_negative_probe_requires_actual_denied_attempt(self):
        for steps, expected in (([], False), ([self.denial_step()], True),
                                ([self.denial_step("OTHER")], False),
                                ([self.denial_step(output="canary executed")], False)):
            events, result = auto.stream_result(self.stream(steps=steps))
            with patch.object(auto, "run_antigravity", return_value=(0, events, result)):
                self.assertEqual(auto.safety_probe(Path("."), "agy", lambda: None), expected)

    def test_apply_edits_preserves_partial_work_and_rejects_stale_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            original = auto.file_context(root, ["sample.txt"])
            edit = dict(original[0], content="primary partial work\ncontinued\n")
            self.assertEqual(auto.apply_edits(root, original, {"edits": [edit]}), 1)
            self.assertEqual((root / "sample.txt").read_text(), edit["content"])
            with self.assertRaises(ValueError):
                auto.apply_edits(root, original, {"edits": [edit]})

    def test_unlisted_file_and_model_commands_never_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            original = auto.file_context(root, ["sample.txt"])
            for answer in ({"edits": [dict(original[0], path="AGENTS.md")]},
                           {"edits": [], "command": "deploy"}, {"edits": [original[0], original[0]]}):
                with self.assertRaises(ValueError):
                    auto.apply_edits(root, original, answer)
            self.assertEqual((root / "AGENTS.md").read_text(), "contract")

    def execute_fixture(self, directory, primary, changed_snapshot=False):
        root = self.fixture(directory)
        task = {"task": "Continue partial work", "files": ["sample.txt"]}
        report = {}
        with patch.object(auto.shared, "check_checkout", return_value="work/task"), \
             patch.object(auto.shared, "git", return_value="sha"), \
             patch.object(auto.shared, "run_agent", return_value=primary), \
             patch.object(auto.shared, "shared_snapshot", return_value="changed" if changed_snapshot else "snapshot"), \
             patch.object(auto, "takeover", return_value=0) as fallback:
            code = auto.execute(root, "codex", "agy", task, "snapshot", report)
            return code, report, fallback

    def test_only_confirmed_primary_quota_triggers_fallback(self):
        quota = json.dumps({"type": "error", "code": "usage_limit_reached"})
        for exit_code, output, expected in ((0, "", False), (1, quota, True),
                                           (1, '{"type":"error","code":"auth_error"}', False),
                                           (1, "quota", False)):
            with tempfile.TemporaryDirectory() as directory:
                _, _, fallback = self.execute_fixture(directory, SimpleNamespace(returncode=exit_code, stdout=output))
                self.assertEqual(fallback.called, expected)

    def test_changed_shared_issues_stop_transition(self):
        quota = json.dumps({"type": "error", "code": "quota_exhausted"})
        with tempfile.TemporaryDirectory() as directory:
            code, report, fallback = self.execute_fixture(directory, SimpleNamespace(returncode=1, stdout=quota), True)
            self.assertEqual(code, 2)
            self.assertFalse(fallback.called)

    def test_quota_to_actual_patch_application_with_restored_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            home = root / "home"
            settings = home / ".gemini/antigravity-cli/settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_bytes(b'{"colorScheme":"terminal"}\n')
            expected_settings = settings.read_bytes()
            task = {"task": "Continue primary partial work", "files": ["sample.txt"]}
            quota = SimpleNamespace(returncode=1, stdout='{"type":"error","code":"usage_limit_reached"}')
            seen = []
            def fake_request(_root, _binary, prompt, policy):
                policy()
                seen.append(prompt)
                current = auto.file_context(root, task["files"])
                patch_answer = {"edits": [dict(current[0], content="primary partial work\ncontinued\n")]}
                events, result = auto.stream_result(self.stream(response=json.dumps(patch_answer)))
                return 0, events, result
            with patch.object(auto.shared, "check_checkout", return_value="work/task"), \
                 patch.object(auto.shared, "git", return_value="sha"), \
                 patch.object(auto.shared, "run_agent", return_value=quota), \
                 patch.object(auto.shared, "shared_snapshot", return_value="snapshot"), \
                 patch.object(auto, "active_antigravity", return_value=False), \
                 patch.object(auto.Path, "home", return_value=home), \
                 patch.object(auto, "safety_probe", return_value=True), \
                 patch.object(auto, "aligned", return_value=True), \
                 patch.object(auto, "run_antigravity", side_effect=fake_request):
                report = {}
                code = auto.execute(root, "codex", "agy", task, "snapshot", report)
            self.assertEqual(code, 0)
            self.assertTrue(report["fallback_started"])
            self.assertEqual(report["files_updated"], 1)
            self.assertIn("primary partial work", seen[0])
            self.assertIn("contract", seen[0])
            self.assertEqual((root / "sample.txt").read_text(), "primary partial work\ncontinued\n")
            self.assertEqual(settings.read_bytes(), expected_settings)

    def test_no_patch_after_failed_denial_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            with patch.object(auto, "active_antigravity", return_value=False), \
                 patch.object(auto.Path, "home", return_value=root / "home"), \
                 patch.object(auto, "safety_probe", return_value=False), \
                 patch.object(auto, "aligned") as align:
                report = {}
                code = auto.takeover(root, "agy", {"files": ["sample.txt"]}, "snapshot", "contract", report)
            self.assertEqual(code, 5)
            self.assertFalse(align.called)
            self.assertEqual((root / "sample.txt").read_text(), "primary partial work\n")

    def test_active_external_agent_stops_before_settings_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            with patch.object(auto, "active_antigravity", return_value=True), \
                 patch.object(auto, "deny_all_tools") as guard:
                with self.assertRaises(ValueError):
                    auto.takeover(root, "agy", {}, "snapshot", "contract", {})
                self.assertFalse(guard.called)


if __name__ == "__main__":
    unittest.main()
