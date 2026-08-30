import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

import db
import payment_gateway as pg
import public_booking


ALL_TESTS = {name: True for name in pg.REQUIRED_CERTIFICATION_TESTS}


class FakeXendit:
    def __init__(self):
        self.session_payload = None
        self.session_status = "ACTIVE"
        self.payment_status = None
        self.payment_amount_delta = 0
        self.refunds = []
        self.fail_create = False
        self.fail_refund = False

    def create_session(self, payload):
        if self.fail_create:
            raise RuntimeError("simulated provider create failure")
        self.session_payload = payload
        return {
            "payment_session_id": "ps-661f87c614802d6c402cd82d",
            "reference_id": payload["reference_id"],
            "payment_link_url": "https://checkout-staging.xendit.co/sessions/ps-661f87c614802d6c402cd82d",
            "status": "ACTIVE", "expires_at": payload["expires_at"],
        }

    def get_session(self, session_id):
        return {
            "payment_session_id": session_id,
            "reference_id": self.session_payload["reference_id"],
            "amount": self.session_payload["amount"], "currency": "PHP",
            "status": self.session_status,
            "payment_id": "py-1402feb0-bb79-47ae-9d1e-e69394d3949c" if self.payment_status else None,
            "payment_request_id": "pr-1102feb0-bb79-47ae-9d1e-e69394d3949c" if self.payment_status else None,
        }

    def get_payment(self, payment_id):
        return {
            "payment_id": payment_id,
            "payment_request_id": "pr-1102feb0-bb79-47ae-9d1e-e69394d3949c",
            "reference_id": self.session_payload["reference_id"],
            "request_amount": self.session_payload["amount"] + self.payment_amount_delta,
            "currency": "PHP", "status": self.payment_status,
            "failure_code": "USER_DECLINED_PAYMENT" if self.payment_status == "FAILED" else None,
        }

    def create_refund(self, payload):
        if self.fail_refund:
            raise RuntimeError("simulated provider refund uncertainty")
        self.refunds.append(payload)
        return {"id": "rfd-69e77490-d2cc-4bf3-8319-e064e121db93", "status": "PENDING"}


class PaymentGatewayTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "PAYMENT_GATEWAY_MODE": "sandbox",
            "PAYMENT_PROVIDER_CERTIFIED": "true",
            "PAYMENT_PRODUCTION_PILOT_APPROVED": "false",
            "PAYMENT_ENABLED_CHANNELS": "gcash,maya,bank_transfer,qrph,card,otc",
            "PAYMENT_RETURN_BASE_URL": "https://app.lifthaul.example",
            "XENDIT_SECRET_KEY": "xnd_development_test_only",
            "XENDIT_WEBHOOK_TOKEN": "webhook-secret",
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.conn = db.connect(":memory:")
        self.admin = {"id": 810, "role": "finance_admin", "tenant_id": 0, "perms": {
            "marketplace.booking.manage", "marketplace.payment.override",
            "marketplace.payment.reconcile", "marketplace.payment.verify",
            "marketplace.refund.request",
        }}
        self.verifier = {"id": 811, "role": "finance_approver", "tenant_id": 0, "perms": {
            "marketplace.payment.verify", "marketplace.payment.reconcile",
        }}
        res = public_booking.submit(self.conn, {
            "contact_name": "Gateway Test", "contact_phone": "+639171234567",
            "origin_island": "LUZON", "dest_island": "LUZON", "vehicle": "moto", "km": 20,
            "origin_city": "Makati", "dest_city": "Quezon City",
            "idempotency_key": "booking-gateway-test",
        })
        self.token = res["tracking_token"]
        self.booking_id = res["booking_id"]
        public_booking.review(self.conn, self.admin, self.booking_id, "QUOTE", quote_amount=1250)
        self.client = FakeXendit()

    def certify(self, channel="gcash"):
        return pg.certify_channel(self.conn, self.admin, channel, "SANDBOX", ALL_TESTS)

    def ready(self, channel="gcash"):
        self.certify(channel)
        pg.accept_final_quote(self.conn, self.token, "quote-accept-1")
        return pg.create_payment_session(self.conn, self.token, channel, "pay-create-1", client=self.client)

    def success_webhook(self, created="2026-08-30T02:00:00Z"):
        self.client.payment_status = "SUCCEEDED"
        self.client.session_status = "COMPLETED"
        return {
            "event": "payment.capture", "created": created,
            "data": {
                "payment_session_id": "ps-661f87c614802d6c402cd82d",
                "payment_id": "py-1402feb0-bb79-47ae-9d1e-e69394d3949c",
                "payment_request_id": "pr-1102feb0-bb79-47ae-9d1e-e69394d3949c",
                "reference_id": self.client.session_payload["reference_id"],
            },
        }

    def test_channel_hidden_until_complete_certification(self):
        self.assertFalse(pg.available_channels(self.conn)["available"])
        with self.assertRaises(Exception):
            pg.certify_channel(self.conn, self.admin, "gcash", "SANDBOX", {"successful_payment": True})
        self.certify()
        public = pg.available_channels(self.conn)
        self.assertTrue(public["available"])
        self.assertEqual([c["key"] for c in public["channels"]], ["gcash"])
        self.assertNotIn("secret", str(public).lower())

    def test_admin_readiness_report_is_secret_free_and_fail_closed(self):
        self.certify()
        report = pg.security_readiness(self.conn, self.admin)
        self.assertFalse(report["live_activation_ready"])
        self.assertEqual(report["decision"], "KEEP_LIVE_FUNDS_DISABLED")
        self.assertFalse(report["legal_escrow_claim_authorized"])
        self.assertIn("gcash", report["certified_channels"])
        rendered = str(report).lower()
        self.assertNotIn("xnd_development_test_only", rendered)
        self.assertNotIn("webhook-secret", rendered)

        outsider = {"id": 812, "role": "customer", "tenant_id": 1, "perms": set()}
        with self.assertRaises(Exception):
            pg.security_readiness(self.conn, outsider)

    def test_session_creation_is_provider_backed_and_idempotent(self):
        created = self.ready()
        self.assertEqual(created["status"], "PENDING")
        self.assertTrue(created["checkout_url"].startswith("https://checkout-staging.xendit.co/"))
        self.assertEqual(self.client.session_payload["allowed_payment_channels"], ["GCASH"])
        replay = pg.create_payment_session(self.conn, self.token, "gcash", "pay-create-1", client=self.client)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["transaction_id"], created["transaction_id"])
        with self.assertRaises(Exception):
            pg.create_payment_session(self.conn, self.token, "maya", "pay-create-1", client=self.client)

    def test_repeated_click_with_new_key_reuses_active_transaction(self):
        created = self.ready()
        repeated = pg.create_payment_session(
            self.conn, self.token, "gcash", "pay-create-new-browser-key", client=self.client,
        )
        self.assertTrue(repeated["idempotent"])
        self.assertTrue(repeated["deduplicated_active_transaction"])
        self.assertEqual(repeated["transaction_id"], created["transaction_id"])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM gateway_payment_transactions WHERE booking_id=?",
                (self.booking_id,),
            ).fetchone()[0],
            1,
        )

    def test_parallel_channel_is_blocked_while_transaction_active(self):
        self.certify("gcash")
        self.certify("maya")
        pg.accept_final_quote(self.conn, self.token, "quote-accept-parallel")
        pg.create_payment_session(
            self.conn, self.token, "gcash", "pay-create-first-channel", client=self.client,
        )
        with self.assertRaises(Exception):
            pg.create_payment_session(
                self.conn, self.token, "maya", "pay-create-second-channel", client=self.client,
            )

    def test_invalid_webhook_never_changes_payment(self):
        created = self.ready()
        with self.assertRaises(Exception):
            pg.process_webhook(self.conn, "wrong", self.success_webhook(), client=self.client)
        self.assertEqual(pg._row(self.conn, created["transaction_id"])["status"], "PENDING")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM gateway_webhook_events").fetchone()[0], 0)

    def test_verified_webhook_plus_api_check_marks_paid_and_deduplicates(self):
        created = self.ready()
        payload = self.success_webhook()
        first = pg.process_webhook(self.conn, "webhook-secret", payload, client=self.client)
        self.assertEqual(first["payment"]["status"], "PAID")
        self.assertEqual(first["payment"]["verification_method"], "PROVIDER_WEBHOOK_PLUS_API")
        self.assertTrue(first["payment"]["verification_factors"]["provider_webhook_verified"])
        self.assertTrue(first["payment"]["verification_factors"]["provider_api_verified"])
        booking = self.conn.execute("SELECT payment_status FROM mkt_bookings WHERE id=?", (self.booking_id,)).fetchone()
        self.assertEqual(booking["payment_status"], "PAID")
        booking = self.conn.execute("SELECT status FROM mkt_bookings WHERE id=?", (self.booking_id,)).fetchone()
        self.assertEqual(booking["status"], "PAYMENT_CONFIRMED")
        second = pg.process_webhook(self.conn, "webhook-secret", payload, client=self.client)
        self.assertTrue(second["idempotent"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM gateway_webhook_events").fetchone()[0], 1)
        self.assertEqual(pg._row(self.conn, created["transaction_id"])["status"], "PAID")

    def test_wrong_amount_is_under_review_not_paid(self):
        created = self.ready()
        self.client.payment_amount_delta = 1
        result = pg.process_webhook(self.conn, "webhook-secret", self.success_webhook(), client=self.client)
        self.assertEqual(result["payment"]["status"], "UNDER_REVIEW")
        self.assertNotEqual(pg._row(self.conn, created["transaction_id"])["status"], "PAID")

    def test_delayed_webhook_api_success_remains_under_review(self):
        created = self.ready()
        pending = pg.refresh_transaction(self.conn, created["transaction_id"], client=self.client)
        self.assertEqual(pending["status"], "PENDING")
        self.client.payment_status = "SUCCEEDED"
        self.client.session_status = "COMPLETED"
        confirmed = pg.refresh_transaction(self.conn, created["transaction_id"], client=self.client)
        self.assertEqual(confirmed["status"], "UNDER_REVIEW")
        self.assertEqual(confirmed["verification_method"], "PROVIDER_API_ONLY")
        self.assertFalse(confirmed["verification_factors"]["provider_webhook_verified"])
        booking = self.conn.execute("SELECT payment_status FROM mkt_bookings WHERE id=?", (self.booking_id,)).fetchone()
        self.assertNotEqual(booking["payment_status"], "PAID")

        verified = pg.process_webhook(self.conn, "webhook-secret", self.success_webhook(), client=self.client)
        self.assertEqual(verified["payment"]["status"], "PAID")

    def test_failed_expired_and_cancelled_states(self):
        created = self.ready()
        self.client.payment_status = "FAILED"
        failed = pg.refresh_transaction(self.conn, created["transaction_id"], client=self.client)
        self.assertEqual(failed["status"], "FAILED")

        self.conn.execute("UPDATE gateway_payment_transactions SET status='PENDING' WHERE id=?", (created["transaction_id"],))
        self.client.payment_status = None; self.client.session_status = "EXPIRED"
        self.assertEqual(pg.refresh_transaction(self.conn, created["transaction_id"], client=self.client)["status"], "EXPIRED")

        self.conn.execute("UPDATE gateway_payment_transactions SET status='PENDING' WHERE id=?", (created["transaction_id"],))
        self.client.session_status = "CANCELED"
        self.assertEqual(pg.refresh_transaction(self.conn, created["transaction_id"], client=self.client)["status"], "CANCELLED")

    def test_refund_and_partial_refund_wait_for_verified_callback(self):
        created = self.ready()
        pg.process_webhook(self.conn, "webhook-secret", self.success_webhook(), client=self.client)
        refund = pg.request_refund(self.conn, self.admin, created["transaction_id"], 250, "REQUESTED_BY_CUSTOMER", "rf-1", client=self.client)
        self.assertEqual(refund["status"], "PENDING")
        self.assertEqual(pg._row(self.conn, created["transaction_id"])["status"], "PAID")
        callback = {
            "event": "refund.succeeded", "created": "2026-08-30T03:00:00Z",
            "data": {
                "id": refund["provider_refund_id"],
                "payment_request_id": "pr-1102feb0-bb79-47ae-9d1e-e69394d3949c",
                "status": "SUCCEEDED",
            },
        }
        pg.process_webhook(self.conn, "webhook-secret", callback, client=self.client)
        tx = pg._row(self.conn, created["transaction_id"])
        self.assertEqual(tx["status"], "PARTIALLY_REFUNDED")
        self.assertEqual(tx["refunded_amount"], 250)

    def test_manual_payment_requires_second_operator_and_official_record(self):
        pg.accept_final_quote(self.conn, self.token, "quote-accept-manual")
        review = pg.open_manual_review(self.conn, self.admin, self.booking_id, 1250,
                                       "Provider outage", supporting_document="screenshot-only.jpg")
        self.assertEqual(review["status"], "UNDER_REVIEW")
        with self.assertRaises(Exception):
            pg.approve_manual_review(self.conn, self.admin, review["review_id"], "BANK-001", "checked")
        with self.assertRaises(Exception):
            pg.approve_manual_review(self.conn, self.verifier, review["review_id"], "", "checked")
        approved = pg.approve_manual_review(self.conn, self.verifier, review["review_id"],
                                            "OFFICIAL-BANK-RECORD-001", "matched official statement")
        self.assertEqual(approved["verification_method"], "MANUAL_OFFICIAL_RECORD")

    def test_reconciliation_flags_provider_mismatch(self):
        self.ready()
        self.client.payment_status = "SUCCEEDED"
        self.client.session_status = "COMPLETED"
        self.client.payment_amount_delta = 20
        result = pg.reconcile_daily(self.conn, self.admin, client=self.client)
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertEqual(result["issues"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM gateway_reconciliation_issues").fetchone()[0], 1)

    def test_provider_failures_never_create_false_success(self):
        self.certify()
        pg.accept_final_quote(self.conn, self.token, "quote-accept-failure")
        self.client.fail_create = True
        with self.assertRaises(RuntimeError):
            pg.create_payment_session(self.conn, self.token, "gcash", "pay-create-failure", client=self.client)
        tx = self.conn.execute(
            "SELECT status FROM gateway_payment_transactions WHERE booking_id=? ORDER BY id DESC LIMIT 1",
            (self.booking_id,),
        ).fetchone()
        self.assertEqual(tx["status"], "FAILED")
        self.assertNotEqual(
            self.conn.execute("SELECT payment_status FROM mkt_bookings WHERE id=?", (self.booking_id,)).fetchone()[0],
            "PAID",
        )

    def test_uncertain_refund_is_durable_and_under_review(self):
        created = self.ready()
        pg.process_webhook(self.conn, "webhook-secret", self.success_webhook(), client=self.client)
        self.client.fail_refund = True
        with self.assertRaises(RuntimeError):
            pg.request_refund(self.conn, self.admin, created["transaction_id"], 250,
                              "REQUESTED_BY_CUSTOMER", "rf-uncertain", client=self.client)
        refund = self.conn.execute(
            "SELECT status FROM gateway_refunds WHERE transaction_id=? AND idempotency_key='rf-uncertain'",
            (created["transaction_id"],),
        ).fetchone()
        self.assertEqual(refund["status"], "UNDER_REVIEW")
        self.assertEqual(pg._row(self.conn, created["transaction_id"])["status"], "PAID")

    def test_tenant_isolation_blocks_cross_tenant_finance_actions(self):
        outsider = {
            "id": 912, "role": "finance_admin", "tenant_id": 2,
            "perms": {"marketplace.payment.reconcile", "marketplace.payment.verify", "marketplace.refund.request"},
        }
        pg.accept_final_quote(self.conn, self.token, "quote-accept-tenant")
        with self.assertRaises(Exception):
            pg.open_manual_review(self.conn, outsider, self.booking_id, 1250, "cross tenant")

    def test_daily_automatic_reconciliation_is_idempotent(self):
        self.ready()
        first = pg.reconcile_automatic(self.conn, day="2026-08-30", client=self.client)
        second = pg.reconcile_automatic(self.conn, day="2026-08-30", client=self.client)
        self.assertFalse(first.get("idempotent", False))
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM gateway_reconciliation_runs WHERE run_key IS NOT NULL").fetchone()[0],
            1,
        )

    def test_payment_state_persists_after_database_restart(self):
        # A named file avoids Windows/OneDrive directory ACL interference while still exercising
        # a real close/reopen cycle against durable SQLite storage.
        with tempfile.NamedTemporaryFile(suffix="-gateway-restart.sqlite", delete=False) as handle:
            path = handle.name
        try:
            first = db.connect("sqlite:///" + path)
            result = public_booking.submit(first, {
                "contact_name": "Restart Test", "contact_phone": "+639171000001",
                "origin_island": "LUZON", "dest_island": "LUZON", "vehicle": "moto", "km": 15,
                "origin_city": "Makati", "dest_city": "Pasig",
                "idempotency_key": "booking-gateway-restart",
            })
            public_booking.review(first, self.admin, result["booking_id"], "QUOTE", quote_amount=900)
            pg.certify_channel(first, self.admin, "gcash", "SANDBOX", ALL_TESTS)
            pg.accept_final_quote(first, result["tracking_token"], "restart-accept")
            pg.create_payment_session(first, result["tracking_token"], "gcash", "restart-payment",
                                      client=FakeXendit())
            first.close()
            reopened = db.connect("sqlite:///" + path)
            try:
                status = pg.latest_status(reopened, result["tracking_token"])
                self.assertEqual(status["status"], "PENDING")
                self.assertTrue(status["reference_number"].startswith("LH-"))
                self.assertTrue(pg.available_channels(reopened)["available"])
            finally:
                reopened.close()
        finally:
            if os.path.exists(path):
                os.unlink(path)


if __name__ == "__main__":
    unittest.main()
