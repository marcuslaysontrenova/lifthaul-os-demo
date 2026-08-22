"""Public Service-Provider self-registration — credential + one-time-code verification flow.

Verifies the governance invariants: reuses canonical domains (mkt_carriers APPLICATION + users +
carrier_principals), the login is INACTIVE until the contact code is verified, the code is single-use /
attempt-limited / expiry-checked, a verified login resolves to its OWN carrier only, and the provider is
never marketplace-eligible from this surface (compliance stays independent). The classification preview is
read-only.
"""
import os
import unittest

os.environ.setdefault("APP_ENV", "development")

import db
import core
import public_provider as pp
import carrier_portal as cp


def _payload(**over):
    base = {"provider_type": "FLEET_OPERATOR", "legal_name": "ABC Logistics Inc.",
            "trade_name": "ABC", "email": "ops@abc.test", "mobile": "09171234567",
            "username": "ops@abc.test", "password": "Str0ngPass!", "island_group": "LUZON"}
    base.update(over)
    return base


class PublicProviderRegistration(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect("sqlite:///:memory:")

    # --- happy path: register -> pending -> verify -> active + own-carrier binding ---
    def test_register_then_verify_activates_and_binds_own_carrier(self):
        r = pp.submit(self.conn, _payload())
        self.assertEqual(r["status"], "VERIFY_CONTACT")
        self.assertTrue(r["ref"].startswith("SP-"))
        self.assertIn("dev_code", r)                      # non-production surfaces the code
        self.assertFalse(r["delivered"])                  # no messaging provider connected -> honest

        # carrier landed as an APPLICATION, not verified/eligible
        car = self.conn.execute("SELECT status FROM mkt_carriers WHERE id=?", (r["carrier_id"],)).fetchone()
        self.assertEqual((car["status"] or "").upper(), "APPLICATION")

        # login is INACTIVE before verification
        with self.assertRaises(core.AuthError):
            core.login(self.conn, "ops@abc.test", "Str0ngPass!")

        v = pp.verify(self.conn, {"challenge_id": r["challenge_id"], "code": r["dev_code"]})
        self.assertEqual(v["role"], "carrier_principal")
        self.assertEqual(v["carrier_id"], r["carrier_id"])
        self.assertTrue(v["token"])
        self.assertEqual(v["redirect"], "portal.html")

        # login now works and resolves to its OWN carrier via the binding
        tok = core.login(self.conn, "ops@abc.test", "Str0ngPass!")
        actor = core.actor_for(self.conn, tok)
        self.assertEqual(cp.resolve_carrier(self.conn, actor), r["carrier_id"])

    # --- code is single-use: cannot verify twice ---
    def test_code_is_single_use(self):
        r = pp.submit(self.conn, _payload())
        pp.verify(self.conn, {"challenge_id": r["challenge_id"], "code": r["dev_code"]})
        with self.assertRaises(core.ValidationError):
            pp.verify(self.conn, {"challenge_id": r["challenge_id"], "code": r["dev_code"]})

    # --- wrong code is rejected and attempt-limited ---
    def test_wrong_code_rejected_and_locks(self):
        r = pp.submit(self.conn, _payload())
        for _ in range(5):
            with self.assertRaises(core.ValidationError):
                pp.verify(self.conn, {"challenge_id": r["challenge_id"], "code": "000000"})
        # locked now — even the correct code fails
        with self.assertRaises(core.ValidationError):
            pp.verify(self.conn, {"challenge_id": r["challenge_id"], "code": r["dev_code"]})

    # --- expiry is enforced ---
    def test_expired_code_rejected(self):
        r = pp.submit(self.conn, _payload())
        self.conn.execute("UPDATE provider_signup SET expires_at='2000-01-01T00:00:00' WHERE id=?",
                          (r["challenge_id"],))
        self.conn.commit()
        with self.assertRaises(core.ValidationError):
            pp.verify(self.conn, {"challenge_id": r["challenge_id"], "code": r["dev_code"]})

    # --- resend issues a fresh usable code and supersedes the old one ---
    def test_resend_supersedes_old_code(self):
        r = pp.submit(self.conn, _payload())
        old = r["dev_code"]
        r2 = pp.resend(self.conn, {"challenge_id": r["challenge_id"]})
        self.assertNotEqual(r2["challenge_id"], r["challenge_id"])
        with self.assertRaises(core.ValidationError):      # old challenge superseded
            pp.verify(self.conn, {"challenge_id": r["challenge_id"], "code": old})
        v = pp.verify(self.conn, {"challenge_id": r2["challenge_id"], "code": r2["dev_code"]})
        self.assertEqual(v["status"], "ACTIVE")

    # --- validation guards ---
    def test_requires_legal_name(self):
        with self.assertRaises(core.ValidationError):
            pp.submit(self.conn, _payload(legal_name=""))

    def test_requires_contact(self):
        with self.assertRaises(core.ValidationError):
            pp.submit(self.conn, _payload(email="", mobile=""))

    def test_rejects_weak_password(self):
        with self.assertRaises(core.ValidationError):
            pp.submit(self.conn, _payload(password="short"))

    def test_duplicate_username_rejected(self):
        pp.submit(self.conn, _payload())
        with self.assertRaises(core.ConflictError):
            pp.submit(self.conn, _payload(legal_name="Other Co", email="x@y.test"))

    # --- no new domain: reuses canonical tables only ---
    def test_reuses_canonical_domains_no_new_carrier_table(self):
        r = pp.submit(self.conn, _payload())
        # a users row + carrier_principals binding exist for the applicant
        u = self.conn.execute("SELECT role,status FROM users WHERE email=?", ("ops@abc.test",)).fetchone()
        self.assertEqual(u["role"], "carrier_principal")
        self.assertEqual(u["status"], "PENDING_VERIFICATION")
        b = self.conn.execute("SELECT carrier_id FROM carrier_principals WHERE carrier_id=?",
                              (r["carrier_id"],)).fetchone()
        self.assertIsNotNone(b)

    # --- audit trail written ---
    def test_audit_trail(self):
        r = pp.submit(self.conn, _payload())
        pp.verify(self.conn, {"challenge_id": r["challenge_id"], "code": r["dev_code"]})
        acts = [x["action"] for x in self.conn.execute(
            "SELECT action FROM audit_logs WHERE entity IN ('mkt_carriers','provider_signup')").fetchall()]
        self.assertIn("PUBLIC_PROVIDER_APPLIED", acts)
        self.assertIn("PROVIDER_SIGNUP_CODE_ISSUED", acts)
        self.assertIn("PROVIDER_SIGNUP_VERIFIED", acts)


class OtpProductionBoundary(unittest.TestCase):
    """The one-time code must fail CLOSED: never surfaced/logged in production or an ambiguous env,
    and delivery-unavailable must leave the account PENDING."""

    def setUp(self):
        self.conn = db.connect("sqlite:///:memory:")
        self._env = os.environ.get("APP_ENV")
        self._cap = os.environ.get("OTP_TEST_CAPTURE")

    def tearDown(self):
        for k, v in (("APP_ENV", self._env), ("OTP_TEST_CAPTURE", self._cap)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        pp._CODE_CAPTURE.clear()

    def test_production_never_returns_dev_code_and_stays_pending(self):
        os.environ["APP_ENV"] = "production"
        os.environ.pop("OTP_TEST_CAPTURE", None)
        r = pp.submit(self.conn, _payload())
        self.assertNotIn("dev_code", r)
        self.assertFalse(r["delivered"])
        self.assertIn("VERIFICATION DELIVERY UNAVAILABLE", r["delivery_note"])
        u = self.conn.execute("SELECT status FROM users WHERE email=?", ("ops@abc.test",)).fetchone()
        self.assertEqual(u["status"], "PENDING_VERIFICATION")

    def test_unknown_env_is_treated_as_production(self):
        os.environ["APP_ENV"] = "whatever"
        r = pp.submit(self.conn, _payload())
        self.assertNotIn("dev_code", r)
        self.assertFalse(pp.env_posture()["recognised"])
        self.assertTrue(pp.env_posture()["treated_as_production"])

    def test_missing_env_is_treated_as_production(self):
        os.environ.pop("APP_ENV", None)
        r = pp.submit(self.conn, _payload())
        self.assertNotIn("dev_code", r)
        self.assertTrue(pp.env_posture()["treated_as_production"])

    def test_capture_is_opt_in_only(self):
        os.environ["APP_ENV"] = "production"
        os.environ.pop("OTP_TEST_CAPTURE", None)
        r = pp.submit(self.conn, _payload())
        self.assertIsNone(pp.peek_code(self.conn, r["challenge_id"]))   # capture off -> no peek

    def test_production_flow_completes_via_capture_harness(self):
        os.environ["APP_ENV"] = "production"
        os.environ["OTP_TEST_CAPTURE"] = "1"
        r = pp.submit(self.conn, _payload())
        self.assertNotIn("dev_code", r)                                 # still never leaked over the API
        code = pp.peek_code(self.conn, r["challenge_id"])
        self.assertTrue(code)
        v = pp.verify(self.conn, {"challenge_id": r["challenge_id"], "code": code})
        self.assertEqual(v["status"], "ACTIVE")

    def test_code_never_written_to_audit(self):
        os.environ["APP_ENV"] = "production"
        os.environ["OTP_TEST_CAPTURE"] = "1"
        r = pp.submit(self.conn, _payload())
        code = pp.peek_code(self.conn, r["challenge_id"])
        rows = self.conn.execute("SELECT new_value FROM audit_logs WHERE action=?",
                                 ("PROVIDER_SIGNUP_CODE_ISSUED",)).fetchall()
        blob = " ".join((x["new_value"] or "") for x in rows)
        self.assertNotIn(code, blob)


class ReferralAttribution(unittest.TestCase):
    """Registration with ?ref= attributes the referral server-side; a bad code never blocks registration."""

    def setUp(self):
        self.conn = db.connect("sqlite:///:memory:")

    def test_valid_referral_code_attributes(self):
        import referral as rf
        import marketplace_onboarding as mo
        sup = {"id": 1, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
        a = mo.create_carrier_application(self.conn, sup, "FLEET_OPERATOR", "Referrer",
                                          registration_type="SEC", registration_number="R1")
        code = rf.issue_code(self.conn, sup, "CARRIER", a, referrer_label="Ref")["code"]
        r = pp.submit(self.conn, _payload(referral_code=code))
        self.assertTrue(r["referral_applied"])
        row = self.conn.execute("SELECT referred_ref,status FROM referrals").fetchone()
        self.assertEqual(row["referred_ref"], str(r["carrier_id"]))
        self.assertEqual(row["status"], "REGISTERED")   # registered != earned

    def test_invalid_referral_code_never_blocks(self):
        r = pp.submit(self.conn, _payload(referral_code="LH-BAD-000000"))
        self.assertEqual(r["status"], "VERIFY_CONTACT")
        self.assertFalse(r["referral_applied"])


class PublicPreviews(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect("sqlite:///:memory:")

    def test_variants_taxonomy_is_readonly_public(self):
        d = pp.variants(self.conn)
        self.assertEqual(len(d["provider_types"]), 14)
        self.assertTrue(d["categories"])                  # master data seeded

    def test_classify_preview_resolves_canonical_class(self):
        d = pp.classify_preview(self.conn, {"vehicle_type": "TRUCK", "wheels": 6,
                                            "body": "closed_van", "payload_kg": 4000})
        self.assertTrue(d.get("class_label"))
        self.assertNotIn("error", d)

    def test_classify_preview_bad_specs_is_soft_error(self):
        d = pp.classify_preview(self.conn, {})
        self.assertFalse(d.get("classified", True))
        self.assertIn("error", d)


if __name__ == "__main__":
    unittest.main()
