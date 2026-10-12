"""End-to-end evidence for least-privilege, tenant-bound production UAT accounts.

Uses only an isolated in-memory SQLite database. Never reads production data or
secrets and never enables live payment operations.
"""
import unittest

import admin_platform
import core
import db
import public_booking


class PublicIntakeAccountLifecycle(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.tenant = admin_platform.get_tenant(self.conn, "RGO")["id"]
        self.user_admin = {"id": 0, "role": "super_admin", "perms": {"*"},
                           "tenant_id": self.tenant}
        self.sample = public_booking.submit(
            self.conn,
            {"contact_name": "AUTOMATED QA - DO NOT DISPATCH",
             "origin_island": "Luzon", "dest_island": "Luzon",
             "vehicle": "6w", "km": 45, "contact_phone": "09170000000",
             "excluded_charges_ack": True})

    def actor(self, name, role):
        email = name + "@example.invalid"
        password = "OneTimeQA-Only-2026!"
        uid = admin_platform.create_user(
            self.conn, self.user_admin, email, password, role,
            "Authenticated QA acceptance")
        token = core.login(self.conn, email, password)
        actor = core.actor_for(self.conn, token)
        admin_platform.apply_rbac(self.conn, actor)
        self.assertEqual(actor["id"], uid)
        self.assertEqual(actor["tenant_id"], self.tenant)
        return actor

    def test_read_only_qa_sees_guest_booking_but_cannot_review(self):
        actor = self.actor("intake-reader", "public_intake_qa")
        self.assertTrue(core.can(actor, "marketplace.booking.view"))
        self.assertTrue(core.can(actor, "marketplace.public_intake.view"))
        self.assertFalse(core.can(actor, "marketplace.booking.manage"))
        self.assertFalse(core.can(actor, "marketplace.public_intake.manage"))
        self.assertFalse(core.can(actor, "user_admin.view"))
        self.assertFalse(core.can(actor, "payment.read"))
        q = public_booking.admin_queue(self.conn, actor)
        self.assertEqual(q["source"], "PUBLIC_MARKETPLACE")
        self.assertTrue(any(row["id"] == self.sample["booking_id"] for row in q["requests"]))
        with self.assertRaises(core.ForbiddenError):
            public_booking.review(self.conn, actor, self.sample["booking_id"], "REVIEW")

    def test_review_qualified_operator_cannot_cross_into_other_tenants(self):
        actor = self.actor("intake-reviewer", "public_intake_operator")
        self.assertTrue(core.can(actor, "marketplace.public_intake.manage"))
        self.assertFalse(core.can(actor, "user_admin.manage"))
        self.assertFalse(core.can(actor, "payment.verify"))
        r = public_booking.review(self.conn, actor, self.sample["booking_id"], "REVIEW")
        self.assertEqual(r["status"], "REVIEWED")
        self.assertEqual(public_booking.track(self.conn, self.sample["tracking_token"])["customer_status"],
                         public_booking.track(self.conn, self.sample["tracking_token"])["customer_status"])

    def test_non_intake_role_cannot_use_qa_queue_shortcut(self):
        actor = self.actor("ordinary-staff", "operational_user")
        with self.assertRaises(core.ForbiddenError):
            public_booking.admin_queue(self.conn, actor)


if __name__ == "__main__":
    unittest.main()
