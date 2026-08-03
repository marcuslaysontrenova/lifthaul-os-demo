"""LiftHaul OS — Phase 10: SaaS commercial layer.

Proves: governed product catalog + IMMUTABLE plan versions; subscription lifecycle; idempotent +
fail-closed tenant provisioning; entitlement enforcement (RBAC AND entitlement — never a replacement);
module/feature dependency; idempotent usage metering; ATOMIC quotas (no negative remaining / no double
count); reserve→commit/release; overage; IMMUTABLE billing evidence (Phase-2 tax, never recalculated);
trials + upgrade/downgrade (non-destructive) + renewal + suspension + reactivation + termination
(legal-hold preserved); marketplace fee/payout snapshots; promotions (SoD + limits); commercial
exceptions; tenant isolation — all with zero financial / operational / entitlement-loss / tenant-access
drift.
"""
import unittest

import db
import core
import admin_platform as ap
import saas


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.a = {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": None}     # platform
        self.a2 = {"id": 2, "role": "admin", "perms": {"*"}, "tenant_id": None}

    def _actor(self, perms, id=9, role="sales", tenant=None):
        return {"id": id, "role": role, "perms": set(perms), "tenant_id": tenant}

    def _published_plan(self, code="starter", price=5000, ents=None):
        if not self.c.execute("SELECT 1 FROM products WHERE code='lifthaul'").fetchone():
            saas.create_product(self.c, self.a, "lifthaul", "LiftHaul OS")
        pid = saas.create_plan(self.c, self.a, "lifthaul", code, code)
        v = self.c.execute("SELECT id FROM plan_versions WHERE plan_id=? AND version_no=1", (pid,)).fetchone()["id"]
        saas.set_plan_version(self.c, self.a, v, base_price=price, trial_days=14)
        for (kind, ecode, mode, qty) in (ents or [("module", "crm", "included", None), ("module", "booking", "included", None),
                                                  ("feature", "active_users", "limited", 3),
                                                  ("module", "ai_assistance", "excluded", None)]):
            saas.add_entitlement(self.c, self.a, v, kind, ecode, mode, quantity=qty)
        saas.validate_plan_version(self.c, self.a, v)
        saas.approve_plan_version(self.c, self.a2, v)
        saas.publish_plan_version(self.c, self.a, v, "go")
        return v

    def _tenant(self, code="ACME", plan="starter", evidence="SOW-001"):
        return saas.provision_tenant(self.c, self.a, code, code + " Ltd", "lifthaul", plan, "admin@" + code.lower(),
                                     commercial_evidence=evidence)


class TestProductsAndPlans(Base):
    def test_duplicate_product(self):
        saas.create_product(self.c, self.a, "p1", "P1")
        with self.assertRaises(core.ConflictError):
            saas.create_product(self.c, self.a, "p1", "P1b")

    def test_immutable_published_plan(self):
        v = self._published_plan()
        with self.assertRaises(core.ForbiddenError):
            saas.set_plan_version(self.c, self.a, v, base_price=9999)

    def test_invalid_price(self):
        saas.create_product(self.c, self.a, "lifthaul", "L")
        pid = saas.create_plan(self.c, self.a, "lifthaul", "bad", "Bad")
        v = self.c.execute("SELECT id FROM plan_versions WHERE plan_id=?", (pid,)).fetchone()["id"]
        with self.assertRaises(core.ValidationError):
            saas.set_plan_version(self.c, self.a, v, base_price=-5)

    def test_unknown_module_entitlement_rejected(self):
        saas.create_product(self.c, self.a, "lifthaul", "L")
        pid = saas.create_plan(self.c, self.a, "lifthaul", "x", "X")
        v = self.c.execute("SELECT id FROM plan_versions WHERE plan_id=?", (pid,)).fetchone()["id"]
        with self.assertRaises(core.ValidationError):
            saas.add_entitlement(self.c, self.a, v, "module", "not_a_real_module", "included")

    def test_new_version_copies_entitlements(self):
        self._published_plan()
        nv = saas.create_plan_version(self.c, self.a, "starter", "v2")
        n = self.c.execute("SELECT COUNT(*) c FROM plan_entitlements WHERE plan_version_id=?", (nv,)).fetchone()["c"]
        self.assertGreaterEqual(n, 3)


class TestProvisioning(Base):
    def test_successful_and_idempotent(self):
        self._published_plan()
        r1 = self._tenant()
        r2 = self._tenant()   # idempotent
        self.assertEqual(r1["tenant_id"], r2["tenant_id"])
        self.assertEqual(r1["status"], "ACTIVATED")

    def test_fail_closed_no_partial_activation(self):
        self._published_plan()
        with self.assertRaises(core.ConflictError):
            saas.provision_tenant(self.c, self.a, "FAILCO", "Fail", "lifthaul", "starter", "admin@fail",
                                  commercial_evidence="X", force_fail_step="activate")
        t = ap.get_tenant(self.c, "FAILCO")
        # tenant exists but is NOT active (fail-closed), and its subscription is not ACTIVE
        self.assertNotEqual(t["status"], "ACTIVE")
        active = self.c.execute("SELECT COUNT(*) c FROM subscriptions WHERE tenant_id=? AND status='ACTIVE'", (t["id"],)).fetchone()["c"]
        self.assertEqual(active, 0)

    def test_activation_requires_commercial_evidence(self):
        self._published_plan()
        # create a tenant + subscription with NO evidence, then attempt activation
        tid = ap.create_tenant(self.c, "NOEV", "No Evidence", actor=self.a)
        sub = saas.create_subscription(self.c, self.a, tid, "lifthaul", "starter", commercial_evidence=None)
        with self.assertRaises(core.ForbiddenError):
            saas.activate_subscription(self.c, self.a, sub, require_evidence=True)


class TestEntitlements(Base):
    def test_entitled_and_excluded(self):
        self._published_plan()
        r = self._tenant()
        ta = self._actor({"*"}, id=10, tenant=r["tenant_id"])
        self.assertTrue(saas.check_entitlement(self.c, ta, "crm")["allowed"])
        self.assertEqual(saas.check_entitlement(self.c, ta, "ai_assistance")["denial_category"], "feature_not_included")

    def test_subscription_inactive_denial(self):
        self._published_plan()
        r = self._tenant()
        saas.suspend_subscription(self.c, self.a, r["subscription_id"], "nonpayment")
        ta = self._actor({"*"}, id=10, tenant=r["tenant_id"])
        self.assertEqual(saas.check_entitlement(self.c, ta, "crm")["denial_category"], "subscription_inactive")

    def test_require_entitlement_raises(self):
        self._published_plan()
        r = self._tenant()
        ta = self._actor({"*"}, id=10, tenant=r["tenant_id"])
        with self.assertRaises(core.ForbiddenError):
            saas.require_entitlement(self.c, ta, "ai_assistance")

    def test_entitlement_does_not_replace_rbac(self):
        # a user with the entitlement but WITHOUT the RBAC permission is still denied by core.require
        self._published_plan()
        r = self._tenant()
        weak = self._actor({"customer.view"}, id=11, tenant=r["tenant_id"])   # lacks saas.usage.manage
        with self.assertRaises(core.ForbiddenError):
            saas.record_usage(self.c, weak, "active_users", 1, idem_key="rbac")


class TestUsageAndQuota(Base):
    def _acme(self):
        self._published_plan()
        r = self._tenant()
        saas.set_quota(self.c, self.a, r["tenant_id"], "active_users", 3, hard_limit=True)
        return r

    def test_metering_idempotent(self):
        r = self._acme()
        ta = self._actor({"*"}, id=10, tenant=r["tenant_id"])
        saas.record_usage(self.c, ta, "active_users", 1, idem_key="u1")
        dup = saas.record_usage(self.c, ta, "active_users", 1, idem_key="u1")
        self.assertTrue(dup["idempotent"])
        self.assertEqual(saas.quota_status(self.c, ta, "active_users")["consumed"], 1)

    def test_quota_hard_stop_atomic(self):
        r = self._acme()
        ta = self._actor({"*"}, id=10, tenant=r["tenant_id"])
        for i in range(3):
            saas.record_usage(self.c, ta, "active_users", 1, idem_key="u" + str(i))
        with self.assertRaises(core.ForbiddenError):
            saas.record_usage(self.c, ta, "active_users", 1, idem_key="u4")
        self.assertGreaterEqual(saas.quota_status(self.c, ta, "active_users")["remaining"], 0)   # never negative

    def test_reserve_commit_release(self):
        self._published_plan()
        r = self._tenant()
        saas.set_quota(self.c, self.a, r["tenant_id"], "ai_executions", 100, hard_limit=True)
        res = saas.reserve_usage(self.c, self.a, "ai_executions", 5, idem_key="r1", tenant_id=r["tenant_id"])
        self.assertEqual(saas.quota_status(self.c, self.a, "ai_executions", tenant_id=r["tenant_id"])["remaining"], 95)
        saas.commit_reservation(self.c, self.a, res["reservation_id"])
        self.assertEqual(saas.quota_status(self.c, self.a, "ai_executions", tenant_id=r["tenant_id"])["consumed"], 5)
        res2 = saas.reserve_usage(self.c, self.a, "ai_executions", 5, idem_key="r2", tenant_id=r["tenant_id"])
        saas.release_reservation(self.c, self.a, res2["reservation_id"])
        self.assertEqual(saas.quota_status(self.c, self.a, "ai_executions", tenant_id=r["tenant_id"])["reserved"], 0)

    def test_reservation_idempotent(self):
        self._published_plan()
        r = self._tenant()
        saas.set_quota(self.c, self.a, r["tenant_id"], "ai_executions", 100)
        a1 = saas.reserve_usage(self.c, self.a, "ai_executions", 5, idem_key="ridem", tenant_id=r["tenant_id"])
        a2 = saas.reserve_usage(self.c, self.a, "ai_executions", 5, idem_key="ridem", tenant_id=r["tenant_id"])
        self.assertEqual(a1["reservation_id"], a2["reservation_id"])

    def test_overage(self):
        self._published_plan()
        r = self._tenant()
        saas.set_quota(self.c, self.a, r["tenant_id"], "api_calls", 2, hard_limit=True, overage_allowed=True)
        ta = self._actor({"*"}, id=10, tenant=r["tenant_id"])
        for i in range(4):
            saas.record_usage(self.c, ta, "api_calls", 1, idem_key="a" + str(i))
        over = self.c.execute("SELECT COUNT(*) c FROM overage_charges WHERE tenant_id=?", (r["tenant_id"],)).fetchone()["c"]
        self.assertGreaterEqual(over, 1)


class TestBilling(Base):
    def test_billing_evidence_immutable_with_tax(self):
        self._published_plan(price=5000)
        r = self._tenant()
        be = saas.generate_billing_evidence(self.c, self.a, r["subscription_id"], "2026-08-01", "2026-08-31")
        self.assertEqual((be["subtotal"], be["tax"], be["total"]), (5000, 600, 5600))   # Phase-2 12% VAT
        again = saas.generate_billing_evidence(self.c, self.a, r["subscription_id"], "2026-08-01", "2026-08-31")
        self.assertTrue(again["idempotent"])                                             # never recalculated

    def test_billing_with_discount(self):
        self._published_plan(price=1000)
        r = self._tenant()
        be = saas.generate_billing_evidence(self.c, self.a, r["subscription_id"], "2026-09-01", "2026-09-30", discount=100)
        self.assertEqual(be["subtotal"], 900)


class TestLifecycle(Base):
    def test_trial_expiry_controlled(self):
        self._published_plan()
        tid = ap.create_tenant(self.c, "TRIALCO", "Trial", actor=self.a)
        sub = saas.create_subscription(self.c, self.a, tid, "lifthaul", "starter", commercial_evidence="trial")
        saas.start_trial(self.c, self.a, sub, trial_days=1)
        saas.expire_trials(self.c, as_of="2099-01-01")
        # controlled restriction (tenant suspended), data NOT deleted
        self.assertEqual(self.c.execute("SELECT status FROM tenants WHERE id=?", (tid,)).fetchone()["status"], "SUSPENDED")
        self.assertEqual(self.c.execute("SELECT status FROM subscriptions WHERE id=?", (sub,)).fetchone()["status"], "EXPIRED")

    def test_downgrade_non_destructive(self):
        self._published_plan(code="starter")
        self._published_plan(code="free", price=0, ents=[("module", "crm", "included", None)])
        r = self._tenant(code="ACME", plan="starter")
        imp = saas.downgrade_impact(self.c, self.a, r["subscription_id"], "free")
        self.assertFalse(imp["destructive"])
        self.assertIn("booking", imp["modules_removed"])
        saas.downgrade_subscription(self.c, self.a, r["subscription_id"], "free")
        # data (users) preserved
        self.assertGreaterEqual(self.c.execute("SELECT COUNT(*) c FROM users WHERE tenant_id=?", (r["tenant_id"],)).fetchone()["c"], 1)

    def test_renewal_keeps_pricing_version(self):
        self._published_plan()
        r = self._tenant()
        res = saas.renew_subscription(self.c, self.a, r["subscription_id"])
        self.assertIn("plan_version", res)

    def test_suspend_reactivate(self):
        self._published_plan()
        r = self._tenant()
        saas.suspend_subscription(self.c, self.a, r["subscription_id"], "nonpayment")
        self.assertEqual(self.c.execute("SELECT status FROM tenants WHERE id=?", (r["tenant_id"],)).fetchone()["status"], "SUSPENDED")
        saas.reactivate_subscription(self.c, self.a, r["subscription_id"])
        self.assertEqual(self.c.execute("SELECT status FROM tenants WHERE id=?", (r["tenant_id"],)).fetchone()["status"], "ACTIVE")

    def test_termination_preserves_legal_hold(self):
        self._published_plan()
        r = self._tenant()
        import settings as sysc
        sysc.set_retention(self.c, {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": r["tenant_id"]}, "documents", 365, legal_hold=True)
        term = saas.terminate_subscription(self.c, self.a, r["subscription_id"])
        self.assertTrue(term["data_preserved"])
        self.assertTrue(term["legal_hold"])

    def test_invalid_transition(self):
        self._published_plan()
        r = self._tenant()
        saas.terminate_subscription(self.c, self.a, r["subscription_id"])
        with self.assertRaises(core.ConflictError):
            saas.reactivate_subscription(self.c, self.a, r["subscription_id"])   # cannot reactivate TERMINATED


class TestMarketplaceAndPromotions(Base):
    def test_marketplace_fee_snapshot(self):
        self._published_plan()
        r = self._tenant()
        saas.create_fee_policy(self.c, self.a, "pct10", "percentage", 10, min_fee=50)
        mt = saas.record_marketplace_transaction(self.c, self.a, r["tenant_id"], "BK-1", 10000, 9000, "pct10")
        self.assertEqual((mt["platform_fee"], mt["carrier_payout"]), (1000, 8000))
        self.assertEqual(mt["fee_policy_version"], 1)

    def test_promotion_self_approval_blocked(self):
        with self.assertRaises(core.ForbiddenError):
            saas.create_promotion(self.c, self.a, "SELF", "percentage", 10, approver=self.a["id"])

    def test_promotion_expiry_and_limit(self):
        saas.create_promotion(self.c, self.a, "OLD", "percentage", 10, ends_at="2020-01-01", approver=self.a2["id"])
        self._published_plan()
        r = self._tenant()
        with self.assertRaises(core.ForbiddenError):
            saas.redeem_promotion(self.c, self.a, "OLD", r["subscription_id"])

    def test_commercial_exception_requires_end_and_sod(self):
        self._published_plan()
        r = self._tenant()
        with self.assertRaises(core.ValidationError):
            saas.create_exception(self.c, self.a, r["tenant_id"], "quota_increase", "active_users", "temp", None, self.a2["id"])
        with self.assertRaises(core.ForbiddenError):
            saas.create_exception(self.c, self.a, r["tenant_id"], "quota_increase", "active_users", "temp", "2099-01-01", self.a["id"])


class TestSecurityAndMigration(Base):
    def test_unauthorized_plan_change(self):
        self._published_plan()
        weak = self._actor({"saas.plan.view"}, id=12)
        with self.assertRaises(core.ForbiddenError):
            saas.create_plan(self.c, weak, "lifthaul", "unauth", "U")

    def test_unauthorized_suspension(self):
        self._published_plan()
        r = self._tenant()
        weak = self._actor({"saas.subscription.view"}, id=13)
        with self.assertRaises(core.ForbiddenError):
            saas.suspend_subscription(self.c, weak, r["subscription_id"], "x")

    def test_migration_zero_drift(self):
        m = saas.classify_existing(self.c)
        self.assertEqual((m["financial_differences"], m["operational_status_differences"], m["entitlement_losses"], m["tenant_access_changes"]), (0, 0, 0, 0))
        self.assertEqual((m["fabricated_contracts"], m["fabricated_pricing"]), (0, 0))

    def test_role_grants(self):
        pa = ap.effective_role_grants(self.c, ap.role_by_code(self.c, "RGO", "platform_admin")["id"])
        self.assertIn("saas.plan.*", pa)
        self.assertIn("saas.subscription.*", pa)

    def test_saas_does_not_change_freight_financials(self):
        a = {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": self.rgo}
        cid = core.create_customer(self.c, a, "SaaS Fin Co")
        bid = core.create_booking(self.c, a, cid, "CRANE_RENTAL", "x", 1)
        core.review_booking(self.c, a, bid); core.ready_for_quotation(self.c, a, bid)
        qid = core.create_quotation(self.c, a, bid, [{"kind": "crane", "description": "x", "qty": 2, "days": 1, "rate": 300000}])
        before = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        self._published_plan()
        saas.generate_billing_evidence(self.c, self.a, self._tenant()["subscription_id"], "2026-08-01", "2026-08-31")
        after = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        self.assertEqual((before["tax"], before["total"]), (72000, 672000))
        self.assertEqual((after["tax"], after["total"]), (72000, 672000))   # freight untouched by SaaS billing


class TestPhase10Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        import db as _db
        server._conn = _db.connect(":memory:")
        cls.server = server
        try:
            core.create_user(server._conn, "p10admin@r", "demo1234", "admin", "P10 Admin")
        except core.ConflictError:
            pass

    def _call(self, method, path, body=None):
        fn, params = self.server._match(method, path)
        assert fn, f"no route for {method} {path}"
        tok = self.server._match("POST", "/login")[0](None, {"email": "p10admin@r", "password": "demo1234"}, {})["token"]
        actor = core.actor_for(self.server._conn, tok)
        return fn(actor, body or {}, params or {})

    def test_products_and_plans_via_api(self):
        self._call("POST", "/admin/saas/products", {"code": "api_prod", "name": "API Product"})
        prods = self._call("GET", "/admin/saas/products")["products"]
        self.assertTrue(any(p["code"] == "api_prod" for p in prods))

    def test_subscriptions_listing_via_api(self):
        subs = self._call("GET", "/admin/saas/subscriptions")
        self.assertIn("subscriptions", subs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
