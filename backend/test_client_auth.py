import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import admin_platform
import client_auth
import core
import db


class ClientAuthTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.uid = core.create_user(self.conn, "qa.client@test.example", "ValidPass123", "customer", "QA Client")
        self.conn.execute("UPDATE users SET mobile='09171234567',verified_at=? WHERE id=?", (core.now(), self.uid))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_email_and_mobile_resolve_to_same_account(self):
        self.assertEqual(client_auth.resolve_identifier(self.conn, "qa.client@test.example"),
                         "qa.client@test.example")
        self.assertEqual(client_auth.resolve_identifier(self.conn, "+63 917 123 4567"),
                         "qa.client@test.example")

    def test_password_reset_is_single_use_and_revokes_sessions(self):
        live = admin_platform.guarded_login(self.conn, "qa.client@test.example", "ValidPass123")
        response = client_auth.request_password_reset(
            self.conn, "qa.client@test.example", reveal_token=True)
        reset = response["development_reset_token"]
        done = client_auth.reset_password(self.conn, reset, "NewValidPass456")
        self.assertIn("successful", done["message"].lower())
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sessions WHERE token=?", (live,)).fetchone())
        with self.assertRaises(core.AuthError):
            client_auth.reset_password(self.conn, reset, "OtherValidPass789")
        self.assertTrue(admin_platform.guarded_login(
            self.conn, "qa.client@test.example", "NewValidPass456"))

    def test_unknown_reset_request_does_not_enumerate_accounts(self):
        known = client_auth.request_password_reset(self.conn, "qa.client@test.example")
        unknown = client_auth.request_password_reset(self.conn, "missing@test.example")
        self.assertEqual(known["message"], unknown["message"])

    def test_account_state_messages_and_reset_required(self):
        self.conn.execute("UPDATE users SET status='SUSPENDED' WHERE id=?", (self.uid,))
        self.conn.commit()
        with self.assertRaisesRegex(core.AuthError, "account suspended"):
            admin_platform.guarded_login(self.conn, "qa.client@test.example", "ValidPass123")
        self.conn.execute("UPDATE users SET status='ACTIVE',password_reset_required=1 WHERE id=?", (self.uid,))
        self.conn.commit()
        with self.assertRaisesRegex(core.AuthError, "password reset required"):
            admin_platform.guarded_login(self.conn, "qa.client@test.example", "ValidPass123")


if __name__ == "__main__":
    unittest.main()
