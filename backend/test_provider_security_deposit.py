"""Refundable provider-security-deposit governance and payout interaction tests."""
import os
import unittest

os.environ.setdefault("APP_ENV", "development")

import admin_platform as ap
import db
import provider_security_deposit as psd


class ProviderSecurityDepositTests(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.tenant = ap.get_tenant(self.c, "RGO")["id"]
        self.maker = {"id": 801, "role": "ops", "perms": {"*"}, "tenant_id": self.tenant}
        self.checker = {"id": 802, "role": "finance", "perms": {"*"}, "tenant_id": self.tenant}
        self.carrier = 4401

    def _active(self, amount=1000):
        pid = psd.propose_policy(
            self.c, self.maker, code="LH_TEST_DEPOSIT", version=1,
            risk_band="STANDARD", provider_scope="CARRIER", required_amount=amount,
            refund_sla_business_days=30, provider_name="MOCK", terms_version="deposit-v1")
        psd.activate_policy(
            self.c, self.checker, pid, legal_memo_ref="LEGAL-1",
            provider_contract_ref="PSP-1", tax_memo_ref="TAX-1", terms_version="deposit-v1")
        return pid

    def test_seeded_policy_is_inert_and_cannot_collect(self):
        gate = psd.gate(self.c, self.tenant, self.carrier)
        self.assertTrue(gate["ok"])
        self.assertFalse(gate["required"])
        with self.assertRaises(Exception):
            psd.record_funding(self.c, self.checker, self.carrier, 500,
                               provider_reference="PSP-PAY-1", idempotency_key="fund-1")

    def test_two_person_policy_and_provider_verified_funding(self):
        pid = psd.propose_policy(
            self.c, self.maker, code="LH_TEST_SOD", version=1, risk_band="STANDARD",
            provider_scope="CARRIER", required_amount=1000, refund_sla_business_days=30,
            provider_name="MOCK", terms_version="deposit-v1")
        with self.assertRaises(Exception):
            psd.activate_policy(self.c, self.maker, pid, legal_memo_ref="L", provider_contract_ref="P",
                                tax_memo_ref="T", terms_version="deposit-v1")
        psd.activate_policy(self.c, self.checker, pid, legal_memo_ref="L", provider_contract_ref="P",
                            tax_memo_ref="T", terms_version="deposit-v1")
        self.assertFalse(psd.gate(self.c, self.tenant, self.carrier)["ok"])
        with self.assertRaises(Exception):
            psd.record_funding(self.c, self.checker, self.carrier, 1000,
                               provider_reference=None, idempotency_key="fund-missing")
        first = psd.record_funding(self.c, self.checker, self.carrier, 1000,
                                   provider_reference="PSP-PAY-1", idempotency_key="fund-1")
        again = psd.record_funding(self.c, self.checker, self.carrier, 1000,
                                   provider_reference="PSP-PAY-1", idempotency_key="fund-1")
        self.assertEqual(first["id"], again["id"])
        self.assertTrue(psd.gate(self.c, self.tenant, self.carrier)["ok"])
        statement = psd.statement(self.c, self.maker, self.carrier)
        self.assertEqual(statement["classification"], "REFUNDABLE_LIABILITY_NOT_REVENUE")
        self.assertEqual(statement["available_amount"], 1000)

    def test_deduction_requires_resolved_case_notice_and_checker(self):
        self._active()
        psd.record_funding(self.c, self.checker, self.carrier, 1000,
                           provider_reference="PSP-PAY-2", idempotency_key="fund-2")
        dep = psd.statement(self.c, self.maker, self.carrier)
        cur = self.c.execute(
            "INSERT INTO mkt_claims(tenant_id,claim_number,claim_type,carrier_id,claimed_amount,"
            "approved_amount,status,opened_by,created_at) VALUES(?,?,?,?,?,?, 'APPROVED',?,datetime('now'))",
            (self.tenant, "CLM-1", "CARGO_DAMAGE", self.carrier, 500, 300, self.maker["id"]))
        self.c.commit()
        aid = psd.request_application(
            self.c, self.maker, dep["id"], use_code="RESOLVED_CARGO_DAMAGE", amount=300,
            notice_reference="NOTICE-1", evidence={"decision": "signed"}, claim_id=cur.lastrowid)
        with self.assertRaises(Exception):
            psd.confirm_application(self.c, self.maker, aid, provider_reference="PSP-DEBIT-1")
        psd.confirm_application(self.c, self.checker, aid, provider_reference="PSP-DEBIT-1")
        statement = psd.statement(self.c, self.maker, self.carrier)
        self.assertEqual(statement["applied_amount"], 300)
        self.assertEqual(statement["available_amount"], 700)
        self.assertFalse(psd.gate(self.c, self.tenant, self.carrier)["ok"])

    def test_unused_balance_refund_needs_provider_confirmation(self):
        self._active(750)
        psd.record_funding(self.c, self.checker, self.carrier, 750,
                           provider_reference="PSP-PAY-3", idempotency_key="fund-3")
        refund = psd.request_refund(self.c, self.maker, self.carrier)
        self.assertEqual(refund["status"], "PENDING_REVIEW")
        with self.assertRaises(Exception):
            psd.confirm_refund(self.c, self.maker, refund["id"], provider_reference="PSP-REF-1")
        self.assertEqual(psd.confirm_refund(self.c, self.checker, refund["id"],
                                            provider_reference="PSP-REF-1"), "PAID")
        statement = psd.statement(self.c, self.maker, self.carrier)
        self.assertEqual(statement["status"], "REFUNDED")
        self.assertEqual(statement["available_amount"], 0)


if __name__ == "__main__":
    unittest.main()
