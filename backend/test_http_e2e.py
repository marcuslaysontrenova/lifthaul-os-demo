"""RGO OS — threaded HTTP integration test.

Starts the real HTTP server (ThreadingHTTPServer, worker threads) against a fresh
file DB and drives the full lifecycle over real sockets. Permanently guards the
cross-thread SQLite defect that only surfaces under the threaded runtime (unit
tests calling services directly cannot catch it).

Uses its own connection (assigned to server._conn) so it is independent of import
order and does not pollute other tests' state.
"""
import os
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import server        # imported normally; we swap its connection below
import validate_e2e
import db
import core

_DBFILE = "rgo_httptest.sqlite"


def _cleanup():
    for f in (_DBFILE, "rgo_e2e_state.json"):
        try:
            os.remove(f)
        except OSError:
            pass


class TestHttpE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _cleanup()
        server._conn = db.connect("sqlite:///" + _DBFILE)     # fresh, isolated DB
        for e, r in [("admin@rgo.demo", "admin"), ("est@rgo.demo", "estimator"),
                     ("appr@rgo.demo", "approver"), ("fin@rgo.demo", "finance")]:
            try:
                core.create_user(server._conn, e, "demo1234", r, r)
            except core.ConflictError:
                pass

    def test_full_lifecycle_over_threaded_http(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            self.assertTrue(validate_e2e.run(f"http://127.0.0.1:{port}"))
        finally:
            srv.shutdown()

    def test_security_headers_and_server_side_logout(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()

        def request(path, method="GET", body=None, token=None):
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = "Bearer " + token
            payload = json.dumps(body).encode() if body is not None else None
            return urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{port}{path}", data=payload, headers=headers, method=method))

        try:
            with request("/login", "POST", {"email": "admin@rgo.demo", "password": "demo1234"}) as res:
                token = json.load(res)["data"]["token"]
                self.assertEqual(res.headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(res.headers["X-Frame-Options"], "DENY")
                self.assertEqual(res.headers["Cache-Control"], "no-store")
            with request("/logout", "POST", {}, token) as res:
                self.assertTrue(json.load(res)["data"]["ok"])
            with self.assertRaises(urllib.error.HTTPError) as denied:
                request("/me/permissions", token=token)
            self.assertEqual(denied.exception.code, 401)
        finally:
            srv.shutdown()

    def test_failed_request_rolls_back_and_does_not_leak_detail(self):
        server._conn.execute("CREATE TABLE IF NOT EXISTS request_rollback_probe(value TEXT)")
        server._conn.commit()

        def fail_after_write(actor, body, params):
            server._conn.execute("INSERT INTO request_rollback_probe(value) VALUES(?)", ("must-rollback",))
            raise RuntimeError("sensitive database diagnostic")

        server.ROUTES[("POST", "/__test/rollback")] = fail_after_write
        srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            payload = json.dumps({"email": "admin@rgo.demo", "password": "demo1234"}).encode()
            with urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:{port}/login", data=payload,
                    headers={"Content-Type": "application/json"}, method="POST")) as res:
                token = json.load(res)["data"]["token"]
            req = urllib.request.Request(f"http://127.0.0.1:{port}/__test/rollback", data=b"{}",
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": "Bearer " + token}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as failed:
                urllib.request.urlopen(req)
            self.assertEqual(failed.exception.code, 500)
            error_body = json.loads(failed.exception.read())
            self.assertNotIn("sensitive", json.dumps(error_body).lower())
            count = server._conn.execute("SELECT COUNT(*) AS n FROM request_rollback_probe").fetchone()["n"]
            self.assertEqual(count, 0)
        finally:
            srv.shutdown()
            server.ROUTES.pop(("POST", "/__test/rollback"), None)

    @classmethod
    def tearDownClass(cls):
        try:
            server._conn.close()
        except Exception:
            pass
        _cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
