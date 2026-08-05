"""Access-model tests for Booking & Quotation role separation."""
import unittest

import admin_platform
import core
import db


def big_lines():
    return [{"kind": "crane", "description": "350t crane", "rate": 180000, "days": 4, "qty": 1}]


class BookingQuotationAccessTests(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.super = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
        self.encoder_id = admin_platform.create_user(
            self.c, self.super, "encoder@rgo.demo", "Demo1234Xy",
            "booking_quotation_administrator", "Encoder",
        )
        self.approver_id = admin_platform.create_user(
            self.c, self.super, "checker@rgo.demo", "Demo1234Xy", "approver", "Checker",
        )
        self.finance_id = admin_platform.create_user(
            self.c, self.super, "finance2@rgo.demo", "Demo1234Xy", "finance_admin", "Finance",
        )
        self.user_admin_id = admin_platform.create_user(
            self.c, self.super, "users@rgo.demo", "Demo1234Xy", "user_administrator", "User Admin",
        )
        self.admin_id = core.create_user(self.c, "legacy-admin@rgo.demo", "pw", "admin", "Admin")
        self.encoder = self._actor("encoder@rgo.demo", "Demo1234Xy")
        self.approver = self._actor("checker@rgo.demo", "Demo1234Xy")
        self.finance = self._actor("finance2@rgo.demo", "Demo1234Xy")
        self.user_admin = self._actor("users@rgo.demo", "Demo1234Xy")
        self.admin = core.actor_for(self.c, core.login(self.c, "legacy-admin@rgo.demo", "pw"))
        self.customer_id = core.create_customer(self.c, self.admin, "Aboitiz Power")

    def _actor(self, email, password):
        actor = core.actor_for(self.c, core.login(self.c, email, password))
        return admin_platform.apply_rbac(self.c, actor)

    def _pending_quote(self):
        bid = core.create_booking(self.c, self.encoder, self.customer_id, "Heavy lift", "Generator", 55)
        core.review_booking(self.c, self.encoder, bid)
        core.ready_for_quotation(self.c, self.encoder, bid)
        qid = core.create_quotation(self.c, self.encoder, bid, big_lines(), est_cost=420000)
        self.assertEqual(core.submit_quotation(self.c, self.encoder, qid), "pending_approval")
        return bid, qid

    def test_encoder_can_edit_booking_but_cannot_approve(self):
        bid = core.create_booking(self.c, self.encoder, self.customer_id, "Crane", "Transformer", 40)
        updated = core.update_booking(self.c, self.encoder, bid, {"weight": 42, "to_loc": "Batangas"})
        self.assertEqual(updated["weight"], 42)
        core.review_booking(self.c, self.encoder, bid)
        core.ready_for_quotation(self.c, self.encoder, bid)
        qid = core.create_quotation(self.c, self.encoder, bid, big_lines())
        core.submit_quotation(self.c, self.encoder, qid)
        with self.assertRaises(core.ForbiddenError):
            core.approve_quotation(self.c, self.encoder, qid)

    def test_financial_fields_are_independently_redacted(self):
        _, qid = self._pending_quote()
        encoder_view = core.get_quotation(self.c, self.encoder, qid)
        self.assertIsNotNone(encoder_view["total"])
        self.assertIsNone(encoder_view["est_cost"])
        self.assertIsNone(encoder_view["margin_pct"])
        self.assertIn("carrier_cost", encoder_view["redacted_fields"])

        approver_view = core.get_quotation(self.c, self.approver, qid)
        self.assertIsNotNone(approver_view["total"])
        self.assertEqual(approver_view["est_cost"], 420000)
        self.assertIsNotNone(approver_view["margin_pct"])

        finance_view = core.get_quotation(self.c, self.finance, qid)
        self.assertEqual(finance_view["est_cost"], 420000)

    def test_return_requires_comment_and_encoder_can_resubmit_revision(self):
        bid, qid = self._pending_quote()
        with self.assertRaises(core.ValidationError):
            core.return_quotation(self.c, self.approver, qid, "")
        core.return_quotation(self.c, self.approver, qid, "Attach revised route survey and reduce standby")
        self.assertEqual(self.c.execute("SELECT stage FROM bookings WHERE id=?", (bid,)).fetchone()["stage"],
                         "REVISION_REQUESTED")
        qid2 = core.create_quotation(self.c, self.encoder, bid, big_lines(), discount_pct=2, est_cost=410000)
        self.assertNotEqual(qid, qid2)
        self.assertEqual(core.submit_quotation(self.c, self.encoder, qid2), "pending_approval")
        core.approve_quotation(self.c, self.approver, qid2)

    def test_material_reviser_cannot_approve(self):
        _, qid = self._pending_quote()
        core.return_quotation(self.c, self.approver, qid, "Rework cost assumptions")
        core.update_quotation_draft(self.c, self.admin, qid, est_cost=400000)
        core.submit_quotation(self.c, self.encoder, qid)
        with self.assertRaises(core.ForbiddenError):
            core.approve_quotation(self.c, self.admin, qid)

    def test_super_administrator_override_requires_reason_and_is_audited(self):
        _, qid = self._pending_quote()
        governed_super = dict(self.admin, role="super_admin", perms={"*"})
        with self.assertRaises(core.ValidationError):
            core.approve_quotation(self.c, governed_super, qid)
        core.approve_quotation(
            self.c, governed_super, qid,
            comment="Exceptional commercial approval reviewed",
            override_reason="Continuity approval under EX-2026-08",
        )
        row = self.c.execute(
            "SELECT reason FROM audit_logs WHERE action='quotation.governed_override' AND entity_id=?",
            (qid,),
        ).fetchone()
        self.assertIn("EX-2026-08", row["reason"])

    def test_user_administrator_can_assign_business_role_but_not_privileged_or_self_elevate(self):
        target_id = admin_platform.create_user(
            self.c, self.super, "target@rgo.demo", "Demo1234Xy", "operational_user", "Target",
        )
        approver_role = admin_platform.role_by_code(self.c, "RGO", "approver")
        admin_platform.assign_role(self.c, target_id, approver_role["id"], actor=self.user_admin)
        self.assertTrue(admin_platform.has_permission(self.c, target_id, "quotation.approve"))

        executive = admin_platform.role_by_code(self.c, "RGO", "executive")
        with self.assertRaises(core.ForbiddenError):
            admin_platform.assign_role(self.c, target_id, executive["id"], actor=self.user_admin)

        encoder_role = admin_platform.role_by_code(self.c, "RGO", "booking_quotation_administrator")
        with self.assertRaises(core.ForbiddenError):
            admin_platform.assign_role(self.c, self.user_admin_id, encoder_role["id"], actor=self.user_admin)


if __name__ == "__main__":
    unittest.main()
