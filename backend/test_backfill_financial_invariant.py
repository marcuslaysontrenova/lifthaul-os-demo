"""Tenant backfill — VERIFY mode + financial invariant (Item 3).

Proves the four-mode contract (ANALYZE / DRY_RUN / APPLY / VERIFY) and, critically, that
a backfill run NEVER alters a financial value: the pre-run financial fingerprint must equal
the post-run fingerprint. Backfill only ever writes tenant_id, so this is byte-identical.
"""
import unittest

import admin_platform
import backfill
import core
import db


class BackfillFinancialInvariantTests(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.sup = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
        # a real priced quotation so there ARE financial values to protect
        enc_id = admin_platform.create_user(self.c, self.sup, "e@bf", "Demo1234Xy",
                                            "booking_quotation_administrator", "E")
        core.create_user(self.c, "a@bf", "pw", "admin", "A")
        self.adm = core.actor_for(self.c, core.login(self.c, "a@bf", "pw"))
        self.enc = admin_platform.apply_rbac(self.c, core.actor_for(self.c, core.login(self.c, "e@bf", "Demo1234Xy")))
        cust = core.create_customer(self.c, self.adm, "ClientCo")
        bid = core.create_booking(self.c, self.enc, cust, "Crane", "Load", 40)
        core.review_booking(self.c, self.enc, bid)
        core.ready_for_quotation(self.c, self.enc, bid)
        core.create_quotation(self.c, self.enc, bid,
                              [{"equipment_code": "CRANE-100T", "qty": 1, "days": 3,
                                "quoted_rate": 58000, "override_reason": "premium"}])

    def test_analyze_dry_run_are_read_only(self):
        before = backfill.financial_fingerprint(self.c)
        backfill.analyze(self.c)
        backfill.dry_run(self.c)
        after = backfill.financial_fingerprint(self.c)
        self.assertEqual(before["sha256"], after["sha256"])       # no writes at all
        self.assertGreater(before["rows"], 0)

    def test_execute_preserves_financial_values_and_verify_passes(self):
        before = backfill.financial_fingerprint(self.c)
        # snapshot the exact quotation totals
        q_before = self.c.execute("SELECT total,tax,dp_amount,est_cost,margin_pct FROM quotations").fetchall()
        backfill.execute(self.c, self.adm)                         # APPLY — assigns tenant_id only
        q_after = self.c.execute("SELECT total,tax,dp_amount,est_cost,margin_pct FROM quotations").fetchall()
        self.assertEqual([tuple(r) for r in q_before], [tuple(r) for r in q_after])  # unchanged
        report = backfill.verify(self.c, financial_before=before)
        self.assertTrue(report["financial_ok"])                   # FINANCIAL INVARIANT held
        self.assertTrue(report["verified"])
        self.assertEqual(report["null_tenant_rows"], {})          # all rows tenant-assigned
        self.assertEqual(report["cross_tenant_orphans"], 0)

    def test_verify_flags_financial_drift(self):
        before = backfill.financial_fingerprint(self.c)
        backfill.execute(self.c, self.adm)
        # simulate an illegitimate financial mutation → verify must FAIL
        self.c.execute("UPDATE quotations SET total = total + 1")
        self.c.commit()
        report = backfill.verify(self.c, financial_before=before)
        self.assertFalse(report["financial_ok"])
        self.assertFalse(report["verified"])
        self.assertIn("FAIL", report["conclusion"])


if __name__ == "__main__":
    unittest.main()
