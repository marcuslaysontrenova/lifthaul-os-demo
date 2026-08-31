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
        with tempfile.NamedTemporaryFile(suffix="-rgo.sqlite", delete=False) as handle:
            path = handle.name
        try:
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
        finally:
            if os.path.exists(path):
                os.unlink(path)


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
        with open(os.path.join(os.path.dirname(__file__), "server.py"), encoding="utf-8") as source:
            src = source.read().lower()
        for bad in ("password =", "api_key =", "secret = \"", "wise_key ="):
            self.assertNotIn(bad, src)

    def test_production_config_rejects_demo_credentials_and_unsafe_origins(self):
        import server
        env = {
            "APP_ENV": "production", "APP_SECRET": "x" * 48,
            "DATABASE_URL": "postgresql://u:p@db/lifthaul",
            "CORS_ORIGINS": "*", "LH_ADMIN_EMAIL": "admin@rgo.demo",
            "LH_ADMIN_PASSWORD": "demo1234",
        }
        errors = server._production_config_errors(env)
        self.assertTrue(any("CORS" in e for e in errors))
        self.assertTrue(any("EMAIL" in e for e in errors))
        self.assertTrue(any("PASSWORD" in e for e in errors))

    def test_production_config_accepts_strong_postgres_bootstrap(self):
        import server
        env = {
            "APP_ENV": "production", "APP_SECRET": "S" * 48,
            "DATABASE_URL": "postgresql://u:p@db/lifthaul",
            "CORS_ORIGINS": "https://app.lifthaul.example",
            "LH_ADMIN_EMAIL": "platform-admin@lifthaul.example",
            "LH_ADMIN_PASSWORD": "LiftHaul-Admin-2026-Strong",
        }
        self.assertEqual(server._production_config_errors(env), [])

    def test_staging_and_unknown_environments_also_fail_closed(self):
        import server
        for app_env in ("staging", "unexpected"):
            errors = server._production_config_errors({"APP_ENV": app_env})
            self.assertTrue(errors, app_env)
            self.assertTrue(any("missing APP_SECRET" in e for e in errors))

    def test_production_payment_mode_fails_closed_without_provider_gates(self):
        import server
        env = {
            "APP_ENV": "production",
            "APP_SECRET": "A" * 40,
            "DATABASE_URL": "postgresql://db/lifthaul",
            "CORS_ORIGINS": "https://app.lifthaul.example",
            "LH_ADMIN_EMAIL": "owner@lifthaul.example",
            "LH_ADMIN_PASSWORD": "StrongBootstrap123",
            "PAYMENT_GATEWAY_MODE": "production",
        }
        errors = server._production_config_errors(env)
        self.assertIn("missing XENDIT_SECRET_KEY for production payments", errors)
        self.assertIn("PAYMENT_PROVIDER_CERTIFIED must be enabled for production payments", errors)
        self.assertIn("PAYMENT_RECONCILIATION_AUTOMATION must be enabled for production payments", errors)
        self.assertIn("PAYMENT_REGULATORY_ROLE_APPROVED must be enabled for production payments", errors)
        self.assertIn("PAYMENT_SAFEGUARDED_FUNDS_APPROVED must be enabled for production payments", errors)
        self.assertIn("PAYMENT_INDEPENDENT_SECURITY_TEST_APPROVED must be enabled for production payments", errors)
        self.assertIn("PAYMENT_DR_RESTORE_APPROVED must be enabled for production payments", errors)

    def test_plain_http_localhost_is_ci_only(self):
        import server
        env = {
            "APP_ENV": "production", "APP_SECRET": "S" * 48,
            "DATABASE_URL": "postgresql://u:p@db/lifthaul",
            "CORS_ORIGINS": "http://localhost:3000",
            "LH_ADMIN_EMAIL": "platform-admin@lifthaul.example",
            "LH_ADMIN_PASSWORD": "LiftHaul-Admin-2026-Strong",
        }
        self.assertTrue(any("HTTPS" in e for e in server._production_config_errors(env)))
        env["LIFTHAUL_CI"] = "true"
        self.assertEqual(server._production_config_errors(env), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
