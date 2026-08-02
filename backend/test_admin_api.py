"""LiftHaul OS — Enterprise Administration console API tests (Platform 1).

Drives the /admin/* endpoints that back the administration menu (Organization /
People & Access / Calendars / Security / Configuration / Governance) through the real
HTTP router (server._match handlers), proving the console is fully backed and
permission-gated.
"""
import os
import unittest

if os.path.exists("rgo_os.sqlite"):
    os.remove("rgo_os.sqlite")

import server   # noqa: E402
import core      # noqa: E402
import admin_platform as ap   # noqa: E402


def call(method, path, body=None, actor=None):
    fn, params = server._match(method, path)
    assert fn, f"no route for {method} {path}"
    return fn(actor, body or {}, params or {})


class TestAdminApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import db
        server._conn = db.connect(":memory:")     # own connection, import-order independent
        c = server._conn
        for e, r in [("adminapi@r", "admin"), ("estapi@r", "estimator")]:
            try:
                core.create_user(c, e, "demo1234", r, r)
            except core.ConflictError:
                pass
        cls.tid = ap.get_tenant(c, "RGO")["id"]

    def _actor(self, email):
        tok = call("POST", "/login", {"email": email, "password": "demo1234"})["token"]
        return core.actor_for(server._conn, tok)

    @property
    def admin(self):
        return self._actor("adminapi@r")

    # ---- Organization -----------------------------------------------------
    def test_org_create_and_tree(self):
        code = "BU_" + os.urandom(3).hex()
        uid = call("POST", "/admin/org/units", {"kind": "business_unit", "code": code, "name": "X"}, self.admin)["id"]
        tree = call("GET", "/admin/org/tree", {}, self.admin)["tree"]
        self.assertTrue(any(n["id"] == uid for n in tree))
        found = call("POST", "/admin/org/units/search", {"kind": "business_unit"}, self.admin)["units"]
        self.assertTrue(any(u["code"] == code for u in found))

    def test_cost_centers_and_profile(self):
        call("POST", "/admin/org/cost-centers", {"code": "CC_" + os.urandom(3).hex(), "name": "Ops"}, self.admin)
        self.assertIsInstance(call("GET", "/admin/org/cost-centers", {}, self.admin)["cost_centers"], list)
        call("POST", "/admin/org/company-profile", {"legal_name": "RGO Inc", "country": "PH"}, self.admin)
        self.assertEqual(call("GET", "/admin/org/company-profile", {}, self.admin)["profile"]["legal_name"], "RGO Inc")

    # ---- People & Access --------------------------------------------------
    def test_users_roles_permissions(self):
        self.assertTrue(len(call("GET", "/admin/users", {}, self.admin)["users"]) >= 1)
        roles = call("GET", "/admin/roles", {}, self.admin)["roles"]
        self.assertTrue(any(r["code"] == "super_platform_admin" for r in roles))
        self.assertTrue(len(call("GET", "/admin/permissions", {}, self.admin)["permissions"]) > 10)

    def test_sessions_and_login_history(self):
        self.assertIsInstance(call("GET", "/admin/sessions", {}, self.admin)["sessions"], list)
        self.assertIsInstance(call("GET", "/admin/login-history", {}, self.admin)["history"], list)

    # ---- Security ---------------------------------------------------------
    def test_security_policies(self):
        pol = call("GET", "/admin/security/policies", {}, self.admin)
        self.assertIn("min_length", pol["password_policy"])
        self.assertIn(pol["authorization_mode"], ("hybrid", "legacy", "db"))

    # ---- Configuration ----------------------------------------------------
    def test_config_effective_and_list(self):
        r = call("POST", "/admin/config/effective",
                 {"key": "approval.quotation_threshold", "tenant": str(self.tid)}, self.admin)
        self.assertEqual(r["value"], "500000")
        self.assertEqual(r["scope"], "platform")
        self.assertIsInstance(call("GET", "/admin/config", {}, self.admin)["config"], list)

    # ---- Governance -------------------------------------------------------
    def test_audit_and_integrity(self):
        call("POST", "/admin/org/units", {"kind": "branch", "code": "BR_" + os.urandom(3).hex(), "name": "B"}, self.admin)
        self.assertTrue(len(call("GET", "/admin/audit", {}, self.admin)["audit"]) >= 1)
        integ = call("GET", "/admin/governance/data-integrity", {}, self.admin)
        self.assertTrue(integ["ok"])
        self.assertIn("tenant_enforced", call("GET", "/admin/governance/backfill-status", {}, self.admin))

    def test_backfill_governance_endpoints(self):
        self.assertIn("tables", call("GET", "/admin/governance/backfill-analyze", {}, self.admin))
        dry = call("POST", "/admin/governance/backfill-dry-run", {}, self.admin)
        self.assertEqual(dry["writes"], 0)
        res = call("POST", "/admin/governance/backfill-execute", {}, self.admin)
        self.assertIn("updated", res)
        st = call("GET", "/admin/governance/backfill-status", {}, self.admin)
        self.assertIn("open_remediation", st)
        self.assertIsInstance(call("GET", "/admin/governance/backfill-remediation", {}, self.admin)["remediation"], list)

    def test_effective_access_from_authz_service(self):
        # create a user, grant a DB role, and read effective access
        uid = ap.create_user(server._conn, self.admin, "eff@r", "Demo1234Xy", "estimator")
        r = ap.role_by_code(server._conn, "RGO", "approver")
        ap.assign_role(server._conn, uid, r["id"])
        ea = call("GET", f"/admin/users/{uid}/effective-access", {}, self.admin)
        self.assertIn("quotation.approve", ea["effective_permissions"])
        self.assertTrue(any(g["source_role"] == "approver" for g in ea["grants"]))

    def test_remediation_resolve_and_cross_access(self):
        call("POST", "/admin/governance/backfill-execute", {}, self.admin)
        rem = call("GET", "/admin/governance/backfill-remediation", {}, self.admin)["remediation"]
        if rem:
            call("POST", f"/admin/governance/backfill-remediation/{rem[0]['id']}/resolve", {}, self.admin)
        # cross-access requires target + reason; admin ('*') is permitted
        res = call("POST", "/admin/security/cross-access", {"target_tenant": "RGO", "reason": "audit"}, self.admin)
        self.assertTrue(res["granted"])
        with self.assertRaises(core.ValidationError):
            call("POST", "/admin/security/cross-access", {"target_tenant": "RGO"}, self.admin)  # no reason

    def test_residual_admin_endpoints(self):
        # role clone
        rc = call("POST", "/admin/roles/approver/clone", {"new_code": "appr2_" + os.urandom(2).hex(), "name": "Appr2"}, self.admin)
        self.assertIn("id", rc)
        # re-parent preview
        bu = call("POST", "/admin/org/units", {"kind": "business_unit", "code": "PV_" + os.urandom(2).hex(), "name": "PV"}, self.admin)["id"]
        pv = call("POST", f"/admin/org/units/{bu}/reparent-preview", {"new_parent_id": bu}, self.admin)
        self.assertFalse(pv["valid"])                        # self-parent invalid
        # config history
        call("POST", "/admin/config", {"scope": "platform", "key": "auth.mfa_policy", "value": "optional"}, self.admin)
        self.assertIsInstance(call("GET", "/admin/config/history", {"key": "auth.mfa_policy"}, self.admin)["history"], list)
        # data-integrity per-check statuses
        di = call("GET", "/admin/governance/data-integrity", {}, self.admin)
        self.assertIn("summary", di)
        self.assertTrue(all("status" in c for c in di["checks"]))
        # cross-access expiry endpoints
        g = call("POST", "/admin/security/cross-access", {"target_tenant": "RGO", "reason": "audit"}, self.admin)
        self.assertIn("expires_at", g)
        call("POST", f"/admin/security/cross-access/{g['grant_id']}/terminate", {}, self.admin)

    # ---- Authorization gating ---------------------------------------------
    def test_non_admin_is_forbidden(self):
        est = self._actor("estapi@r")
        with self.assertRaises(core.ForbiddenError):
            call("GET", "/admin/users", {}, est)
        with self.assertRaises(core.ForbiddenError):
            call("POST", "/admin/org/units", {"kind": "branch", "code": "Z", "name": "Z"}, est)


if __name__ == "__main__":
    unittest.main(verbosity=2)
