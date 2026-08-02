"""LiftHaul OS — Path A controls: expiring platform cross-access + admin guardrails."""
import unittest

import core
import admin_platform as ap
import org
import backfill
import tenant


class Base(unittest.TestCase):
    def setUp(self):
        self.c = core.connect(":memory:")
        self.c.executescript(core.SCHEMA); self.c.commit()
        ap.init(self.c); ap.seed(self.c); org.init(self.c); backfill.init(self.c)
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.actor = {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": self.rgo}

    def _user(self, email, role="estimator"):
        return core.create_user(self.c, email, "Demo1234Xy", role, "U")


class TestCrossAccessExpiry(Base):                               # Item 5
    def test_activate_enriches_and_permits_cross(self):
        g = tenant.activate_cross_access(self.c, self.actor, "OTHER", "audit review")
        self.assertIn("expires_at", g)
        act = {"id": 1, "perms": {"*"}}
        tenant.enrich_cross_access(self.c, act)
        self.assertTrue(tenant.can_cross(act))

    def test_permission_alone_does_not_permit_cross(self):
        # holding the permission but WITHOUT an active grant must NOT bypass tenant scope
        act = {"id": 1, "perms": {"*"}}                      # no grant activated
        self.assertFalse(tenant.can_cross(act))

    def test_expiry_denies_after_window(self):
        g = tenant.activate_cross_access(self.c, self.actor, "OTHER", "x", ttl=60)
        self.c.execute("UPDATE cross_access_grants SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                       (g["grant_id"],)); self.c.commit()
        self.assertIsNone(tenant.active_cross_grant(self.c, 1))
        act = {"id": 1, "perms": {"*"}}; tenant.enrich_cross_access(self.c, act)
        self.assertFalse(tenant.can_cross(act))

    def test_manual_termination(self):
        g = tenant.activate_cross_access(self.c, self.actor, "OTHER", "x")
        tenant.terminate_cross_access(self.c, self.actor, g["grant_id"])
        self.assertIsNone(tenant.active_cross_grant(self.c, 1))

    def test_ttl_is_capped(self):
        g = tenant.activate_cross_access(self.c, self.actor, "OTHER", "x", ttl=10_000_000)
        self.assertLessEqual(g["ttl_seconds"], tenant.CROSS_ACCESS_MAX_TTL)

    def test_requires_permission_and_reason(self):
        weak = {"id": 2, "role": "estimator", "perms": {"booking.create"}}
        with self.assertRaises(core.ForbiddenError):
            tenant.activate_cross_access(self.c, weak, "OTHER", "x")
        with self.assertRaises(core.ValidationError):
            tenant.activate_cross_access(self.c, self.actor, "OTHER", None)

    def test_activation_and_termination_audited_high_severity(self):
        g = tenant.activate_cross_access(self.c, self.actor, "OTHER", "x")
        tenant.terminate_cross_access(self.c, self.actor, g["grant_id"])
        actions = {a["action"] for a in self.c.execute("SELECT action FROM audit_logs").fetchall()}
        self.assertIn("PLATFORM_CROSS_ACCESS_ACTIVATED", actions)
        self.assertIn("PLATFORM_CROSS_ACCESS_TERMINATED", actions)


class TestAdminGuardrails(Base):                                 # Item 6
    def test_cannot_assign_platform_layer_role_without_authority(self):
        u = self._user("g@r")
        actor = {"id": 9, "role": "business_admin", "perms": {"user_admin.*", "role_admin.*"}}
        plat = ap.role_by_code(self.c, "RGO", "platform_admin")     # layer 1
        with self.assertRaises(core.ForbiddenError):
            ap.assign_role(self.c, u, plat["id"], actor=actor)

    def test_self_elevation_blocked(self):
        u = self._user("s@r")
        actor = {"id": 9, "role": "x", "perms": {"customer.view"}}   # weak
        appr = ap.role_by_code(self.c, "RGO", "approver")            # grants quotation.approve
        with self.assertRaises(core.ForbiddenError):
            ap.assign_role(self.c, u, appr["id"], actor=actor)

    def test_platform_super_may_assign(self):
        u = self._user("ok@r")
        appr = ap.role_by_code(self.c, "RGO", "approver")
        ap.assign_role(self.c, u, appr["id"], actor={"id": 9, "role": "admin", "perms": {"*"}})
        self.assertIn("quotation.approve", ap.effective_permissions(self.c, u))

    def test_last_super_admin_protected(self):
        u = self._user("sa@r", "admin")
        ap.assign_role(self.c, u, ap.role_by_code(self.c, "RGO", "super_platform_admin")["id"])
        with self.assertRaises(core.ForbiddenError):
            ap.set_status(self.c, self.actor, u, "SUSPENDED")       # would orphan the platform

    def test_super_admin_deactivate_ok_when_another_active(self):
        sup = ap.role_by_code(self.c, "RGO", "super_platform_admin")["id"]
        u1, u2 = self._user("sa1@r", "admin"), self._user("sa2@r", "admin")
        ap.assign_role(self.c, u1, sup); ap.assign_role(self.c, u2, sup)
        ap.set_status(self.c, self.actor, u1, "SUSPENDED")
        self.assertEqual(ap.get_user(self.c, u1)["status"], "SUSPENDED")


class TestResidualControls(Base):                               # Items 5/6 residual
    def test_role_clone_copies_grants(self):
        rid = ap.clone_role(self.c, "RGO", "approver", "approver_copy", "Approver Copy", actor=self.actor)
        self.assertEqual(ap.effective_role_grants(self.c, rid),
                         ap.effective_role_grants(self.c, ap.role_by_code(self.c, "RGO", "approver")["id"]))

    def test_reparent_preview_reports_impact_and_validity(self):
        a = org.create_business_unit(self.c, self.actor, self.rgo, "A", "A")
        b = org.create_branch(self.c, self.actor, self.rgo, "B", "B", parent_id=a)
        pv = org.reparent_preview(self.c, a, b)              # a under its own descendant
        self.assertFalse(pv["valid"])
        self.assertGreaterEqual(pv["descendants"], 1)

    def test_calendar_validation(self):
        with self.assertRaises(core.ValidationError):        # inverted shift
            org.create_working_calendar(self.c, self.actor, self.rgo, "BAD", "Bad",
                                        shift_start="18:00", shift_end="09:00")
        cal = org.create_holiday_calendar(self.c, self.actor, self.rgo, "PH", "PH")
        org.add_holiday(self.c, self.actor, cal, "New Year", "2027-01-01")
        with self.assertRaises(core.ConflictError):          # duplicate holiday
            org.add_holiday(self.c, self.actor, cal, "Dup", "2027-01-01")


class TestAdminViewers(Base):                                   # Items 1/5/6 viewers
    def test_role_compare_and_sod_conflict_shown(self):
        cmp = ap.compare_roles(self.c, "RGO", "estimator", "approver")
        self.assertIn("quotation.create", cmp["only_a"])
        self.assertIn("quotation.approve", cmp["only_b"])
        self.assertTrue(any(c["a"] == "quotation.create" for c in cmp["sod_conflicts"]))

    def test_role_dependency_protected(self):
        dep = ap.role_dependency(self.c, "RGO", "super_platform_admin")
        self.assertTrue(dep["protected"])
        self.assertFalse(dep["can_archive"])

    def test_sod_blocks_conflicting_assignment_with_exception(self):
        u = self._user("sod@r")
        ap.assign_role(self.c, u, ap.role_by_code(self.c, "RGO", "estimator")["id"])
        with self.assertRaises(core.ForbiddenError):
            ap.assign_role(self.c, u, ap.role_by_code(self.c, "RGO", "approver")["id"])
        ap.assign_role(self.c, u, ap.role_by_code(self.c, "RGO", "approver")["id"],
                       allow_sod_exception=True, reason="temporary cover")   # governed exception
        self.assertIn("quotation.approve", ap.effective_permissions(self.c, u))

    def test_config_preview_is_non_mutating(self):
        br = org.create_branch(self.c, self.actor, self.rgo, "B", "B")
        before = org.resolve_org_config(self.c, "approval.quotation_threshold", tenant=self.rgo, branch=br)
        pv = org.effective_config_preview(self.c, "approval.quotation_threshold", "branch", str(br),
                                          "750000", tenant=self.rgo, branch=br)
        self.assertEqual(pv["proposed_effective"]["value"], "750000")
        self.assertTrue(pv["changed"])
        after = org.resolve_org_config(self.c, "approval.quotation_threshold", tenant=self.rgo, branch=br)
        self.assertEqual(before["value"], after["value"])           # preview did not mutate

    def test_config_preview_scope_breakdown(self):
        pv = org.effective_config_preview(self.c, "approval.quotation_threshold", "tenant", str(self.rgo),
                                          "900000", tenant=str(self.rgo))
        self.assertEqual(pv["scope_values"]["platform"], "500000")   # seeded platform default visible
        self.assertIn("branch", pv["scope_values"])

    def test_reparent_preview_config_inheritance(self):
        p1 = org.create_business_unit(self.c, self.actor, self.rgo, "P1", "P1")
        p2 = org.create_business_unit(self.c, self.actor, self.rgo, "P2", "P2")
        dep = org.create_department(self.c, self.actor, self.rgo, "D", "D", parent_id=p1)
        ap.set_config(self.c, "business_unit", str(p2), "approval.quotation_threshold", "999999")
        pv = org.reparent_preview(self.c, dep, p2)
        self.assertIn("config_inheritance", pv)
        self.assertTrue(any(ci["key"] == "approval.quotation_threshold" for ci in pv["config_inheritance"]))

    def test_config_preview_validates_numeric(self):
        pv = org.effective_config_preview(self.c, "approval.quotation_threshold", "platform", "",
                                          "not-a-number", tenant=self.rgo)
        self.assertFalse(pv["valid"])

    def test_working_calendar_conflict_detection(self):
        parent = org.create_working_calendar(self.c, self.actor, self.rgo, "P", "Parent")
        child = org.create_working_calendar(self.c, self.actor, self.rgo, "CH", "Child", parent_id=parent)
        self.c.execute("UPDATE working_calendars SET status='INACTIVE' WHERE id=?", (parent,)); self.c.commit()
        wc = org.working_calendar_conflicts(self.c, child)
        self.assertTrue(any(x["type"] == "inactive_parent_calendar" for x in wc["conflicts"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
