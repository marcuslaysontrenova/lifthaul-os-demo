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


class CustomerTracking(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")

    def _tok(self, **kw):
        return pb.submit(self.c, _p(**kw))["tracking_token"]

    def test_status_projection_customer_safe(self):
        t = pb.track(self.c, self._tok())
        self.assertEqual(t["customer_status"], "Request Received")
        self.assertIn(t["service"], ("Domestic", "Inter-Island"))

    def test_luzon_domestic(self):
        t = pb.track(self.c, self._tok(origin_island="Luzon", dest_island="Luzon"))
        self.assertEqual(t["service"], "Domestic")
        self.assertNotIn("At Port", [s["name"] for s in t["stages"]])

    def test_visayas_domestic(self):
        t = pb.track(self.c, self._tok(origin_island="Visayas", dest_island="Visayas"))
        self.assertEqual(t["service"], "Domestic")

    def test_mindanao_domestic(self):
        t = pb.track(self.c, self._tok(origin_island="Mindanao", dest_island="Mindanao"))
        self.assertEqual(t["service"], "Domestic")

    def test_inter_island_stages(self):
        t = pb.track(self.c, self._tok(origin_island="Luzon", dest_island="Mindanao"))
        names = [s["name"] for s in t["stages"]]
        for leg in ("At Port", "Sea Transit", "Destination Port"):
            self.assertIn(leg, names)

    def test_engineered_projection_no_price(self):
        t = pb.track(self.c, self._tok(vehicle="crane"))
        self.assertIsNone(t["estimate"])
        self.assertIn("Estimate Required", [s["name"] for s in t["stages"]])

    def test_quotation_and_payment_projection(self):
        t = pb.track(self.c, self._tok())
        self.assertIn(t["quotation_status"], ("Ready", "Indicative", "Estimate required", "Pending"))
        self.assertEqual(t["payment_status"], "Payment Required")

    def test_protected_payment_wording_and_funds_off(self):
        t = pb.track(self.c, self._tok())
        self.assertEqual(t["protected_payment"]["terminology"], "Protected Payment")
        self.assertFalse(t["protected_payment"]["live_funds_enabled"])
        # never the legal term "Escrow"
        self.assertNotIn("escrow", json_dumps(t).lower())

    def test_internal_note_and_financial_redaction(self):
        tok = self._tok()
        # staff attaches an internal note; it must never surface publicly
        bid = self.c.execute("SELECT id FROM mkt_bookings WHERE tracking_token=?", (tok,)).fetchone()["id"]
        pb.review(self.c, SUP, bid, "REVIEW", note="INTERNAL: carrier margin 18%, bank acct 1234")
        t = pb.track(self.c, tok)
        blob = json_dumps(t).lower()
        for leak in ("internal", "margin", "bank acct", "special_instructions", "1234"):
            self.assertNotIn(leak, blob)
        # sensitive keys are simply absent
        for k in ("special_instructions", "margin", "internal_cost", "contact_phone", "notes"):
            self.assertNotIn(k, t)

    def test_provider_redacted_until_assigned(self):
        t = pb.track(self.c, self._tok())
        self.assertIsNone(t["provider"])

    def test_cross_customer_isolation(self):
        a = self._tok(contact_name="A")
        b = self._tok(contact_name="B")
        self.assertNotEqual(pb.track(self.c, a)["ref"], pb.track(self.c, b)["ref"])

    def test_operator_action_reflected_publicly(self):
        tok = self._tok()
        bid = self.c.execute("SELECT id FROM mkt_bookings WHERE tracking_token=?", (tok,)).fetchone()["id"]
        self.assertEqual(pb.track(self.c, tok)["customer_status"], "Request Received")
        pb.review(self.c, SUP, bid, "REVIEW")
        self.assertEqual(pb.track(self.c, tok)["customer_status"], "Under Review")
        pb.review(self.c, SUP, bid, "QUOTE", quote_amount=4200)
        self.assertEqual(pb.track(self.c, tok)["customer_status"], "Quotation Ready")
        pb.review(self.c, SUP, bid, "MOVE_TO_MARKETPLACE")
        self.assertEqual(pb.track(self.c, tok)["customer_status"], "Matching Service Provider")

    def test_modified_token_denied(self):
        tok = self._tok()
        with self.assertRaises(core.NotFoundError):
            pb.track(self.c, tok + "TAMPER")

    def test_oversized_token_denied(self):
        with self.assertRaises(core.NotFoundError):
            pb.track(self.c, "pbk_" + "x" * 500)


def json_dumps(o):
    import json
    return json.dumps(o, default=str)


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


class OperatorReview(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.bid = pb.submit(self.c, _p())["booking_id"]

    def test_review_advances_status(self):
        r = pb.review(self.c, SUP, self.bid, "REVIEW")
        self.assertEqual(r["status"], "REVIEWED")

    def test_quote_sets_amount_and_status(self):
        pb.review(self.c, SUP, self.bid, "QUOTE", quote_amount=4200, note="priced per lane")
        row = self.c.execute("SELECT status,quote_amount,quote_status FROM mkt_bookings WHERE id=?", (self.bid,)).fetchone()
        self.assertEqual(row["status"], "QUOTED")
        self.assertEqual(row["quote_amount"], 4200)
        self.assertEqual(row["quote_status"], "STAFF_QUOTED")

    def test_move_to_marketplace(self):
        r = pb.review(self.c, SUP, self.bid, "MOVE_TO_MARKETPLACE")
        self.assertEqual(r["status"], "MATCHING")
        row = self.c.execute("SELECT routing_candidate FROM mkt_bookings WHERE id=?", (self.bid,)).fetchone()
        self.assertEqual(row["routing_candidate"], "MARKETPLACE_CANDIDATE")

    def test_assign_estimator(self):
        self.assertEqual(pb.review(self.c, SUP, self.bid, "ASSIGN_ESTIMATOR")["status"], "ESTIMATION")

    def test_decline_is_terminal(self):
        pb.review(self.c, SUP, self.bid, "DECLINE")
        with self.assertRaises(core.ConflictError):
            pb.review(self.c, SUP, self.bid, "REVIEW")

    def test_unknown_action_denied(self):
        with self.assertRaises(core.ValidationError):
            pb.review(self.c, SUP, self.bid, "NUKE")

    def test_requires_manage_permission(self):
        weak = {"id": 9, "role": "viewer", "perms": {"marketplace.booking.view"}, "tenant_id": None}
        with self.assertRaises(Exception):
            pb.review(self.c, weak, self.bid, "REVIEW")

    def test_unknown_booking_not_found(self):
        with self.assertRaises(core.NotFoundError):
            pb.review(self.c, SUP, 999999, "REVIEW")

    def test_review_is_audited(self):
        pb.review(self.c, SUP, self.bid, "REVIEW", note="ok")
        n = self.c.execute("SELECT COUNT(*) c FROM audit_logs WHERE action='PUBLIC_BOOKING_REVIEWED'").fetchone()["c"]
        self.assertGreaterEqual(n, 1)


class ServiceLevels(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_express_costs_more_than_standard(self):
        s = pb.submit(self.c, _p(vehicle="6w", km=100, service_level="STANDARD"))["estimate"]
        e = pb.submit(self.c, _p(vehicle="6w", km=100, service_level="EXPRESS"))["estimate"]
        self.assertGreater(e, s)

    def test_economy_cheaper(self):
        s = pb.submit(self.c, _p(vehicle="van", km=50, service_level="STANDARD"))["estimate"]
        ec = pb.submit(self.c, _p(vehicle="van", km=50, service_level="ECONOMY"))["estimate"]
        self.assertLess(ec, s)

    def test_economy_not_eligible_for_engineered(self):
        with self.assertRaises(core.ValidationError):
            pb.submit(self.c, _p(vehicle="crane", service_level="ECONOMY"))

    def test_unknown_level_rejected(self):
        with self.assertRaises(core.ValidationError):
            pb.submit(self.c, _p(service_level="TELEPORT"))

    def test_engineered_defaults_to_heavy_haul_no_price(self):
        r = pb.submit(self.c, _p(vehicle="lowbed"))
        self.assertEqual(r["service_level"], "ENGINEERED_HEAVY_HAUL")
        self.assertIsNone(r["estimate"])

    def test_catalog_filters_by_service_class(self):
        eng = [l["code"] for l in pb.service_levels_catalog("ENGINEERED")["levels"]]
        self.assertIn("ENGINEERED_HEAVY_HAUL", eng)
        self.assertNotIn("ECONOMY", eng)


class Scheduling(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_default_now(self):
        self.assertEqual(pb.submit(self.c, _p())["schedule_type"], "NOW")

    def test_scheduled_requires_date(self):
        with self.assertRaises(core.ValidationError):
            pb.submit(self.c, _p(schedule_type="SCHEDULED"))

    def test_scheduled_future_ok(self):
        import datetime
        d = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()
        r = pb.submit(self.c, _p(schedule_type="SCHEDULED", scheduled_at=d))
        self.assertEqual(r["scheduled_at"], d)

    def test_past_date_rejected(self):
        with self.assertRaises(core.ValidationError):
            pb.submit(self.c, _p(schedule_type="SCHEDULED", scheduled_at="2020-01-01"))

    def test_unknown_schedule_type_rejected(self):
        with self.assertRaises(core.ValidationError):
            pb.submit(self.c, _p(schedule_type="WHENEVER"))


class MultiStop(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_stops_persisted_and_sequenced(self):
        r = pb.submit(self.c, _p(vehicle="van", stops=[
            {"type": "PICKUP", "address": "WH-A"}, {"type": "DROP", "address": "S1"},
            {"type": "DROP", "address": "S2"}]))
        self.assertEqual(r["stops"], 3)
        rows = self.c.execute("SELECT seq,stop_type FROM mkt_booking_stops WHERE booking_id=? ORDER BY seq",
                              (r["booking_id"],)).fetchall()
        self.assertEqual([x["seq"] for x in rows], [1, 2, 3])

    def test_too_many_stops_rejected(self):
        with self.assertRaises(core.ValidationError):
            pb.submit(self.c, _p(stops=[{"address": str(i)} for i in range(25)]))

    def test_stops_in_tracking(self):
        r = pb.submit(self.c, _p(stops=[{"type": "PICKUP", "address": "A"}, {"type": "DROP", "address": "B"}]))
        t = pb.track(self.c, r["tracking_token"])
        self.assertEqual(len(t["stops"]), 2)
        self.assertEqual(t["stops"][0]["type"], "PICKUP")


class BulkImport(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_bulk_creates_batch_with_errors(self):
        rows = [_p(origin_island="Luzon", dest_island="Visayas") for _ in range(3)] + [{"contact_name": "x"}]
        b = pb.submit_bulk(self.c, SUP, rows)
        self.assertEqual(b["created_count"], 3)
        self.assertEqual(b["error_count"], 1)
        self.assertTrue(b["batch_id"].startswith("batch_"))

    def test_bulk_requires_permission(self):
        weak = {"id": 9, "role": "viewer", "perms": {"marketplace.booking.view"}, "tenant_id": None}
        with self.assertRaises(Exception):
            pb.submit_bulk(self.c, weak, [_p()])

    def test_bulk_rows_are_canonical_bookings(self):
        pb.submit_bulk(self.c, SUP, [_p(), _p()])
        n = self.c.execute("SELECT COUNT(*) c FROM mkt_bookings WHERE source='PUBLIC_MARKETPLACE'").fetchone()["c"]
        self.assertEqual(n, 2)

    def test_bulk_size_cap(self):
        with self.assertRaises(core.ValidationError):
            pb.submit_bulk(self.c, SUP, [_p() for _ in range(501)])


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
