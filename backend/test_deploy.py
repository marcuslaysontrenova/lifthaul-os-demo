"""RGO OS backend — deployment/persistence tests (Phase 3/4/5/6)."""
import os
import tempfile
import unittest
import db
import core


class TestDbFactory(unittest.TestCase):
    def test_sqlite_default_and_schema_version(self):
        conn = db.connect()                      # no DATABASE_URL -> sqlite :memory:
        self.assertEqual(db.current_version(conn), db.SCHEMA_VERSION)
        # full schema present
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("users", "bookings", "quotations", "jobs", "invoices", "audit_logs",
                  "equipment", "documents", "schema_version"):
            self.assertIn(t, tables)

    def test_postgres_selection_is_honest_blocked(self):
        with self.assertRaises(RuntimeError) as ctx:
            db.connect("postgresql://user:pw@host:5432/rgo")
        self.assertIn("PostgreSQL", str(ctx.exception))

    def test_persistence_across_reconnect(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "rgo.sqlite")
            c1 = db.connect("sqlite:///" + path)
            admin = c1.execute  # noqa
            uid = core.create_user(c1, "a@r", "pw", "admin", "A")
            actor = core.actor_for(c1, core.login(c1, "a@r", "pw"))
            cid = core.create_customer(c1, actor, "Persisted Co")
            c1.close()
            # "restart": reopen the same file
            c2 = db.connect("sqlite:///" + path)
            n = c2.execute("SELECT COUNT(*) c FROM customers WHERE name='Persisted Co'").fetchone()["c"]
            self.assertEqual(n, 1)
            self.assertEqual(db.current_version(c2), db.SCHEMA_VERSION)
            c2.close()


class TestCors(unittest.TestCase):
    def test_cors_origin_allowlist(self):
        import server
        server.CORS_ORIGINS = ["https://app.rgo.example"]
        self.assertEqual(server._cors_origin("https://app.rgo.example"), "https://app.rgo.example")
        self.assertIsNone(server._cors_origin("https://evil.example"))
        server.CORS_ORIGINS = ["*"]
        self.assertEqual(server._cors_origin("https://anything"), "*")
        server.CORS_ORIGINS = []
        self.assertIsNone(server._cors_origin("https://app.rgo.example"))

    def test_no_secret_literals_in_server(self):
        src = open(os.path.join(os.path.dirname(__file__), "server.py"), encoding="utf-8").read().lower()
        for bad in ("password =", "api_key =", "secret = \"", "wise_key ="):
            self.assertNotIn(bad, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
