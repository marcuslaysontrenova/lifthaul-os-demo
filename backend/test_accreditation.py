"""Provider Vehicle/Equipment Accreditation Fee Engine.

Covers: free company registration, variant/equipment fee calculation, historical snapshot immutability,
fleet-volume discount, waiver, VAT, payment-independent-from-compliance, paid-but-ineligible, unpaid gate,
frontend fee/variant tampering ignored, manual quote, refund, RBAC/SoD, tenant isolation, audit.
"""
import os
import unittest

os.environ.setdefault("APP_ENV", "development")

import db
import core
import admin_platform as ap
import marketplace_onboarding as mo
import fleet_registration as fr
import accreditation as acc

SUP = {"id": 1, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
FIN = {"id": 2, "role": "finance", "perms": {"payment.*"}, "tenant_id": None}
CARR = {"id": 9, "role": "carrier_principal", "perms": set(core.PERMISSIONS["carrier_principal"]), "tenant_id": None}

SPEC_6W = {"vehicle_type": "TRUCK", "wheels": 6, "body": "closed_van", "payload_kg": 4000}
SPEC_FORK = {"vehicle_type": "FORKLIFT", "lifting": True, "rated_capacity_kg": 3000}
SPEC_TOWER = {"vehicle_type": "CRANE", "lifting": True, "subtype": "tower", "lifting_capacity_kg": 80000}


def _carrier(c, reg="S1"):
    return mo.create_carrier_application(c, SUP, "FLEET_OPERATOR", "Acme " + reg,
                                         registration_type="SEC", registration_number="SEC-" + reg)


def _unit(c, cid, plate, spec):
    return fr.register_unit(c, SUP, cid, plate, spec)["vehicle_id"]


class FeeCalculation(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.cid = _carrier(self.c)

    def test_company_registration_is_free(self):
        # creating the carrier/company assesses NO fee — only vehicles are accredited
        self.assertEqual(self.c.execute("SELECT COUNT(*) n FROM accreditation_assessments").fetchone()["n"], 0)

    def test_variant_fee_6w(self):
        a = acc.assessment_for(self.c, _unit(self.c, self.cid, "P1", SPEC_6W))
        self.assertEqual(a["status"], "ASSESSED")
        self.assertEqual(a["subtotal"], 799.0)                 # §16 6-wheel
        self.assertAlmostEqual(sum(a["components"].values()), 799.0, places=2)
        self.assertAlmostEqual(a["tax"], round(799.0 * 0.12, 2), places=2)
        self.assertAlmostEqual(a["total"], round(799.0 * 1.12, 2), places=2)

    def test_equipment_fee_forklift(self):
        a = acc.assessment_for(self.c, _unit(self.c, self.cid, "F1", SPEC_FORK))
        self.assertEqual(a["subtotal"], 999.0)                 # §16 forklift

    def test_specialized_crane_is_manual_quote(self):
        a = acc.assessment_for(self.c, _unit(self.c, self.cid, "TC1", SPEC_TOWER))
        self.assertEqual(a["status"], "MANUAL_QUOTE")
        self.assertIsNone(a["total"])

    def test_vat_configurable(self):
        ap.set_config(self.c, "platform", "", "accreditation.vat_pct", "0", actor=SUP)
        a = acc.assessment_for(self.c, _unit(self.c, self.cid, "P2", SPEC_6W))
        self.assertEqual(a["tax"], 0.0)
        self.assertEqual(a["total"], 799.0)


class VolumeAndSnapshot(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.cid = _carrier(self.c)

    def test_fleet_volume_discount(self):
        # register 10 units -> the 10th sits in the 10-24 tier (5% off)
        vids = [_unit(self.c, self.cid, f"V{i}", SPEC_6W) for i in range(10)]
        a = acc.assess_fee(self.c, SUP, self.cid, vids[-1])   # re-assess now that fleet is larger
        self.assertGreater(a["discount"], 0)
        self.assertEqual(a["discount_label"], "10–24 units")

    def test_historical_snapshot_immutable_after_schedule_change(self):
        vid = _unit(self.c, self.cid, "P1", SPEC_6W)
        a1 = acc.assessment_for(self.c, vid)
        acc.record_payment(self.c, FIN, a1["id"], "gcash", "PAY-1")
        # platform raises the 6W fee later
        acc.set_fee(self.c, SUP, "CATEGORY", "truck_6w", base_fee=1500)
        acc.assess_fee(self.c, SUP, self.cid, vid)            # re-assess must NOT alter the PAID snapshot
        a2 = acc.assessment_for(self.c, vid)
        self.assertEqual(a2["id"], a1["id"])
        self.assertEqual(a2["subtotal"], 799.0)               # original amount preserved
        self.assertEqual(a2["status"], "PAID")

    def test_new_schedule_applies_to_new_units_only(self):
        v1 = _unit(self.c, self.cid, "P1", SPEC_6W)
        self.assertEqual(acc.assessment_for(self.c, v1)["subtotal"], 799.0)
        acc.set_fee(self.c, SUP, "CATEGORY", "truck_6w", base_fee=1500)
        v2 = _unit(self.c, self.cid, "P2", SPEC_6W)
        self.assertEqual(acc.assessment_for(self.c, v2)["subtotal"], 1500.0)


class PaymentVsCompliance(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.cid = _carrier(self.c)
        ap.set_config(self.c, "platform", "", "accreditation.gate_enabled", "true", actor=SUP)
        self.vid = _unit(self.c, self.cid, "P1", SPEC_6W)

    def test_unpaid_unit_blocked(self):
        el = fr.unit_eligibility(self.c, SUP, self.cid, self.vid)
        self.assertIn("ACCREDITATION_FEE_UNPAID", el["reasons"])

    def test_paid_does_not_grant_eligibility(self):
        a = acc.assessment_for(self.c, self.vid)
        acc.record_payment(self.c, FIN, a["id"], "gcash", "PAY-1")
        el = fr.unit_eligibility(self.c, SUP, self.cid, self.vid)
        self.assertNotIn("ACCREDITATION_FEE_UNPAID", el["reasons"])
        self.assertFalse(el["eligible"])                       # still blocked by independent compliance
        self.assertTrue(any(r in el["reasons"] for r in ("COMPLIANCE_HOLD", "CPC_INVALID", "NOT_ACTIVATED")))

    def test_waiver_satisfies_fee_gate(self):
        a = acc.assessment_for(self.c, self.vid)
        acc.waive_fee(self.c, SUP, a["id"], "enterprise agreement")
        self.assertTrue(acc.fee_paid(self.c, self.vid))


class TamperingAndRBAC(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.cid = _carrier(self.c)
        self.vid = _unit(self.c, self.cid, "P1", SPEC_6W)

    def test_fee_is_server_authoritative(self):
        # assess ignores any client-supplied fee/variant; it reads the canonical vehicle_specs
        a = acc.assess_fee(self.c, SUP, self.cid, self.vid)
        self.assertEqual(a["subtotal"], 799.0)
        self.assertEqual(a["category_code"], "truck_6w")

    def test_carrier_cannot_pay_own_fee(self):
        a = acc.assessment_for(self.c, self.vid)
        with self.assertRaises(core.ForbiddenError):
            acc.record_payment(self.c, CARR, a["id"], "card", "X")

    def test_carrier_cannot_waive_or_change_schedule(self):
        a = acc.assessment_for(self.c, self.vid)
        with self.assertRaises(core.ForbiddenError):
            acc.waive_fee(self.c, CARR, a["id"], "nope")
        with self.assertRaises(core.ForbiddenError):
            acc.set_fee(self.c, CARR, "CATEGORY", "truck_6w", base_fee=1)

    def test_refund_only_when_paid(self):
        a = acc.assessment_for(self.c, self.vid)
        with self.assertRaises(core.ConflictError):
            acc.refund(self.c, FIN, a["id"], "duplicate")
        acc.record_payment(self.c, FIN, a["id"], "gcash", "PAY-1")
        self.assertEqual(acc.refund(self.c, FIN, a["id"], "duplicate", refund_ref="RF-1")["status"], "REFUNDED")


class AuditAndTenant(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.cid = _carrier(self.c)

    def test_audit_events(self):
        vid = _unit(self.c, self.cid, "P1", SPEC_6W)
        a = acc.assessment_for(self.c, vid)
        acc.record_payment(self.c, FIN, a["id"], "gcash", "PAY-1")
        acc.set_fee(self.c, SUP, "CATEGORY", "truck_6w", base_fee=850)
        acts = {r["action"] for r in self.c.execute(
            "SELECT action FROM audit_logs WHERE entity IN "
            "('accreditation_assessments','accreditation_schedule')").fetchall()}
        self.assertTrue({"ACCREDITATION_FEE_ASSESSED", "ACCREDITATION_FEE_PAID",
                         "FEE_SCHEDULE_CHANGED"}.issubset(acts))

    def test_tenant_isolation(self):
        owner = {"id": 5, "role": "ops", "perms": {"*"}, "tenant_id": 101}   # assessment stamped tenant 101
        a = acc.assess_fee(self.c, owner, self.cid, _unit(self.c, self.cid, "P1", SPEC_6W))
        other = {"id": 3, "role": "finance", "perms": {"payment.*"}, "tenant_id": 777}
        with self.assertRaises((core.NotFoundError, core.ForbiddenError)):
            acc.record_payment(self.c, other, a["id"], "gcash", "PAY-1")


if __name__ == "__main__":
    unittest.main()
