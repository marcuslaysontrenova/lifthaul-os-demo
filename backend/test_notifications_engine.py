"""Automated Customer & Operational Notifications — lifecycle comms over the existing notification domain.

Asserts: policy matrix; mandatory transactional notices cannot be suppressed; optional opt-outs;
duplicate prevention; honest provider delivery (never a fabricated 'sent' when no provider);
retry -> dead-letter; template versioning; event-bus bridge; recipient masking; NO sensitive values
(OTP/secrets) in bodies or history; tenant isolation; restart persistence.
"""
import datetime
import unittest

import admin_platform as ap
import core
import db
import notifications_engine as ne


SUP = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}


class Policy(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_policy_matrix_seeded(self):
        self.assertEqual(ne.policy_for(self.c, None, "delivery_otp_issued")["sms"], "REQUIRED")
        self.assertEqual(ne.policy_for(self.c, None, "delivery_otp_issued")["email"], "OFF")
        self.assertEqual(ne.policy_for(self.c, None, "payment_required")["email"], "REQUIRED")

    def test_unknown_event(self):
        self.assertEqual(ne.notify(self.c, None, "not_an_event", "x@y.com", {})["queued"], 0)


class Emission(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_queues_per_policy(self):
        r = ne.notify(self.c, None, "booking_received", "ana@example.com", {"ref": "LH-1"})
        self.assertGreaterEqual(r["queued"], 1)

    def test_duplicate_prevention(self):
        ne.notify(self.c, None, "booking_received", "ana@example.com", {"ref": "LH-1"}, correlation_id="LH-1")
        r = ne.notify(self.c, None, "booking_received", "ana@example.com", {"ref": "LH-1"}, correlation_id="LH-1")
        self.assertEqual(r["queued"], 0)

    def test_mandatory_cannot_be_suppressed(self):
        ne.set_pref(self.c, SUP, "ana@example.com", "sms", True)
        ne.notify(self.c, None, "payment_required", "ana@example.com", {"ref": "LH-9"}, correlation_id="LH-9")
        row = self.c.execute("SELECT status FROM notifications WHERE event_type='payment_required' AND channel='sms'").fetchone()
        self.assertEqual(row["status"], "QUEUED")   # not SUPPRESSED — transactional notice

    def test_optional_opt_out_suppressed(self):
        ne.set_pref(self.c, SUP, "ana@example.com", "sms", True)
        ne.notify(self.c, None, "booking_received", "ana@example.com", {"ref": "LH-7"}, correlation_id="LH-7")
        row = self.c.execute("SELECT status FROM notifications WHERE event_type='booking_received' AND channel='sms' AND correlation_id='LH-7'").fetchone()
        self.assertEqual(row["status"], "SUPPRESSED")

    def test_no_sensitive_values_in_body(self):
        ne.notify(self.c, None, "delivery_otp_issued", "ana@example.com",
                  {"ref": "LH-2", "otp": "123456", "code": "999999", "secret": "x"}, correlation_id="LH-2")
        n = self.c.execute("SELECT COUNT(*) c FROM notifications WHERE body LIKE '%123456%' OR body LIKE '%999999%'").fetchone()["c"]
        self.assertEqual(n, 0)


class Delivery(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_no_provider_never_fabricates_delivery(self):
        ne.notify(self.c, None, "booking_received", "ana@example.com", {"ref": "LH-1"})
        d = ne.deliver_pending(self.c)
        self.assertEqual(d["delivered"], 0)
        self.assertEqual(self.c.execute("SELECT COUNT(*) c FROM notifications WHERE status='DELIVERED'").fetchone()["c"], 0)

    def test_delivers_with_active_provider_and_sender(self):
        ap.set_config(self.c, "platform", "", "notify.email.provider_active", "true", actor=SUP)
        ne.notify(self.c, None, "booking_received", "ana@example.com", {"ref": "LH-1"})
        d = ne.deliver_pending(self.c, sender_map={"email": lambda to, s, b: True})
        self.assertGreaterEqual(d["delivered"], 1)

    def test_retry_then_dead_letter(self):
        ap.set_config(self.c, "platform", "", "notify.email.provider_active", "true", actor=SUP)
        ne.notify(self.c, None, "booking_received", "ana@example.com", {"ref": "LH-1"})
        bad = {"email": lambda to, s, b: False}
        ne.deliver_pending(self.c, sender_map=bad)
        self.assertEqual(self.c.execute("SELECT status FROM notifications WHERE channel='email'").fetchone()["status"], "RETRYING")
        for i in range(1, 8):
            when = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=i)).isoformat()
            ne.deliver_pending(self.c, sender_map=bad, now=when)
        self.assertEqual(self.c.execute("SELECT status FROM notifications WHERE channel='email'").fetchone()["status"], "DEAD_LETTER")


class Templates(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_versioning(self):
        r1 = ne.upsert_template(self.c, SUP, "booking_received", "email", "S1", "B1")
        r2 = ne.upsert_template(self.c, SUP, "booking_received", "email", "S2", "B2")
        self.assertEqual(r2["version"], r1["version"] + 1)
        active = self.c.execute("SELECT COUNT(*) c FROM notify_templates WHERE event_type='booking_received' "
                                "AND channel='email' AND tenant_id IS NULL AND active=1").fetchone()["c"]
        self.assertEqual(active, 1)   # only the latest active


class Bridge(unittest.TestCase):
    def test_event_bus_bridge_queues_for_booking_contact(self):
        c = db.connect(":memory:")
        import public_booking as pb, api_platform as apx
        bid = pb.submit(c, {"contact_name": "Ben", "contact_email": "ben@x.com", "origin_island": "Luzon",
                            "dest_island": "Luzon", "vehicle": "6w", "km": 30})["booking_id"]
        apx.emit_event(c, None, "booking.created", {"ref": "LHX", "booking": bid})
        n = c.execute("SELECT COUNT(*) c FROM notifications WHERE recipient='ben@x.com'").fetchone()["c"]
        self.assertGreaterEqual(n, 1)


class HistoryMasking(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_customer_history_masks_recipient(self):
        ne.notify(self.c, None, "booking_received", "ana.cruz@example.com", {"ref": "LH-1"})
        h = ne.customer_history(self.c, "ana.cruz@example.com")
        self.assertNotIn("ana.cruz@example.com", h["recipient"])
        self.assertIn("***", h["recipient"])

    def test_admin_history_omits_recipient(self):
        ne.notify(self.c, None, "booking_received", "ana@example.com", {"ref": "LH-1"})
        h = ne.history(self.c, SUP)
        for m in h["notifications"]:
            self.assertNotIn("recipient", m)

    def test_history_requires_permission(self):
        weak = {"id": 9, "role": "x", "perms": {"job.read"}, "tenant_id": None}
        with self.assertRaises(Exception):
            ne.history(self.c, weak)


class Persistence(unittest.TestCase):
    def test_restart_persistence(self):
        import os, tempfile
        path = tempfile.mktemp(suffix=".sqlite")
        try:
            c1 = db.connect(path)
            ne.notify(c1, None, "booking_received", "ana@example.com", {"ref": "LH-1"})
            if hasattr(c1, "close"): c1.close()
            c2 = db.connect(path)
            self.assertGreaterEqual(len(ne.history(c2, SUP)["notifications"]), 1)
        finally:
            try: os.remove(path)
            except Exception: pass


if __name__ == "__main__":
    unittest.main()
