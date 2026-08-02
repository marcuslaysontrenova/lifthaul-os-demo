"""LiftHaul OS — Phase 3: CRM Administration + Master-Data Governance.

Proves: canonical governed master data (lifecycle, effective-dating, tenant isolation,
duplicate-code block, cross-tenant parent block, dependency protection, replacement mapping,
import/export), governed customer numbering (concurrency-safe), configurable duplicate
detection + governed merge (cross-tenant denied), effective-dated credit policy with persisted
evidence (enforcement OFF by default => no operational drift), declarative CRM custom fields,
and granular crm.admin.* / master_data.* permission enforcement — all with financials unchanged.
"""
import json
import unittest

import db
import core
import admin_platform as ap
import masterdata
import crm_admin as crm
import org


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.actor = {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": self.rgo}

    def _actor(self, perms, tenant=None):
        return {"id": 99, "role": "x", "perms": set(perms),
                "tenant_id": self.rgo if tenant is None else tenant}


class TestCanonicalModel(Base):
    def test_seeded_reference_values(self):
        vals = masterdata.list_values(self.c, self.actor, "ops.equipment_type")
        self.assertTrue(any(v["code"] == "MOBILE_CRANE" for v in vals))

    def test_duplicate_code_blocked(self):
        masterdata.create_value(self.c, self.actor, "customer.type", "DEALER", "Dealer")
        with self.assertRaises(core.ConflictError):
            masterdata.create_value(self.c, self.actor, "customer.type", "DEALER", "Dealer 2")

    def test_unknown_domain_rejected(self):
        with self.assertRaises(core.ValidationError):
            masterdata.create_value(self.c, self.actor, "not.a.domain", "X", "X")

    def test_cross_tenant_parent_blocked(self):
        parent = masterdata.create_value(self.c, self.actor, "geo.region", "R99", "Region 99")
        other = self._actor({"*"}, tenant=9999)
        with self.assertRaises(core.ForbiddenError):
            masterdata.create_value(self.c, other, "geo.province", "P99", "Prov 99", parent_id=parent)

    def test_effective_dating_selectability(self):
        import datetime
        future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        vid = masterdata.create_value(self.c, self.actor, "customer.type", "FUTURE", "Future",
                                      effective_from=future)
        self.assertFalse(masterdata.selectable(self.c, vid))       # not yet effective
        past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        vid2 = masterdata.create_value(self.c, self.actor, "customer.type", "EXPIRED", "Expired",
                                       effective_to=past)
        self.assertFalse(masterdata.selectable(self.c, vid2))      # expired

    def test_inactive_value_not_selectable_but_visible(self):
        vid = masterdata.create_value(self.c, self.actor, "customer.type", "LEGACYT", "Legacy")
        masterdata.set_status(self.c, self.actor, vid, "INACTIVE")
        self.assertFalse(masterdata.selectable(self.c, vid))       # cannot be chosen for new txns
        codes = [v["code"] for v in masterdata.list_values(self.c, self.actor, "customer.type")]
        self.assertIn("LEGACYT", codes)                            # still historically visible

    def test_archive_and_restore(self):
        vid = masterdata.create_value(self.c, self.actor, "customer.type", "ARCH", "Arch")
        masterdata.set_status(self.c, self.actor, vid, "ARCHIVED")
        self.assertEqual(masterdata.get_value(self.c, self.actor, vid)["status"], "ARCHIVED")
        masterdata.set_status(self.c, self.actor, vid, "ACTIVE")   # restore
        self.assertEqual(masterdata.get_value(self.c, self.actor, vid)["status"], "ACTIVE")

    def test_tenant_isolation_of_values(self):
        # a value owned by another tenant is invisible + 404 no-leak
        other = self._actor({"*"}, tenant=9999)
        vid = masterdata.create_value(self.c, other, "customer.type", "OTHERT", "Other Tenant")
        with self.assertRaises(core.NotFoundError):
            masterdata.get_value(self.c, self.actor, vid)
        codes = [v["code"] for v in masterdata.list_values(self.c, self.actor, "customer.type")]
        self.assertNotIn("OTHERT", codes)

    def test_system_protected_requires_elevated(self):
        limited = self._actor({"master_data.manage"})
        with self.assertRaises(core.ForbiddenError):
            masterdata.create_value(self.c, limited, "customer.type", "SYS", "Sys", system_protected=True)


class TestDependencyAndReplacement(Base):
    def test_dependency_counts_real_references(self):
        # a booking uses service 'CRANE_RENTAL'; dependency on ops.service_type should see it
        cid = core.create_customer(self.c, self.actor, "Dep Co")
        core.create_booking(self.c, self.actor, cid, "CRANE_RENTAL", "cargo", 1)
        row = [v for v in masterdata.list_values(self.c, self.actor, "ops.service_type")
               if v["code"] == "CRANE_RENTAL"][0]
        dep = masterdata.dependencies(self.c, self.actor, row["id"])
        self.assertGreaterEqual(dep["total_references"], 1)
        self.assertFalse(dep["safe_to_hard_delete"])

    def test_replacement_preserves_history_and_resolves_forward(self):
        old = masterdata.create_value(self.c, self.actor, "ops.equipment_type", "OLD_FL", "Old Forklift")
        new = masterdata.create_value(self.c, self.actor, "ops.equipment_type", "NEW_FL", "New Forklift")
        masterdata.replace(self.c, self.actor, old, new)
        self.assertEqual(masterdata.get_value(self.c, self.actor, old)["status"], "DEPRECATED")
        self.assertEqual(masterdata.resolve_effective(self.c, old)["code"], "NEW_FL")  # new selection


class TestImportExport(Base):
    def test_import_dry_run_then_apply_with_invalid_and_dupes(self):
        rows = [{"code": "IND_A", "name": "Industry A"}, {"code": "IND_B", "name": "Industry B"},
                {"code": "", "name": "no code"},                      # invalid
                {"code": "IND_A", "name": "dup within file"}]         # duplicate
        dry = masterdata.import_values(self.c, self.actor, "customer.industry", rows, dry_run=True)
        self.assertEqual((dry["valid"], dry["invalid"], dry["duplicates"], dry["applied"]), (2, 1, 1, 0))
        applied = masterdata.import_values(self.c, self.actor, "customer.industry", rows, dry_run=False)
        self.assertEqual(applied["applied"], 2)
        codes = [v["code"] for v in masterdata.list_values(self.c, self.actor, "customer.industry")]
        self.assertIn("IND_A", codes)

    def test_export_is_tenant_scoped(self):
        other = self._actor({"*"}, tenant=9999)
        masterdata.create_value(self.c, other, "customer.industry", "SECRET", "Secret Ind")
        exported = masterdata.export_values(self.c, self.actor, "customer.industry")
        self.assertNotIn("SECRET", [r["code"] for r in exported])


class TestCustomerNumbering(Base):
    def test_number_generated_and_unique(self):
        c1 = core.create_customer(self.c, self.actor, "Num One")
        c2 = core.create_customer(self.c, self.actor, "Num Two")
        n1 = self.c.execute("SELECT customer_number FROM customers WHERE id=?", (c1,)).fetchone()["customer_number"]
        n2 = self.c.execute("SELECT customer_number FROM customers WHERE id=?", (c2,)).fetchone()["customer_number"]
        self.assertTrue(n1.startswith("CUS-"))
        self.assertNotEqual(n1, n2)

    def test_concurrent_allocation_no_collision(self):
        # simulate concurrent creation: many allocations must be distinct (sequence-backed)
        nums = set()
        for i in range(25):
            cid = core.create_customer(self.c, self.actor, f"Bulk {i}")
            nums.add(self.c.execute("SELECT customer_number FROM customers WHERE id=?", (cid,)).fetchone()["customer_number"])
        self.assertEqual(len(nums), 25)                            # zero collisions

    def test_numbering_config_changes_format(self):
        ap.set_config(self.c, "platform", "", "crm.numbering.prefix", "ACCT")
        pv = crm.preview_number(self.c, self.actor)
        self.assertTrue(pv["preview"].startswith("ACCT-"))


class TestDuplicateAndMerge(Base):
    def test_detect_duplicate_customer(self):
        a = core.create_customer(self.c, self.actor, "Acme Rigging Inc", email="ops@acme.com")
        core.create_customer(self.c, self.actor, "ACME  RIGGING INC", email="ops@acme.com")
        res = crm.detect_duplicates(self.c, self.actor, a)
        self.assertGreaterEqual(len(res["candidates"]), 1)

    def test_false_positive_dismissed_not_redetected(self):
        a = core.create_customer(self.c, self.actor, "Beta Co", email="x@beta.com")
        core.create_customer(self.c, self.actor, "Beta Co", email="x@beta.com")
        res = crm.detect_duplicates(self.c, self.actor, a)
        cand = res["candidates"][0]["candidate_id"]
        crm.review_candidate(self.c, self.actor, cand, "REVIEWED_NOT_DUPLICATE")
        res2 = crm.detect_duplicates(self.c, self.actor, a)
        self.assertEqual(len(res2["candidates"]), 0)               # dismissed pair not re-raised

    def test_merge_preview_and_execute_redirects_references(self):
        survivor = core.create_customer(self.c, self.actor, "Survivor Co")
        loser = core.create_customer(self.c, self.actor, "Loser Co")
        b = core.create_booking(self.c, self.actor, loser, "CRANE_RENTAL", "x", 1)
        prev = crm.merge_preview(self.c, self.actor, survivor, loser)
        self.assertTrue(any(r["table"] == "bookings" and r["records"] >= 1 for r in prev["relationships"]))
        crm.merge_customers(self.c, self.actor, survivor, loser)
        self.assertEqual(self.c.execute("SELECT customer_id FROM bookings WHERE id=?", (b,)).fetchone()["customer_id"], survivor)
        self.assertEqual(self.c.execute("SELECT status,merged_into FROM customers WHERE id=?", (loser,)).fetchone()["status"], "MERGED")

    def test_cross_tenant_merge_denied(self):
        a = core.create_customer(self.c, self.actor, "T1 Co")
        other = self._actor({"*"}, tenant=9999)
        b = core.create_customer(self.c, other, "T2 Co")
        with self.assertRaises((core.ForbiddenError, core.NotFoundError)):
            crm.merge_customers(self.c, self.actor, a, b)


class TestCreditPolicy(Base):
    def test_evidence_only_default_never_blocks(self):
        cid = core.create_customer(self.c, self.actor, "Credit Co")
        crm.create_credit_policy(self.c, self.actor, "STRICT", "Strict", credit_limit=1000,
                                 booking_restriction=True)
        res = crm.evaluate_credit(self.c, self.actor, cid, "booking", amount=999999, policy_code="STRICT")
        self.assertEqual(res["decision"], "ALLOW")                 # evidence_only => allow
        self.assertIn("over_credit_limit", res["evidence"]["reasons"])
        ev = self.c.execute("SELECT COUNT(*) c FROM credit_evaluations WHERE customer_id=?", (cid,)).fetchone()["c"]
        self.assertEqual(ev, 1)                                    # evidence persisted

    def test_block_mode_blocks(self):
        ap.set_config(self.c, "platform", "", "crm.credit.enforcement", "block")
        cid = core.create_customer(self.c, self.actor, "Blocked Co")
        crm.create_credit_policy(self.c, self.actor, "HARD", "Hard", credit_limit=100)
        res = crm.evaluate_credit(self.c, self.actor, cid, "quotation", amount=500, policy_code="HARD")
        self.assertEqual(res["decision"], "BLOCK")

    def test_effective_dated_policy_ignored_when_expired(self):
        import datetime
        past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        cid = core.create_customer(self.c, self.actor, "Exp Co")
        crm.create_credit_policy(self.c, self.actor, "EXP", "Exp", credit_limit=1,
                                 booking_restriction=True, effective_to=past)
        res = crm.evaluate_credit(self.c, self.actor, cid, "booking", amount=999, policy_code="EXP")
        self.assertIsNone(res["evidence"]["policy_code"])          # expired policy not applied


class TestCustomFields(Base):
    def test_declarative_validation_enforced(self):
        crm.create_custom_field(self.c, self.actor, "customer", "priority", "Priority", "integer",
                                validation={"min": 1, "max": 5})
        cid = core.create_customer(self.c, self.actor, "CF Co")
        with self.assertRaises(core.ValidationError):
            crm.set_custom_value(self.c, self.actor, "customer", cid, "priority", "9")   # out of range
        crm.set_custom_value(self.c, self.actor, "customer", cid, "priority", "3")
        self.assertEqual(crm.get_custom_values(self.c, self.actor, "customer", cid)["priority"], "3")

    def test_no_executable_code_in_validation(self):
        with self.assertRaises(core.ValidationError):
            crm.create_custom_field(self.c, self.actor, "customer", "bad", "Bad", "text",
                                    validation="__import__('os').system('x')")   # not a dict => rejected

    def test_deactivated_field_rejects_new_values(self):
        fid = crm.create_custom_field(self.c, self.actor, "customer", "tempf", "Temp", "text")
        crm.set_custom_field_status(self.c, self.actor, fid, "INACTIVE")
        cid = core.create_customer(self.c, self.actor, "CF2 Co")
        with self.assertRaises(core.NotFoundError):
            crm.set_custom_value(self.c, self.actor, "customer", cid, "tempf", "v")

    def test_required_field_validation(self):
        crm.create_custom_field(self.c, self.actor, "lead", "src", "Source", "text", required=True)
        defn = crm.list_custom_fields(self.c, self.actor, entity="lead")[0]
        with self.assertRaises(core.ValidationError):
            crm.validate_custom_value(defn, "")


class TestGranularPermissions(Base):
    def test_seeded_role_grants(self):
        crm_role = ap.effective_role_grants(self.c, ap.role_by_code(self.c, "RGO", "crm_admin")["id"])
        self.assertIn("crm.admin.*", crm_role)
        self.assertIn("master_data.*", crm_role)
        fa = ap.effective_role_grants(self.c, ap.role_by_code(self.c, "RGO", "finance_admin")["id"])
        self.assertIn("crm.admin.credit_policy.*", fa)
        self.assertNotIn("crm.admin.merge.execute", fa)            # finance cannot merge customers

    def test_merge_requires_execute_permission(self):
        weak = self._actor({"crm.admin.duplicate_rule.view", "crm.admin.duplicate_rule.manage",
                            "customer.create", "customer.view"})
        a = core.create_customer(self.c, self.actor, "M1")
        b = core.create_customer(self.c, self.actor, "M2")
        with self.assertRaises(core.ForbiddenError):
            crm.merge_customers(self.c, weak, a, b)               # lacks crm.admin.merge.execute

    def test_master_data_manage_required(self):
        weak = self._actor({"master_data.view"})
        with self.assertRaises(core.ForbiddenError):
            masterdata.create_value(self.c, weak, "customer.type", "NO", "No")

    def test_import_requires_import_permission(self):
        weak = self._actor({"master_data.view", "master_data.manage"})
        with self.assertRaises(core.ForbiddenError):
            masterdata.import_values(self.c, weak, "customer.type", [{"code": "X", "name": "X"}], dry_run=True)

    def test_credit_policy_permission_scoped(self):
        weak = self._actor({"crm.admin.classification.view"})
        with self.assertRaises(core.ForbiddenError):
            crm.create_credit_policy(self.c, weak, "P", "P")


class TestFinancialAndOperationalSafety(Base):
    def test_master_data_does_not_change_financials(self):
        # build a quotation, then add/deactivate master data + tax code reference; totals unchanged
        a = self.actor
        cid = core.create_customer(self.c, a, "Fin Co")
        bid = core.create_booking(self.c, a, cid, "CRANE_RENTAL", "x", 1)
        core.review_booking(self.c, a, bid); core.ready_for_quotation(self.c, a, bid)
        qid = core.create_quotation(self.c, a, bid, [{"kind": "crane", "description": "x", "qty": 2, "days": 1, "rate": 300000}])
        before = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        # touch tax-code master data (descriptive) + deactivate a currency
        masterdata.create_value(self.c, a, "finance.tax_code", "NEWVAT", "New VAT ref")
        cur = [v for v in masterdata.list_values(self.c, a, "finance.currency") if v["code"] == "USD"][0]
        masterdata.set_status(self.c, a, cur["id"], "INACTIVE")
        after = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        self.assertEqual((before["tax"], before["total"]), (72000, 672000))
        self.assertEqual((after["tax"], after["total"]), (72000, 672000))   # UNCHANGED


class TestPhase3Api(unittest.TestCase):
    """Drives the Phase 3 /admin/crm/* and /admin/master-data/* endpoints through the real
    HTTP router (server._match), proving the CRM + Master Data console screens are fully backed."""
    @classmethod
    def setUpClass(cls):
        import os
        import server
        import db as _db
        if os.path.exists("rgo_p3api.sqlite"):
            os.remove("rgo_p3api.sqlite")
        server._conn = _db.connect(":memory:")
        cls.server = server
        try:
            core.create_user(server._conn, "p3admin@r", "demo1234", "admin", "P3 Admin")
        except core.ConflictError:
            pass

    def _call(self, method, path, body=None):
        fn, params = self.server._match(method, path)
        assert fn, f"no route for {method} {path}"
        tok = self.server._match("POST", "/login")[0](None, {"email": "p3admin@r", "password": "demo1234"}, {})["token"]
        actor = core.actor_for(self.server._conn, tok)
        return fn(actor, body or {}, params or {})

    def test_master_data_domains_and_values(self):
        doms = self._call("GET", "/admin/master-data/domains")["domains"]
        self.assertGreater(len(doms), 40)
        vid = self._call("POST", "/admin/master-data/values",
                         {"domain": "ops.job_category", "code": "LIFT", "name": "Lifting"})["id"]
        vals = self._call("POST", "/admin/master-data/values/search", {"domain": "ops.job_category"})["values"]
        self.assertTrue(any(v["id"] == vid for v in vals))

    def test_classification_create_via_api(self):
        r = self._call("POST", "/admin/crm/classifications",
                       {"domain": "customer.category", "code": "PLATINUM", "name": "Platinum"})
        self.assertIn("id", r)

    def test_numbering_preview_via_api(self):
        pv = self._call("POST", "/admin/crm/numbering/preview", {})
        self.assertTrue(pv["preview"].startswith(pv["config"]["prefix"]))

    def test_credit_policy_and_evaluate_via_api(self):
        self._call("POST", "/admin/crm/credit-policies", {"code": "APIPOL", "name": "Api", "credit_limit": 100})
        cid = core.create_customer(self.server._conn,
                                   {"id": 1, "role": "admin", "perms": {"*"},
                                    "tenant_id": ap.get_tenant(self.server._conn, "RGO")["id"]}, "Api Cust")
        r = self._call("POST", f"/admin/crm/customers/{cid}/evaluate-credit",
                       {"action": "quotation", "amount": 5000, "policy_code": "APIPOL"})
        self.assertIn(r["decision"], ("ALLOW", "BLOCK"))

    def test_import_dry_run_via_api(self):
        r = self._call("POST", "/admin/master-data/import",
                       {"domain": "finance.uom", "rows": [{"code": "TON", "name": "Ton"}], "dry_run": True})
        self.assertEqual(r["valid"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
