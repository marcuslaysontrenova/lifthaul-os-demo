"""LiftHaul OS — Enterprise Administration Platform foundation tests.

Proves the acceptance criteria from Volume 2 / Volume 5 for C-003 (tenant dimension),
C-005 (data-driven RBAC), and C-008 (configuration cascade), plus parity with the
legacy core.PERMISSIONS so the additive model is behavior-equivalent before any cutover.
"""
import unittest

import core
import admin_platform as ap


class Base(unittest.TestCase):
    def setUp(self):
        self.c = core.connect(":memory:")
        self.c.executescript(core.SCHEMA)     # users + audit_logs for assignment/audit
        self.c.commit()
        ap.init(self.c)
        ap.seed(self.c)
        self.actor = {"id": 1, "role": "admin"}

    def _user(self, email="u@rgo.demo"):
        return core.create_user(self.c, email, "Demo1234Xy", "admin", "U")


class TestTenantDimension(Base):                                  # C-003
    def test_seed_creates_tenant_zero_rgo(self):
        t = ap.get_tenant(self.c, "RGO")
        self.assertIsNotNone(t)
        self.assertEqual(t["legal_name"], "RGO Machine Rigging Services")

    def test_create_and_isolate_tenants(self):
        ap.create_tenant(self.c, "ACME", "ACME Cranes Inc", actor=self.actor)
        ap.create_role(self.c, "RGO", "rgo_only", "RGO Only", grants={"customer.view"})
        ap.create_role(self.c, "ACME", "acme_only", "ACME Only", grants={"customer.view"})
        rgo_codes = {r["code"] for r in ap.list_roles(self.c, "RGO")}
        acme_codes = {r["code"] for r in ap.list_roles(self.c, "ACME")}
        self.assertIn("rgo_only", rgo_codes)
        self.assertNotIn("acme_only", rgo_codes)
        self.assertIn("acme_only", acme_codes)
        self.assertNotIn("rgo_only", acme_codes)
        # system roles are visible to both tenants (global templates)
        self.assertIn("estimator", rgo_codes)
        self.assertIn("estimator", acme_codes)

    def test_duplicate_tenant_rejected(self):
        with self.assertRaises(core.ConflictError):
            ap.create_tenant(self.c, "RGO", "dupe")


class TestDataDrivenRBAC(Base):                                   # C-005
    def test_parity_with_core_permissions(self):
        # seeded system-role grants must equal today's code grants (behavior parity)
        for role_code in ("operations_manager", "estimator", "approver", "finance",
                          "dispatcher", "customer"):
            role = ap.role_by_code(self.c, "RGO", role_code)
            granted = ap.effective_role_grants(self.c, role["id"])
            self.assertEqual(granted, core.PERMISSIONS[role_code],
                             f"parity mismatch for {role_code}")

    def test_admin_creates_role_and_enforcement_follows_without_code_change(self):
        u = self._user()
        rid = ap.create_role(self.c, "RGO", "power_user", "Power User",
                             grants={"quotation.approve"}, actor=self.actor)
        ap.assign_role(self.c, u, rid, actor=self.actor)
        self.assertTrue(ap.has_permission(self.c, u, "quotation.approve"))
        self.assertFalse(ap.has_permission(self.c, u, "payment.verify"))
        # grant more at runtime — still no code change
        ap.grant_permission(self.c, rid, "payment.*", actor=self.actor)
        self.assertTrue(ap.has_permission(self.c, u, "payment.verify"))

    def test_wildcard_and_exact_grants(self):
        u = self._user("super@rgo.demo")
        sup = ap.role_by_code(self.c, "RGO", "super_platform_admin")
        ap.assign_role(self.c, u, sup["id"])
        self.assertTrue(ap.has_permission(self.c, u, "anything.at.all"))

    def test_unknown_tenant_role_rejected(self):
        with self.assertRaises(core.ConflictError):
            ap.create_role(self.c, "NOPE", "x", "X")

    def test_system_role_is_locked(self):
        est = ap.role_by_code(self.c, "RGO", "estimator")
        with self.assertRaises(core.ForbiddenError):
            ap.grant_permission(self.c, est["id"], "payment.verify")

    def test_four_layer_hierarchy_present(self):
        layers = {r["layer"] for r in ap.list_roles(self.c)}
        self.assertEqual(layers, {1, 2, 3, 4})
        self.assertEqual(ap.role_by_code(self.c, "RGO", "super_platform_admin")["layer"], 1)
        self.assertEqual(ap.role_by_code(self.c, "RGO", "business_admin")["layer"], 2)
        self.assertEqual(ap.role_by_code(self.c, "RGO", "crm_admin")["layer"], 3)
        self.assertEqual(ap.role_by_code(self.c, "RGO", "driver")["layer"], 4)


class TestConfigCascade(Base):                                    # C-008
    KEY = "approval.quotation_threshold"

    def test_platform_default_resolves(self):
        val, src = ap.resolve_config(self.c, self.KEY, tenant="RGO")
        self.assertEqual(val, "500000")
        self.assertEqual(src, "platform")

    def test_tenant_override_wins_over_platform(self):
        ap.set_config(self.c, "tenant", "RGO", self.KEY, "1000000", actor=self.actor)
        val, src = ap.resolve_config(self.c, self.KEY, tenant="RGO")
        self.assertEqual((val, src), ("1000000", "tenant"))
        # a different tenant with no override still gets the platform default
        val2, src2 = ap.resolve_config(self.c, self.KEY, tenant="ACME")
        self.assertEqual((val2, src2), ("500000", "platform"))

    def test_user_override_wins_over_all(self):
        ap.set_config(self.c, "tenant", "RGO", self.KEY, "1000000")
        ap.set_config(self.c, "user", "42", self.KEY, "250000")
        val, src = ap.resolve_config(self.c, self.KEY, tenant="RGO", user="42")
        self.assertEqual((val, src), ("250000", "user"))

    def test_unset_key_returns_none(self):
        self.assertEqual(ap.resolve_config(self.c, "nope.key", tenant="RGO"), (None, None))

    def test_invalid_scope_rejected(self):
        with self.assertRaises(core.ConflictError):
            ap.set_config(self.c, "galaxy", "", "k", "v")


class TestRbacCutover(Base):                                      # C-005 enforcement cutover
    def _actor(self, uid, role="estimator"):
        # mimic core.actor_for output shape, then apply the DB-RBAC resolver
        actor = {"id": uid, "role": role}
        ap.apply_rbac(self.c, actor)
        return actor

    def test_hybrid_leaves_unassigned_user_on_legacy(self):
        # default flag is hybrid; a user with no DB roles keeps legacy behavior
        u = self._user("legacy@rgo.demo")
        actor = self._actor(u, role="estimator")
        self.assertNotIn("perms", actor)                     # untouched -> core.can uses legacy
        self.assertTrue(core.can(actor, "booking.create"))   # via legacy PERMISSIONS[estimator]
        self.assertFalse(core.can(actor, "quotation.approve"))

    def test_db_enforcement_after_role_assignment_no_code_change(self):
        u = self._user("est@rgo.demo")
        ap.assign_role(self.c, u, ap.role_by_code(self.c, "RGO", "estimator")["id"])
        actor = self._actor(u)
        self.assertIn("perms", actor)                        # now DB-sourced
        self.assertTrue(core.can(actor, "booking.create"))
        self.assertFalse(core.can(actor, "quotation.approve"))
        # grant approver at runtime -> enforcement changes with NO code change
        ap.assign_role(self.c, u, ap.role_by_code(self.c, "RGO", "approver")["id"])
        actor = self._actor(u)
        self.assertTrue(core.can(actor, "quotation.approve"))

    def test_flag_db_denies_user_with_no_roles(self):
        ap.set_config(self.c, "platform", "", "iam.rbac_source", "db")
        u = self._user("norole@rgo.demo")
        actor = self._actor(u)
        self.assertEqual(actor["perms"], set())              # db mode: empty = deny-all
        self.assertFalse(core.can(actor, "booking.create"))

    def test_flag_legacy_is_reversible(self):
        u = self._user("rev@rgo.demo")
        ap.assign_role(self.c, u, ap.role_by_code(self.c, "RGO", "estimator")["id"])
        ap.set_config(self.c, "platform", "", "iam.rbac_source", "legacy")
        actor = self._actor(u)
        self.assertNotIn("perms", actor)                     # reverted to legacy path
        self.assertTrue(core.can(actor, "booking.create"))

    def test_backfill_maps_legacy_roles_at_parity(self):
        u = self._user("bf@rgo.demo")                        # created with role 'admin'
        made = ap.backfill_user_roles(self.c)
        self.assertGreaterEqual(made, 1)
        actor = self._actor(u, role="admin")
        self.assertTrue(core.can(actor, "anything.at.all"))  # admin -> '*' via DB
        # idempotent
        self.assertEqual(ap.backfill_user_roles(self.c), 0)


class TestUserLifecycle(Base):                                    # C-006
    def test_invite_creates_active_user_with_role(self):
        uid = ap.create_user(self.c, self.actor, "new@rgo.demo", "Demo1234Xy", "estimator", "New")
        u = ap.get_user(self.c, uid)
        self.assertEqual(u["status"], "ACTIVE")
        self.assertIn("estimator", {r["code"] for r in ap.user_roles(self.c, uid)})
        self.assertTrue(core.login(self.c, "new@rgo.demo", "Demo1234Xy"))  # can authenticate

    def test_suspend_blocks_login_and_kills_sessions(self):
        uid = ap.create_user(self.c, self.actor, "s@rgo.demo", "Demo1234Xy", "estimator")
        tok = core.login(self.c, "s@rgo.demo", "Demo1234Xy")
        ap.suspend_user(self.c, self.actor, uid)
        with self.assertRaises(core.AuthError):
            core.actor_for(self.c, tok)                       # live session revoked
        with self.assertRaises(core.AuthError):
            core.login(self.c, "s@rgo.demo", "Demo1234Xy")      # cannot re-authenticate

    def test_activate_restores_access(self):
        uid = ap.create_user(self.c, self.actor, "a@rgo.demo", "Demo1234Xy", "estimator")
        ap.suspend_user(self.c, self.actor, uid)
        ap.activate_user(self.c, self.actor, uid)
        self.assertTrue(core.login(self.c, "a@rgo.demo", "Demo1234Xy"))

    def test_lock_unlock(self):
        uid = ap.create_user(self.c, self.actor, "l@rgo.demo", "Demo1234Xy", "estimator")
        ap.lock_user(self.c, self.actor, uid)
        with self.assertRaises(core.AuthError):
            core.login(self.c, "l@rgo.demo", "Demo1234Xy")
        ap.unlock_user(self.c, self.actor, uid)
        self.assertTrue(core.login(self.c, "l@rgo.demo", "Demo1234Xy"))

    def test_deactivate_offboard_is_soft(self):
        uid = ap.create_user(self.c, self.actor, "o@rgo.demo", "Demo1234Xy", "estimator")
        ap.deactivate_user(self.c, self.actor, uid)
        with self.assertRaises(core.AuthError):
            core.login(self.c, "o@rgo.demo", "Demo1234Xy")
        self.assertIsNotNone(ap.get_user(self.c, uid))        # row retained for audit
        self.assertIn(uid, {u["id"] for u in ap.list_users(self.c)})

    def test_reset_password_invalidates_old_and_sessions(self):
        uid = ap.create_user(self.c, self.actor, "p@rgo.demo", "Demo1234Xy", "estimator")
        tok = core.login(self.c, "p@rgo.demo", "Demo1234Xy")
        ap.reset_password(self.c, self.actor, uid, "NewPass1234")
        with self.assertRaises(core.AuthError):
            core.actor_for(self.c, tok)                       # old session gone
        with self.assertRaises(core.AuthError):
            core.login(self.c, "p@rgo.demo", "Demo1234Xy")      # old password rejected
        self.assertTrue(core.login(self.c, "p@rgo.demo", "NewPass1234"))

    def test_permission_review_and_audit_trail(self):
        uid = ap.create_user(self.c, self.actor, "r@rgo.demo", "Demo1234Xy", "approver")
        self.assertIn("quotation.approve", ap.permission_review(self.c, uid))
        ap.suspend_user(self.c, self.actor, uid)
        actions = {a["action"] for a in ap.user_audit(self.c, uid)}
        self.assertIn("USER_INVITED", actions)
        self.assertIn("USER_STATUS_CHANGED", actions)

    def test_invalid_status_rejected(self):
        uid = ap.create_user(self.c, self.actor, "x@rgo.demo", "Demo1234Xy", "estimator")
        with self.assertRaises(core.ConflictError):
            ap.set_status(self.c, self.actor, uid, "ZOMBIE")


class TestAuthPolicy(Base):                                       # C-007 password policy
    def test_policy_enforced_on_create(self):
        with self.assertRaises(core.ValidationError):
            ap.create_user(self.c, self.actor, "weak@rgo.demo", "short", "estimator")
        with self.assertRaises(core.ValidationError):
            ap.create_user(self.c, self.actor, "weak2@rgo.demo", "alllowercase99", "estimator")

    def test_policy_is_configurable(self):
        ap.set_config(self.c, "platform", "", "auth.pw_min_length", "4")
        ap.set_config(self.c, "platform", "", "auth.pw_require_complexity", "false")
        uid = ap.create_user(self.c, self.actor, "ok@rgo.demo", "abcd", "estimator")
        self.assertTrue(uid)                                  # weak password now allowed by config

    def test_reset_enforces_policy(self):
        uid = ap.create_user(self.c, self.actor, "r@rgo.demo", "Demo1234Xy", "estimator")
        with self.assertRaises(core.ValidationError):
            ap.reset_password(self.c, self.actor, uid, "weak")


class TestMFA(Base):                                             # C-007 MFA / TOTP
    def test_totp_roundtrip(self):
        secret = ap._b32secret()
        self.assertTrue(ap.verify_totp(secret, ap._totp(secret)))
        self.assertFalse(ap.verify_totp(secret, "000000", t=0))

    def test_enroll_confirm_verify(self):
        uid = ap.create_user(self.c, self.actor, "m@rgo.demo", "Demo1234Xy", "estimator")
        secret = ap.enroll_mfa(self.c, uid)
        self.assertFalse(ap.mfa_enrolled(self.c, uid))        # unconfirmed
        ap.confirm_mfa(self.c, uid, ap._totp(secret))
        self.assertTrue(ap.mfa_enrolled(self.c, uid))
        self.assertTrue(ap.verify_mfa(self.c, uid, ap._totp(secret)))
        self.assertFalse(ap.verify_mfa(self.c, uid, "999999"))

    def test_guarded_login_requires_mfa_once_enrolled(self):
        uid = ap.create_user(self.c, self.actor, "g@rgo.demo", "Demo1234Xy", "estimator")
        secret = ap.enroll_mfa(self.c, uid)
        ap.confirm_mfa(self.c, uid, ap._totp(secret))
        with self.assertRaises(core.AuthError):
            ap.guarded_login(self.c, "g@rgo.demo", "Demo1234Xy")            # missing code
        self.assertTrue(ap.guarded_login(self.c, "g@rgo.demo", "Demo1234Xy", mfa_code=ap._totp(secret)))

    def test_mfa_required_policy_forces_all(self):
        ap.set_config(self.c, "platform", "", "auth.mfa_policy", "required")
        row = {"id": 999}
        self.assertTrue(ap.mfa_required_for(self.c, row))


class TestSessionsAndLockout(Base):                              # C-007 sessions + lockout
    def test_lockout_after_threshold(self):
        ap.create_user(self.c, self.actor, "k@rgo.demo", "Demo1234Xy", "estimator")
        for _ in range(5):
            with self.assertRaises(core.AuthError):
                ap.guarded_login(self.c, "k@rgo.demo", "wrongpass")
        with self.assertRaises(core.AuthError):
            ap.guarded_login(self.c, "k@rgo.demo", "Demo1234Xy")           # locked despite correct pw
        self.assertTrue(any(h["reason"] == "locked"
                            for h in ap.list_login_history(self.c, email="k@rgo.demo")))

    def test_login_history_records_success_and_failure(self):
        ap.create_user(self.c, self.actor, "h@rgo.demo", "Demo1234Xy", "estimator")
        with self.assertRaises(core.AuthError):
            ap.guarded_login(self.c, "h@rgo.demo", "nope")
        ap.guarded_login(self.c, "h@rgo.demo", "Demo1234Xy")
        reasons = [h["reason"] for h in ap.list_login_history(self.c, email="h@rgo.demo")]
        self.assertIn("ok", reasons)
        self.assertIn("invalid_credentials", reasons)

    def test_session_admin_list_and_revoke(self):
        uid = ap.create_user(self.c, self.actor, "sa@rgo.demo", "Demo1234Xy", "estimator")
        tok = ap.guarded_login(self.c, "sa@rgo.demo", "Demo1234Xy")
        self.assertEqual(len(ap.list_sessions(self.c, uid)), 1)
        ap.revoke_session(self.c, tok, actor=self.actor)
        self.assertEqual(len(ap.list_sessions(self.c, uid)), 0)
        with self.assertRaises(core.AuthError):
            core.actor_for(self.c, tok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
