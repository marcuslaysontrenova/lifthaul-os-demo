"""Quotation pricing & rate-override subsystem tests.

Covers the LiftHaul quotation rate/override enhancement: governed rate catalog, editable
quoted rate, internal cost/margin separation & masking, override governance, approval
escalation, server-side recalculation authority, versioning, audit, and restart persistence.
"""
import os
import tempfile
import unittest

import admin_platform
import core
import db
import policy
import rates


def _crane_line(quoted_rate=None, discount_pct=0, internal_cost=None, override_reason=None):
    line = {"equipment_code": "CRANE-100T", "description": "100t All-Terrain Crane",
            "qty": 1, "days": 3, "discount_pct": discount_pct}
    if quoted_rate is not None:
        line["quoted_rate"] = quoted_rate
    if internal_cost is not None:
        line["internal_cost"] = internal_cost
    if override_reason is not None:
        line["override_reason"] = override_reason
    return line


class PricingSubsystemTests(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.super = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
        self.encoder_id = admin_platform.create_user(
            self.c, self.super, "enc@rgo.demo", "Demo1234Xy", "booking_quotation_administrator", "Enc")
        self.approver_id = admin_platform.create_user(
            self.c, self.super, "chk@rgo.demo", "Demo1234Xy", "approver", "Chk")
        self.finance_id = admin_platform.create_user(
            self.c, self.super, "fin@rgo.demo", "Demo1234Xy", "finance_admin", "Fin")
        self.encoder = self._actor("enc@rgo.demo")
        self.approver = self._actor("chk@rgo.demo")
        self.finance = self._actor("fin@rgo.demo")
        self.admin = core.actor_for(self.c, core.login(
            self.c, *self._legacy_admin()))
        self.customer_id = core.create_customer(self.c, self.admin, "Aboitiz Power")

    def _legacy_admin(self):
        core.create_user(self.c, "adm@rgo.demo", "pw", "admin", "Adm")
        return ("adm@rgo.demo", "pw")

    def _actor(self, email):
        a = core.actor_for(self.c, core.login(self.c, email, "Demo1234Xy"))
        return admin_platform.apply_rbac(self.c, a)

    def _ready_booking(self):
        bid = core.create_booking(self.c, self.encoder, self.customer_id, "Crane", "Transformer", 40)
        core.review_booking(self.c, self.encoder, bid)
        core.ready_for_quotation(self.c, self.encoder, bid)
        return bid

    # ---- 1. equipment selection loads default standard rate ----
    def test_rate_catalog_resolves_standard_rate(self):
        card = rates.resolve_rate(self.c, "CRANE-100T")
        self.assertIsNotNone(card)
        self.assertEqual(card["standard_rate"], 55000)
        self.assertEqual(card["internal_cost"], 39000)

    # ---- 2. quoted rate defaults to standard rate ----
    def test_quoted_rate_defaults_to_standard(self):
        bid = self._ready_booking()
        qid = core.create_quotation(self.c, self.encoder, bid, [_crane_line()])
        line = core.get_quotation(self.c, self.approver, qid)["lines"][0]
        self.assertEqual(line["standard_rate"], 55000)
        self.assertEqual(line["quoted_rate"], 55000)          # defaulted, no override
        self.assertEqual(line["rate_source"], "catalog")

    # ---- 3. authorized quoted-rate edit ----
    def test_authorized_quoted_rate_override(self):
        bid = self._ready_booking()
        qid = core.create_quotation(self.c, self.encoder, bid,
                                    [_crane_line(quoted_rate=58000, override_reason="Client tier premium")])
        line = core.get_quotation(self.c, self.approver, qid)["lines"][0]
        self.assertEqual(line["quoted_rate"], 58000)
        self.assertEqual(line["rate_source"], "override")

    # ---- 4. unauthorized quoted-rate edit denied ----
    def test_unauthorized_quoted_rate_override_denied(self):
        bid = self._ready_booking()
        # finance holds cost.edit but NOT rate.override; build a quote via a role lacking rate.override
        no_override = admin_platform.create_user(
            self.c, self.super, "noov@rgo.demo", "Demo1234Xy", "operational_user", "NoOv")
        # give it minimal create ability by cloning encoder then revoking rate.override
        admin_platform.clone_role(self.c, "RGO", "booking_quotation_administrator", "enc_no_ov", "Enc No Override", actor=self.super)
        role = admin_platform.role_by_code(self.c, "RGO", "enc_no_ov")
        admin_platform.revoke_permission(self.c, role["id"], "quotation.rate.override", actor=self.super)
        admin_platform.assign_role(self.c, no_override, role["id"], actor=self.super)
        actor = self._actor("noov@rgo.demo")
        with self.assertRaises(core.ForbiddenError):
            core.create_quotation(self.c, actor, bid,
                                  [_crane_line(quoted_rate=58000, override_reason="x")])

    # ---- 5. standard rate remains unchanged after quote override ----
    def test_master_standard_rate_unchanged_after_override(self):
        bid = self._ready_booking()
        core.create_quotation(self.c, self.encoder, bid,
                              [_crane_line(quoted_rate=58000, override_reason="premium")])
        card = rates.resolve_rate(self.c, "CRANE-100T")
        self.assertEqual(card["standard_rate"], 55000)        # master untouched

    # ---- 6. internal cost unauthorized access denied (masking) ----
    def test_internal_cost_and_margin_masked_for_encoder(self):
        bid = self._ready_booking()
        qid = core.create_quotation(self.c, self.encoder, bid, [_crane_line()])
        enc_view = core.get_quotation(self.c, self.encoder, qid)
        self.assertIsNone(enc_view["lines"][0]["internal_cost"])   # encoder cannot see vendor cost
        self.assertIsNone(enc_view["lines"][0]["margin_percent"])
        self.assertIn("carrier_cost", enc_view["redacted_fields"])
        fin_view = core.get_quotation(self.c, self.finance, qid)
        self.assertEqual(fin_view["lines"][0]["internal_cost"], 39000)
        self.assertIsNotNone(fin_view["lines"][0]["margin_percent"])

    # ---- 7. margin calculation correctness ----
    def test_margin_calculation(self):
        bid = self._ready_booking()
        qid = core.create_quotation(self.c, self.encoder, bid, [_crane_line()])
        line = core.get_quotation(self.c, self.finance, qid)["lines"][0]
        # subtotal 55000*3 = 165000; internal 39000*3 = 117000; gp = 48000; margin = 29.09%
        self.assertEqual(line["subtotal"], 165000)
        self.assertEqual(line["gross_profit"], 48000)
        self.assertAlmostEqual(line["margin_percent"], 29.09, places=1)

    # ---- 8. discount override beyond policy requires permission ----
    def test_discount_override_requires_permission(self):
        bid = self._ready_booking()
        admin_platform.clone_role(self.c, "RGO", "booking_quotation_administrator", "enc_no_disc", "No Disc", actor=self.super)
        role = admin_platform.role_by_code(self.c, "RGO", "enc_no_disc")
        admin_platform.revoke_permission(self.c, role["id"], "quotation.discount.override", actor=self.super)
        uid = admin_platform.create_user(self.c, self.super, "nd@rgo.demo", "Demo1234Xy", "operational_user", "ND")
        admin_platform.assign_role(self.c, uid, role["id"], actor=self.super)
        actor = self._actor("nd@rgo.demo")
        with self.assertRaises(core.ForbiddenError):
            core.create_quotation(self.c, actor, bid, [_crane_line(discount_pct=25)])
        # authorized encoder can apply the same discount
        qid = core.create_quotation(self.c, self.encoder, bid, [_crane_line(discount_pct=25)])
        self.assertIsNotNone(qid)

    # ---- 9. override reason requirement ----
    def test_material_override_requires_reason(self):
        bid = self._ready_booking()
        with self.assertRaises(core.ValidationError):
            core.create_quotation(self.c, self.encoder, bid, [_crane_line(quoted_rate=70000)])  # +27%, no reason
        # with reason it succeeds
        qid = core.create_quotation(self.c, self.encoder, bid,
                                    [_crane_line(quoted_rate=70000, override_reason="Rush weekend mobilization")])
        self.assertIsNotNone(qid)

    # ---- 10. approval escalation on rate variance ----
    def test_rate_variance_triggers_approval(self):
        bid = self._ready_booking()
        # small quote total (below amount threshold) but big rate variance → approval still required
        qid = core.create_quotation(self.c, self.encoder, bid,
                                    [_crane_line(quoted_rate=70000, override_reason="premium")])
        q = core.get_quotation(self.c, self.approver, qid)
        import json
        snap = json.loads(q["approval_snapshot"])
        self.assertTrue(snap["required"])
        self.assertTrue(any("variance" in r for r in snap["reasons"]))

    # ---- 11 & 12. server-side recalculation authoritative; tampered total ignored ----
    def test_server_recalculates_and_ignores_client_totals(self):
        bid = self._ready_booking()
        # client sends absurd COMPUTED fields — server must ignore and recompute from the inputs.
        # (quoted_rate/qty/days are legitimate inputs; subtotal/amount/gross_profit are outputs.)
        line = _crane_line()
        line.update({"subtotal": 999999, "amount": 999999, "gross_profit": 999999, "margin_percent": 999})
        qid = core.create_quotation(self.c, self.encoder, bid, [line])
        got = core.get_quotation(self.c, self.finance, qid)["lines"][0]
        self.assertEqual(got["subtotal"], 165000)             # authoritative, not the tampered 999999
        self.assertEqual(got["gross_profit"], 48000)

    # ---- 13. locked quotation cannot be edited ----
    def test_locked_quotation_cannot_be_edited(self):
        bid = self._ready_booking()
        # a large-variance override forces approval, so the quote reaches a locked 'approved' state
        qid = core.create_quotation(self.c, self.encoder, bid,
                                    [_crane_line(quoted_rate=80000, override_reason="premium")])
        self.assertEqual(core.submit_quotation(self.c, self.encoder, qid), "pending_approval")
        core.approve_quotation(self.c, self.approver, qid)     # now approved/locked
        with self.assertRaises(core.ConflictError):
            core.update_quotation_draft(self.c, self.encoder, qid, discount_pct=5)

    # ---- 14 & 15. revision creates a new version; previous remains unchanged ----
    def test_revision_creates_new_version_preserving_prior(self):
        bid = self._ready_booking()
        qid1 = core.create_quotation(self.c, self.encoder, bid, [_crane_line()])
        v1 = core.get_quotation(self.c, self.finance, qid1)
        # revising produces a NEW version and supersedes the prior; the prior row is never mutated
        qid2 = core.create_quotation(self.c, self.encoder, bid,
                                     [_crane_line(quoted_rate=60000, override_reason="premium")])
        self.assertNotEqual(qid1, qid2)
        v2 = core.get_quotation(self.c, self.finance, qid2)
        self.assertEqual(v2["version"], v1["version"] + 1)
        # prior version preserved (superseded, original numbers intact)
        prior = self.c.execute("SELECT status,superseded,total FROM quotations WHERE id=?", (qid1,)).fetchone()
        self.assertEqual(prior["superseded"], 1)
        self.assertEqual(prior["total"], v1["total"])          # historical value unchanged

    # ---- 16. audit history ----
    def test_override_is_audited(self):
        bid = self._ready_booking()
        qid = core.create_quotation(self.c, self.encoder, bid,
                                    [_crane_line(quoted_rate=58000, override_reason="Client premium")])
        row = self.c.execute(
            "SELECT new_value FROM audit_logs WHERE action='quotation.rate_override' AND entity_id=?",
            (qid,)).fetchone()
        self.assertIsNotNone(row)
        self.assertIn("58000", row["new_value"])
        self.assertIn("Client premium", row["new_value"])

    # ---- rate card is effective-dated: edit never overwrites history ----
    def test_rate_card_edit_creates_new_version(self):
        rid = rates.create_rate_card(self.c, self.finance, "CRANE-TEST", "Test Crane", 40000,
                                     internal_cost=28000, min_rate=30000)
        new_rid = rates.update_rate_card(self.c, self.finance, rid, standard_rate=45000)
        self.assertNotEqual(rid, new_rid)
        old = self.c.execute("SELECT standard_rate,superseded,version FROM rate_cards WHERE id=?", (rid,)).fetchone()
        new = self.c.execute("SELECT standard_rate,superseded,version FROM rate_cards WHERE id=?", (new_rid,)).fetchone()
        self.assertEqual(old["standard_rate"], 40000)          # history preserved, not overwritten
        self.assertEqual(old["superseded"], 1)
        self.assertEqual(new["standard_rate"], 45000)
        self.assertEqual(new["version"], 2)
        active = rates.resolve_rate(self.c, "CRANE-TEST")
        self.assertEqual(active["standard_rate"], 45000)       # resolver returns latest active

    # ---- unauthorized cost edit denied ----
    def test_internal_cost_edit_requires_finance_permission(self):
        bid = self._ready_booking()
        # encoder tries to set internal_cost different from catalog → needs carrier_cost.edit (finance-only)
        with self.assertRaises(core.ForbiddenError):
            core.create_quotation(self.c, self.encoder, bid, [_crane_line(internal_cost=10000)])


class RestartPersistenceTests(unittest.TestCase):
    def test_pricing_survives_reconnect(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            c = db.connect(path)
            sup = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
            enc_id = admin_platform.create_user(c, sup, "e@rgo.demo", "Demo1234Xy",
                                                "booking_quotation_administrator", "E")
            core.create_user(c, "a@rgo.demo", "pw", "admin", "A")
            adm = core.actor_for(c, core.login(c, "a@rgo.demo", "pw"))
            enc = admin_platform.apply_rbac(c, core.actor_for(c, core.login(c, "e@rgo.demo", "Demo1234Xy")))
            fin_id = admin_platform.create_user(c, sup, "f@rgo.demo", "Demo1234Xy", "finance_admin", "F")
            fin = admin_platform.apply_rbac(c, core.actor_for(c, core.login(c, "f@rgo.demo", "Demo1234Xy")))
            cust = core.create_customer(c, adm, "ClientCo")
            bid = core.create_booking(c, enc, cust, "Crane", "Load", 40)
            core.review_booking(c, enc, bid)
            core.ready_for_quotation(c, enc, bid)
            qid = core.create_quotation(c, enc, bid,
                                        [{"equipment_code": "CRANE-100T", "qty": 1, "days": 3,
                                          "quoted_rate": 58000, "override_reason": "premium"}])
            c.commit()
            c.close()

            c2 = db.connect(path)                                # reopen — migration must re-add columns
            fin2 = admin_platform.apply_rbac(c2, core.actor_for(c2, core.login(c2, "f@rgo.demo", "Demo1234Xy")))
            line = core.get_quotation(c2, fin2, qid)["lines"][0]
            self.assertEqual(line["quoted_rate"], 58000)        # override persisted across restart
            self.assertEqual(line["standard_rate"], 55000)
            self.assertEqual(line["internal_cost"], 39000)
            self.assertEqual(line["subtotal"], 174000)
            self.assertEqual(line["rate_source"], "override")
            c2.close()
        finally:
            try:
                os.unlink(path)
            except PermissionError:
                pass   # Windows may hold the handle briefly; temp file is harmless


class RateCardTenantIsolationTests(unittest.TestCase):
    """A tenant must never resolve or list another tenant's custom rate card (Item 1 gap
    introduced by the pricing subsystem — closed here)."""
    def setUp(self):
        self.c = db.connect(":memory:")
        self.sup = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
        self.tA = admin_platform.create_tenant(self.c, "RCA", "Rate Tenant A")
        self.tB = admin_platform.create_tenant(self.c, "RCB", "Rate Tenant B")
        self.finA = self._finance("fa@rc", self.tA)
        self.finB = self._finance("fb@rc", self.tB)

    def _finance(self, email, tid):
        import tenant
        uid = core.create_user(self.c, email, "demo1234", "finance_admin", "Fin")
        tenant.bind_user_tenant(self.c, None, uid, tid)
        a = core.actor_for(self.c, core.login(self.c, email, "demo1234"))
        return admin_platform.apply_rbac(self.c, a)

    def test_tenant_cannot_resolve_or_list_other_tenants_rate_card(self):
        # same equipment code, different tenant-specific standard rates
        rates.create_rate_card(self.c, self.finA, "SHARED-CODE", "Crane A", 90000, internal_cost=60000)
        rates.create_rate_card(self.c, self.finB, "SHARED-CODE", "Crane B", 50000, internal_cost=30000)
        cardA = rates.resolve_rate(self.c, "SHARED-CODE", tenant_id=self.tA)
        cardB = rates.resolve_rate(self.c, "SHARED-CODE", tenant_id=self.tB)
        self.assertEqual(cardA["standard_rate"], 90000)          # A resolves ONLY A's card
        self.assertEqual(cardB["standard_rate"], 50000)          # B resolves ONLY B's card
        # list is tenant-scoped: A never sees B's SHARED-CODE card
        codesA = [(r["equipment_code"], r["standard_rate"]) for r in rates.list_rate_cards(self.c, self.finA)]
        self.assertIn(("SHARED-CODE", 90000), codesA)
        self.assertNotIn(("SHARED-CODE", 50000), codesA)

    def test_global_seed_cards_visible_to_all_tenants(self):
        # NULL-tenant seeded catalog remains shared (single-tenant/legacy compatible)
        self.assertIsNotNone(rates.resolve_rate(self.c, "CRANE-100T", tenant_id=self.tA))
        self.assertIsNotNone(rates.resolve_rate(self.c, "CRANE-100T", tenant_id=self.tB))


if __name__ == "__main__":
    unittest.main()
