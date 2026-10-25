import argparse
import importlib.util
import json
import pathlib
import unittest

FILE = pathlib.Path(__file__).with_name("interactive_persona_uat.py")
SPEC = importlib.util.spec_from_file_location("interactive_persona_uat", FILE)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
SHA = "a" * 40


class InteractivePersonaUatTests(unittest.TestCase):
    def args(self, execute=True, sha=SHA, ref="GATE5-APPROVED-123"):
        return argparse.Namespace(execute=execute, expected_sha=sha, approval_reference=ref,
                                  output="ignored.json")

    def test_safe_default_has_no_network_or_prompts(self):
        self.assertEqual(MOD.run(self.args(execute=False),
                         confirm=lambda _: self.fail("asked for approval"),
                         secret_prompt=lambda _: self.fail("requested secret"),
                         call=lambda *_: self.fail("network request"),
                         terminal_ok=False), 0)

    def test_noninteractive_refused_before_network(self):
        self.assertEqual(MOD.run(self.args(), terminal_ok=False,
                         call=lambda *_: self.fail("network request")), 2)

    def test_unapproved_request_refused_before_network(self):
        self.assertEqual(MOD.run(self.args(ref=""), terminal_ok=True,
                         call=lambda *_: self.fail("network request")), 2)

    def test_wrong_release_refused_before_credentials(self):
        fake = lambda method, path, *rest: (200, {"status": "ready",
            "release_sha": "b" * 40, "deployment_id": "x", "schema_version": 27})
        self.assertEqual(MOD.run(self.args(), terminal_ok=True, call=fake,
                         secret_prompt=lambda _: self.fail("requested secret")), 2)

    def test_declined_approval_does_not_request_passwords(self):
        fake = lambda method, path, *rest: (200, {"status": "ready",
            "release_sha": SHA, "deployment_id": "x", "schema_version": 27})
        self.assertEqual(MOD.run(self.args(), terminal_ok=True, call=fake,
                         confirm=lambda _: "NO",
                         secret_prompt=lambda _: self.fail("requested secret")), 2)

    def test_approved_existing_accounts_produce_redacted_evidence(self):
        original = MOD.gate5.execute
        original_supplement = MOD.supplement.execute
        saved = {}
        def execute(manifest, sha, call=None):
            saved["num"] = len(manifest)
            return {"result": "PASS", "personas_passed": 11,
                    "sessions_revoked": 10, "live_funds_enabled": False,
                    "external_persona_fixture_isolation": "PASS"}
        def secret(prompt):
            if secret.index < 33:
                persona = MOD.gate5.PERSONAS[secret.index // 3]
                part = secret.index % 3
                value = (persona + "@uat.invalid", "SyntheticLongPassword!", "fixture-" + persona)[part]
            else:
                value = ("/portal/client/bookings/101", "/driver/trips/102", "/admin/carrier-portal/overview/103", "/admin/carrier-portal/overview/104")[secret.index - 33]
            secret.index += 1
            return value
        secret.index = 0
        def sink(content):
            saved["evidence"] = content
        # Keep this test fully isolated: intercept evidence writer.
        original_path = MOD.pathlib.Path
        class StubPath:
            def __init__(self, *a): pass
            @property
            def parent(self): return self
            def mkdir(self, **kwargs): pass
            def write_text(self, text, **kwargs): sink(text)
            def chmod(self, *_): pass
        MOD.gate5.execute = execute
        MOD.supplement.execute = lambda *a, **kw: {"result":"PASS","deployment_id":"x"}
        MOD.pathlib.Path = StubPath
        try:
            fake = lambda method, path: (200, {"status": "ready",
                "release_sha": SHA, "deployment_id": "x", "schema_version": 27})
            self.assertEqual(MOD.run(self.args(), terminal_ok=True, call=fake,
                             confirm=lambda _: MOD.APPROVAL_PHRASE,
                             secret_prompt=secret), 0)
        finally:
            MOD.gate5.execute = original
            MOD.supplement.execute = original_supplement
            MOD.pathlib.Path = original_path
        self.assertEqual(saved["num"], 11)
        self.assertNotIn("SyntheticLongPassword", saved["evidence"])
        self.assertNotIn("@uat.invalid", saved["evidence"])
        self.assertEqual(json.loads(saved["evidence"])["independent_qa_signoff"], "PENDING")

    def test_browser_child_receives_only_persona_credentials_without_fixtures(self):
        originals = (MOD.collect_existing_accounts, MOD.gate5.execute,
                     MOD.supplement.execute, MOD.subprocess.run, MOD.pathlib.Path)
        captured = {}
        class LocalPath:
            def __init__(self, *args): pass
            @property
            def parent(self): return self
            @property
            def parents(self): return [self, self, self]
            def resolve(self): return self
            def __truediv__(self, other): return self
            def mkdir(self, **kwargs): pass
            def write_text(self, *args, **kwargs): pass
            def chmod(self, *_): pass
        def mock_child(command, **kwargs):
            captured["payload"] = json.loads(kwargs["input"])
            class Child:
                stdout = json.dumps({"result": "BLOCKED", "release_sha": SHA,
                                     "deployment_id": "x"})
            return Child()
        MOD.collect_existing_accounts = lambda _: {
            persona: {"email": persona+"@uat.invalid",
                      "password": "test-private-"+persona,
                      "marker": "never-transfer-fixture-"+persona,
                      "object_path": "/private/never-transfer-path"}
            for persona in MOD.gate5.PERSONAS}
        MOD.gate5.execute = lambda *a, **kw: {
            "result": "PASS", "personas_passed": 11, "sessions_revoked": 10,
            "live_funds_enabled": False, "external_persona_fixture_isolation": "PASS"}
        MOD.supplement.execute = lambda *a, **kw: {"result": "PASS", "deployment_id": "x"}
        MOD.subprocess.run = mock_child
        MOD.pathlib.Path = LocalPath
        try:
            args = self.args()
            args.browser = True
            api = lambda method, path: (200, {"status": "ready", "release_sha": SHA,
                        "deployment_id": "x", "schema_version": 27})
            self.assertEqual(MOD.run(args, terminal_ok=True, call=api,
                                     confirm=lambda _: MOD.APPROVAL_PHRASE,
                                     secret_prompt=lambda _: ""), 1)
        finally:
            (MOD.collect_existing_accounts, MOD.gate5.execute,
             MOD.supplement.execute, MOD.subprocess.run,
             MOD.pathlib.Path) = originals
        self.assertEqual(set(captured["payload"]["manifest"]), set(MOD.gate5.PERSONAS))
        self.assertNotIn("never-transfer-fixture", json.dumps(captured["payload"]))
        self.assertNotIn("never-transfer-path", json.dumps(captured["payload"]))
        self.assertEqual(set(captured["payload"]["manifest"]["booker"]), {"email", "password"})

if __name__ == "__main__":
    unittest.main()
