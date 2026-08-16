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
