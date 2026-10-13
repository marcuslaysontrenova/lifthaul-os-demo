"""Exercise encrypted transport, honest failure and production secret boundaries offline."""
import os
import json
import unittest
import urllib.error
from unittest import mock

import auth_email
import client_auth
import core
import db
import public_provider


SMTP_ENV = {"APP_ENV": "production", "LH_AUTH_SMTP_HOST": "smtp.example.invalid",
            "LH_AUTH_SMTP_USERNAME": "test-sender", "LH_AUTH_SMTP_PASSWORD": "offline-secret",
            "LH_AUTH_EMAIL_FROM": "sender@example.invalid"}


class AuthEmailTests(unittest.TestCase):
    def test_resend_uses_pinned_https_and_requires_provider_message_id(self):
        env = {"LH_AUTH_EMAIL_PROVIDER": "resend", "LH_AUTH_RESEND_API_KEY": "offline-api-key",
               "LH_AUTH_EMAIL_FROM": "sender@example.invalid"}
        for response_body, accepted in ((b'{"id":"test-message"}', True),
                                         (b'{"error":"rejected"}', False), (b'{}', False),
                                         (b'not-json', False)):
            with self.subTest(response=response_body), mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch("urllib.request.build_opener") as opener, mock.patch("smtplib.SMTP") as smtp:
                response = opener.return_value.open.return_value.__enter__.return_value
                response.status = 200
                response.read.return_value = response_body
                self.assertEqual(auth_email.send_code("test@example.invalid", "123456"), accepted)
                request = opener.return_value.open.call_args.args[0]
                self.assertEqual(request.full_url, "https://api.resend.com/emails")
                self.assertEqual(request.get_method(), "POST")
                payload = json.loads(request.data)
                self.assertEqual(payload["to"], ["test@example.invalid"])
                self.assertIn("123456", payload["text"])
                smtp.assert_not_called()

    def test_resend_refusals_rate_limits_and_redirects_fail_honestly(self):
        env = {"LH_AUTH_EMAIL_PROVIDER": "resend", "LH_AUTH_RESEND_API_KEY": "offline-api-key",
               "LH_AUTH_EMAIL_FROM": "sender@example.invalid"}
        for status in (401, 403, 429, 500, 307):
            with self.subTest(status=status), mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch("urllib.request.build_opener") as opener:
                opener.return_value.open.side_effect = urllib.error.HTTPError(
                    "https://api.resend.com/emails", status, "sensitive provider response", {}, None)
                self.assertFalse(auth_email.send_code("test@example.invalid", "123456"))
                handler = opener.call_args.args[0]
                self.assertIsNone(handler.redirect_request(None, None, 307, "", {}, "https://other.invalid"))

    def test_resend_missing_key_or_unknown_provider_never_falls_back(self):
        for env in ({"LH_AUTH_EMAIL_PROVIDER": "resend", "LH_AUTH_EMAIL_FROM": "sender@example.invalid"},
                    {**SMTP_ENV, "LH_AUTH_EMAIL_PROVIDER": "unknown"}):
            with mock.patch.dict(os.environ, env, clear=True), mock.patch("urllib.request.build_opener") as opener, \
                    mock.patch("smtplib.SMTP") as smtp:
                self.assertFalse(auth_email.configured())
                self.assertFalse(auth_email.send_code("test@example.invalid", "123456"))
                opener.assert_not_called()
                smtp.assert_not_called()

    def test_missing_config_and_plaintext_transport_fail_without_network(self):
        for env in ({}, {**SMTP_ENV, "LH_AUTH_SMTP_SECURITY": "none"}):
            with mock.patch.dict(os.environ, env, clear=True), mock.patch("smtplib.SMTP") as smtp:
                self.assertFalse(auth_email.send_code("test@example.invalid", "123456"))
                smtp.assert_not_called()

    def test_starttls_precedes_credentials_and_sends_code_only_in_message(self):
        with mock.patch.dict(os.environ, SMTP_ENV, clear=True), \
                mock.patch("ssl.create_default_context", return_value=mock.sentinel.tls_context), \
                mock.patch("smtplib.SMTP") as smtp:
            session = smtp.return_value.__enter__.return_value
            session.send_message.return_value = {}
            self.assertTrue(auth_email.send_code("test@example.invalid", "123456"))
            self.assertEqual([c[0] for c in session.method_calls],
                             ["ehlo", "starttls", "ehlo", "login", "send_message"])
            self.assertIs(session.starttls.call_args.kwargs["context"], mock.sentinel.tls_context)
            self.assertIn("123456", session.send_message.call_args.args[0].get_content())

    def test_ssl_transport_and_recipient_refusal(self):
        with mock.patch.dict(os.environ, {**SMTP_ENV, "LH_AUTH_SMTP_SECURITY": "ssl"}, clear=True), \
                mock.patch("ssl.create_default_context", return_value=mock.sentinel.tls_context), \
                mock.patch("smtplib.SMTP_SSL") as smtp:
            session = smtp.return_value.__enter__.return_value
            session.send_message.return_value = {"test@example.invalid": (550, b"refused")}
            self.assertFalse(auth_email.send_code("test@example.invalid", "123456"))
            self.assertEqual(smtp.call_args.args[1], 465)
            self.assertIs(smtp.call_args.kwargs["context"], mock.sentinel.tls_context)
            session.starttls.assert_not_called()

    def test_failure_and_header_injection_do_not_claim_delivery(self):
        with mock.patch.dict(os.environ, SMTP_ENV, clear=True), mock.patch("smtplib.SMTP") as smtp:
            smtp.side_effect = RuntimeError("sensitive provider response")
            self.assertFalse(auth_email.send_code("test@example.invalid", "123456"))
            smtp.reset_mock()
            self.assertFalse(auth_email.send_code("test@example.invalid\nBcc: other@example.invalid", "123456"))
            smtp.assert_not_called()

    def test_configuration_flag_cannot_fabricate_otp_delivery(self):
        with mock.patch.dict(os.environ, {"APP_ENV": "production"}, clear=True), \
                mock.patch.object(public_provider, "_provider_active", return_value=True), \
                mock.patch.object(auth_email, "send_code", return_value=False):
            delivered, note = public_provider._deliver_code(None, "email", "test@example.invalid", "123456")
            self.assertFalse(delivered)
            self.assertIn("UNAVAILABLE", note)

    def test_sms_is_not_claimed_sent_by_email_adapter(self):
        with mock.patch.dict(os.environ, SMTP_ENV, clear=True), mock.patch.object(auth_email, "send_code") as send:
            self.assertFalse(public_provider._deliver_code(None, "sms", "09171234567", "123456")[0])
            send.assert_not_called()

    def test_reset_does_not_reveal_tokens_or_enumerate_accounts_in_any_prod_posture(self):
        with mock.patch.dict(os.environ, {"APP_ENV": "test"}, clear=True):
            conn = db.connect(":memory:")
            core.create_user(conn, "test@example.invalid", "ValidPass123", "customer", "Test")
        try:
            for env in ({}, {"APP_ENV": "production"}, {"APP_ENV": "unexpected"}):
                with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(auth_email, "send_reset", return_value=False) as send:
                    self.assertFalse(client_auth.development_token_allowed())
                    known = client_auth.request_password_reset(conn, "test@example.invalid", reveal_token=True)
                    unknown = client_auth.request_password_reset(conn, "unknown@example.invalid", reveal_token=True)
                    self.assertEqual(known, unknown)
                    self.assertNotIn("development_reset_token", known)
                    send.assert_called_once()
                    token = send.call_args.args[1]
                    self.assertIsNone(conn.execute("SELECT 1 FROM client_password_resets WHERE token_hash=?", (token,)).fetchone())
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
