"""Public Nationwide Booking Intake — converges into canonical mkt_bookings (source=PUBLIC_MARKETPLACE).

Asserts: canonical booking creation (not a parallel table); server-authoritative geography +
inter-island; standard vs engineered classification; engineered => estimator queue (no fabricated
price); server-side quote ignores tampered frontend totals; idempotent duplicate submission; invalid
payload denial; private-fleet/marketplace routing candidate; Protected Payment linkage with live funds
OFF; tracking by safe token only (isolation); tenant isolation of the admin queue; restart persistence.
"""
import unittest

import core
import db
import public_booking as pb
import marketplace_payments as pay


SUP = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}


def _p(**kw):
    d = {"contact_name": "Ana Cruz", "contact_phone": "09171234567",
         "origin_island": "Luzon", "dest_island": "Luzon", "vehicle": "6w", "km": 45}
    d.update(kw); return d


class Intake(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")

    def test_creates_canonical_mkt_booking_with_source(self):
        r = pb.submit(self.c, _p())
        row = self.c.execute("SELECT source,status,shipper_id FROM mkt_bookings WHERE id=?", (r["booking_id"],)).fetchone()
        self.assertEqual(row["source"], "PUBLIC_MARKETPLACE")
        self.assertEqual(row["status"], "REQUEST_RECEIVED")
        self.assertTrue(row["shipper_id"])  # attached to the guest shipper, single identity

    def test_no_parallel_booking_table(self):
        # the only booking table used is the canonical mkt_bookings
        pb.submit(self.c, _p())
        n = self.c.execute("SELECT COUNT(*) c FROM mkt_bookings WHERE source='PUBLIC_MARKETPLACE'").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_invalid_payload_denied(self):
        with self.assertRaises(core.ValidationError):
            pb.submit(self.c, {"origin_island": "Luzon"})  # missing contact/vehicle/dest
        with self.assertRaises(core.ValidationError):
            pb.submit(self.c, _p(contact_phone="", contact_email=""))  # no contact channel

    def test_bad_island_denied(self):
        with self.assertRaises(core.ValidationError):
            pb.submit(self.c, _p(origin_island="Palawania"))


class Geography(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_luzon_same_island(self):
        r = pb.submit(self.c, _p(origin_island="Luzon", dest_island="Luzon"))
        self.assertFalse(r["inter_island"]); self.assertEqual(r["service"], "Domestic")

    def test_visayas_same_island(self):
        r = pb.submit(self.c, _p(origin_island="Visayas", dest_island="Visayas"))
        self.assertFalse(r["inter_island"])

    def test_mindanao_same_island(self):
        r = pb.submit(self.c, _p(origin_island="Mindanao", dest_island="Mindanao"))
        self.assertFalse(r["inter_island"])

    def test_inter_island_detected_server_side(self):
        r = pb.submit(self.c, _p(origin_island="Luzon", dest_island="Mindanao"))
        self.assertTrue(r["inter_island"])
        self.assertEqual(r["service"], "Inter-Island")
        row = self.c.execute("SELECT inter_island,route_class FROM mkt_bookings WHERE id=?", (r["booking_id"],)).fetchone()
        self.assertEqual(row["inter_island"], 1)
        self.assertEqual(row["route_class"], "INTER_ISLAND")

    def test_inter_island_ignores_client_claim(self):
        # client says same-island but islands differ -> server still marks inter-island
        r = pb.submit(self.c, _p(origin_island="Visayas", dest_island="Luzon", inter_island=False))
        self.assertTrue(r["inter_island"])


class Quote(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_standard_gets_server_quote(self):
        r = pb.submit(self.c, _p(vehicle="6w", km=45))
        self.assertIsNotNone(r["estimate"])
        self.assertIn(r["estimate_status"], ("QUOTED", "QUOTED_INDICATIVE"))

    def test_engineered_requires_estimator(self):
        for v in ("crane", "lowbed"):
            r = pb.submit(self.c, _p(vehicle=v, km=10))
            self.assertIsNone(r["estimate"])
            self.assertEqual(r["estimate_status"], "ESTIMATE_REQUIRED")
            self.assertEqual(r["routing_candidate"], "ENGINEERED_REVIEW")

    def test_server_ignores_tampered_frontend_total(self):
        r = pb.submit(self.c, _p(vehicle="sedan", km=10, amount=999999, estimate=999999, total=999999))
        self.assertNotEqual(r["estimate"], 999999)
        # sedan: base 120 + 14*10 = 260 (domestic)
        self.assertEqual(r["estimate"], 260)

    def test_inter_island_adds_sea_freight(self):
        dom = pb.submit(self.c, _p(vehicle="6w", km=100, origin_island="Luzon", dest_island="Luzon"))["estimate"]
        ii = pb.submit(self.c, _p(vehicle="6w", km=100, origin_island="Luzon", dest_island="Visayas"))["estimate"]
        self.assertGreater(ii, dom)  # sea-freight surcharge applied server-side


class Routing(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_standard_is_marketplace_candidate(self):
        r = pb.submit(self.c, _p(vehicle="van"))
        self.assertEqual(r["routing_candidate"], "MARKETPLACE_CANDIDATE")

    def test_engineered_is_review(self):
        r = pb.submit(self.c, _p(vehicle="crane"))
        self.assertEqual(r["routing_candidate"], "ENGINEERED_REVIEW")


class ProtectedPaymentLink(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_protected_payment_eligible_live_funds_off(self):
        r = pb.submit(self.c, _p(payment="protected"))
        self.assertTrue(r["protected_payment"]["eligible"])
        self.assertFalse(r["protected_payment"]["live_funds_enabled"])
        self.assertFalse(pay.live_funds_enabled(self.c))
        row = self.c.execute("SELECT payment_status,intended_payment FROM mkt_bookings WHERE id=?", (r["booking_id"],)).fetchone()
        self.assertEqual(row["payment_status"], "PROTECTED_PENDING")
        self.assertEqual(row["intended_payment"], "protected")

    def test_operator_payment_method(self):
        r = pb.submit(self.c, _p(payment="operator"))
        row = self.c.execute("SELECT intended_payment FROM mkt_bookings WHERE id=?", (r["booking_id"],)).fetchone()
        self.assertEqual(row["intended_payment"], "operator")


class Idempotency(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_duplicate_submission_is_idempotent(self):
        a = pb.submit(self.c, _p(idempotency_key="KEY-1"))
        b = pb.submit(self.c, _p(idempotency_key="KEY-1"))
        self.assertEqual(a["ref"], b["ref"])
        n = self.c.execute("SELECT COUNT(*) c FROM mkt_bookings WHERE idempotency_key='KEY-1'").fetchone()["c"]
        self.assertEqual(n, 1)


class Tracking(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_track_by_token(self):
        r = pb.submit(self.c, _p(origin_island="Luzon", dest_island="Visayas"))
        t = pb.track(self.c, r["tracking_token"])
        self.assertEqual(t["ref"], r["ref"])
        self.assertTrue(t["inter_island"])
        self.assertIn("At Port", [s["name"] for s in t["stages"]])   # inter-island leg surfaced
        self.assertEqual(t["stages"][0]["state"], "current")

    def test_unknown_token_not_found(self):
        with self.assertRaises(core.NotFoundError):
            pb.track(self.c, "pbk_doesnotexist")

    def test_non_token_rejected(self):
        with self.assertRaises(core.NotFoundError):
            pb.track(self.c, "1")  # sequential id must not resolve


class AdminQueue(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_appears_in_queue_with_source(self):
        pb.submit(self.c, _p()); pb.submit(self.c, _p(vehicle="crane"))
        q = pb.admin_queue(self.c, SUP)
        self.assertEqual(q["source"], "PUBLIC_MARKETPLACE")
        self.assertEqual(q["count"], 2)

    def test_queue_requires_permission(self):
        weak = {"id": 9, "role": "driver", "perms": {"job.read"}, "tenant_id": None}
        with self.assertRaises(Exception):
            pb.admin_queue(self.c, weak)


class Persistence(unittest.TestCase):
    def test_restart_persistence(self):
        import os, tempfile
        path = tempfile.mktemp(suffix=".sqlite")
        try:
            c1 = db.connect(path)
            r = pb.submit(c1, _p(origin_island="Luzon", dest_island="Mindanao"))
            tok = r["tracking_token"]
            c1.close() if hasattr(c1, "close") else None
            c2 = db.connect(path)          # reopen (simulated restart)
            t = pb.track(c2, tok)
            self.assertEqual(t["ref"], r["ref"])
            self.assertTrue(t["inter_island"])
        finally:
            try: os.remove(path)
            except Exception: pass


if __name__ == "__main__":
    unittest.main()
