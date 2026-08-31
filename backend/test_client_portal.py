import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import client_portal as cp
import core
import db
import protected_payment as pp


class ClientPortalTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.admin = {"id": 900, "role": "super_admin", "tenant_id": None, "perms": {"*"}}
        self.conn.execute(
            "INSERT INTO mkt_shippers(applicant_type,legal_name,status,created_by,created_at) VALUES('CORPORATION','Acme Projects','ACTIVE',?,?)",
            (self.admin["id"], core.now()),
        )
        self.shipper_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            "INSERT INTO mkt_shippers(applicant_type,legal_name,status,created_by,created_at) VALUES('CORPORATION','Other Client','ACTIVE',?,?)",
            (self.admin["id"], core.now()),
        )
        self.other_shipper_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.user_id = core.create_user(self.conn, "client@acme.test", "pw", "customer", "Acme Booker")
        self.other_user_id = core.create_user(self.conn, "other@client.test", "pw", "customer", "Other Booker")
        cp.bind_principal(self.conn, self.admin, self.user_id, self.shipper_id)
        cp.bind_principal(self.conn, self.admin, self.other_user_id, self.other_shipper_id)
        self.actor = core.actor_for(self.conn, core.login(self.conn, "client@acme.test", "pw"))
        self.other_actor = core.actor_for(self.conn, core.login(self.conn, "other@client.test", "pw"))
        self.conn.execute(
            "INSERT INTO mkt_bookings(shipper_id,cargo_code,cargo_description,origin_zone,dest_zone,status,created_by,created_at) "
            "VALUES(?,'GENERAL','Machine parts','NCR','CALABARZON','DRAFT',?,?)",
            (self.shipper_id, self.user_id, core.now()),
        )
        self.booking_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            "INSERT INTO mkt_bookings(shipper_id,cargo_code,cargo_description,origin_zone,dest_zone,status,created_by,created_at) "
            "VALUES(?,'GENERAL','Private cargo','CEBU','BOHOL','DRAFT',?,?)",
            (self.other_shipper_id, self.other_user_id, core.now()),
        )
        self.conn.commit()

    def test_identity_bound_booking_isolation(self):
        mine = cp.bookings(self.conn, self.actor)["bookings"]
        other = cp.bookings(self.conn, self.other_actor)["bookings"]
        self.assertEqual([b["cargo_description"] for b in mine], ["Machine parts"])
        self.assertEqual([b["cargo_description"] for b in other], ["Private cargo"])

    def test_unbound_user_cannot_open_workspace(self):
        uid = core.create_user(self.conn, "unbound@client.test", "pw", "customer", "Unbound")
        actor = core.actor_for(self.conn, core.login(self.conn, "unbound@client.test", "pw"))
        self.assertTrue(uid)
        with self.assertRaises(core.ForbiddenError):
            cp.overview(self.conn, actor)

    def test_saved_address_and_payment_alias_are_scoped_and_safe(self):
        saved = cp.add_address(self.conn, self.actor, "Main warehouse", "Gate 2, Industrial Park",
                               island_group="LUZON", region_code="130000000", province_code="NCR",
                               city_code="Makati", barangay_code="San Lorenzo")
        self.assertEqual(saved["status"], "ACTIVE")
        self.assertEqual(len(cp.addresses(self.conn, self.actor)["addresses"]), 1)
        self.assertEqual(len(cp.addresses(self.conn, self.other_actor)["addresses"]), 0)

        pref = cp.add_payment_preference(self.conn, self.actor, "XENDIT", "GCASH",
                                         "ptkn-safe-alias", "GCash •••• 0917", True)
        self.assertEqual(pref["status"], "PENDING_PROVIDER_VERIFICATION")
        public = cp.payment_preferences(self.conn, self.actor)["payment_preferences"][0]
        self.assertNotIn("provider_alias", public)
        with self.assertRaises(core.ValidationError):
            cp.add_payment_preference(self.conn, self.actor, "XENDIT", "CARD",
                                      "4111111111111111", "4111111111111111")

    def test_protected_payment_projection_has_shared_timeline_and_no_escrow_claim(self):
        tx = pp.create_transaction(self.conn, self.admin, booking_id=self.booking_id, carrier_id=77,
                                   contract_amount=10000, protected_amount=10000,
                                   platform_fee=500, provider_fee=150, tax=0,
                                   funding_deadline="2026-09-01T10:00:00+00:00",
                                   dispute_policy="Release after verified delivery and 72-hour dispute window")
        payments = cp.payments(self.conn, self.actor)["payments"]
        self.assertEqual(len(payments), 1)
        view = payments[0]
        self.assertEqual(view["protected_payment_id"], tx)
        self.assertEqual(view["terminology"], "Protected Payment")
        self.assertEqual(view["legal_escrow_status"], "NOT_AUTHORIZED_FOR_PUBLIC_USE")
        self.assertEqual(len(view["timeline"]), 7)
        self.assertIn("next_action", view)
        self.assertNotIn("carrier_cost", view)

    def test_notifications_are_user_scoped_and_mark_read(self):
        self.conn.execute(
            "INSERT INTO notifications(template,recipient,subject,body,channel,status,created_at) "
            "VALUES('booking','client@acme.test','Booking received','Received','email','DELIVERED',?)",
            (core.now(),),
        )
        nid = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.commit()
        self.assertEqual(cp.overview(self.conn, self.actor)["unread_notifications"], 1)
        self.assertEqual(cp.notifications(self.conn, self.other_actor)["notifications"], [])
        cp.mark_notification_read(self.conn, self.actor, nid)
        self.assertEqual(cp.overview(self.conn, self.actor)["unread_notifications"], 0)


if __name__ == "__main__":
    unittest.main()
