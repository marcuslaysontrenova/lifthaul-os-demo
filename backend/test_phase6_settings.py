"""LiftHaul OS — Phase 6: Platform & System Settings.

Proves: typed/scoped/effective-dated governed settings; SECURITY MINIMUMS a tenant may strengthen
but never weaken (0 security-policy weakening); a secret-reference boundary (values never stored/
returned); feature flags with dependency + tenant isolation + kill switch; a module registry with
dependency + unsafe-disable guards; scoped maintenance mode with mandatory expiry; retention with
legal hold + audit-retention platform floor; governed backup + restore approval (separation of
duties); sanitized branding + allowlisted-variable templates; system-integrity checks; tenant
isolation; and migration with zero financial/operational/security drift.
"""
import datetime
import unittest

import db
import core
import admin_platform as ap
import settings as s


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.actor = {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": self.rgo}

    def _actor(self, perms, id=9, role="estimator", tenant=None):
        return {"id": id, "role": role, "perms": set(perms),
                "tenant_id": self.rgo if tenant is None else tenant}


class TestSettingsAndScope(Base):
    def test_platform_value_and_effective(self):
        s.set_value(self.c, self.actor, "platform.name", "RGO Ops", scope="platform")
        self.assertEqual(s.effective_value(self.c, self.actor, "platform.name")["value"], "RGO Ops")

    def test_unknown_key_rejected(self):
        with self.assertRaises(core.ValidationError):
            s.set_value(self.c, self.actor, "nonsense.key", "x", scope="platform")

    def test_invalid_type_rejected(self):
        with self.assertRaises(core.ValidationError):
            s.set_value(self.c, self.actor, "fiscal.year_start_month", "20", scope="platform")   # >12

    def test_invalid_scope_rejected(self):
        with self.assertRaises(core.ValidationError):
            s.set_value(self.c, self.actor, "platform.name", "x", scope="branch")   # platform-only

    def test_tenant_override_and_history(self):
        s.set_value(self.c, self.actor, "platform.default_timezone", "Asia/Tokyo", scope="tenant")
        self.assertEqual(s.effective_value(self.c, self.actor, "platform.default_timezone")["source"], "tenant")
        s.set_value(self.c, self.actor, "platform.default_timezone", "Asia/Singapore", scope="tenant")
        self.assertGreaterEqual(len(s.value_history(self.c, self.actor, "platform.default_timezone")), 2)

    def test_org_override_effective_dates(self):
        future = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
        s.set_value(self.c, self.actor, "platform.default_timezone", "Asia/Dubai", scope="branch",
                    scope_ref="BR1", effective_from=future)
        # not yet effective -> falls back
        eff = s.effective_value(self.c, self.actor, "platform.default_timezone", org_chain=[("branch", "BR1")])
        self.assertNotEqual(eff["source"], "branch:BR1")


class TestSecurityMinimums(Base):
    def test_tenant_may_strengthen(self):
        s.set_value(self.c, self.actor, "auth.password.min_length", "14", scope="tenant")
        self.assertEqual(s.effective_value(self.c, self.actor, "auth.password.min_length")["value"], "14")

    def test_tenant_may_not_weaken_min(self):
        with self.assertRaises(core.ForbiddenError):
            s.set_value(self.c, self.actor, "auth.password.min_length", "6", scope="tenant")   # below platform 10

    def test_tenant_may_not_weaken_max(self):
        with self.assertRaises(core.ForbiddenError):
            s.set_value(self.c, self.actor, "session.idle_timeout_min", "120", scope="tenant")  # platform max 30

    def test_mfa_rank_enforced(self):
        s.set_value(self.c, self.actor, "auth.mfa.policy", "required", scope="tenant")   # stronger ok
        with self.assertRaises(core.ForbiddenError):
            s.set_value(self.c, self.actor, "auth.mfa.policy", "off", scope="tenant")    # weaker blocked

    def test_branch_cannot_override_security_invariant(self):
        # security invariants are platform/tenant only — a branch override is rejected outright
        with self.assertRaises((core.ForbiddenError, core.ValidationError)):
            s.set_value(self.c, self.actor, "auth.lockout.threshold", "20", scope="branch", scope_ref="BR1")


class TestSecrets(Base):
    def test_value_never_stored_or_returned(self):
        s.create_secret_reference(self.c, self.actor, "wise_key", "wise", "WISE_API_KEY")
        refs = s.list_secret_references(self.c, self.actor)
        self.assertEqual(refs[0]["value"], s.SENSITIVE_MASK)
        self.assertNotIn("env_name", refs[0])

    def test_secret_setting_rejects_value(self):
        # a definition marked secret cannot take an ordinary value
        self.c.execute("INSERT INTO setting_definitions(key,data_type,secret,scopes,created_at) VALUES('x.secret','string',1,'platform',?)",
                       (s._now(),)); self.c.commit()
        with self.assertRaises(core.ValidationError):
            s.set_value(self.c, self.actor, "x.secret", "plaintext", scope="platform")

    def test_validate_reference_returns_boolean_only(self):
        import os
        s.create_secret_reference(self.c, self.actor, "app_secret_ref", "env", "APP_SECRET")
        os.environ["APP_SECRET"] = "z"
        try:
            r = s.validate_secret_reference(self.c, self.actor, "app_secret_ref")
            self.assertEqual(set(r.keys()), {"code", "present"})
            self.assertTrue(r["present"])
        finally:
            del os.environ["APP_SECRET"]


class TestFeatureFlags(Base):
    def test_tenant_isolation_and_default(self):
        s.create_flag(self.c, self.actor, "beta_ui", platform_default=False)
        s.set_flag_override(self.c, self.actor, "beta_ui", True, tenant=self.rgo)
        self.assertTrue(s.is_flag_enabled(self.c, "beta_ui", tenant=self.rgo))
        self.assertFalse(s.is_flag_enabled(self.c, "beta_ui", tenant=9999))

    def test_dependency_validation(self):
        s.create_flag(self.c, self.actor, "base_feat", platform_default=False)
        s.create_flag(self.c, self.actor, "child_feat", dependency="base_feat")
        with self.assertRaises(core.ValidationError):
            s.set_flag_override(self.c, self.actor, "child_feat", True, tenant=self.rgo)   # base disabled

    def test_emergency_kill_switch(self):
        s.create_flag(self.c, self.actor, "risky", platform_default=True)
        self.assertTrue(s.is_flag_enabled(self.c, "risky", tenant=self.rgo))
        s.emergency_disable_flag(self.c, self.actor, "risky", reason="incident")
        self.assertFalse(s.is_flag_enabled(self.c, "risky", tenant=self.rgo))

    def test_expired_flag_off(self):
        past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        s.create_flag(self.c, self.actor, "temp", platform_default=True, expires_at=past)
        self.assertFalse(s.is_flag_enabled(self.c, "temp", tenant=self.rgo))


class TestModules(Base):
    def test_unsafe_disable_blocked(self):
        with self.assertRaises(core.ConflictError):
            s.set_module_status(self.c, self.actor, "booking", False)   # quotation depends on it

    def test_safe_disable_allowed(self):
        self.assertTrue(s.set_module_status(self.c, self.actor, "reporting", False))

    def test_disable_impact_preview(self):
        imp = s.module_disable_impact(self.c, self.actor, "booking")
        self.assertIn("quotation", imp["enabled_dependents"])
        self.assertFalse(imp["safe_to_disable"])


class TestMaintenance(Base):
    def test_requires_expiry(self):
        with self.assertRaises(core.ValidationError):
            s.schedule_maintenance(self.c, self.actor, "read_only", s._now(), None)

    def test_active_then_expired(self):
        start = "2026-01-01T00:00:00+00:00"
        end = "2026-01-01T02:00:00+00:00"
        wid = s.schedule_maintenance(self.c, self.actor, "read_only", start, end)
        self.assertIsNotNone(s.maintenance_status(self.c, tenant=self.rgo, now_iso="2026-01-01T01:00:00+00:00"))
        self.assertIsNone(s.maintenance_status(self.c, tenant=self.rgo, now_iso="2026-01-01T03:00:00+00:00"))  # expired

    def test_platform_maintenance_requires_platform_perm(self):
        weak = self._actor({"maintenance.manage"})   # tenant-level only
        with self.assertRaises(core.ForbiddenError):
            s.schedule_maintenance(self.c, weak, "full_lock", s._now(),
                                   (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat(), scope="platform")


class TestRetention(Base):
    def test_audit_retention_floor(self):
        with self.assertRaises(core.ForbiddenError):
            s.set_retention(self.c, self.actor, "audit", 30)   # below 2555 floor

    def test_legal_hold_blocks_deletion(self):
        s.set_retention(self.c, self.actor, "documents", 365, legal_hold=True)
        self.assertFalse(s.can_delete_category(self.c, self.rgo, "documents"))
        s.set_retention(self.c, self.actor, "documents", 365, legal_hold=False)
        self.assertTrue(s.can_delete_category(self.c, self.rgo, "documents"))


class TestBackupRestore(Base):
    def test_backup_execute_and_metadata(self):
        r = s.execute_backup(self.c, self.actor)
        self.assertEqual(r["status"], "SUCCESS")
        self.assertTrue(r["checksum"])

    def test_restore_requires_separate_approver(self):
        bk = s.execute_backup(self.c, self.actor)
        rid = s.request_restore(self.c, self.actor, bk["backup_run_id"])
        s.validate_restore(self.c, self.actor, rid)
        with self.assertRaises(core.ForbiddenError):
            s.approve_restore(self.c, self.actor, rid)   # requester == approver
        approver = self._actor({"restore.approve"}, id=2, role="admin")
        self.assertTrue(s.approve_restore(self.c, approver, rid))

    def test_restore_unauthorized_denied(self):
        bk = s.execute_backup(self.c, self.actor)
        weak = self._actor({"backup.view"})
        with self.assertRaises(core.ForbiddenError):
            s.request_restore(self.c, weak, bk["backup_run_id"])


class TestBrandingTemplates(Base):
    def test_branding_rejects_scripts(self):
        with self.assertRaises(core.ValidationError):
            s.set_branding(self.c, self.actor, "document_header", value="<script>alert(1)</script>")

    def test_branding_persists(self):
        s.set_branding(self.c, self.actor, "primary_color", value="#0A5")
        self.assertEqual(s.get_branding(self.c, self.actor)["primary_color"]["value"], "#0A5")

    def test_template_variable_allowlist(self):
        with self.assertRaises(core.ValidationError):
            s.create_template(self.c, self.actor, "quote_email", "Quote", "email",
                              "Hello {{customer_name}} {{secret_field}}", allowed_variables=["customer_name"])
        tid = s.create_template(self.c, self.actor, "quote_email", "Quote", "email",
                                "Hello {{customer_name}}", allowed_variables=["customer_name"])
        self.assertTrue(tid)

    def test_template_publish_immutable_checksum(self):
        tid = s.create_template(self.c, self.actor, "inv_email", "Inv", "email", "Total {{total}}", allowed_variables=["total"])
        r = s.publish_template(self.c, self.actor, tid)
        self.assertTrue(r["checksum"])


class TestIntegrityAndMigration(Base):
    def test_integrity_healthy(self):
        rep = s.integrity_checks(self.c, self.actor)
        self.assertTrue(rep["healthy"])
        self.assertTrue(any(c["check"] == "tenant_policy_below_platform_minimum" for c in rep["checks"]))

    def test_migration_zero_drift(self):
        m = s.classify_existing(self.c)
        self.assertEqual((m["financial_differences"], m["operational_status_differences"], m["security_policy_weakening"]), (0, 0, 0))
        self.assertIn("APP_SECRET", m["settings_retained_in_env"])

    def test_role_grants(self):
        pa = ap.effective_role_grants(self.c, ap.role_by_code(self.c, "RGO", "platform_admin")["id"])
        self.assertIn("platform.settings.*", pa)
        self.assertIn("security.policy.*", pa)

    def test_settings_do_not_change_financials(self):
        a = self.actor
        cid = core.create_customer(self.c, a, "Set Fin Co")
        bid = core.create_booking(self.c, a, cid, "CRANE_RENTAL", "x", 1)
        core.review_booking(self.c, a, bid); core.ready_for_quotation(self.c, a, bid)
        qid = core.create_quotation(self.c, a, bid, [{"kind": "crane", "description": "x", "qty": 2, "days": 1, "rate": 300000}])
        before = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        s.set_value(self.c, a, "platform.default_currency", "USD", scope="tenant")
        s.set_value(self.c, a, "fiscal.year_start_month", "4", scope="tenant")
        after = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        self.assertEqual((before["tax"], before["total"]), (72000, 672000))
        self.assertEqual((after["tax"], after["total"]), (72000, 672000))   # UNCHANGED (tax stays in Phase-2 model)


class TestPhase6Api(unittest.TestCase):
    """Drives the Phase 6 /admin/settings* endpoints through the real HTTP router."""
    @classmethod
    def setUpClass(cls):
        import server
        import db as _db
        server._conn = _db.connect(":memory:")
        cls.server = server
        try:
            core.create_user(server._conn, "p6admin@r", "demo1234", "admin", "P6 Admin")
        except core.ConflictError:
            pass

    def _call(self, method, path, body=None):
        fn, params = self.server._match(method, path)
        assert fn, f"no route for {method} {path}"
        tok = self.server._match("POST", "/login")[0](None, {"email": "p6admin@r", "password": "demo1234"}, {})["token"]
        actor = core.actor_for(self.server._conn, tok)
        return fn(actor, body or {}, params or {})

    def test_settings_crud_via_api(self):
        self._call("POST", "/admin/settings/values", {"key": "platform.name", "value": "Api Platform", "scope": "platform"})
        eff = self._call("POST", "/admin/settings/effective", {"key": "platform.name"})
        self.assertEqual(eff["value"], "Api Platform")

    def test_security_floor_via_api(self):
        # weakening a security invariant below the platform floor is rejected through the API too
        with self.assertRaises(core.ForbiddenError):
            self._call("POST", "/admin/settings/values", {"key": "auth.password.min_length", "value": "4", "scope": "tenant"})

    def test_flags_and_modules_via_api(self):
        self._call("POST", "/admin/settings/flags", {"key": "api_beta", "platform_default": False})
        self._call("POST", "/admin/settings/flags/api_beta/override", {"enabled": True})
        flags = self._call("GET", "/admin/settings/flags")["flags"]
        self.assertTrue(any(f["key"] == "api_beta" for f in flags))
        mods = self._call("GET", "/admin/settings/modules")["modules"]
        self.assertTrue(any(m["code"] == "booking" for m in mods))

    def test_integrity_via_api(self):
        rep = self._call("GET", "/admin/settings/integrity")
        self.assertIn("summary", rep)


if __name__ == "__main__":
    unittest.main(verbosity=2)
