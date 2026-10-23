import json
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
