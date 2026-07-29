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
        self.assertEqual(call("GET", "/admin/governance/backfill-status", {}, self.admin)["status"],
                         "PLANNED_NOT_EXECUTED")

    # ---- Authorization gating ---------------------------------------------
    def test_non_admin_is_forbidden(self):
        est = self._actor("estapi@r")
        with self.assertRaises(core.ForbiddenError):
            call("GET", "/admin/users", {}, est)
        with self.assertRaises(core.ForbiddenError):
            call("POST", "/admin/org/units", {"kind": "branch", "code": "Z", "name": "Z"}, est)


if __name__ == "__main__":
    unittest.main(verbosity=2)
