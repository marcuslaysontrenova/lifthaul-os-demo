"""Corporate Billing & Statements — consolidated A/R over the existing revenue streams.

Proves: one account per customer; idempotent charge posting; A/R payments that move the balance but
NEVER move funds; deterministic statement rollup with opening carry-forward; late charges swept into
the next statement (never lost); aging buckets; credit-limit flagging via the reused CRM credit engine;
statement immutability (statemented items are not re-swept); rental->billing consolidation; RBAC;
tenant isolation; integrity.
"""
import datetime
import unittest

import db
import core
import admin_platform as ap
import crm_admin
import rental as rt
import marketplace_onboarding as mo
import billing as bl


def _days_ago(n):
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.op = self._a(10)
        self.cust = core.create_customer(self.c, self.op, "Acme Corp", "ops@acme.com", "billing@acme.com")

    def _a(self, id, perms=("*",)):
        return {"id": id, "role": "ops", "perms": set(perms), "tenant_id": self.rgo}

    def _account(self, **kw):
        return bl.open_account(self.c, self.op, self.cust, **kw)["account_id"]


# --------------------------------------------------------------------------- #
class Accounts(Base):
    def test_open_and_no_duplicate(self):
        self._account(credit_limit=100000)
        with self.assertRaises(core.ConflictError):
            self._account()

    def test_requires_existing_customer(self):
        with self.assertRaises(core.NotFoundError):
            bl.open_account(self.c, self.op, 999999)

    def test_rbac(self):
        weak = self._a(20, perms=("marketplace.billing.view",))
        with self.assertRaises(core.ForbiddenError):
            bl.open_account(self.c, weak, self.cust)


# --------------------------------------------------------------------------- #
class Ledger(Base):
    def setUp(self):
        super().setUp()
        self.acct = self._account(credit_limit=100000, payment_terms_days=30)

    def test_charge_idempotent_per_source(self):
        bl.post_charge(self.c, self.op, self.acct, "FREIGHT", 501, "f", 30000, tax=3600)
        r = bl.post_charge(self.c, self.op, self.acct, "FREIGHT", 501, "dup", 30000, tax=3600)
        self.assertTrue(r["idempotent"])
        n = self.c.execute("SELECT COUNT(*) c FROM billing_items WHERE source_type='FREIGHT' AND source_id=501").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_payment_records_credit_without_moving_funds(self):
        bl.post_charge(self.c, self.op, self.acct, "FREIGHT", 1, "f", 30000, tax=3600)
        p = bl.record_payment(self.c, self.op, self.acct, 33600, method="BANK_TRANSFER", reference="OR-1")
        self.assertFalse(p["funds_moved"])
        bal = bl.account_balance(self.c, self.op, self.acct)
        self.assertEqual(bal["balance"], 0)

    def test_negative_charge_rejected(self):
        with self.assertRaises(core.ValidationError):
            bl.post_charge(self.c, self.op, self.acct, "X", 1, "bad", -5)

    def test_payment_must_be_positive(self):
        with self.assertRaises(core.ValidationError):
            bl.record_payment(self.c, self.op, self.acct, 0)


# --------------------------------------------------------------------------- #
class Statements(Base):
    def setUp(self):
        super().setUp()
        self.acct = self._account(credit_limit=100000, payment_terms_days=30)

    def test_rollup_and_opening_carryforward(self):
        bl.post_charge(self.c, self.op, self.acct, "FREIGHT", 1, "f", 30000, tax=3600)
        s1 = bl.generate_statement(self.c, self.op, self.acct, "2026-08-01", "2026-08-31")
        self.assertEqual(s1["opening_balance"], 0)
        self.assertEqual(s1["closing_balance"], 33600)
        # a NEW charge posted after s1 is swept into s2 (opening carries s1 closing)
        bl.post_charge(self.c, self.op, self.acct, "RENTAL", 2, "r", 10000, tax=0)
        s2 = bl.generate_statement(self.c, self.op, self.acct, "2026-09-01", "2026-09-30")
        self.assertEqual(s2["opening_balance"], 33600)
        self.assertEqual(s2["charges_total"], 10000)
        self.assertEqual(s2["closing_balance"], 43600)

    def test_statemented_items_not_reswept(self):
        bl.post_charge(self.c, self.op, self.acct, "FREIGHT", 1, "f", 30000)
        bl.generate_statement(self.c, self.op, self.acct, "2026-08-01", "2026-08-31")
        s2 = bl.generate_statement(self.c, self.op, self.acct, "2026-09-01", "2026-09-30")
        self.assertEqual(s2["charges_total"], 0)   # nothing new -> not double counted
        self.assertEqual(s2["closing_balance"], 30000)

    def test_over_limit_flag_and_credit_status(self):
        bl.post_charge(self.c, self.op, self.acct, "R", 1, "big", 150000)
        s = bl.generate_statement(self.c, self.op, self.acct, "2026-08-01", "2026-08-31")
        self.assertTrue(s["over_limit"])
        self.assertEqual(self.c.execute("SELECT credit_status FROM billing_accounts WHERE id=?", (self.acct,)).fetchone()["credit_status"],
                         "OVER_LIMIT")

    def test_credit_evaluation_persisted(self):
        bl.post_charge(self.c, self.op, self.acct, "R", 1, "c", 5000)
        bl.generate_statement(self.c, self.op, self.acct, "2026-08-01", "2026-08-31")
        n = self.c.execute("SELECT COUNT(*) c FROM credit_evaluations WHERE customer_id=? AND action='statement'",
                           (self.cust,)).fetchone()["c"]
        self.assertGreaterEqual(n, 1)   # reused CRM credit engine recorded evidence

    def test_due_date_uses_terms(self):
        bl.post_charge(self.c, self.op, self.acct, "R", 1, "c", 1000)
        s = bl.generate_statement(self.c, self.op, self.acct, "2026-08-01", "2026-08-31")
        self.assertEqual(s["due_date"], "2026-09-30")   # 08-31 + 30d

    def test_mark_paid(self):
        bl.post_charge(self.c, self.op, self.acct, "R", 1, "c", 1000)
        s = bl.generate_statement(self.c, self.op, self.acct, "2026-08-01", "2026-08-31")
        bl.mark_statement_paid(self.c, self.op, s["statement_id"])
        self.assertEqual(self.c.execute("SELECT status FROM billing_statements WHERE id=?", (s["statement_id"],)).fetchone()["status"],
                         "PAID")

    def test_period_order_validated(self):
        with self.assertRaises(core.ValidationError):
            bl.generate_statement(self.c, self.op, self.acct, "2026-09-01", "2026-08-01")


# --------------------------------------------------------------------------- #
class Aging(Base):
    def test_aging_buckets(self):
        acct = self._account(payment_terms_days=30)
        bl.post_charge(self.c, self.op, acct, "R", 1, "fresh", 1000, item_date=_days_ago(5))     # current
        bl.post_charge(self.c, self.op, acct, "R", 2, "overdue", 2000, item_date=_days_ago(150))  # 90+
        s = bl.generate_statement(self.c, self.op, acct, _days_ago(200), datetime.date.today().isoformat())
        self.assertEqual(s["aging"]["current"], 1000)      # 5 days old, within terms
        self.assertEqual(s["aging"]["d90_plus"], 2000)     # 150 - 30 = 120 days past due


# --------------------------------------------------------------------------- #
class RentalConsolidation(Base):
    def _fleet(self):
        cid = mo.create_carrier_application(self.c, self.op, "FLEET_OPERATOR", "Cr", registration_type="SEC",
                                            registration_number="C1", operating_address="M")
        mo.submit_carrier(self.c, self.op, cid); mo.verify_carrier(self.c, self._a(11), cid)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION", "AUTHORITY_TO_OPERATE", "INSURANCE"):
            d = mo.upload_document(self.c, self.op, dt, "CARRIER", cid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self._a(11), d)
        mo.activate_carrier(self.c, self._a(12), cid)
        v = mo.register_vehicle(self.c, self.op, cid, "truck_6w", "P1"); mo.verify_vehicle(self.c, self._a(11), v)
        for dt in ("VEHICLE_REGISTRATION", "INSURANCE"):
            d = mo.upload_document(self.c, self.op, dt, "VEHICLE", v, expiry_date="2027-01-01")
            mo.verify_document(self.c, self._a(11), d)
        mo.activate_vehicle(self.c, self._a(12), v)
        return cid, v

    def test_finalized_rental_accrues_to_account(self):
        acct = self._account()
        cid, v = self._fleet()
        q = rt.quote_rental(self.c, self.op, "truck_6w", "DAILY", 2, carrier_id=cid, vehicle_id=v, customer_id=self.cust)
        rt.confirm_rental(self.c, self.op, q["agreement_id"]); rt.activate_rental(self.c, self.op, q["agreement_id"])
        rt.record_usage(self.c, self.op, q["agreement_id"], 2)
        rt.finalize_rental(self.c, self.op, q["agreement_id"])
        n = self.c.execute("SELECT COUNT(*) c FROM billing_items WHERE account_id=? AND source_type='RENTAL'",
                           (acct,)).fetchone()["c"]
        self.assertEqual(n, 1)


# --------------------------------------------------------------------------- #
class IsolationIntegrity(Base):
    def test_tenant_isolation(self):
        acct = self._account()
        other = {"id": 99, "role": "ops", "perms": {"*"}, "tenant_id": 999999}
        with self.assertRaises(core.NotFoundError):
            bl.account_balance(self.c, other, acct)

    def test_integrity_clean(self):
        acct = self._account()
        bl.post_charge(self.c, self.op, acct, "R", 1, "c", 1000)
        self.assertTrue(bl.run_integrity(self.c, self.op)["ok"])


if __name__ == "__main__":
    unittest.main()
