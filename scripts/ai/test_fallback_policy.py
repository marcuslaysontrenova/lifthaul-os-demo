import unittest
from fallback_policy import select_next

class PolicyTests(unittest.TestCase):
    def test_codex_to_claude(self):
        self.assertEqual(select_next("codex", "quota_exhausted", {"claude", "gemini"})["provider"], "claude")
    def test_codex_to_gemini_free_only(self):
        self.assertEqual(select_next("codex", "rate_limit_exhausted", {"gemini"})["provider"], "gemini")
    def test_claude_to_gemini(self):
        self.assertEqual(select_next("claude", "quota_exhausted", {"gemini"})["provider"], "gemini")
    def test_does_not_retry_auth_errors(self):
        self.assertEqual(select_next("codex", "auth_error", {"gemini"})["decision"], "stop")
    def test_does_not_retry_test_failures(self):
        self.assertEqual(select_next("codex", "test_failed", {"gemini"})["decision"], "stop")
    def test_does_not_retry_unknown_errors(self):
        self.assertEqual(select_next("codex", "transient_unknown", {"gemini"})["decision"], "stop")
    def test_no_available_fallback(self):
        self.assertEqual(select_next("codex", "quota_exhausted", set())["decision"], "stop")
    def test_grok_fallback_when_claude_and_gemini_are_unavailable(self):
        result = select_next("codex", "quota_exhausted", {"grok", "kimi"})
        self.assertEqual(result["provider"], "grok")
        self.assertEqual(result["mode"], "offline_policy_only")
        self.assertIs(result["executed"], False)

    def test_kimi_fallback_after_grok_limit(self):
        self.assertEqual(select_next("grok", "rate_limit_exhausted", {"kimi"})["provider"], "kimi")

    def test_no_retry_into_grok_or_kimi_on_auth_or_billing_failure(self):
        for failure in ("auth_error", "billing_error", "test_failed", "policy_denied", "ci_failed"):
            with self.subTest(failure=failure):
                self.assertEqual(select_next("gemini", failure, {"grok", "kimi"})["decision"], "stop")

    def test_no_unverified_fallback_activation(self):
        self.assertEqual(select_next("gemini", "quota_exhausted", set())["decision"], "stop")

    def test_no_backtracking(self):
        self.assertEqual(select_next("gemini", "quota_exhausted", {"codex", "claude"})["decision"], "stop")

if __name__ == "__main__":
    unittest.main()
