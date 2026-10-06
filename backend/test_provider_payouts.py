"""Provider/driver/fleet-owner earnings-wallet and payout-policy tests."""
import datetime
import os
import unittest

os.environ.setdefault("APP_ENV", "development")

import admin_platform as ap
import db
import marketplace_trust_closure as tc
import provider_payouts as pp


class ProviderPayoutTests(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.tenant = ap.get_tenant(self.c, "RGO")["id"]
        self.maker = {"id": 501, "role": "ops", "perms": {"*"}, "tenant_id": self.tenant}
        self.checker = {"id": 502, "role": "ops", "perms": {"*"}, "tenant_id": self.tenant}
        self.carrier_id = 777
        self.account_id = tc.submit_payout_account(
            self.c, self.maker, self.carrier_id, "Juan Provider", "Juan Provider",
            "gcash:verified-token", "09171234567", cooling_hours=0)
        tc.approve_payout_account(self.c, self.checker, self.account_id,
                                  beneficiary_verified=True, mfa_ok=True)
        pp.configure_profile(
            self.c, self.maker, self.carrier_id,
            beneficiary_type="OWNER_OPERATOR", allocation_mode="OWNER_ONLY",
            payout_mode="ON_DEMAND", payout_account_id=self.account_id,
            destination_channel="GCASH", provider_name="MOCK")

    def _earning(self, amount=1000):
        cur = self.c.execute(
            "INSERT INTO mkt_payouts(tenant_id,carrier_id,gross_value,carrier_amount,"
            "platform_commission,payment_fee,tax,net_payout,currency,payout_provider,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,'MOCK','AVAILABLE',?)",
            (self.tenant, self.carrier_id, amount * 1.1, amount, amount * .1, 0, 0,
             amount, "PHP", datetime.datetime.now(datetime.timezone.utc).isoformat()))
        self.c.commit()
        pp.credit_release(self.c, self.maker, cur.lastrowid)
        return cur.lastrowid

    def test_cutoff_and_weekend_estimates(self):
        before = datetime.datetime(2026, 10, 6, 3, 0, tzinfo=datetime.timezone.utc)  # 11:00 Manila
        after = datetime.datetime(2026, 10, 6, 5, 0, tzinfo=datetime.timezone.utc)   # 13:00 Manila
        weekend = datetime.datetime(2026, 10, 10, 2, 0, tzinfo=datetime.timezone.utc)
        self.assertTrue(pp.expected_processing_at("ON_DEMAND", before).startswith("2026-10-06"))
        self.assertTrue(pp.expected_processing_at("ON_DEMAND", after).startswith("2026-10-07"))
        self.assertTrue(pp.expected_processing_at("ON_DEMAND", weekend).startswith("2026-10-12"))

    def test_weekly_tuesday_option_is_available(self):
        policy = self.c.execute(
            "SELECT * FROM mkt_provider_payout_policies WHERE code='LH_WEEKLY_TUE' AND active=1"
        ).fetchone()
        self.assertIsNotNone(policy)
        self.assertEqual(policy["schedule_day"], 1)
        monday = datetime.datetime(2026, 10, 5, 2, 0, tzinfo=datetime.timezone.utc)
        self.assertTrue(pp.expected_processing_at("WEEKLY_AUTOMATIC", monday,
                                                  schedule_day=1).startswith("2026-10-06"))

    def test_available_is_not_paid_until_provider_reference(self):
        source = self._earning(1200)
        self.assertEqual(pp.wallet(self.c, self.maker, self.carrier_id)["withdrawable"], 1200)
        request = pp.request_payout(self.c, self.maker, self.carrier_id, 1200, "cashout-1")
        self.assertEqual(request["status"], "PENDING_REVIEW")
        self.assertEqual(pp.wallet(self.c, self.maker, self.carrier_id)["withdrawable"], 0)
        paid = pp.submit_payout(self.c, self.checker, request["id"])
        self.assertEqual(paid["status"], "PAID")
        self.assertTrue(paid["provider_reference"])
        source_row = self.c.execute("SELECT status,provider_beneficiary_reference FROM mkt_payouts WHERE id=?",
                                    (source,)).fetchone()
        self.assertEqual(source_row["status"], "PAID")
        self.assertTrue(source_row["provider_beneficiary_reference"])

    def test_request_is_idempotent_and_cannot_overdraw(self):
        self._earning(500)
        first = pp.request_payout(self.c, self.maker, self.carrier_id, 200, "same-key")
        again = pp.request_payout(self.c, self.maker, self.carrier_id, 200, "same-key")
        self.assertEqual(first["id"], again["id"])
        with self.assertRaises(ValueError):
            pp.request_payout(self.c, self.maker, self.carrier_id, 201, "same-key")
        with self.assertRaises(ValueError):
            pp.request_payout(self.c, self.maker, self.carrier_id, 400, "overdraw")

    def test_split_requires_provider_contract_support(self):
        with self.assertRaises(ValueError):
            pp.configure_profile(
                self.c, self.maker, self.carrier_id,
                beneficiary_type="FLEET_OWNER", allocation_mode="CONTRACTUAL_SPLIT",
                payout_mode="WEEKLY_AUTOMATIC", payout_account_id=self.account_id,
                destination_channel="BANK", driver_share_bps=3000, owner_share_bps=7000,
                split_supported=False)

    def test_unverified_destination_rejected(self):
        pending = tc.submit_payout_account(
            self.c, self.maker, self.carrier_id, "Another", "Another", "maya:pending", "09990000000")
        with self.assertRaises(ValueError):
            pp.configure_profile(
                self.c, self.maker, self.carrier_id,
                beneficiary_type="OWNER_OPERATOR", allocation_mode="OWNER_ONLY",
                payout_mode="ON_DEMAND", payout_account_id=pending,
                destination_channel="MAYA")


if __name__ == "__main__":
    unittest.main()
