"""Protected Payment authoritative domain — state machine, immutable ledger, reconciliation,
live-funds gate. Reuses (does not rebuild) the marketplace payment/trust/trip controls.
"""
import os
import tempfile
import unittest

import admin_platform as ap
import core
import db
import marketplace_trust as mt
import marketplace_trust_closure as tc
import protected_payment as pp
import tenant


class ProtectedPaymentDomainTests(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.sup = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
        self.a1 = self._staff("p1@pp")
        self.a2 = self._staff("p2@pp")
        # a verified carrier + approved (cooling-cleared) payout account for the happy path
        self.cid = 1
        k = mt.submit_kyb(self.c, self.a1, "CARRIER", self.cid, "SEC", "SEC-1", "Co")
        mt.verify_kyb(self.c, self.a2, k, "VERIFIED", source="SEC")
        self.pid = tc.submit_payout_account(self.c, self.a1, self.cid, "Bene", "Ent", "ref", "1234567890", cooling_hours=0)
        tc.approve_payout_account(self.c, self.a2, self.pid, beneficiary_verified=True, mfa_ok=True)
        self.tx = pp.create_transaction(self.c, self.a1, booking_id=555, carrier_id=self.cid,
                                        contract_amount=100000, protected_amount=100000,
                                        platform_fee=8000, provider_fee=2000, tax=0)

    def _staff(self, e):
        core.create_user(self.c, e, "pw", "admin", "S")
        return core.actor_for(self.c, core.login(self.c, e, "pw"))

    # ---- 1. canonical state machine: only declared transitions ----
    def test_initial_state(self):
        self.assertEqual(pp._tx(self.c, self.a1, self.tx)["state"], "PAYMENT_REQUIRED")

    def test_illegal_transition_denied(self):
        with self.assertRaises(core.ConflictError):
            pp.transition(self.c, self.a1, self.tx, "SETTLED")   # cannot jump from PAYMENT_REQUIRED

    def test_no_arbitrary_status_edit(self):
        # the only path to change state is transition(); a bogus target is rejected
        with self.assertRaises(core.ValidationError):
            pp.transition(self.c, self.a1, self.tx, "NOT_A_STATE")

    def _advance_to(self, target):
        order = ["PAYMENT_INTENT_CREATED", "AWAITING_CUSTOMER_FUNDS", "CUSTOMER_FUNDED",
                 "FUNDING_CONFIRMED", "FUNDS_PROTECTED", "TRIP_AUTHORIZED", "SERVICE_IN_PROGRESS",
                 "DELIVERY_EVIDENCE_PENDING", "DISPUTE_WINDOW", "RELEASE_ELIGIBLE",
                 "RELEASE_APPROVAL_PENDING", "RELEASE_APPROVED", "RELEASE_REQUESTED",
                 "RELEASE_CONFIRMED", "SETTLED"]
        for s in order:
            pp.transition(self.c, self.a1, self.tx, s, payout_account_id=self.pid, job_value=100000)
            if s == target:
                return

    def test_trip_cannot_authorize_before_protection(self):
        pp.transition(self.c, self.a1, self.tx, "PAYMENT_INTENT_CREATED")
        pp.transition(self.c, self.a1, self.tx, "AWAITING_CUSTOMER_FUNDS")
        pp.transition(self.c, self.a1, self.tx, "CUSTOMER_FUNDED")
        pp.transition(self.c, self.a1, self.tx, "FUNDING_CONFIRMED")
        # skipping FUNDS_PROTECTED is impossible (only declared next is FUNDS_PROTECTED)
        with self.assertRaises(core.ConflictError):
            pp.transition(self.c, self.a1, self.tx, "TRIP_AUTHORIZED")

    def test_release_gate_enforced_in_transition(self):
        self._advance_to("RELEASE_APPROVAL_PENDING")
        # open a dispute → release transition must be denied by the composed gate
        tc.open_dispute(self.c, self.a2, 555, self.cid, 1000, "damage")
        with self.assertRaises(core.ForbiddenError):
            pp.transition(self.c, self.a1, self.tx, "RELEASE_APPROVED", payout_account_id=self.pid, job_value=100000)

    def test_happy_path_settles_when_reconciled(self):
        # fund the ledger consistently so reconciliation balances at SETTLED
        pp.append_ledger(self.c, self.a1, self.tx, "funding", 100000)
        pp.append_ledger(self.c, self.a1, self.tx, "platform_fee", 8000)
        pp.append_ledger(self.c, self.a1, self.tx, "provider_fee", 2000)
        pp.append_ledger(self.c, self.a1, self.tx, "release", 90000)
        self._advance_to("SETTLED")
        self.assertEqual(pp._tx(self.c, self.a1, self.tx)["state"], "SETTLED")

    # ---- 10. immutable ledger ----
    def test_ledger_correction_is_a_reversing_entry(self):
        e = pp.append_ledger(self.c, self.a1, self.tx, "release", 50000)
        rev = pp.reverse_ledger_entry(self.c, self.a2, e, "released in error")
        row = self.c.execute("SELECT event,amount,reverses_entry_id FROM mkt_protected_ledger WHERE id=?", (rev,)).fetchone()
        self.assertEqual(row["event"], "reversal")
        self.assertEqual(row["amount"], -50000)              # opposite amount; original untouched
        self.assertEqual(row["reverses_entry_id"], e)
        orig = self.c.execute("SELECT amount FROM mkt_protected_ledger WHERE id=?", (e,)).fetchone()
        self.assertEqual(orig["amount"], 50000)              # history never edited
        self.assertTrue(pp.run_integrity(self.c)["ok"])

    # ---- 11. reconciliation blocks settlement ----
    def test_reconciliation_imbalance_blocks_settlement(self):
        # fund but over-release → cannot settle
        pp.append_ledger(self.c, self.a1, self.tx, "funding", 100000)
        pp.append_ledger(self.c, self.a1, self.tx, "release", 200000)
        self._advance_to("RELEASE_CONFIRMED")
        with self.assertRaises(core.ConflictError):
            pp.transition(self.c, self.a1, self.tx, "SETTLED")

    def test_daily_reconciliation_flags_exceptions(self):
        pp.append_ledger(self.c, self.a1, self.tx, "funding", 100000)
        pp.append_ledger(self.c, self.a1, self.tx, "release", 999999)   # imbalance
        rep = pp.daily_reconciliation(self.c, self.sup)
        self.assertTrue(rep["settlement_blocked"])
        self.assertTrue(any(x["tx_id"] == self.tx for x in rep["exceptions"]))

    # ---- 2 + 3. provider abstraction + live-funds hard gate ----
    def test_live_provider_refused(self):
        with self.assertRaises(core.ForbiddenError):
            pp.provider("WISE")

    def test_live_funds_gate_default_off(self):
        self.assertFalse(pp.live_funds_enabled(self.c))
        with self.assertRaises(core.ForbiddenError):
            pp.assert_live_allowed(self.c, moving_real_funds=True)

    def test_live_funds_requires_all_three(self):
        for k in ("payments.live_protected_funds_enabled", "payments.legal_operating_model_approved"):
            ap.set_config(self.c, "platform", "", k, "true", actor=self.sup)
        self.assertFalse(pp.live_funds_enabled(self.c))       # still missing licensed_provider_active
        ap.set_config(self.c, "platform", "", "payments.licensed_provider_active", "true", actor=self.sup)
        self.assertTrue(pp.live_funds_enabled(self.c))

    # ---- 17. finance queues ----
    def test_finance_queues(self):
        q = pp.finance_queues(self.c, self.sup)
        self.assertIn("PAYMENT_REQUIRED", q["by_state"])
        self.assertEqual(q["terminology"], "Protected Payment")
        self.assertEqual(q["legal_escrow"], "NOT_YET_AUTHORIZED")
        self.assertFalse(q["live_funds_enabled"])


class ProtectedPaymentTenantRestartTests(unittest.TestCase):
    def test_tenant_isolation_and_restart(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite"); os.close(fd)
        try:
            c = db.connect(path)
            tA = ap.create_tenant(c, "PPA", "A"); tB = ap.create_tenant(c, "PPB", "B")
            uA = core.create_user(c, "a@pp", "pw", "admin", "A"); tenant.bind_user_tenant(c, None, uA, tA)
            uB = core.create_user(c, "b@pp", "pw", "admin", "B"); tenant.bind_user_tenant(c, None, uB, tB)
            aA = core.actor_for(c, core.login(c, "a@pp", "pw"))
            aB = core.actor_for(c, core.login(c, "b@pp", "pw"))
            tx = pp.create_transaction(c, aA, booking_id=1, carrier_id=1, contract_amount=50000, protected_amount=50000)
            pp.append_ledger(c, aA, tx, "funding", 50000)
            c.commit(); c.close()
            c2 = db.connect(path)                              # restart
            aA2 = core.actor_for(c2, core.login(c2, "a@pp", "pw"))
            aB2 = core.actor_for(c2, core.login(c2, "b@pp", "pw"))
            self.assertEqual(pp.get_transaction(c2, aA2, tx)["state"], "PAYMENT_REQUIRED")  # persisted
            self.assertEqual(pp.reconcile(c2, tx)["funded"], 50000)                          # ledger persisted
            with self.assertRaises(core.NotFoundError):
                pp.get_transaction(c2, aB2, tx)               # cross-tenant → 404
            c2.close()
        finally:
            try: os.unlink(path)
            except PermissionError: pass


if __name__ == "__main__":
    unittest.main()
