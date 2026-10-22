import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from scripts.go_live import railway_set_bootstrap_identity as sync


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return b'{"data":{"variableCollectionUpsert":true}}'


class TestRailwayBootstrapIdentitySync(unittest.TestCase):
    def test_sends_project_scoped_collection_upsert_without_printing_secrets(self):
        env = {
            "RAILWAY_PROJECT_TOKEN": "project-token-private",
            "RAILWAY_PROJECT_ID": "project-id",
            "RAILWAY_ENVIRONMENT_ID": "environment-id",
            "RAILWAY_SERVICE_ID": "service-id",
            "PROVISION_ADMIN_EMAIL": "gate5-unique@qa.lifthaul.com.ph",
            "PROVISION_ADMIN_PASSWORD": "StrongGate5Password123456789",
        }
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _Response()

        output = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sync.urllib.request, "urlopen", fake_urlopen), \
                redirect_stdout(output):
            self.assertEqual(sync.main(), 0)

        request = captured["request"]
        payload = json.loads(request.data.decode())
        self.assertEqual(request.get_header("Project-access-token"), env["RAILWAY_PROJECT_TOKEN"])
        self.assertEqual(captured["timeout"], 30)
        self.assertEqual(payload["variables"]["input"]["variables"], {
            "LH_ADMIN_EMAIL": env["PROVISION_ADMIN_EMAIL"],
            "LH_ADMIN_PASSWORD": env["PROVISION_ADMIN_PASSWORD"],
        })
        rendered = output.getvalue()
        self.assertNotIn(env["RAILWAY_PROJECT_TOKEN"], rendered)
        self.assertNotIn(env["PROVISION_ADMIN_EMAIL"], rendered)
        self.assertNotIn(env["PROVISION_ADMIN_PASSWORD"], rendered)
        self.assertIn('"secret_values_recorded": false', rendered)

    def test_rejects_non_qa_email_before_network(self):
        env = {
            "RAILWAY_PROJECT_TOKEN": "project-token-private",
            "RAILWAY_PROJECT_ID": "project-id",
            "RAILWAY_ENVIRONMENT_ID": "environment-id",
            "RAILWAY_SERVICE_ID": "service-id",
            "PROVISION_ADMIN_EMAIL": "owner@example.com",
            "PROVISION_ADMIN_PASSWORD": "StrongGate5Password123456789",
        }
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sync.urllib.request, "urlopen") as urlopen, \
                self.assertRaisesRegex(SystemExit, "controlled QA domain"):
            sync.main()
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
