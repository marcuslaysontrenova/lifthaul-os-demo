"""LiftHaul OS — Tenant backfill tests (Phase 1 #2-7).

Proves the backfill is safe and additive: legacy rows get tenant_id=RGO (DETERMINISTIC,
single-tenant history), organization scope is queued for remediation (never auto-assigned),
execution is idempotent, no non-tenant column changes, and the enforcement guard works.
"""
import unittest

import catalog
import core
import admin_platform as ap
import org
import backfill


class Base(unittest.TestCase):
    def setUp(self):
        self.c = catalog.connect_full(":memory:")     # full operational schema
        ap.init(self.c); ap.seed(self.c); org.init(self.c); backfill.init(self.c)
        self.actor = {"id": 1, "role": "admin"}
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.cid = core.create_customer(self.c, self.actor, "Acme Co", "contact", "a@co")
        self.bid = core.create_booking(self.c, self.actor, self.cid, "Crane", "Transformer", 40)


class TestBackfill(Base):
    def test_add_columns_idempotent(self):
        backfill.add_tenant_columns(self.c)
        backfill.add_tenant_columns(self.c)               # second run must not error
        self.assertTrue(backfill._has_tenant_col(self.c, "customers"))
        self.assertTrue(backfill._has_tenant_col(self.c, "bookings"))

    def test_analyze_reports_unassigned_deterministic(self):
        rep = backfill.analyze(self.c)
        cust = next(t for t in rep["tables"] if t["table"] == "customers")
        self.assertGreaterEqual(cust["total"], 1)
        self.assertEqual(cust["tenant_classification"], "DETERMINISTIC")
        bk = next(t for t in rep["tables"] if t["table"] == "bookings")
        self.assertEqual(bk["org_classification"], "AMBIGUOUS")   # branch cannot be inferred

    def test_dry_run_writes_nothing(self):
        d = backfill.dry_run(self.c)
        self.assertEqual(d["writes"], 0)
        self.assertGreaterEqual(d["planned_tenant_updates"], 2)   # customer + booking at least
        # confirm nothing was actually assigned
        backfill.add_tenant_columns(self.c)
        self.assertIsNone(self.c.execute("SELECT tenant_id FROM customers WHERE id=?", (self.cid,)).fetchone()["tenant_id"])

    def test_execute_assigns_tenant_and_queues_org(self):
        res = backfill.execute(self.c, self.actor)
        self.assertEqual(self.c.execute("SELECT tenant_id FROM customers WHERE id=?", (self.cid,)).fetchone()["tenant_id"], self.rgo)
        self.assertEqual(self.c.execute("SELECT tenant_id FROM bookings WHERE id=?", (self.bid,)).fetchone()["tenant_id"], self.rgo)
        self.assertIn("bookings", {r["table"] for r in res["remediation_queued"]})   # org scope queued
        # bookings has NO auto-assigned branch — org scope stays unresolved
        q = self.c.execute("SELECT * FROM org_backfill_remediation WHERE table_name='bookings'").fetchone()
        self.assertEqual(q["classification"], "AMBIGUOUS")
        self.assertEqual(q["status"], "OPEN")

    def test_execute_is_idempotent(self):
        backfill.execute(self.c, self.actor)
        res2 = backfill.execute(self.c, self.actor)
        self.assertEqual(res2["updated"], {})                     # nothing left unassigned
        # and no duplicate remediation rows
        n = self.c.execute("SELECT COUNT(*) c FROM org_backfill_remediation WHERE table_name='bookings'").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_no_non_tenant_column_changed(self):
        before = dict(self.c.execute("SELECT * FROM customers WHERE id=?", (self.cid,)).fetchone())
        backfill.execute(self.c, self.actor)
        after = dict(self.c.execute("SELECT * FROM customers WHERE id=?", (self.cid,)).fetchone())
        for k in before:
            if k != "tenant_id":
                self.assertEqual(before[k], after[k], f"non-tenant column {k} changed")

    def test_status_and_audit(self):
        backfill.execute(self.c, self.actor)
        st = backfill.status(self.c)
        self.assertTrue(st["tenant_enforced"])                    # no unassigned rows remain
        self.assertTrue(any(r["table_name"] == "bookings" for r in st["open_remediation"]))
        actions = {a["action"] for a in self.c.execute("SELECT action FROM audit_logs").fetchall()}
        self.assertIn("TENANT_BACKFILL_EXECUTED", actions)

    def test_remediation_resolution(self):
        backfill.execute(self.c, self.actor)
        rid = self.c.execute("SELECT id FROM org_backfill_remediation WHERE table_name='bookings'").fetchone()["id"]
        backfill.resolve_remediation(self.c, self.actor, rid)
        self.assertEqual(self.c.execute("SELECT status FROM org_backfill_remediation WHERE id=?", (rid,)).fetchone()["status"], "RESOLVED")

    def test_enforcement_guard(self):
        at = backfill.actor_tenant(self.c, self.actor)
        backfill.assert_in_tenant(at, at)                         # same tenant OK
        with self.assertRaises(core.ForbiddenError):
            backfill.assert_in_tenant(9999, at)                   # foreign tenant denied


if __name__ == "__main__":
    unittest.main(verbosity=2)
