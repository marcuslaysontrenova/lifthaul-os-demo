"""RGO OS backend — security hardening tests (§24)."""
import os
import tempfile
import unittest
import core
import security
from security import (validate_password, register_user, LoginLimiter, login_guarded,
                      actor_checked, logout, SecretManager, RateLimitError, backup_db)
from core import ValidationError, AuthError, ForbiddenError, create_user, login, actor_for, create_customer


class Base(unittest.TestCase):
    def setUp(self):
        self.c = core.connect(":memory:")
        create_user(self.c, "admin@r", "Strong1Pass", "admin", "Admin")
        self.admin = actor_for(self.c, login(self.c, "admin@r", "Strong1Pass"))


class TestPasswordPolicy(Base):
    def test_policy(self):
        for weak in ("short", "alllowercase1", "ALLUPPER1", "NoDigitsHere"):
            with self.assertRaises(ValidationError):
                validate_password(weak)
        validate_password("Str0ngEnough")

    def test_register_enforces_policy(self):
        with self.assertRaises(ValidationError):
            register_user(self.c, self.admin, "u@r", "weak", "estimator")
        uid = register_user(self.c, self.admin, "u@r", "GoodPass123", "estimator")
        self.assertTrue(uid)

    def test_register_requires_admin(self):
        create_user(self.c, "e@r", "GoodPass123", "estimator")
        est = actor_for(self.c, login(self.c, "e@r", "GoodPass123"))
        with self.assertRaises(ForbiddenError):
            register_user(self.c, est, "x@r", "GoodPass123", "estimator")


class TestRateLimit(Base):
    def test_blocks_after_failures(self):
        lim = LoginLimiter(max_fails=3, window_seconds=300)
        for _ in range(3):
            with self.assertRaises(AuthError):
                login_guarded(self.c, lim, "admin@r", "wrong")
        with self.assertRaises(RateLimitError):        # 4th blocked by limiter
            login_guarded(self.c, lim, "admin@r", "wrong")

    def test_success_resets(self):
        lim = LoginLimiter(max_fails=3)
        with self.assertRaises(AuthError):
            login_guarded(self.c, lim, "admin@r", "wrong")
        self.assertTrue(login_guarded(self.c, lim, "admin@r", "Strong1Pass"))
        self.assertEqual(lim._recent("admin@r"), [])


class TestSessions(Base):
    def test_ttl_expiry_and_logout(self):
        tok = login(self.c, "admin@r", "Strong1Pass")
        self.assertTrue(actor_checked(self.c, tok))                      # fresh ok
        with self.assertRaises(AuthError):
            actor_checked(self.c, tok, ttl=-1)                          # already older than -1s -> expired
        # expired session was deleted
        self.assertIsNone(self.c.execute("SELECT 1 FROM sessions WHERE token=?", (tok,)).fetchone())
        tok2 = login(self.c, "admin@r", "Strong1Pass")
        logout(self.c, tok2)
        with self.assertRaises(AuthError):
            actor_checked(self.c, tok2)

    def test_checked_actor_retains_tenant_scope(self):
        self.c.execute("UPDATE users SET tenant_id=7 WHERE email='admin@r'")
        self.c.commit()
        tok = login(self.c, "admin@r", "Strong1Pass")
        self.assertEqual(actor_checked(self.c, tok)["tenant_id"], 7)


class TestSecretsBackup(Base):
    def test_secret_from_env_only(self):
        sm = SecretManager()
        with self.assertRaises(ValidationError):
            sm.get("WISE_API_KEY")                  # not configured -> error, never a literal
        os.environ["WISE_API_KEY"] = "test-key-123"
        try:
            self.assertEqual(sm.get("WISE_API_KEY"), "test-key-123")
        finally:
            del os.environ["WISE_API_KEY"]

    def test_no_secret_literals_in_source(self):
        with open(security.__file__, encoding="utf-8") as source:
            src = source.read().lower()
        for bad in ("api_key =", "apikey=", "secret =", "password ="):
            self.assertNotIn(bad, src)

    def test_backup(self):
        create_customer(self.c, self.admin, "Acme")
        with tempfile.NamedTemporaryFile(suffix="-backup.sqlite", delete=False) as handle:
            path = handle.name
        os.unlink(path)
        try:
            path = backup_db(self.c, path)
            import sqlite3
            bk = sqlite3.connect(path)
            n = bk.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
            bk.close()
            self.assertEqual(n, 1)
        finally:
            if os.path.exists(path):
                os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
