"""Cargo Insurance Compliance — provider-uploaded document gate (NOT an insurance product).

Covers: required/off default, upload→SUBMITTED, independent verify/reject (no self-verify), expiry
(EXPIRING passes / EXPIRED blocks), pending/rejected block, separation from vehicle insurance, audit.
"""
import os
import datetime
import unittest

os.environ.setdefault("APP_ENV", "development")

import db
import core
import admin_platform as ap
import marketplace_onboarding as mo
import cargo_insurance as ci

SUP = {"id": 1, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
CARR = {"id": 9, "role": "carrier_principal", "perms": set(core.PERMISSIONS["carrier_principal"]), "tenant_id": None}
PROV = {"id": 7, "role": "ops", "perms": {"marketplace.vehicle.manage"}, "tenant_id": None}  # upload-only


def _future(days):
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def _past(days):
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()


class RequiredGate(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.cid = mo.create_carrier_application(self.c, SUP, "FLEET_OPERATOR", "Acme",
                                                 registration_type="SEC", registration_number="S1")

    def test_not_required_by_default(self):
        self.assertFalse(ci.required(self.c))
        self.assertEqual(ci.status_for(self.c, self.cid, 1), "NOT_REQUIRED")
        self.assertEqual(ci.eligibility_gate(self.c, self.cid, 1), "PASS")

    def _require(self):
        ap.set_config(self.c, "platform", "", "cargo_insurance.required", "true", actor=SUP)

    def test_required_missing_blocks(self):
        self._require()
        self.assertEqual(ci.status_for(self.c, self.cid, 1), "MISSING")
        self.assertEqual(ci.eligibility_gate(self.c, self.cid, 1), "CARGO_INSURANCE_MISSING")

    def test_upload_pending_then_verified(self):
        self._require()
        up = ci.upload(self.c, PROV, self.cid, "ACME Insurance", "POL-1", "cert.pdf",
                       vehicle_id=1, coverage_amount=1_000_000, effective_from=_past(10), expiry_date=_future(300))
        self.assertEqual(up["status"], "SUBMITTED")
        self.assertEqual(ci.eligibility_gate(self.c, self.cid, 1), "CARGO_INSURANCE_PENDING")
        ci.review(self.c, SUP, up["id"], "VERIFY", verification_source="insurer confirmation")
        self.assertEqual(ci.status_for(self.c, self.cid, 1), "VERIFIED")
        self.assertEqual(ci.eligibility_gate(self.c, self.cid, 1), "PASS")

    def test_provider_cannot_self_verify(self):
        self._require()
        up = ci.upload(self.c, PROV, self.cid, "ACME", "POL-1", "cert.pdf", vehicle_id=1, expiry_date=_future(300))
        with self.assertRaises(core.ForbiddenError):
            ci.review(self.c, CARR, up["id"], "VERIFY")
        with self.assertRaises(core.ForbiddenError):
            ci.review(self.c, PROV, up["id"], "VERIFY")   # uploader perm cannot verify

    def test_rejected_blocks(self):
        self._require()
        up = ci.upload(self.c, PROV, self.cid, "ACME", "POL-1", "cert.pdf", vehicle_id=1, expiry_date=_future(300))
        ci.review(self.c, SUP, up["id"], "REJECT", rejection_reason="illegible certificate")
        self.assertEqual(ci.eligibility_gate(self.c, self.cid, 1), "CARGO_INSURANCE_REJECTED")

    def test_expired_blocks_expiring_passes(self):
        self._require()
        # expired
        up = ci.upload(self.c, PROV, self.cid, "ACME", "POL-1", "cert.pdf", vehicle_id=1, expiry_date=_past(1))
        ci.review(self.c, SUP, up["id"], "VERIFY")
        self.assertEqual(ci.status_for(self.c, self.cid, 1), "EXPIRED")
        self.assertEqual(ci.eligibility_gate(self.c, self.cid, 1), "CARGO_INSURANCE_EXPIRED")
        # expiring (within 30d) still passes but is flagged
        up2 = ci.upload(self.c, PROV, self.cid, "ACME", "POL-2", "cert.pdf", vehicle_id=2, expiry_date=_future(10))
        ci.review(self.c, SUP, up2["id"], "VERIFY")
        self.assertEqual(ci.status_for(self.c, self.cid, 2), "EXPIRING")
        self.assertEqual(ci.eligibility_gate(self.c, self.cid, 2), "PASS")

    def test_upload_requires_insurer_policy_document(self):
        with self.assertRaises(core.ValidationError):
            ci.upload(self.c, PROV, self.cid, "", "POL", "doc", vehicle_id=1)
        with self.assertRaises(core.ValidationError):
            ci.upload(self.c, PROV, self.cid, "I", "POL", "", vehicle_id=1)


class SeparationFromVehicleInsurance(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.cid = mo.create_carrier_application(self.c, SUP, "FLEET_OPERATOR", "Acme",
                                                 registration_type="SEC", registration_number="S1")
        ap.set_config(self.c, "platform", "", "cargo_insurance.required", "true", actor=SUP)

    def test_vehicle_insurance_does_not_satisfy_cargo(self):
        # a verified VEHICLE insurance document must NOT make cargo-insurance status VERIFIED
        op = {"id": 10, "role": "ops", "perms": {"*"}, "tenant_id": None}
        vf = {"id": 11, "role": "ops", "perms": {"*"}, "tenant_id": None}   # maker/checker for docs
        vid = mo.register_vehicle(self.c, SUP, self.cid, "truck_6w", "P1")
        doc = mo.upload_document(self.c, op, "INSURANCE", "VEHICLE", vid, expiry_date="2030-01-01")
        mo.verify_document(self.c, vf, doc)
        self.assertEqual(ci.status_for(self.c, self.cid, vid), "MISSING")   # cargo still missing
        self.assertEqual(ci.eligibility_gate(self.c, self.cid, vid), "CARGO_INSURANCE_MISSING")
        self.assertTrue(ci.summary(self.c, self.cid, vid)["separate_from_vehicle_insurance"])


class Audit(unittest.TestCase):
    def test_audit_events(self):
        c = db.connect(":memory:")
        cid = mo.create_carrier_application(c, SUP, "FLEET_OPERATOR", "A", registration_type="SEC", registration_number="S1")
        up = ci.upload(c, PROV, cid, "ACME", "POL-1", "cert.pdf", vehicle_id=1, expiry_date=_future(300))
        ci.review(c, SUP, up["id"], "VERIFY")
        acts = {r["action"] for r in c.execute("SELECT action FROM audit_logs WHERE entity='cargo_insurance'").fetchall()}
        self.assertIn("CARGO_INSURANCE_UPLOADED", acts)
        self.assertIn("CARGO_INSURANCE_VERIFIED", acts)


if __name__ == "__main__":
    unittest.main()
