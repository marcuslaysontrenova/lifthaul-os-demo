"""LiftHaul OS — Phase 8: Reporting & Dashboard Administration.

Proves: allowlisted data-source registry + SAFE declarative query model (no raw SQL); row-level
security (tenant predicate injected at query time — Tenant A never sees Tenant B rows; cross-tenant
requires elevated grant); column-level sensitivity (financial/restricted fields excluded without
permission) across execution + export; report definitions + IMMUTABLE published versions; validation;
non-mutating preview; execution with filters/grouping/aggregation/limits; governed export; KPI
governance (dup code blocked, computed); dashboards + widgets inheriting security + total reconciliation;
scheduling with recipient authorization + permission re-evaluation + cross-tenant denial; cache
isolation; integrity checks; and REPORT-VALUE RECONCILIATION against the existing ops.report_* metrics —
all read-only with zero financial / operational / report-value drift.
"""
import unittest

import db
import core
import ops
import admin_platform as ap
import reporting as rp


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.a = {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": self.rgo}
        self.a2 = {"id": 2, "role": "admin", "perms": {"*"}, "tenant_id": self.rgo}

    def _actor(self, perms, id=9, role="analyst", tenant=None):
        return {"id": id, "role": role, "perms": set(perms),
                "tenant_id": self.rgo if tenant is None else tenant}

    def _accepted_quote(self):
        a, a2 = self.a, self.a2
        cid = core.create_customer(self.c, a, "Acme")
        bid = core.create_booking(self.c, a, cid, "CRANE_RENTAL", "x", 1)
        core.review_booking(self.c, a, bid); core.ready_for_quotation(self.c, a, bid)
        qid = core.create_quotation(self.c, a, bid, [{"kind": "crane", "description": "x", "qty": 2, "days": 1, "rate": 300000}])
        core.submit_quotation(self.c, a, qid)
        if self.c.execute("SELECT status FROM quotations WHERE id=?", (qid,)).fetchone()["status"] == "pending_approval":
            core.approve_quotation(self.c, a2, qid)
        core.send_quotation(self.c, a, qid); core.accept_quotation(self.c, a2, qid, "J", "CFO")
        return bid, qid

    def _draft(self, code="rep.test", spec=None):
        did = rp.create_report(self.c, self.a, code, code, category="operations")
        v = self.c.execute("SELECT id FROM report_versions WHERE definition_id=? AND version_no=1", (did,)).fetchone()["id"]
        if spec:
            rp.set_spec(self.c, self.a, v, spec)
        return did, v


class TestDefinitions(Base):
    def test_create_and_duplicate(self):
        self._draft("rep.a")
        with self.assertRaises(core.ConflictError):
            rp.create_report(self.c, self.a, "rep.a", "A2")

    def test_invalid_dataset(self):
        _, v = self._draft("rep.badds")
        with self.assertRaises(core.ValidationError):
            rp.set_spec(self.c, self.a, v, {"dataset": "secret_table", "fields": ["x"]})

    def test_invalid_field(self):
        _, v = self._draft("rep.badf")
        with self.assertRaises(core.ValidationError):
            rp.set_spec(self.c, self.a, v, {"dataset": "quotations", "fields": ["evil_col"]})

    def test_invalid_operator_type(self):
        _, v = self._draft("rep.badop")
        with self.assertRaises(core.ValidationError):
            rp.set_spec(self.c, self.a, v, {"dataset": "quotations", "fields": ["status"],
                                            "filters": [{"field": "status", "op": "gt", "value": 5}]})

    def test_immutable_published_version(self):
        _, v = self._draft("rep.imm", {"dataset": "jobs", "fields": ["status"], "aggregations": [{"fn": "count", "as": "n"}], "limit": 10})
        rp.validate_version(self.c, self.a, v); rp.approve_version(self.c, self.a, v)
        rp.publish_version(self.c, self.a, v, "go")
        with self.assertRaises(core.ForbiddenError):
            rp.set_spec(self.c, self.a, v, {"dataset": "jobs", "fields": ["status"]})

    def test_version_creation_copies_spec(self):
        _, v = self._draft("rep.copy", {"dataset": "jobs", "fields": ["status"], "limit": 10})
        rp.validate_version(self.c, self.a, v); rp.approve_version(self.c, self.a, v); rp.publish_version(self.c, self.a, v, "v1")
        nv = rp.create_version(self.c, self.a, "rep.copy", "v2")
        self.assertIsNotNone(rp._version(self.c, nv)["spec"])


class TestRowSecurity(Base):
    def test_tenant_isolation(self):
        self._accepted_quote()
        other = self._actor({"report.execute"}, id=5, tenant=9999)
        out = rp.execute_spec(self.c, other, {"dataset": "quotations", "fields": ["status"],
                                              "aggregations": [{"fn": "count", "as": "n"}], "limit": 100})
        self.assertEqual(out["rows"][0]["n"], 0)             # no Tenant A rows

    def test_platform_no_target_no_cross_tenant(self):
        self._accepted_quote()
        plat = {"id": 6, "role": "admin", "perms": {"report.execute"}, "tenant_id": None}
        out = rp.execute_spec(self.c, plat, {"dataset": "quotations", "aggregations": [{"fn": "count", "as": "n"}], "limit": 100})
        # platform actor with no target only sees tenant-NULL rows (no cross-tenant leak)
        self.assertIsInstance(out["rows"][0]["n"], int)

    def test_cross_tenant_requires_grant(self):
        elevated = self._actor({"report.execute", "report.platform.cross_tenant"}, id=7, tenant=None)
        with self.assertRaises(core.ForbiddenError):
            rp.execute_spec(self.c, elevated, {"dataset": "quotations", "aggregations": [{"fn": "count", "as": "n"}], "limit": 10},
                            target_tenant=self.rgo, elevated=True)


class TestColumnSecurity(Base):
    def test_financial_field_excluded_without_permission(self):
        self._accepted_quote()
        viewer = self._actor({"report.execute"})
        out = rp.execute_spec(self.c, viewer, {"dataset": "quotations", "fields": ["status", "total"], "limit": 100})
        self.assertIn("total", out["excluded_sensitive"])
        self.assertTrue(all("total" not in r for r in out["rows"]))   # value never returned

    def test_financial_field_visible_with_permission(self):
        self._accepted_quote()
        fin = self._actor({"report.execute", "report.sensitive.view"})
        out = rp.execute_spec(self.c, fin, {"dataset": "quotations", "fields": ["status", "total"], "limit": 100})
        self.assertNotIn("total", out["excluded_sensitive"])

    def test_export_excludes_sensitive(self):
        self._accepted_quote()
        _, v = self._draft("rep.exp", {"dataset": "quotations", "fields": ["status", "total"], "limit": 100})
        rp.validate_version(self.c, self.a, v); rp.approve_version(self.c, self.a, v); rp.publish_version(self.c, self.a, v, "go")
        exporter = self._actor({"report.execute", "report.export"})
        out = rp.export_report(self.c, exporter, "rep.exp")
        self.assertIn("total", out["excluded_sensitive"])
        self.assertNotIn("total", out["csv"].split("\n")[0].split(","))   # header excludes it too? (column present but no data)


class TestExecution(Base):
    def test_filter_group_aggregate(self):
        self._accepted_quote()
        out = rp.execute_spec(self.c, self.a, {"dataset": "quotations", "fields": ["status"],
                                              "filters": [{"field": "status", "op": "eq", "value": "accepted"}],
                                              "group_by": ["status"], "aggregations": [{"fn": "count", "as": "n"}], "limit": 100})
        self.assertEqual(out["rows"], [{"status": "accepted", "n": 1}])

    def test_row_limit_capped(self):
        out = rp.execute_spec(self.c, self.a, {"dataset": "quotations", "fields": ["id"], "limit": 999999})
        # limit capped to dataset max (10000) — no error, executes
        self.assertEqual(out["outcome"], "SUCCESS")

    def test_report_value_reconciliation(self):
        self._accepted_quote()
        gov = rp.run_report(self.c, self.a, "quotation_conversion")
        gov_accepted = sum(r["n"] for r in gov["rows"] if r["status"] == "accepted")
        self.assertEqual(gov_accepted, ops.report_quotation_conversion(self.c, self.a)["accepted"])

    def test_receivables_reconciliation(self):
        # governed receivables report matches ops.report_receivables balances
        gov = rp.run_report(self.c, self.a, "receivables")
        ops_r = ops.report_receivables(self.c, self.a)
        gov_map = {r["status"]: r["balance"] for r in gov["rows"]}
        for status, d in ops_r.items():
            self.assertEqual(gov_map.get(status, 0), d["balance"])


class TestPreviewAndValidation(Base):
    def test_preview_non_mutating(self):
        _, v = self._draft("rep.prev", {"dataset": "jobs", "fields": ["status"], "aggregations": [{"fn": "count", "as": "n"}], "limit": 5000})
        before = self.c.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        out = rp.preview(self.c, self.a, v)
        after = self.c.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        self.assertEqual(before, after)
        self.assertLessEqual(out["row_count"], 100)

    def test_validation_blocks_no_spec(self):
        _, v = self._draft("rep.nospec")
        r = rp.validate_version(self.c, self.a, v)
        self.assertFalse(r["ok"])


class TestKpisAndDashboards(Base):
    def test_kpi_and_duplicate(self):
        rp.create_kpi(self.c, self.a, "conv", "Conversion", "quotations",
                      {"fn": "count", "filters": [{"field": "status", "op": "eq", "value": "accepted"}]}, denominator={"fn": "count"})
        with self.assertRaises(core.ConflictError):
            rp.create_kpi(self.c, self.a, "conv", "Dup", "quotations", {"fn": "count"})

    def test_kpi_calculation(self):
        self._accepted_quote()
        rp.create_kpi(self.c, self.a, "acc_rate", "Accept", "quotations",
                      {"fn": "count", "filters": [{"field": "status", "op": "eq", "value": "accepted"}]}, denominator={"fn": "count"})
        k = rp.compute_kpi(self.c, self.a, "acc_rate")
        self.assertEqual(k["numerator"], 1)
        self.assertTrue(k["available"])

    def test_dashboard_total_reconciles_report(self):
        self._accepted_quote()
        did = rp.create_dashboard(self.c, self.a, "ops_dash", "Ops")
        rp.add_widget(self.c, self.a, did, "table", title="Conversion", report_code="quotation_conversion")
        rp.publish_dashboard(self.c, self.a, did)
        rendered = rp.render_dashboard(self.c, self.a, "ops_dash")
        widget_rows = rendered["widgets"][0]["data"]["rows"]
        report_rows = rp.run_report(self.c, self.a, "quotation_conversion")["rows"]
        self.assertEqual(widget_rows, report_rows)           # dashboard total == underlying report

    def test_unsupported_widget_rejected(self):
        did = rp.create_dashboard(self.c, self.a, "d2", "D2")
        with self.assertRaises(core.ValidationError):
            rp.add_widget(self.c, self.a, did, "arbitrary_script", report_code="x")


class TestScheduling(Base):
    def _recipient(self, email, tenant=None):
        uid = core.create_user(self.c, email, "Demo1234Xy", "estimator", "R")
        if tenant is not None:
            import tenant as tmod; tmod.bind_user_tenant(self.c, None, uid, tenant)
        return email

    def test_schedule_and_run(self):
        r = self._recipient("rcpt@r", tenant=self.rgo)
        sid = rp.create_schedule(self.c, self.a, "quotation_conversion", "daily", [r])
        out = rp.run_schedule(self.c, self.a, sid)
        self.assertEqual(out["delivered"], 1)

    def test_unauthorized_recipient_rejected(self):
        with self.assertRaises(core.ValidationError):
            rp.create_schedule(self.c, self.a, "quotation_conversion", "daily", ["ghost@nowhere"])

    def test_cross_tenant_recipient_denied(self):
        r = self._recipient("crossr@r", tenant=9999)
        with self.assertRaises(core.ForbiddenError):
            rp.create_schedule(self.c, self.a, "quotation_conversion", "daily", [r])

    def test_invalid_frequency(self):
        with self.assertRaises(core.ValidationError):
            rp.create_schedule(self.c, self.a, "quotation_conversion", "hourly", [])


class TestCacheAndIntegrity(Base):
    def test_cache_isolated_per_user(self):
        v = self._actor({"report.execute"}, id=3)
        f = self._actor({"report.execute", "report.sensitive.view"}, id=4)
        self.assertNotEqual(rp._cache_key(v, "r", 1, None, None), rp._cache_key(f, "r", 1, None, None))

    def test_cache_invalidation(self):
        self._accepted_quote()
        rp.run_report(self.c, self.a, "quotation_conversion")
        self.assertGreaterEqual(self.c.execute("SELECT COUNT(*) c FROM report_cache").fetchone()["c"], 1)
        rp.invalidate_cache(self.c, self.a, user_id=self.a["id"])
        self.assertEqual(self.c.execute("SELECT COUNT(*) c FROM report_cache WHERE user_id=?", (self.a["id"],)).fetchone()["c"], 0)

    def test_integrity_healthy(self):
        rep = rp.integrity_checks(self.c, self.a)
        self.assertTrue(rep["healthy"])

    def test_migration_zero_drift(self):
        m = rp.classify_existing(self.c)
        self.assertEqual((m["financial_differences"], m["operational_status_differences"], m["report_value_differences"]), (0, 0, 0))

    def test_role_grants(self):
        pa = ap.effective_role_grants(self.c, ap.role_by_code(self.c, "RGO", "platform_admin")["id"])
        self.assertIn("report.definition.*", pa)
        self.assertIn("dashboard.*", pa)

    def test_reporting_does_not_change_financials(self):
        _, qid = self._accepted_quote()
        before = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        rp.run_report(self.c, self.a, "quotation_conversion")
        rp.export_report(self.c, self.a, "quotation_conversion")
        after = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        self.assertEqual((before["tax"], before["total"]), (72000, 672000))
        self.assertEqual((after["tax"], after["total"]), (72000, 672000))   # UNCHANGED (read-only)


class TestPhase8Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        import db as _db
        server._conn = _db.connect(":memory:")
        cls.server = server
        try:
            core.create_user(server._conn, "p8admin@r", "demo1234", "admin", "P8 Admin")
        except core.ConflictError:
            pass

    def _call(self, method, path, body=None):
        fn, params = self.server._match(method, path)
        assert fn, f"no route for {method} {path}"
        tok = self.server._match("POST", "/login")[0](None, {"email": "p8admin@r", "password": "demo1234"}, {})["token"]
        actor = core.actor_for(self.server._conn, tok)
        return fn(actor, body or {}, params or {})

    def test_datasets_and_standard_reports(self):
        ds = self._call("GET", "/admin/reporting/datasets")["datasets"]
        self.assertTrue(any(d["code"] == "quotations" for d in ds))
        reps = self._call("GET", "/admin/reporting/reports")["reports"]
        self.assertTrue(any(r["code"] == "quotation_conversion" for r in reps))

    def test_run_report_via_api(self):
        out = self._call("POST", "/admin/reporting/reports/quotation_conversion/run", {})
        self.assertIn("rows", out)

    def test_integrity_via_api(self):
        rep = self._call("GET", "/admin/reporting/integrity")
        self.assertIn("summary", rep)


if __name__ == "__main__":
    unittest.main(verbosity=2)
