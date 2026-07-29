"""LiftHaul OS — Organization Hierarchy tests (C-004).

Covers the C-004 §13 test list: creation, tenant isolation, duplicate-code, circular +
cross-tenant + inactive-parent prevention, re-parenting, effective dates, primary/temporary
assignments, org-scoped authorization (branch/cost-center/site boundaries), inactive
assignment denial, calendar inheritance/override, config cascade extension, archive/restore,
dependency protection, and audit completeness. (PostgreSQL portability is guarded by
test_pg_portability, which now includes org.SCHEMA.)
"""
import datetime
import unittest

import core
import admin_platform as ap
import org


class Base(unittest.TestCase):
    def setUp(self):
        self.c = core.connect(":memory:")
        self.c.executescript(core.SCHEMA)
        self.c.commit()
        ap.init(self.c); ap.seed(self.c)
        org.init(self.c)
        self.actor = {"id": 1, "role": "admin"}
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.acme = ap.create_tenant(self.c, "ACME", "ACME Cranes Inc", actor=self.actor)

    def _user(self, email, admin_role):
        """User with EXACTLY one admin/operational role assigned (clean scope for authz)."""
        uid = core.create_user(self.c, email, "Demo1234Xy", "estimator", "U")
        r = ap.role_by_code(self.c, "RGO", admin_role)
        ap.assign_role(self.c, uid, r["id"])
        return uid


class TestCreationAndGraphRules(Base):
    def test_create_units(self):
        bu = org.create_business_unit(self.c, self.actor, self.rgo, "BU1", "Cranes BU")
        br = org.create_branch(self.c, self.actor, self.rgo, "MNL", "Manila Branch", parent_id=bu)
        dep = org.create_department(self.c, self.actor, self.rgo, "OPS", "Operations", parent_id=br)
        self.assertEqual(org.get_unit(self.c, dep)["parent_id"], br)
        self.assertEqual({n["code"] for n in org.children(self.c, br)}, {"OPS"})

    def test_tenant_isolation(self):
        org.create_branch(self.c, self.actor, self.rgo, "MNL", "Manila")
        org.create_branch(self.c, self.actor, self.acme, "MNL", "Manila")  # same code, other tenant OK
        rgo_codes = {u["code"] for u in org.list_units(self.c, self.rgo)}
        self.assertEqual(len(org.list_units(self.c, self.rgo, kind="branch")), 1)
        self.assertNotIn(self.acme, {u["tenant_id"] for u in org.list_units(self.c, self.rgo)})

    def test_duplicate_code_prevented(self):
        org.create_branch(self.c, self.actor, self.rgo, "MNL", "Manila")
        with self.assertRaises(core.ConflictError):
            org.create_branch(self.c, self.actor, self.rgo, "MNL", "Dup")

    def test_cross_tenant_parent_denied(self):
        acme_bu = org.create_business_unit(self.c, self.actor, self.acme, "ABU", "ACME BU")
        with self.assertRaises(core.ForbiddenError):
            org.create_branch(self.c, self.actor, self.rgo, "X", "X", parent_id=acme_bu)

    def test_inactive_parent_denied(self):
        bu = org.create_business_unit(self.c, self.actor, self.rgo, "BU1", "BU")
        org.deactivate_unit(self.c, self.actor, bu)
        with self.assertRaises(core.ValidationError):
            org.create_branch(self.c, self.actor, self.rgo, "B", "B", parent_id=bu)

    def test_reparenting(self):
        br1 = org.create_branch(self.c, self.actor, self.rgo, "B1", "B1")
        br2 = org.create_branch(self.c, self.actor, self.rgo, "B2", "B2")
        dep = org.create_department(self.c, self.actor, self.rgo, "D", "D", parent_id=br1)
        org.reparent(self.c, self.actor, dep, br2)
        self.assertEqual(org.get_unit(self.c, dep)["parent_id"], br2)

    def test_circular_hierarchy_prevented(self):
        a = org.create_business_unit(self.c, self.actor, self.rgo, "A", "A")
        b = org.create_branch(self.c, self.actor, self.rgo, "B", "B", parent_id=a)
        with self.assertRaises(core.ValidationError):
            org.reparent(self.c, self.actor, a, b)          # a under its own descendant

    def test_effective_date_validation(self):
        with self.assertRaises(core.ValidationError):
            org.create_branch(self.c, self.actor, self.rgo, "B", "B",
                              effective_from="2026-12-01", effective_to="2026-01-01")

    def test_archive_restore_and_dependency_protection(self):
        br = org.create_branch(self.c, self.actor, self.rgo, "B", "B")
        dep = org.create_department(self.c, self.actor, self.rgo, "D", "D", parent_id=br)
        with self.assertRaises(core.ConflictError):
            org.archive_unit(self.c, self.actor, br)        # has an active child
        with self.assertRaises(core.ConflictError):
            org.delete_unit(self.c, self.actor, br)         # has children
        org.archive_unit(self.c, self.actor, dep)
        self.assertEqual(org.get_unit(self.c, dep)["status"], "ARCHIVED")
        org.restore_unit(self.c, self.actor, dep)
        self.assertEqual(org.get_unit(self.c, dep)["status"], "ACTIVE")


class TestUserAssignments(Base):
    def test_primary_uniqueness_and_secondary_allowed(self):
        u = self._user("a@rgo.demo", "estimator")
        b1 = org.create_branch(self.c, self.actor, self.rgo, "B1", "B1")
        b2 = org.create_branch(self.c, self.actor, self.rgo, "B2", "B2")
        org.assign_user(self.c, self.actor, self.rgo, u, "branch", b1, "PRIMARY")
        with self.assertRaises(core.ConflictError):
            org.assign_user(self.c, self.actor, self.rgo, u, "branch", b2, "PRIMARY")
        org.assign_user(self.c, self.actor, self.rgo, u, "branch", b2, "SECONDARY")  # ok

    def test_cannot_assign_to_inactive_unit(self):
        u = self._user("b@rgo.demo", "estimator")
        b = org.create_branch(self.c, self.actor, self.rgo, "B", "B")
        org.deactivate_unit(self.c, self.actor, b)
        with self.assertRaises(core.ConflictError):
            org.assign_user(self.c, self.actor, self.rgo, u, "branch", b, "PRIMARY")

    def test_temporary_assignment_effective_dates(self):
        u = self._user("t@rgo.demo", "estimator")
        b = org.create_branch(self.c, self.actor, self.rgo, "B", "B")
        today = datetime.date.today().isoformat()
        org.assign_user(self.c, self.actor, self.rgo, u, "branch", b, "TEMPORARY",
                        effective_from=today, effective_to=today)
        self.assertIn(b, org.governed_unit_ids(self.c, u))


class TestOrgScopedAuthorization(Base):
    def test_branch_admin_boundary(self):
        u = self._user("ba@rgo.demo", "business_admin")     # grants user_admin.*
        a = org.create_branch(self.c, self.actor, self.rgo, "A", "A")
        b = org.create_branch(self.c, self.actor, self.rgo, "B", "B")
        dep = org.create_department(self.c, self.actor, self.rgo, "D", "D", parent_id=a)
        org.assign_user(self.c, self.actor, self.rgo, u, "branch", a, "PRIMARY")
        self.assertTrue(org.authorize(self.c, u, "user_admin.manage", "branch", a))
        self.assertTrue(org.authorize(self.c, u, "user_admin.manage", "department", dep))  # descendant
        self.assertFalse(org.authorize(self.c, u, "user_admin.manage", "branch", b))

    def test_finance_cost_center_boundary(self):
        u = self._user("fin@rgo.demo", "finance_admin")     # grants expense.*
        cc1 = org.create_cost_center(self.c, self.actor, self.rgo, "CC1", "CC1")
        cc2 = org.create_cost_center(self.c, self.actor, self.rgo, "CC2", "CC2")
        org.assign_user(self.c, self.actor, self.rgo, u, "cost_center", cc1, "PRIMARY")
        self.assertTrue(org.authorize(self.c, u, "expense.view", "cost_center", cc1))
        self.assertFalse(org.authorize(self.c, u, "expense.view", "cost_center", cc2))

    def test_dispatcher_site_boundary(self):
        u = self._user("disp@rgo.demo", "dispatcher")       # grants job.dispatch
        s1 = org.create_operating_site(self.c, self.actor, self.rgo, "S1", "Site 1")
        s2 = org.create_operating_site(self.c, self.actor, self.rgo, "S2", "Site 2")
        org.assign_user(self.c, self.actor, self.rgo, u, "operating_site", s1, "PRIMARY")
        self.assertTrue(org.authorize(self.c, u, "job.dispatch", "operating_site", s1))
        self.assertFalse(org.authorize(self.c, u, "job.dispatch", "operating_site", s2))

    def test_permission_missing_denies_regardless_of_scope(self):
        u = self._user("d@rgo.demo", "dispatcher")
        b = org.create_branch(self.c, self.actor, self.rgo, "B", "B")
        org.assign_user(self.c, self.actor, self.rgo, u, "branch", b, "PRIMARY")
        self.assertFalse(org.authorize(self.c, u, "user_admin.manage", "branch", b))

    def test_inactive_assignment_denial(self):
        u = self._user("ia@rgo.demo", "business_admin")
        b = org.create_branch(self.c, self.actor, self.rgo, "B", "B")
        aid = org.assign_user(self.c, self.actor, self.rgo, u, "branch", b, "PRIMARY")
        self.assertTrue(org.authorize(self.c, u, "user_admin.manage", "branch", b))
        org.remove_assignment(self.c, self.actor, aid)
        self.assertFalse(org.authorize(self.c, u, "user_admin.manage", "branch", b))


class TestCalendars(Base):
    def test_holiday_inheritance(self):
        country = org.create_holiday_calendar(self.c, self.actor, self.rgo, "PH", "Philippines")
        company = org.create_holiday_calendar(self.c, self.actor, self.rgo, "CO", "Company",
                                              parent_id=country)
        org.add_holiday(self.c, self.actor, country, "Independence Day", "2026-06-12")
        org.add_holiday(self.c, self.actor, company, "Founders Day", "2026-09-01")
        days = {h["day"] for h in org.effective_holidays(self.c, company)}
        self.assertEqual(days, {"2026-06-12", "2026-09-01"})   # inherited + own

    def test_working_calendar_override(self):
        base = org.create_working_calendar(self.c, self.actor, self.rgo, "STD", "Standard",
                                           shift_start="08:00", shift_end="17:00")
        branch = org.create_working_calendar(self.c, self.actor, self.rgo, "MNL", "Manila",
                                            shift_start="07:00", shift_end=None, parent_id=base)
        resolved, source = org.effective_working_calendar(self.c, branch)
        self.assertEqual(resolved["shift_start"], "07:00")     # child override
        self.assertEqual(resolved["shift_end"], "17:00")       # inherited from parent
        self.assertEqual(source["shift_start"], branch)
        self.assertEqual(source["shift_end"], base)


class TestConfigCascadeExtension(Base):
    KEY = "approval.quotation_threshold"

    def test_branch_and_user_override(self):
        br = org.create_branch(self.c, self.actor, self.rgo, "B", "B")
        ap.set_config(self.c, "branch", str(br), self.KEY, "750000")
        r = org.resolve_org_config(self.c, self.KEY, tenant=self.rgo, branch=br)
        self.assertEqual((r["value"], r["scope"]), ("750000", "branch"))
        ap.set_config(self.c, "user", "99", self.KEY, "100000")
        r2 = org.resolve_org_config(self.c, self.KEY, tenant=self.rgo, branch=br, user=99)
        self.assertEqual((r2["value"], r2["scope"]), ("100000", "user"))

    def test_inactive_scope_fallback(self):
        br = org.create_branch(self.c, self.actor, self.rgo, "B", "B")
        ap.set_config(self.c, "branch", str(br), self.KEY, "750000")
        org.deactivate_unit(self.c, self.actor, br)            # branch config now ignored
        r = org.resolve_org_config(self.c, self.KEY, tenant=self.rgo, branch=br)
        self.assertEqual(r["scope"], "platform")               # falls back past inactive branch
        self.assertEqual(r["value"], "500000")

    def test_expired_config_fallback(self):
        br = org.create_branch(self.c, self.actor, self.rgo, "B", "B")
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        ap.set_config(self.c, "branch", str(br), self.KEY, "750000", effective_to=yesterday)
        r = org.resolve_org_config(self.c, self.KEY, tenant=self.rgo, branch=br)
        self.assertEqual(r["scope"], "platform")               # expired branch value skipped

    def test_cross_tenant_isolation(self):
        ap.set_config(self.c, "tenant", str(self.rgo), self.KEY, "900000")
        r = org.resolve_org_config(self.c, self.KEY, tenant=self.acme)
        self.assertNotEqual(r["value"], "900000")              # ACME cannot see RGO's tenant value
        self.assertEqual(r["scope"], "platform")


class TestAuditCompleteness(Base):
    def test_org_mutations_are_audited(self):
        br = org.create_branch(self.c, self.actor, self.rgo, "B", "B")
        dep = org.create_department(self.c, self.actor, self.rgo, "D", "D", parent_id=br)
        org.reparent(self.c, self.actor, dep, br)
        org.archive_unit(self.c, self.actor, dep)
        actions = {a["action"] for a in self.c.execute(
            "SELECT action FROM audit_logs WHERE entity IN ('org_units','user_organization_assignments')"
        ).fetchall()}
        self.assertTrue({"ORG_UNIT_CREATED", "ORG_UNIT_REPARENTED", "ORG_UNIT_STATUS_CHANGED"} <= actions)

    def test_company_profile_and_assignment_audited(self):
        org.upsert_company_profile(self.c, self.actor, self.rgo, legal_name="RGO Inc", country="PH")
        self.assertEqual(org.company_profile(self.c, self.rgo)["legal_name"], "RGO Inc")
        u = self._user("z@rgo.demo", "estimator")
        b = org.create_branch(self.c, self.actor, self.rgo, "B", "B")
        org.assign_user(self.c, self.actor, self.rgo, u, "branch", b, "PRIMARY")
        actions = {a["action"] for a in self.c.execute("SELECT action FROM audit_logs").fetchall()}
        self.assertIn("COMPANY_PROFILE_UPSERTED", actions)
        self.assertIn("USER_ORG_ASSIGNED", actions)


class TestAuditCorrelation(Base):                                # Phase 1 #1
    def test_correlation_id_threads_through_audit(self):
        core.set_correlation_id("req-abc123")
        try:
            org.create_branch(self.c, self.actor, self.rgo, "B", "B")
        finally:
            core.set_correlation_id(None)
        row = self.c.execute("SELECT correlation_id FROM audit_logs WHERE action='ORG_UNIT_CREATED'"
                             " ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["correlation_id"], "req-abc123")

    def test_explicit_correlation_overrides_ambient(self):
        core.set_correlation_id("ambient")
        try:
            core.audit(self.c, self.actor, "X", "org_units", 1, correlation_id="explicit")
        finally:
            core.set_correlation_id(None)
        row = self.c.execute("SELECT correlation_id FROM audit_logs WHERE action='X'").fetchone()
        self.assertEqual(row["correlation_id"], "explicit")


if __name__ == "__main__":
    unittest.main(verbosity=2)
