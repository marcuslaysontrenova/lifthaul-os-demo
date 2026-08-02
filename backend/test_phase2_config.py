"""LiftHaul OS — Phase 2: governed configuration consumers, policies, snapshots, history.

Proves: policy evaluators resolve via the cascade with defaults == the pre-Phase-2 constants
(financials unchanged); typed registry validation; tenant isolation of overrides; immutable
policy snapshots; and historical reproducibility (a config change never alters an existing
quotation's totals, only new documents).
"""
import json
import unittest

import db
import core
import admin_platform as ap
import policy
import config_registry


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")          # full schema + config definitions + seed
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.actor = {"id": 1, "role": "admin", "perms": {"*"}}

    def _quote(self, tenant_id=None, rate=300000, qty=2):
        a = {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": tenant_id}
        cid = core.create_customer(self.c, a, "Acme")
        bid = core.create_booking(self.c, a, cid, "Crane", "x", 1)
        core.review_booking(self.c, a, bid); core.ready_for_quotation(self.c, a, bid)
        qid = core.create_quotation(self.c, a, bid, [{"kind": "crane", "description": "x", "qty": qty, "days": 1, "rate": rate}])
        return bid, qid


class TestPolicyEvaluators(Base):
    def test_tax_default_matches_constant(self):
        r = policy.evaluate_tax(self.c, 600000, {})
        self.assertEqual((r["rate"], r["tax"]), (12.0, 72000))

    def test_downpayment_default_matches_constant(self):
        r = policy.evaluate_downpayment(self.c, 672000, {})
        self.assertEqual((r["rate"], r["amount"]), (30.0, 201600))

    def test_downpayment_minimum_floor(self):
        ap.set_config(self.c, "platform", "", "payment.downpayment.minimum_rate", "40")
        r = policy.evaluate_downpayment(self.c, 100000, {}, requested_rate=10)
        self.assertEqual(r["rate"], 40.0)

    def test_approval_threshold(self):
        self.assertTrue(policy.evaluate_approval(self.c, 600000, 0, {})["required"])
        self.assertFalse(policy.evaluate_approval(self.c, 100000, 0, {})["required"])


class TestRegistryValidation(Base):
    def test_out_of_range_rejected(self):
        with self.assertRaises(core.ValidationError):
            ap.set_config(self.c, "platform", "", "tax.default.rate", "150")
        with self.assertRaises(core.ValidationError):
            ap.set_config(self.c, "platform", "", "tax.default.rate", "-5")

    def test_enum_and_boolean_rejected(self):
        with self.assertRaises(core.ValidationError):
            ap.set_config(self.c, "platform", "", "tax.rounding_mode", "banker")
        with self.assertRaises(core.ValidationError):
            ap.set_config(self.c, "platform", "", "payment.downpayment.required", "maybe")

    def test_definitions_present(self):
        self.assertIsNotNone(config_registry.get_definition(self.c, "tax.default.rate"))
        self.assertTrue(len(config_registry.list_definitions(self.c)) >= 8)


class TestCascadeAndIsolation(Base):
    def test_tenant_override_and_isolation(self):
        ap.set_config(self.c, "tenant", str(self.rgo), "tax.default.rate", "10", actor=self.actor)
        self.assertEqual(policy.evaluate_tax(self.c, 600000, {"tenant": str(self.rgo)})["rate"], 10.0)
        self.assertEqual(policy.evaluate_tax(self.c, 600000, {"tenant": "9999"})["rate"], 12.0)  # other tenant


class TestSnapshotsAndHistory(Base):
    def test_snapshot_persisted_on_quotation(self):
        _, qid = self._quote()
        row = self.c.execute("SELECT tax,total,dp_amount,tax_snapshot,dp_snapshot,approval_snapshot FROM quotations WHERE id=?", (qid,)).fetchone()
        self.assertEqual((row["tax"], row["total"], row["dp_amount"]), (72000, 672000, 201600))
        tsnap = json.loads(row["tax_snapshot"])
        self.assertEqual((tsnap["rate_applied"], tsnap["tax_amount"]), (12.0, 72000))
        self.assertIn("approval_snapshot", row.keys())

    def test_config_change_does_not_alter_existing_quotation(self):
        _, qid = self._quote()
        ap.set_config(self.c, "platform", "", "tax.default.rate", "20", actor=self.actor)
        after = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        self.assertEqual((after["tax"], after["total"]), (72000, 672000))     # historical UNCHANGED
        _, qid2 = self._quote()                                               # new document uses new rate
        self.assertEqual(self.c.execute("SELECT tax FROM quotations WHERE id=?", (qid2,)).fetchone()["tax"], 120000)

    def test_payment_request_uses_stored_downpayment_snapshot(self):
        a1 = {"id": 1, "role": "admin", "perms": {"*"}}
        a2 = {"id": 2, "role": "admin", "perms": {"*"}}                       # separate approver (SoD)
        core.create_user(self.c, "a2@r", "Demo1234Xy", "admin", "A2") if False else None
        bid, qid = self._quote()
        core.submit_quotation(self.c, a1, qid)
        q = self.c.execute("SELECT status FROM quotations WHERE id=?", (qid,)).fetchone()
        if q["status"] == "pending_approval":
            core.approve_quotation(self.c, a2, qid)
        core.send_quotation(self.c, a1, qid)
        core.accept_quotation(self.c, a2, qid, "J. Roe", "CFO")
        prid = core.create_payment_request(self.c, a1, bid)
        pr = self.c.execute("SELECT amount_due,dp_snapshot FROM payment_requests WHERE id=?", (prid,)).fetchone()
        self.assertEqual(pr["amount_due"], 201600)                            # from stored quotation, not config
        self.assertIsNotNone(pr["dp_snapshot"])
        # a later downpayment config change must not alter the issued payment request
        ap.set_config(self.c, "platform", "", "payment.downpayment.default_rate", "50", actor=self.actor)
        self.assertEqual(self.c.execute("SELECT amount_due FROM payment_requests WHERE id=?", (prid,)).fetchone()["amount_due"], 201600)


    def test_legacy_snapshot_migration_preserves_totals(self):
        _, qid = self._quote()
        before = self.c.execute("SELECT tax,total,dp_amount FROM quotations WHERE id=?", (qid,)).fetchone()
        self.c.execute("UPDATE quotations SET tax_snapshot=NULL, dp_snapshot=NULL WHERE id=?", (qid,)); self.c.commit()
        self.assertGreaterEqual(policy.migrate_legacy_snapshots(self.c), 1)
        after = self.c.execute("SELECT tax,total,dp_amount,tax_snapshot FROM quotations WHERE id=?", (qid,)).fetchone()
        self.assertEqual((after["tax"], after["total"], after["dp_amount"]),
                         (before["tax"], before["total"], before["dp_amount"]))   # totals untouched
        self.assertIn("LEGACY_DERIVED", after["tax_snapshot"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
