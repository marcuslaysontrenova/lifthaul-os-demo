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


if __name__ == "__main__":
    unittest.main(verbosity=2)
