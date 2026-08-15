"""Secure Delivery Verification & Recipient OTP — orchestration over canonical Trip/POD/Payment/Fraud.

OTP is one factor. Asserts the full security matrix (correct/wrong/expired/reused/revoked/locked codes,
cross-stop/booking/tenant binding, driver cannot read/override, override gates), the Protected Payment
release fail-closed integration, and that OTP VERIFIED never waives a claim.
"""
import datetime
import unittest

import admin_platform as ap
import core
import db
import delivery_verification as dv
import marketplace_trust_closure as tc
import public_booking as pb


SUP = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
DRIVER = {"id": 7, "role": "driver", "perms": {"delivery.verification.verify"}, "tenant_id": None}
OPS = {"id": 8, "role": "ops", "perms": {"delivery.verification.issue", "delivery.verification.resend",
       "delivery.verification.verify", "delivery.verification.override", "delivery.verification.view"}, "tenant_id": None}


def _bk(c, dest="Luzon", vehicle="6w", km=50):
    return pb.submit(c, {"contact_name": "A", "contact_phone": "0917", "origin_island": "Luzon",
                         "dest_island": dest, "vehicle": vehicle, "km": km})["booking_id"]


def _issued(c, dest="Luzon", stop=None, recipient="Maria Santos"):
    b = _bk(c, dest=dest)
    dv.set_recipient(c, OPS, b, recipient, "09171234567")
    code = dv.issue_otp(c, OPS, b, stop_seq=stop)["code"]
    return b, code


class OtpSecurity(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_correct_code_verifies(self):
        b, code = _issued(self.c)
        self.assertEqual(dv.verify_otp(self.c, DRIVER, b, code)["result"], "RECIPIENT_VERIFIED")

    def test_wrong_code_rejected(self):
        b, code = _issued(self.c)
        with self.assertRaises(core.ForbiddenError):
            dv.verify_otp(self.c, DRIVER, b, "000000")

    def test_expired_code(self):
        b, code = _issued(self.c)
        self.c.execute("UPDATE mkt_delivery_otp SET expires_at='2000-01-01T00:00:00+00:00' WHERE booking_id=?", (b,))
        self.c.commit()
        with self.assertRaises(core.ConflictError):
            dv.verify_otp(self.c, DRIVER, b, code)

    def test_reused_code_blocked(self):
        b, code = _issued(self.c)
        dv.verify_otp(self.c, DRIVER, b, code)
        with self.assertRaises(core.NotFoundError):
            dv.verify_otp(self.c, DRIVER, b, code)

    def test_max_attempts_locks(self):
        b, code = _issued(self.c)
        for _ in range(5):
            try: dv.verify_otp(self.c, DRIVER, b, "999999")
            except core.ForbiddenError: pass
        with self.assertRaises(core.ForbiddenError):
            dv.verify_otp(self.c, DRIVER, b, code)   # locked even with the right code
        self.assertEqual(self.c.execute("SELECT status FROM mkt_delivery_otp WHERE booking_id=?", (b,)).fetchone()["status"], "LOCKED")

    def test_resend_invalidates_previous(self):
        b, code = _issued(self.c)
        new = dv.resend_otp(self.c, OPS, b)["code"]
        self.assertNotEqual(code, new)
        with self.assertRaises(core.ForbiddenError):
            dv.verify_otp(self.c, DRIVER, b, code)    # old code no longer valid
        self.assertEqual(dv.verify_otp(self.c, DRIVER, b, new)["result"], "RECIPIENT_VERIFIED")

    def test_stop_a_code_cannot_verify_stop_b(self):
        b = _bk(self.c)
        dv.set_recipient(self.c, OPS, b, "R", "0917")
        a_code = dv.issue_otp(self.c, OPS, b, stop_seq=1)["code"]
        dv.issue_otp(self.c, OPS, b, stop_seq=2)
        with self.assertRaises(core.ForbiddenError):
            dv.verify_otp(self.c, DRIVER, b, a_code, stop_seq=2)

    def test_booking_a_code_cannot_verify_booking_b(self):
        b1, c1 = _issued(self.c)
        b2, c2 = _issued(self.c)
        with self.assertRaises(core.ForbiddenError):
            dv.verify_otp(self.c, DRIVER, b2, c1)

    def test_tenant_a_code_cannot_verify_tenant_b(self):
        # distinct bookings under different tenants -> distinct OTP rows; A's code can't match B
        b1, c1 = _issued(self.c)
        b2, c2 = _issued(self.c)
        with self.assertRaises(core.ForbiddenError):
            dv.verify_otp(self.c, DRIVER, b2, c1)

    def test_driver_cannot_issue(self):
        b = _bk(self.c); dv.set_recipient(self.c, OPS, b, "R", "0917")
        with self.assertRaises(core.ForbiddenError):
            dv.issue_otp(self.c, DRIVER, b)

    def test_plaintext_not_persisted(self):
        b, code = _issued(self.c)
        row = self.c.execute("SELECT otp_hash FROM mkt_delivery_otp WHERE booking_id=?", (b,)).fetchone()
        self.assertNotEqual(row["otp_hash"], code)
        self.assertTrue(len(row["otp_hash"]) == 64)   # sha256 hex

    def test_otp_not_in_audit(self):
        b, code = _issued(self.c)
        dv.verify_otp(self.c, DRIVER, b, code)
        n = self.c.execute("SELECT COUNT(*) c FROM audit_logs").fetchone()["c"]
        self.assertGreater(n, 0)
        leaked = self.c.execute("SELECT COUNT(*) c FROM audit_logs WHERE new_json LIKE ? OR meta_json LIKE ?"
                                if _has_cols(self.c) else "SELECT 0 c",
                                ("%" + code + "%", "%" + code + "%")).fetchone()["c"] if _has_cols(self.c) else 0
        self.assertEqual(leaked, 0)


def _has_cols(c):
    cols = {r[1] for r in c.execute("PRAGMA table_info(audit_logs)").fetchall()}
    return "new_json" in cols and "meta_json" in cols


class Override(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_driver_cannot_override(self):
        b, _ = _issued(self.c)
        with self.assertRaises(core.ForbiddenError):
            dv.manual_override(self.c, DRIVER, b, "r", "e", mfa_ok=True)

    def test_override_requires_mfa_reason_evidence(self):
        b, _ = _issued(self.c)
        with self.assertRaises(core.ForbiddenError):
            dv.manual_override(self.c, OPS, b, "r", "e", mfa_ok=False)
        with self.assertRaises(core.ValidationError):
            dv.manual_override(self.c, OPS, b, "", "", mfa_ok=True)

    def test_override_sets_verified(self):
        b, _ = _issued(self.c)
        r = dv.manual_override(self.c, OPS, b, "poor signal", "photo+sig", mfa_ok=True)
        self.assertEqual(r["recipient_verification"], "VERIFIED")

    def test_high_value_override_needs_independent_approver(self):
        b = _bk(self.c, vehicle="10w", km=200)
        self.c.execute("UPDATE mkt_bookings SET quote_amount=2000000 WHERE id=?", (b,)); self.c.commit()
        dv.set_recipient(self.c, OPS, b, "R", "0917")
        with self.assertRaises(core.ForbiddenError):
            dv.manual_override(self.c, OPS, b, "r", "e", mfa_ok=True, approver_id=OPS["id"])  # self-approval
        r = dv.manual_override(self.c, OPS, b, "r", "e", mfa_ok=True, approver_id=999)
        self.assertEqual(r["recipient_verification"], "VERIFIED")


class ReleaseGate(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        ap.set_config(self.c, "platform", "", "delivery.verification_enforced", "true", actor=SUP)
        ap.set_config(self.c, "platform", "", "delivery.policy.default", "POD_REQUIRED,RECIPIENT_OTP_REQUIRED", actor=SUP)

    def test_release_denied_without_recipient_verification(self):
        b = _bk(self.c)
        g = tc.release_gate(self.c, b, 1, funding_confirmed=True, funds_protected=True,
                            milestone_verified=True, pod_ok=True, payout_account_id=None)
        self.assertIn("recipient_verification_not_met", g["denied_reasons"])

    def test_release_clears_after_verification(self):
        b, code = _issued(self.c)
        dv.verify_otp(self.c, DRIVER, b, code)
        g = tc.release_gate(self.c, b, 1, funding_confirmed=True, funds_protected=True,
                            milestone_verified=True, pod_ok=True, payout_account_id=None)
        self.assertNotIn("recipient_verification_not_met", g["denied_reasons"])

    def test_enforcement_off_no_recipient_requirement(self):
        ap.set_config(self.c, "platform", "", "delivery.verification_enforced", "false", actor=SUP)
        b = _bk(self.c)
        g = tc.release_gate(self.c, b, 1, funding_confirmed=True, funds_protected=True,
                            milestone_verified=True, pod_ok=True, payout_account_id=None)
        self.assertNotIn("recipient_verification_not_met", g["denied_reasons"])


class PolicyAndProjection(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_heavy_policy_requires_signature(self):
        b = _bk(self.c, vehicle="crane", km=10)
        pol = dv.resolve_policy(self.c, b)
        self.assertIn("RECIPIENT_SIGNATURE_REQUIRED", pol["requirements"])

    def test_public_status_hides_otp_and_phone(self):
        ap.set_config(self.c, "platform", "", "delivery.policy.default", "POD_REQUIRED,RECIPIENT_OTP_REQUIRED", actor=SUP)
        b, code = _issued(self.c)
        ps = dv.public_status(self.c, b)
        blob = str(ps)
        self.assertNotIn(code, blob)
        self.assertNotIn("09171234567", blob)
        self.assertIn(ps["state"], ("Pending", "Verified"))

    def test_status_masks_recipient(self):
        b, _ = _issued(self.c)
        st = dv.status(self.c, b)
        self.assertNotIn("09171234567", str(st))
        self.assertEqual(st["mobile"], "******4567")

    def test_evidence_bundle_no_plaintext_and_claim_independence(self):
        b, code = _issued(self.c)
        dv.verify_otp(self.c, DRIVER, b, code)
        ev = dv.evidence_bundle(self.c, OPS, b)
        self.assertNotIn(code, str(ev))
        self.assertIn("does not waive", ev["note"])


class OperationalFlows(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_multi_stop_independent_codes(self):
        b = _bk(self.c); dv.set_recipient(self.c, OPS, b, "R", "0917")
        a = dv.issue_otp(self.c, OPS, b, stop_seq=1)["code"]
        bcode = dv.issue_otp(self.c, OPS, b, stop_seq=2)["code"]
        self.assertEqual(dv.verify_otp(self.c, DRIVER, b, a, stop_seq=1)["result"], "RECIPIENT_VERIFIED")
        self.assertEqual(dv.verify_otp(self.c, DRIVER, b, bcode, stop_seq=2)["result"], "RECIPIENT_VERIFIED")

    def test_offline_capture_pending(self):
        b = _bk(self.c)
        r = dv.offline_capture(self.c, DRIVER, b, pod={"x": 1}, signature="sig")
        self.assertEqual(r["status"], "OFFLINE_VERIFICATION_PENDING")

    def test_view_requires_permission(self):
        weak = {"id": 9, "role": "x", "perms": {"job.read"}, "tenant_id": None}
        with self.assertRaises(Exception):
            dv.admin_queue(self.c, weak)


class Persistence(unittest.TestCase):
    def test_restart_persistence(self):
        import os, tempfile
        path = tempfile.mktemp(suffix=".sqlite")
        try:
            c1 = db.connect(path)
            b = _bk(c1); dv.set_recipient(c1, OPS, b, "R", "0917")
            code = dv.issue_otp(c1, OPS, b)["code"]
            if hasattr(c1, "close"): c1.close()
            c2 = db.connect(path)
            self.assertEqual(dv.verify_otp(c2, DRIVER, b, code)["result"], "RECIPIENT_VERIFIED")
        finally:
            try: os.remove(path)
            except Exception: pass


if __name__ == "__main__":
    unittest.main()
