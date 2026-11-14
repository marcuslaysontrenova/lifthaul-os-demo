"""Local HTTP proof; never hosted release certification."""
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch


class SafetyHttpTests(unittest.TestCase):
    def test_authenticated_read_only_status_and_real_denials(self):
        import server
        import db
        original = server._conn
        conn = db.connect(":memory:")
        server._conn = conn
        try:
            server._seed_users()
            http = server.LiftHaulHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            base = "http://127.0.0.1:" + str(http.server_port)
            def request(path, token=None, payload=None):
                headers = {"Content-Type": "application/json"}
                if token:
                    headers["Authorization"] = "Bearer " + token
                req = urllib.request.Request(base + path, headers=headers,
                    data=None if payload is None else json.dumps(payload).encode())
                try:
                    with urllib.request.urlopen(req, timeout=5) as response:
                        body = json.load(response)
                        return response.status, body.get("data", body)
                except urllib.error.HTTPError as error:
                    return error.code, {}
            admin = request("/login", payload={"email": "admin@rgo.demo", "password": "demo1234"})[1]["token"]
            finance = request("/login", payload={"email": "fin@rgo.demo", "password": "demo1234"})[1]["token"]
            path = "/admin/security/operational-safety"
            with patch.dict(os.environ, {"APP_ENV": "production", "LH_LIVE_OPERATIONS": "off",
                    "LIFTHAUL_RELEASE_SHA": "a" * 40, "LIFTHAUL_DEPLOYMENT_ID": "synthetic-local"}):
                self.assertIn(request(path)[0], (401, 403))
                self.assertEqual(request(path, finance)[0], 403)
                code, evidence = request(path, admin)
                self.assertEqual(code, 200)
                self.assertEqual(evidence["result"], "PASS")
                self.assertTrue(all(value is False for value in evidence["capabilities"].values()))
                self.assertEqual(request(path, admin, {})[0], 404)
                self.assertEqual(evidence["release_sha"], request("/readyz")[1]["release_sha"])
        finally:
            if 'http' in locals():
                http.shutdown()
                http.server_close()
                thread.join(timeout=2)
            server._conn = original
            conn.close()
