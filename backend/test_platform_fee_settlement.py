"""Governed Wise settlement coverage for LiftHaul's 10% administration fee."""
import os
import unittest
from unittest.mock import patch

import db
import platform_fee_settlement as pfs
import public_booking as pb


class FakeWise:
    def __init__(self, fund_status="processing", fail=False):
        self.fund_status = fund_status
        self.fail = fail
        self.quote_amounts = []
        self.transfer_ids = []
        self.fund_calls = 0

    def create_quote(self, *, amount, currency, correlation_id):
        if self.fail:
            raise pfs.WiseFeeError("simulated Wise failure")
        self.quote_amounts.append((amount, currency))
        return {"id": "quote-admin-fee-1"}

    def create_transfer(self, *, quote_id, customer_transaction_id, reference, correlation_id):
        self.transfer_ids.append(customer_transaction_id)
        return {"id": "990001", "status": "incoming_payment_waiting"}

    def fund_transfer(self, *, transfer_id, correlation_id):
        self.fund_calls += 1
        return {"status": self.fund_status}

    def get_transfer(self, transfer_id, correlation_id=None):
        return {"id": transfer_id, "status": self.fund_status}


class AdministrationFeeWiseSettlementTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "ADMIN_FEE_WISE_ENABLED": "true",
            "ADMIN_FEE_WISE_TRANSFER_TIMING": "payment_confirmed",
            "ADMIN_FEE_EARLY_RELEASE_APPROVED": "true",
            "WISE_BUSINESS_ACCOUNT_APPROVED": "true",
            "WISE_API_FUNDING_APPROVED": "true",
            "WISE_API_KEY": "test-secret-never-persisted",
            "WISE_PROFILE_ID": "101",
            "WISE_ADMIN_FEE_RECIPIENT_ID": "202",
            "WISE_BALANCE_ID": "303",
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.conn = db.connect(":memory:")
        result = pb.submit(self.conn, {
            "contact_name": "Fee Settlement Test", "contact_phone": "+639171234567",
            "origin_island": "LUZON", "dest_island": "LUZON",
            "origin_city": "Makati", "dest_city": "Pasig",
            "vehicle": "sedan", "km": 10, "idempotency_key": "fee-booking-1",
        })
        self.booking_id = result["booking_id"]
        self.assertEqual(result["quote_breakdown"]["administration_fee"], 26)
        now = "2026-08-31T00:00:00+00:00"
        cur = self.conn.execute(
            "INSERT INTO gateway_payment_transactions(tenant_id,booking_id,provider,environment,channel_key,"
            "amount,currency,reference_id,status,verification_method,idempotency_key,request_hash,created_at,updated_at) "
            "VALUES(NULL,?,'XENDIT','SANDBOX','gcash',?,'PHP','LH-FEE-1','PAID',"
            "'PROVIDER_WEBHOOK_PLUS_API','fee-payment-1','hash',?,?)",
            (self.booking_id, result["estimate"], now, now),
        )
        self.transaction_id = cur.lastrowid
        self.conn.commit()

    def test_exact_10_percent_is_submitted_once_and_never_duplicated(self):
        wise = FakeWise("outgoing_payment_sent")
        first = pfs.record_verified_payment(self.conn, self.transaction_id, client=wise)
        second = pfs.record_verified_payment(self.conn, self.transaction_id, client=wise)
        self.assertEqual(first["status"], "COMPLETED")
        self.assertEqual(first["fee_rate"], 0.10)
        self.assertEqual(first["fee_amount"], 26)
        self.assertEqual(wise.quote_amounts, [(26, "PHP")])
        self.assertEqual(wise.fund_calls, 1)
        self.assertTrue(second["idempotent"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM platform_fee_settlements").fetchone()[0], 1)
        self.assertNotIn("test-secret-never-persisted", str(dict(
            self.conn.execute("SELECT * FROM platform_fee_settlements").fetchone())))

    def test_missing_approval_blocks_without_calling_wise(self):
        with patch.dict(os.environ, {"WISE_API_FUNDING_APPROVED": "false"}, clear=False):
            wise = FakeWise("outgoing_payment_sent")
            result = pfs.record_verified_payment(self.conn, self.transaction_id, client=wise)
        self.assertEqual(result["status"], "BLOCKED_CONFIGURATION")
        self.assertEqual(wise.quote_amounts, [])
        self.assertIsNone(result["wise_transfer_id"])

    def test_non_provider_verified_payment_is_never_settled(self):
        self.conn.execute(
            "UPDATE gateway_payment_transactions SET verification_method='MANUAL_OFFICIAL_RECORD' WHERE id=?",
            (self.transaction_id,),
        )
        self.conn.commit()
        result = pfs.record_verified_payment(self.conn, self.transaction_id, client=FakeWise())
        self.assertEqual(result["status"], "NOT_ELIGIBLE")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM platform_fee_settlements").fetchone()[0], 0)

    def test_provider_failure_is_action_required_not_false_success(self):
        result = pfs.record_verified_payment(self.conn, self.transaction_id, client=FakeWise(fail=True))
        self.assertEqual(result["status"], "ACTION_REQUIRED")
        self.assertIsNone(result["wise_transfer_id"])
        self.assertNotEqual(result["status"], "COMPLETED")

    def test_refund_after_submission_creates_fee_recovery_case(self):
        result = pfs.record_verified_payment(self.conn, self.transaction_id, client=FakeWise("processing"))
        self.assertEqual(result["status"], "PROCESSING")
        impact = pfs.handle_refund(self.conn, self.transaction_id, 143)
        self.assertEqual(impact["status"], "REFUND_REVIEW_REQUIRED")
        self.assertEqual(impact["recovery_status"], "OPEN")
        self.assertEqual(impact["recovery_amount"], 13)

    def test_sync_reports_complete_only_from_wise_terminal_status(self):
        created = pfs.record_verified_payment(self.conn, self.transaction_id, client=FakeWise("processing"))
        actor = {"id": 55, "role": "finance_admin", "tenant_id": None,
                 "perms": {"marketplace.payment.reconcile"}}
        synced = pfs.sync_transfer(self.conn, actor, created["id"], client=FakeWise("outgoing_payment_sent"))
        self.assertEqual(synced["status"], "COMPLETED")
        self.assertIsNotNone(synced["completed_at"])


if __name__ == "__main__":
    unittest.main()
