"""Offline regression tests: no provider calls, no production operations."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.ai import ollama_backup as backup


class BackupTests(unittest.TestCase):
    def test_structured_usage_limit(self):
        self.assertTrue(backup.quota_failure(json.dumps(
            {"type": "turn.failed", "error": {"code": "usage_limit_reached"}})))

    def test_exact_cli_message(self):
        self.assertTrue(backup.quota_failure(json.dumps(
            {"type": "error", "message": "You've hit your usage limit. Try later."})))

    def test_tool_output_cannot_trigger_handoff(self):
        self.assertFalse(backup.quota_failure(json.dumps({"type": "item.completed", "item": {
            "type": "command_execution", "aggregated_output": "You've hit your usage limit."}})))

    def test_other_failures_stop(self):
        for code in ("auth_error", "billing_error", "test_failed", "rate_limit_exceeded", "unknown"):
            with self.subTest(code=code):
                self.assertFalse(backup.quota_failure(json.dumps(
                    {"type": "error", "code": code, "message": "You've hit your usage limit."})))

    def test_mixed_failures_stop(self):
        self.assertFalse(backup.quota_failure('\n'.join([
            '{"type":"error","code":"usage_limit_reached"}',
            '{"type":"error","code":"auth_error"}'])))

    def test_unstructured_or_malformed_stop(self):
        for text in ("You've hit your usage limit.", "429", "[]", "null", "{}", "broken json",
                     '{"type":"turn.failed","error":null}'):
            self.assertFalse(backup.quota_failure(text))

    def test_local_command_keeps_sandbox(self):
        args = backup.command("codex", "installed:local")
        for value in ("--oss", "--local-provider", "ollama", "workspace-write", "--json"):
            self.assertIn(value, args)
        self.assertEqual(args[-1], "-")
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)

    def test_windows_npm_entry_does_not_use_command_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "codex.cmd"
            entry = Path(directory) / "node_modules/@openai/codex/bin/codex.js"
            entry.parent.mkdir(parents=True)
            entry.write_text("// fixture")
            with patch.object(backup.shutil, "which", return_value="node.exe"):
                args = backup.command(str(binary), "local:1")
            self.assertEqual(args[:2], ["node.exe", str(entry)])
            self.assertNotIn("cmd.exe", args)

    def test_local_backup_disables_thinking_with_supported_effort(self):
        args = backup.command("codex", "qwen2.5-coder:latest")
        index = args.index("model_reasoning_effort=none")
        self.assertEqual(args[index - 1], "-c")
        self.assertFalse(any("model_supports_reasoning_summaries" in arg for arg in args))

    def test_primary_reasoning_configuration_unchanged(self):
        self.assertFalse(any("model_reasoning_effort" in arg
                             for arg in backup.command("codex")))

    def test_production_branch_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.object(backup, "git", side_effect=[str(root),
                    f"https://github.com/{backup.REPO}.git", "main"]):
                with self.assertRaises(ValueError):
                    backup.check_checkout(root)

    def test_git_forward_slash_root_accepted_on_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "AGENTS.md").write_text("shared instructions")
            with patch.object(backup, "git", side_effect=[root.as_posix(),
                    f"https://github.com/{backup.REPO}.git", "work/test", ""]):
                self.assertEqual(backup.check_checkout(root), "work/test")

    def test_wrong_origin_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.object(backup, "git", side_effect=[str(root), "https://other.invalid/repo"]):
                with self.assertRaises(ValueError):
                    backup.check_checkout(root)

    def test_model_metadata(self):
        with patch.object(backup, "request_json", side_effect=[
                {"models": [{"name": "local:1"}]}, {"capabilities": ["tools"]}]):
            backup.check_model("local:1")

    def test_missing_model_or_no_tools_blocks(self):
        for responses in ([{"models": []}], [{"models": [{"name": "local:1"}]}, {}]):
            with patch.object(backup, "request_json", side_effect=responses):
                with self.assertRaises(ValueError):
                    backup.check_model("local:1")

    def test_remote_alias_blocked(self):
        with patch.object(backup, "request_json", side_effect=[
                {"models": [{"name": "alias:1"}]},
                {"capabilities": ["tools"], "remote_host": "https://ollama.com"}]):
            with self.assertRaises(ValueError):
                backup.check_model("alias:1")

    def test_cloud_and_shell_names_blocked(self):
        for model in ("gpt-oss:120b-cloud", "x;whoami", "x & whoami", "-m", "x\ny"):
            with self.assertRaises(ValueError):
                backup.check_model(model)

    def test_single_owner_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "lock"
            with backup.single_owner(lock):
                with self.assertRaises(ValueError):
                    with backup.single_owner(lock):
                        self.fail("Concurrent owner allowed")
            self.assertFalse(lock.exists())

    def run_case(self, results, snapshot="shared", alignment=True, context=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "AGENTS.md").write_text("Shared instructions: Gates 5/6 OPEN")
            report = {}
            with patch.object(backup, "run_agent", side_effect=results) as runner, \
                    patch.object(backup, "shared_snapshot", return_value=snapshot), \
                    patch.object(backup, "git", return_value="unchanged-work"), \
                    patch.object(backup, "check_checkout"), \
                    patch.object(backup, "check_model"), \
                    patch.object(backup, "alignment_probe", return_value=alignment), \
                    patch.object(backup, "context_fits", return_value=context), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = backup.execute(root, "codex", "local:1", "Close Gate 5", "shared", report)
            return code, report, runner.call_args_list

    def test_primary_success_does_not_call_fallback(self):
        code, report, calls = self.run_case([subprocess.CompletedProcess([], 0, "", "")])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertFalse(report["fallback_started"])

    def test_quota_switch_carries_task_contract_and_checkpoint(self):
        code, report, calls = self.run_case([
            subprocess.CompletedProcess([], 1, '{"type":"error","code":"usage_limit_reached"}', ""),
            subprocess.CompletedProcess([], 0, "", "")])
        self.assertEqual(code, 0)
        self.assertTrue(report["fallback_started"])
        self.assertEqual(calls[1].args[3], "local:1")
        for marker in ("Close Gate 5", "Gates 5/6 OPEN", "unchanged-work", "SHARED ISSUE SNAPSHOT"):
            self.assertIn(marker, calls[1].args[2])
        self.assertIn("UNVERIFIED", report["status"])

    def test_auth_failure_does_not_switch(self):
        code, _, calls = self.run_case([
            subprocess.CompletedProcess([], 1, '{"type":"error","code":"auth_error"}', "")])
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 1)

    def test_changed_shared_instructions_stop_handoff(self):
        code, report, calls = self.run_case([
            subprocess.CompletedProcess([], 1, '{"type":"error","code":"usage_limit_reached"}', "")],
            snapshot="new instructions")
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(report["status"], "STOPPED_SHARED_INSTRUCTIONS_CHANGED")

    def test_local_failure_stops_without_retry_loop(self):
        code, report, calls = self.run_case([
            subprocess.CompletedProcess([], 1, '{"type":"error","code":"usage_limit_reached"}', ""),
            subprocess.CompletedProcess([], 1, "private-output", "private-error")])
        self.assertEqual(code, 3)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("private", json.dumps(report))

    def test_primary_timeout_does_not_switch(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_case([subprocess.TimeoutExpired("codex", 1)])

    def test_alignment_failure_prevents_work_handoff(self):
        code, report, calls = self.run_case([
            subprocess.CompletedProcess([], 1, '{"type":"error","code":"usage_limit_reached"}', "")],
            alignment=False)
        self.assertEqual(code, 5)
        self.assertFalse(report["fallback_started"])
        self.assertEqual(len(calls), 1)

    def test_context_failure_prevents_work_handoff(self):
        code, report, calls = self.run_case([
            subprocess.CompletedProcess([], 1, '{"type":"error","code":"usage_limit_reached"}', "")],
            context=False)
        self.assertEqual(code, 6)
        self.assertFalse(report["fallback_started"])
        self.assertEqual(len(calls), 1)

    def alignment_events(self, answer):
        return json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": json.dumps(answer)}})

    def valid_alignment(self):
        return {"mvp_issue": 20, "work_order_issue": 73, "gate5": "OPEN", "gate6": "OPEN",
                "production_changes_allowed": False, "live_funds_allowed": False}

    def test_alignment_expected_contract(self):
        self.assertTrue(backup.alignment_answer_ok(self.alignment_events(self.valid_alignment())))

    def test_alignment_invented_tool_json_rejected(self):
        self.assertFalse(backup.alignment_answer_ok(self.alignment_events({
            "name": "fictional_mutation", "arguments": {"MVP_LEDGER": "1.0"}})))

    def test_alignment_wrong_number_status_or_permissions_rejected(self):
        for key, value in (("mvp_issue", 1), ("work_order_issue", 5), ("gate5", "PASS"),
                           ("production_changes_allowed", True), ("live_funds_allowed", 0)):
            answer = self.valid_alignment()
            answer[key] = value
            self.assertFalse(backup.alignment_answer_ok(self.alignment_events(answer)))

    def test_alignment_tool_event_rejected_even_with_correct_answer(self):
        tool = json.dumps({"type": "item.completed", "item": {"type": "mcp_tool_call"}})
        self.assertFalse(backup.alignment_answer_ok(tool + '\n' +
                         self.alignment_events(self.valid_alignment())))

    def test_alignment_multiple_or_malformed_answers_rejected(self):
        event = self.alignment_events(self.valid_alignment())
        for output in ("garbage", "", event + '\n' + event, '{"type":"error"}'):
            self.assertFalse(backup.alignment_answer_ok(output))

    def test_alignment_probe_explicitly_supplies_contract_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "AGENTS.md").write_text("FULL SHARED CONTRACT")
            result = subprocess.CompletedProcess([], 0, self.alignment_events(self.valid_alignment()), "")
            with patch.object(backup, "run_agent", return_value=result) as runner:
                self.assertTrue(backup.alignment_probe(root, "codex", "local:1"))
                self.assertIn("FULL SHARED CONTRACT", runner.call_args.args[2])
                self.assertTrue(runner.call_args.kwargs["read_only"])
        self.assertIn("read-only", backup.command("codex", "local:1", read_only=True))

    def test_context_missing_small_or_unknown_fails_closed(self):
        for runtime in ({"models": []}, {"models": [{"name": "local:1", "context_length": 4096}]},
                        {"models": [{"name": "local:1", "context_length": "unknown"}]}):
            with patch.object(backup, "request_json", return_value=runtime):
                self.assertFalse(backup.context_fits("local:1", "prompt"))

    def test_context_large_enough_accepts_without_truncation(self):
        with patch.object(backup, "request_json", return_value={"models": [
                {"name": "local:1", "context_length": 8192}]}):
            self.assertTrue(backup.context_fits("local:1", "small prompt"))
            self.assertFalse(backup.context_fits("local:1", "x" * 8192))


if __name__ == "__main__":
    unittest.main()
