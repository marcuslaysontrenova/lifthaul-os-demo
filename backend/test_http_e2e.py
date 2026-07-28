"""RGO OS — threaded HTTP integration test.

Starts the real HTTP server (ThreadingHTTPServer, worker threads) against a fresh
file DB and drives the full lifecycle over real sockets. Permanently guards the
cross-thread SQLite defect that only surfaces under the threaded runtime (unit
tests calling services directly cannot catch it).

Uses its own connection (assigned to server._conn) so it is independent of import
order and does not pollute other tests' state.
"""
import os
import threading
import unittest
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

    @classmethod
    def tearDownClass(cls):
        try:
            server._conn.close()
        except Exception:
            pass
        _cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
