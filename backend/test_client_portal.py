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

    def test_booking_detail_is_owned_and_excludes_internal_fields(self):
        detail = cp.booking_detail(self.conn, self.actor, self.booking_id)
        self.assertEqual(detail["cargo_description"], "Machine parts")
        self.assertEqual(detail["trip_events"], [])
        self.assertNotIn("created_by", detail)
        self.assertNotIn("shipper_id", detail)
        for booking_id in (self.booking_id, 999999):
            with self.assertRaises(core.NotFoundError):
                cp.booking_detail(self.conn, self.other_actor, booking_id)
        with self.assertRaises(core.ForbiddenError):
            cp.booking_detail(self.conn, dict(self.actor, perms=set()), self.booking_id)

    def test_booking_detail_trip_timeline_is_safe_and_current(self):
        assignment = self.conn.execute(
            "INSERT INTO mkt_assignments(booking_id,shipper_id,status) VALUES(?,?,'CONFIRMED')",
            (self.booking_id, self.shipper_id)).lastrowid
        trip = self.conn.execute(
            "INSERT INTO mkt_trips(booking_id,assignment_id,status,progress_pct,eta,last_ping_at,mock_label) "
            "VALUES(?,?,'IN_TRANSIT',42,'2026-10-13T12:00:00Z',?,'MOCK_ONLY')",
            (self.booking_id, assignment, core.now())).lastrowid
        self.conn.execute(
            "INSERT INTO mkt_trip_events(trip_id,from_status,to_status,actor,note,gps_lat,occurred_at) "
            "VALUES(?,'DEPARTED','IN_TRANSIT',900,'Internal operator note',14.5,?)",
            (trip, core.now()))
        self.conn.commit()
        detail = cp.booking_detail(self.conn, self.actor, self.booking_id)
        self.assertEqual(detail["trip_status"], "IN_TRANSIT")
        self.assertEqual(detail["progress_pct"], 42)
        self.assertEqual(detail["tracking_label"], "MOCK_ONLY")
        self.assertTrue(detail["last_ping_at"])
        self.assertEqual(set(detail["trip_events"][0]), {"from_status", "to_status", "occurred_at"})
        self.conn.execute("UPDATE mkt_trips SET status='ARRIVED_DESTINATION',progress_pct=100 WHERE id=?", (trip,))
        self.assertEqual(cp.booking_detail(self.conn, self.actor, self.booking_id)["trip_status"], "ARRIVED_DESTINATION")

    def test_booking_detail_rejects_cross_tenant_booking_and_trip(self):
        actor = dict(self.actor, tenant_id=101)
        self.conn.execute("UPDATE mkt_bookings SET tenant_id=202 WHERE id=?", (self.booking_id,))
        with self.assertRaises(core.NotFoundError):
            cp.booking_detail(self.conn, actor, self.booking_id)
        self.conn.execute("UPDATE mkt_bookings SET tenant_id=101 WHERE id=?", (self.booking_id,))
        assignment = self.conn.execute(
            "INSERT INTO mkt_assignments(booking_id,shipper_id,status) VALUES(?,?,'CONFIRMED')",
            (self.booking_id, self.shipper_id)).lastrowid
        self.conn.execute("INSERT INTO mkt_trips(assignment_id,tenant_id) VALUES(?,202)", (assignment,))
        with self.assertRaises(core.NotFoundError):
            cp.booking_detail(self.conn, actor, self.booking_id)

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

    def test_overview_counts_and_clickable_projections_have_required_detail(self):
        view = cp.overview(self.conn, self.actor)
        self.assertEqual(view["summary"]["total"], 1)
        self.assertEqual(view["summary"]["draft"], 1)
        booking = cp.bookings(self.conn, self.actor)["bookings"][0]
        for key in ("booking_reference", "pickup_location", "delivery_location", "cargo_type",
                    "booking_amount", "status", "status_bucket", "protected_payment_status",
                    "updated_at", "available_action"):
            self.assertIn(key, booking)
        self.assertEqual(booking["available_action"], "Continue Draft")

    def test_payment_detail_and_mark_all_remain_user_scoped(self):
        tx = pp.create_transaction(self.conn, self.admin, booking_id=self.booking_id, carrier_id=77,
                                   contract_amount=10000, protected_amount=10000,
                                   platform_fee=1000, provider_fee=150, tax=0,
                                   funding_deadline="2026-09-01T10:00:00+00:00",
                                   dispute_policy="Verified delivery plus dispute window")
        detail = cp.payment_detail(self.conn, self.actor, tx)
        self.assertEqual(detail["protected_payment_id"], tx)
        with self.assertRaises(core.NotFoundError):
            cp.payment_detail(self.conn, self.other_actor, tx)
        self.conn.execute(
            "INSERT INTO notifications(template,recipient,subject,body,channel,status,created_at) "
            "VALUES('payment','client@acme.test','Payment verified','Verified','email','DELIVERED',?)",
            (core.now(),),
        )
        self.conn.commit()
        result = cp.mark_all_notifications_read(self.conn, self.actor)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(cp.overview(self.conn, self.actor)["unread_notifications"], 0)


if __name__ == "__main__":
    unittest.main()
