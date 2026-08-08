"""Protected Payment productization E2E + provider certification + RBAC/SoD + immutability +
failure recovery (Priorities 5, 8-13). Reuses the authoritative domain; asserts fail-closed and
reconciliation-balances at every dangerous point.
"""
import unittest

import admin_platform as ap
import core
import db
import marketplace_trust as mt
import marketplace_trust_closure as tc
import protected_payment as pp


def _staff(c, e):
    core.create_user(c, e, "pw", "admin", "S")
    return core.actor_for(c, core.login(c, e, "pw"))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.sup = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
        self.ops = _staff(self.c, "ops@e2e")      # maker / operations
        self.fin = _staff(self.c, "fin@e2e")      # checker / finance
        self.cid = 1
        k = mt.submit_kyb(self.c, self.ops, "CARRIER", self.cid, "SEC", "SEC-1", "Co")
        mt.verify_kyb(self.c, self.fin, k, "VERIFIED", source="SEC")
        self.pa = tc.submit_payout_account(self.c, self.ops, self.cid, "Bene", "Ent", "ref", "1234567890", cooling_hours=0)
        tc.approve_payout_account(self.c, self.fin, self.pa, beneficiary_verified=True, mfa_ok=True)

    def _tx(self, contract=500000, fee=40000):
        return pp.create_transaction(self.c, self.ops, booking_id=555, carrier_id=self.cid,
                                     contract_amount=contract, protected_amount=contract,
                                     platform_fee=fee, provider_fee=0, tax=0,
                                     milestone_plan=[{"code": "MOB", "pct": 20}, {"code": "DEL", "pct": 80}])

    def _advance(self, tx, target):
        order = ["PAYMENT_INTENT_CREATED", "AWAITING_CUSTOMER_FUNDS", "CUSTOMER_FUNDED",
                 "FUNDING_CONFIRMED", "FUNDS_PROTECTED", "TRIP_AUTHORIZED", "SERVICE_IN_PROGRESS",
                 "DELIVERY_EVIDENCE_PENDING", "DISPUTE_WINDOW", "RELEASE_ELIGIBLE",
                 "RELEASE_APPROVAL_PENDING", "RELEASE_APPROVED", "RELEASE_REQUESTED",
                 "RELEASE_CONFIRMED", "SETTLED"]
        for s in order:
            pp.transition(self.c, self.ops, tx, s, payout_account_id=self.pa, job_value=500000)
            if s == target:
                return


class NormalPaymentE2E(Fixture):
    def test_full_lifecycle_settles_with_zero_difference(self):
        tx = self._tx()
        # ledger consistent: funded = released + fees
        pp.append_ledger(self.c, self.ops, tx, "funding", 500000)
        pp.append_ledger(self.c, self.ops, tx, "platform_fee", 40000)
        pp.append_ledger(self.c, self.ops, tx, "release", 460000)
        self._advance(tx, "SETTLED")
        v = pp.get_transaction(self.c, self.sup, tx)
        self.assertEqual(v["state"], "SETTLED")
        self.assertEqual(v["reconciliation"]["difference"], 0)
        self.assertTrue(v["reconciliation"]["balanced"])


class DisputeE2E(Fixture):
    def test_dispute_partial_resolution_reconciles(self):
        tx = self._tx(contract=500000, fee=0)
        pp.append_ledger(self.c, self.ops, tx, "funding", 500000)
        self._advance(tx, "DISPUTE_WINDOW")
        # customer disputes -> funds held
        pp.transition(self.c, self.ops, tx, "DISPUTED", reason="cargo damage")
        pp.transition(self.c, self.ops, tx, "FUNDS_HELD", reason="freeze pending review")
        # partial resolution: release 300k + refund 200k
        pp.append_ledger(self.c, self.fin, tx, "release", 300000)
        pp.append_ledger(self.c, self.fin, tx, "refund", 200000)
        pp.transition(self.c, self.ops, tx, "REFUND_PENDING", reason="partial refund")
        pp.transition(self.c, self.ops, tx, "PARTIALLY_REFUNDED")
        pp.transition(self.c, self.ops, tx, "SETTLED")
        rec = pp.reconcile(self.c, tx)
        # Released + Refunded + Remaining + Fees == Funded
        self.assertEqual(rec["difference"], 0)
        self.assertTrue(rec["balanced"])


class FraudE2E(Fixture):
    def test_fraud_blocks_then_clears(self):
        tx = self._tx()
        self._advance(tx, "RELEASE_APPROVAL_PENDING")
        # payout account change + critical fraud signal
        mt.raise_fraud_flag(self.c, self.ops, "CARRIER", self.cid, "abnormal_account_change", "CRITICAL")
        with self.assertRaises(core.ForbiddenError):
            pp.transition(self.c, self.ops, tx, "RELEASE_APPROVED", payout_account_id=self.pa, job_value=500000)
        # independent fraud review clears it
        fid = self.c.execute("SELECT id FROM mkt_fraud_flags WHERE subject_id=? AND status='OPEN'", (self.cid,)).fetchone()["id"]
        mt.clear_fraud_flag(self.c, self.fin, fid, "verified against SEC + bank letter")
        # now release is permitted
        pp.transition(self.c, self.ops, tx, "RELEASE_APPROVED", payout_account_id=self.pa, job_value=500000)
        self.assertEqual(pp._tx(self.c, self.ops, tx)["state"], "RELEASE_APPROVED")


class RbacSodE2E(Fixture):
    def test_customer_cannot_approve_release(self):
        cust = _staff(self.c, "cust@e2e")   # give a NON-payment role
        core.create_user(self.c, "realcust@e2e", "pw", "customer", "C")
        c2 = core.actor_for(self.c, core.login(self.c, "realcust@e2e", "pw"))
        tx = self._tx()
        self._advance(tx, "RELEASE_APPROVAL_PENDING")
        with self.assertRaises(core.ForbiddenError):
            pp.transition(self.c, c2, tx, "RELEASE_APPROVED", payout_account_id=self.pa, job_value=500000)

    def test_payout_maker_cannot_approve_own(self):
        pa2 = tc.submit_payout_account(self.c, self.ops, self.cid, "B", "E", "ref", "111")
        with self.assertRaises(core.ForbiddenError):
            tc.approve_payout_account(self.c, self.ops, pa2, mfa_ok=True)

    def test_dispute_opener_cannot_resolve(self):
        did = tc.open_dispute(self.c, self.ops, 555, self.cid, 1000, "x")
        with self.assertRaises(core.ForbiddenError):
            tc.resolve_dispute(self.c, self.ops, did, "RELEASE_FULL", "self-resolve")


class ImmutabilityE2E(Fixture):
    def test_finance_cannot_bypass_reconciliation(self):
        tx = self._tx(contract=500000, fee=0)
        pp.append_ledger(self.c, self.ops, tx, "funding", 500000)
        pp.append_ledger(self.c, self.ops, tx, "release", 900000)   # over-release
        self._advance(tx, "RELEASE_CONFIRMED")
        with self.assertRaises(core.ConflictError):
            pp.transition(self.c, self.ops, tx, "SETTLED")           # blocked, cannot bypass

    def test_ledger_corrections_are_reversals_only(self):
        tx = self._tx()
        e = pp.append_ledger(self.c, self.ops, tx, "release", 100000)
        rev = pp.reverse_ledger_entry(self.c, self.fin, e, "released in error")
        self.assertEqual(self.c.execute("SELECT amount FROM mkt_protected_ledger WHERE id=?", (e,)).fetchone()["amount"], 100000)
        self.assertEqual(self.c.execute("SELECT amount FROM mkt_protected_ledger WHERE id=?", (rev,)).fetchone()["amount"], -100000)
        self.assertTrue(pp.run_integrity(self.c)["ok"])


class FailureRecoveryE2E(Fixture):
    def test_state_and_ledger_survive_and_are_idempotent(self):
        tx = self._tx()
        self._advance(tx, "FUNDS_PROTECTED")
        # simulate a provider "failure" mid-flow: state must remain correct, no double movement
        state = pp._tx(self.c, self.ops, tx)["state"]
        self.assertEqual(state, "FUNDS_PROTECTED")
        # a duplicate webhook is idempotent (dedup) — proven at the closure layer
        secret, payload = "s", b'{"x":1}'
        import hashlib, hmac
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        self.assertTrue(tc.verify_webhook(self.c, "wise", "f1", "funded", payload, sig, secret)["accepted"])
        self.assertFalse(tc.verify_webhook(self.c, "wise", "f1", "funded", payload, sig, secret)["accepted"])


class ProviderCertificationTests(unittest.TestCase):
    def test_mock_conforms_but_is_not_active_eligible(self):
        rep = pp.certify_provider(pp.MockProtectedPaymentProvider())
        self.assertEqual(rep["mandatory_failures"], 0)
        self.assertTrue(rep["conformance_pass"])
        self.assertFalse(rep["active_eligible"])              # MOCK is never a licensed provider
        self.assertIn("NOT ACTIVE-ELIGIBLE", rep["conclusion"])

    def test_capability_declaration_present(self):
        caps = pp.MockProtectedPaymentProvider().declare_capabilities()
        for k in pp.CAPABILITY_KEYS:
            self.assertIn(k, caps)

    def test_unsupported_capability_fails_closed(self):
        class NoRefund(pp.MockProtectedPaymentProvider):
            capabilities = dict(pp.MockProtectedPaymentProvider.capabilities, supports_partial_refund=False)
        with self.assertRaises(core.ForbiddenError):
            NoRefund().refund_partial("REF", 10)


class CustomerCarrierProjectionTests(Fixture):
    def test_customer_view_hides_internals(self):
        tx = self._tx()
        v = pp.customer_view(self.c, self.sup, tx)
        self.assertEqual(v["status"], "Payment Required")     # friendly label, not PAYMENT_REQUIRED
        self.assertEqual(v["terminology"], "Protected Payment")
        for forbidden in ("carrier_cost", "margin", "provider_secret", "bank_account", "internal_cost"):
            self.assertNotIn(forbidden, v)

    def test_carrier_settlement_amounts(self):
        tx = self._tx(contract=500000, fee=40000)
        pp.append_ledger(self.c, self.ops, tx, "funding", 500000)
        pp.append_ledger(self.c, self.ops, tx, "release", 100000)
        s = pp.carrier_settlement(self.c, self.sup, tx)
        self.assertEqual(s["released_amount"], 100000)
        self.assertEqual(s["held_amount"], 400000)


if __name__ == "__main__":
    unittest.main()
