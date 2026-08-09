"""B2B Developer Platform (Platform Control -> Integrations): API clients + scopes + /api/v1 reuse of
canonical booking/quote/tracking + outbound webhooks (HMAC, retry, dead-letter, replay).

Asserts the security matrix: invalid/revoked key, wrong tenant, missing scope, excessive rate,
idempotent replay, invalid signature, cross-tenant webhook access, secret never leaked, production gate.
"""
import datetime
import unittest

import api_platform as ap
import core
import db


SUP = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}


def _actor(tid):
    return {"id": 1, "role": "platform_admin", "perms": {"*"}, "tenant_id": tid}


class Clients(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")

    def test_create_returns_secret_once_and_hashes_it(self):
        cl = ap.create_client(self.c, SUP, "ERP", ["bookings:create"])
        self.assertIn("secret", cl)
        row = self.c.execute("SELECT secret_hash FROM api_clients WHERE id=?", (cl["id"],)).fetchone()
        self.assertNotEqual(row["secret_hash"], cl["secret"])          # stored hashed, not plaintext
        self.assertNotIn(cl["secret"], row["secret_hash"])

    def test_list_never_exposes_secret(self):
        ap.create_client(self.c, SUP, "ERP", ["bookings:read"])
        for cli in ap.list_clients(self.c, SUP)["clients"]:
            self.assertNotIn("secret", cli)
            self.assertNotIn("secret_hash", cli)

    def test_wildcard_scope_rejected(self):
        with self.assertRaises(core.ValidationError):
            ap.create_client(self.c, SUP, "bad", ["*"])

    def test_unknown_scope_rejected(self):
        with self.assertRaises(core.ValidationError):
            ap.create_client(self.c, SUP, "bad", ["everything:do"])

    def test_authenticate_and_scopes(self):
        cl = ap.create_client(self.c, SUP, "ERP", ["bookings:create", "tracking:read"])
        a = ap.authenticate(self.c, cl["api_key"])
        self.assertEqual(a["scopes"], {"bookings:create", "tracking:read"})

    def test_invalid_key_denied(self):
        with self.assertRaises(core.ForbiddenError):
            ap.authenticate(self.c, "nope:nope")

    def test_revoked_key_denied(self):
        cl = ap.create_client(self.c, SUP, "ERP", ["bookings:read"])
        ap.revoke_client(self.c, SUP, cl["id"])
        with self.assertRaises(core.ForbiddenError):
            ap.authenticate(self.c, cl["api_key"])

    def test_secret_rotation_invalidates_old(self):
        cl = ap.create_client(self.c, SUP, "ERP", ["bookings:read"])
        ap.rotate_secret(self.c, SUP, cl["id"])
        with self.assertRaises(core.ForbiddenError):
            ap.authenticate(self.c, cl["api_key"])       # old key no longer valid

    def test_production_requires_approval(self):
        cl = ap.create_client(self.c, SUP, "ERP", ["bookings:read"], environment="PRODUCTION")
        with self.assertRaises(core.ForbiddenError):
            ap.authenticate(self.c, cl["api_key"])       # not approved yet
        ap.approve_production(self.c, SUP, cl["id"])
        a = ap.authenticate(self.c, cl["api_key"])
        self.assertEqual(a["environment"], "PRODUCTION")

    def test_tenant_isolation(self):
        a1, a2 = _actor(1), _actor(2)
        ap.create_client(self.c, a1, "T1", ["bookings:read"])
        self.assertEqual(len(ap.list_clients(self.c, a1)["clients"]), 1)
        self.assertEqual(len(ap.list_clients(self.c, a2)["clients"]), 0)   # tenant 2 sees nothing


class Scopes(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        cl = ap.create_client(self.c, SUP, "ERP", ["bookings:create", "quotations:read", "tracking:read"])
        self.a = ap.authenticate(self.c, cl["api_key"])

    def test_scope_present(self):
        ap.require_scope(self.a, "bookings:create")   # no raise

    def test_scope_missing_denied(self):
        with self.assertRaises(core.ForbiddenError):
            ap.require_scope(self.a, "payments:read")


class ApiBooking(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        cl = ap.create_client(self.c, SUP, "ERP", ["bookings:create", "bookings:read", "tracking:read", "quotations:read"])
        self.a = ap.authenticate(self.c, cl["api_key"])

    def test_single_booking_is_canonical(self):
        r = ap.api_create_booking(self.c, self.a, {"contact_name": "A", "contact_phone": "0917",
                                                   "origin_island": "Luzon", "dest_island": "Luzon", "vehicle": "6w", "km": 40})
        n = self.c.execute("SELECT COUNT(*) c FROM mkt_bookings WHERE source='PUBLIC_MARKETPLACE'").fetchone()["c"]
        self.assertEqual(n, 1)
        self.assertTrue(r["ref"])

    def test_multi_stop_scheduling_service_level_pass_through(self):
        import datetime as _dt
        d = (_dt.date.today() + _dt.timedelta(days=10)).isoformat()
        r = ap.api_create_booking(self.c, self.a, {"contact_name": "A", "contact_phone": "0917",
                                                   "origin_island": "Luzon", "dest_island": "Luzon", "vehicle": "van", "km": 20,
                                                   "service_level": "EXPRESS", "schedule_type": "SCHEDULED", "scheduled_at": d,
                                                   "stops": [{"type": "PICKUP", "address": "A"}, {"type": "DROP", "address": "B"}]})
        self.assertEqual(r["service_level"], "EXPRESS")
        self.assertEqual(r["schedule_type"], "SCHEDULED")
        self.assertEqual(r["stops"], 2)

    def test_inter_island(self):
        r = ap.api_create_booking(self.c, self.a, {"contact_name": "A", "contact_phone": "0917",
                                                   "origin_island": "Luzon", "dest_island": "Mindanao", "vehicle": "6w", "km": 300})
        self.assertTrue(r["inter_island"])

    def test_bulk(self):
        rows = [{"contact_name": "A", "contact_phone": "0917", "origin_island": "Luzon",
                 "dest_island": "Visayas", "vehicle": "6w", "km": 200} for _ in range(3)] + [{"contact_name": "x"}]
        b = ap.api_bulk(self.c, self.a, rows)
        self.assertEqual(b["created_count"], 3)
        self.assertEqual(b["error_count"], 1)

    def test_engineered_estimate_fallback(self):
        q = ap.api_quote_estimate(self.c, self.a, {"origin_island": "Luzon", "dest_island": "Luzon",
                                                    "vehicle": "crane", "km": 10})
        self.assertEqual(q["result"], "ESTIMATE_REQUIRED")
        self.assertIsNone(q["estimate"])

    def test_standard_instant_estimate(self):
        q = ap.api_quote_estimate(self.c, self.a, {"origin_island": "Luzon", "dest_island": "Luzon",
                                                    "vehicle": "6w", "km": 100})
        self.assertEqual(q["result"], "INSTANT_ESTIMATE")

    def test_idempotent_create(self):
        p = {"contact_name": "A", "contact_phone": "0917", "origin_island": "Luzon",
             "dest_island": "Luzon", "vehicle": "6w", "km": 40}
        r1 = ap.api_create_booking(self.c, self.a, dict(p), idem_key="IDEM-1")
        r2 = ap.api_create_booking(self.c, self.a, dict(p), idem_key="IDEM-1")
        self.assertEqual(r1["ref"], r2["ref"])
        n = self.c.execute("SELECT COUNT(*) c FROM mkt_bookings WHERE source='PUBLIC_MARKETPLACE'").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_scope_denied_for_create_without_scope(self):
        cl = ap.create_client(self.c, SUP, "RO", ["tracking:read"])
        a = ap.authenticate(self.c, cl["api_key"])
        with self.assertRaises(core.ForbiddenError):
            ap.api_create_booking(self.c, a, {"contact_name": "A", "contact_phone": "0917",
                                              "origin_island": "Luzon", "dest_island": "Luzon", "vehicle": "6w", "km": 40})


class RateLimit(unittest.TestCase):
    def test_per_minute_limit(self):
        c = db.connect(":memory:")
        cl = ap.create_client(c, SUP, "ERP", ["bookings:read"], rate_per_min=2)
        a = ap.authenticate(c, cl["api_key"])
        ap.check_rate(a); ap.check_rate(a)
        with self.assertRaises(core.AppError) as ctx:
            ap.check_rate(a)
        self.assertEqual(getattr(ctx.exception, "http", None), 429)


class Webhooks(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")

    def test_create_returns_secret_once(self):
        w = ap.create_webhook(self.c, SUP, "https://x.example/h", ["booking.created"])
        self.assertIn("secret", w)
        for wh in ap.list_webhooks(self.c, SUP)["webhooks"]:
            self.assertNotIn("secret", wh)              # never listed

    def test_bad_url_rejected(self):
        with self.assertRaises(core.ValidationError):
            ap.create_webhook(self.c, SUP, "ftp://x", ["booking.created"])

    def test_unknown_event_rejected(self):
        with self.assertRaises(core.ValidationError):
            ap.create_webhook(self.c, SUP, "https://x.example/h", ["not.an.event"])

    def test_signature_is_deterministic_and_tamper_evident(self):
        s1 = ap.sign("sek", "evt_1", "2026-01-01", '{"a":1}')
        s2 = ap.sign("sek", "evt_1", "2026-01-01", '{"a":1}')
        s3 = ap.sign("sek", "evt_1", "2026-01-01", '{"a":2}')  # tampered body
        self.assertEqual(s1, s2)
        self.assertNotEqual(s1, s3)
        self.assertTrue(s1.startswith("sha256="))

    def test_emit_queues_pending(self):
        ap.create_webhook(self.c, SUP, "https://x.example/h", ["booking.created"])
        r = ap.emit_event(self.c, None, "booking.created", {"ref": "LH-1"})
        self.assertEqual(r["queued"], 1)
        d = ap.list_deliveries(self.c, SUP)["deliveries"][0]
        self.assertEqual(d["status"], "PENDING")

    def test_valid_delivery(self):
        ap.create_webhook(self.c, SUP, "https://x.example/h", ["booking.created"])
        ap.emit_event(self.c, None, "booking.created", {"ref": "LH-1"})
        ap.deliver_pending(self.c, sender=lambda u, h, b: True)
        self.assertEqual(ap.list_deliveries(self.c, SUP)["deliveries"][0]["status"], "DELIVERED")

    def test_retry_then_dead_letter(self):
        ap.create_webhook(self.c, SUP, "https://x.example/h", ["booking.created"])
        ap.emit_event(self.c, None, "booking.created", {"ref": "LH-1"})
        # first failure -> RETRYING
        ap.deliver_pending(self.c, sender=lambda u, h, b: False)
        self.assertEqual(ap.list_deliveries(self.c, SUP)["deliveries"][0]["status"], "RETRYING")
        # keep failing on advancing clock -> DEAD_LETTER after max attempts
        for i in range(1, 7):
            when = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=i)).isoformat()
            ap.deliver_pending(self.c, sender=lambda u, h, b: False, now=when)
        self.assertEqual(ap.list_deliveries(self.c, SUP)["deliveries"][0]["status"], "DEAD_LETTER")

    def test_replay_resets_and_counts(self):
        ap.create_webhook(self.c, SUP, "https://x.example/h", ["booking.created"])
        ap.emit_event(self.c, None, "booking.created", {"ref": "LH-1"})
        d = ap.list_deliveries(self.c, SUP)["deliveries"][0]
        eid = d["event_id"]
        r = ap.replay_delivery(self.c, SUP, d["id"])
        self.assertEqual(r["status"], "PENDING")
        self.assertEqual(r["replays"], 1)
        self.assertEqual(r["event_id"], eid)             # same event id preserved

    def test_offline_endpoint_never_blocks(self):
        # a failing endpoint only affects its own delivery record; emit + deliver never raise
        ap.create_webhook(self.c, SUP, "https://x.example/h", ["booking.created"])
        ap.emit_event(self.c, None, "booking.created", {"ref": "LH-1"})
        ap.deliver_pending(self.c, sender=lambda u, h, b: (_ for _ in ()).throw(RuntimeError("down")))
        self.assertEqual(ap.list_deliveries(self.c, SUP)["deliveries"][0]["status"], "RETRYING")

    def test_cross_tenant_replay_denied(self):
        a1, a2 = _actor(1), _actor(2)
        ap.create_webhook(self.c, a1, "https://x.example/h", ["booking.created"])
        ap.emit_event(self.c, 1, "booking.created", {"ref": "LH-1"})
        d = ap.list_deliveries(self.c, a1)["deliveries"][0]
        with self.assertRaises(core.NotFoundError):
            ap.replay_delivery(self.c, a2, d["id"])       # tenant 2 cannot touch tenant 1's delivery


class Persistence(unittest.TestCase):
    def test_restart_persistence(self):
        import os, tempfile
        path = tempfile.mktemp(suffix=".sqlite")
        try:
            c1 = db.connect(path)
            cl = ap.create_client(c1, SUP, "ERP", ["bookings:read"])
            ap.create_webhook(c1, SUP, "https://x.example/h", ["booking.created"])
            if hasattr(c1, "close"):
                c1.close()
            c2 = db.connect(path)   # reopen (simulated restart)
            self.assertEqual(len(ap.list_clients(c2, SUP)["clients"]), 1)
            self.assertEqual(len(ap.list_webhooks(c2, SUP)["webhooks"]), 1)
            a = ap.authenticate(c2, cl["api_key"])   # key still valid after restart
            self.assertTrue(a["scopes"])
        finally:
            try: os.remove(path)
            except Exception: pass


if __name__ == "__main__":
    unittest.main()
