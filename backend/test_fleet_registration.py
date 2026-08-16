"""Service Provider & Fleet Registration Workspace — dynamic, master-data-driven registration over the
existing carrier / vehicle / driver / compliance domains.

Proves: variant master data maps to real categories; the deterministic classification engine
(specs -> canonical variant + tonnage class) incl. the exact "6-Wheeler Closed Van - 4T Class" example;
unclassifiable specs are rejected; register_unit classifies then reuses register_vehicle and lands the
unit DRAFT (provider never self-verifies); per-unit eligibility returns SPECIFIC coded reasons composed
from the existing gates; service-area gating; fleet dashboard; bulk import isolates bad rows; new
variants are admin-extendable; RBAC; tenant isolation; integrity.
"""
import unittest

import db
import core
import admin_platform as ap
import marketplace_onboarding as mo
import fleet_registration as fr


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.op = self._a(10)
        self.cid = self._carrier()

    def _a(self, id, perms=("*",)):
        return {"id": id, "role": "ops", "perms": set(perms), "tenant_id": self.rgo}

    def _carrier(self, reg="C1", kyb=False):
        cid = mo.create_carrier_application(self.c, self.op, "FLEET_OPERATOR", "ABC Logistics",
                                            registration_type="SEC", registration_number=reg, operating_address="M")
        mo.submit_carrier(self.c, self.op, cid); mo.verify_carrier(self.c, self._a(11), cid)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION", "AUTHORITY_TO_OPERATE", "INSURANCE"):
            d = mo.upload_document(self.c, self.op, dt, "CARRIER", cid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self._a(11), d)
        mo.activate_carrier(self.c, self._a(12), cid); return cid


# --------------------------------------------------------------------------- #
class Taxonomy(Base):
    def test_seeded_and_map_to_real_categories(self):
        self.assertGreaterEqual(len(fr.list_variants(self.c, self.op)), 20)
        self.assertTrue(fr.run_integrity(self.c, self.op)["ok"])   # every variant -> real category

    def test_admin_can_add_variant(self):
        fr.set_variant(self.c, self.op, "TRUCK", "truck_8w_custom", "8-Wheeler Custom", "truck_10w",
                       class_group="MEDIUM_HEAVY", rules={"vehicle_type": "TRUCK", "wheels": 8}, priority=9)
        # tenant-scoped variant -> classify must be called with the tenant (as register_unit does)
        got = fr.classify(self.c, {"vehicle_type": "TRUCK", "wheels": 8, "payload_kg": 12000}, tenant_id=self.rgo)
        self.assertEqual(got["variant_code"], "truck_8w_custom")

    def test_variant_manage_rbac(self):
        weak = self._a(20, perms=("marketplace.fleet.view",))
        with self.assertRaises(core.ForbiddenError):
            fr.set_variant(self.c, weak, "TRUCK", "x", "X", "truck_6w")

    def test_variant_needs_real_underlying_category(self):
        with self.assertRaises(core.NotFoundError):
            fr.set_variant(self.c, self.op, "TRUCK", "x", "X", "nonexistent_cat")


# --------------------------------------------------------------------------- #
class Classification(Base):
    def test_six_wheeler_closed_van_4t(self):
        r = fr.classify(self.c, {"vehicle_type": "TRUCK", "wheels": 6, "body": "closed_van", "payload_kg": 4000})
        self.assertEqual(r["class_label"], "6-Wheeler Closed Van - 4T Class")
        self.assertEqual(r["category_code"], "truck_6w")

    def test_ten_wheeler_wing_15t(self):
        r = fr.classify(self.c, {"vehicle_type": "TRUCK", "wheels": 10, "body": "wing_van", "payload_kg": 15000})
        self.assertEqual(r["class_label"], "10-Wheeler Wing Van - 15T Class")

    def test_refrigerated_wins_over_plain(self):
        r = fr.classify(self.c, {"vehicle_type": "TRUCK", "wheels": 6, "refrigerated": True, "payload_kg": 7000})
        self.assertEqual(r["variant_code"], "truck_6w_ref")

    def test_forklift_supported(self):
        r = fr.classify(self.c, {"vehicle_type": "FORKLIFT", "lifting": True, "payload_kg": 3000})
        self.assertEqual(r["category_code"], "forklift")

    def test_unclassifiable_rejected(self):
        with self.assertRaises(core.ValidationError):
            fr.classify(self.c, {"vehicle_type": "SPACESHIP"})

    def test_deterministic(self):
        specs = {"vehicle_type": "TRUCK", "wheels": 6, "body": "wing_van", "payload_kg": 7500}
        a = fr.classify(self.c, specs); b = fr.classify(self.c, specs)
        self.assertEqual(a["variant_code"], b["variant_code"])


# --------------------------------------------------------------------------- #
class Registration(Base):
    def test_register_unit_lands_draft(self):
        r = fr.register_unit(self.c, self.op, self.cid, "ABC-001",
                             {"vehicle_type": "TRUCK", "wheels": 6, "body": "closed_van", "payload_kg": 4000})
        self.assertEqual(r["status"], "DRAFT")
        row = self.c.execute("SELECT status,category_code FROM mkt_vehicles WHERE id=?", (r["vehicle_id"],)).fetchone()
        self.assertEqual(row["status"], "DRAFT")           # provider never self-activates
        self.assertEqual(row["category_code"], "truck_6w")
        spec = fr.unit_spec(self.c, self.op, r["vehicle_id"])
        self.assertEqual(spec["spec"]["class_label"], "6-Wheeler Closed Van - 4T Class")

    def test_stores_both_provider_and_canonical(self):
        r = fr.register_unit(self.c, self.op, self.cid, "ABC-002",
                             {"vehicle_type": "TRUCK", "wheels": 10, "body": "wing_van", "payload_kg": 14000})
        s = fr.unit_spec(self.c, self.op, r["vehicle_id"])["spec"]
        self.assertEqual(s["provider_specs"]["wheels"], 10)          # provider-entered
        self.assertEqual(s["canonical"]["variant_code"], "truck_10w_wing")   # LiftHaul canonical


# --------------------------------------------------------------------------- #
class Eligibility(Base):
    def test_coded_reasons_for_unverified_unit(self):
        r = fr.register_unit(self.c, self.op, self.cid, "ABC-001",
                             {"vehicle_type": "TRUCK", "wheels": 6, "body": "closed_van", "payload_kg": 4000})
        el = fr.unit_eligibility(self.c, self.op, self.cid, r["vehicle_id"])
        self.assertFalse(el["eligible"])
        self.assertIn("NOT_ACTIVATED", el["reasons"])
        self.assertIn("CPC_INVALID", el["reasons"])                 # no LTFRB authority on file
        self.assertTrue(all(x in fr.ELIG for x in el["reasons"]))   # every reason is a governed code

    def test_service_area_gate(self):
        r = fr.register_unit(self.c, self.op, self.cid, "ABC-001",
                             {"vehicle_type": "TRUCK", "wheels": 6, "body": "closed_van", "payload_kg": 4000})
        fr.set_service_area(self.c, self.op, self.cid, "NCR")
        el = fr.unit_eligibility(self.c, self.op, self.cid, r["vehicle_id"], job_area="CEBU")
        self.assertIn("OUTSIDE_SERVICE_AREA", el["reasons"])
        el2 = fr.unit_eligibility(self.c, self.op, self.cid, r["vehicle_id"], job_area="NCR")
        self.assertNotIn("OUTSIDE_SERVICE_AREA", el2["reasons"])

    def test_maintenance_hold(self):
        r = fr.register_unit(self.c, self.op, self.cid, "ABC-001",
                             {"vehicle_type": "TRUCK", "wheels": 6, "body": "closed_van", "payload_kg": 4000})
        mo.set_vehicle_status(self.c, self.op, r["vehicle_id"], "MAINTENANCE")
        el = fr.unit_eligibility(self.c, self.op, self.cid, r["vehicle_id"])
        self.assertIn("MAINTENANCE_HOLD", el["reasons"])


# --------------------------------------------------------------------------- #
class Coverage(Base):
    def test_capabilities_and_areas(self):
        fr.set_capability(self.c, self.op, self.cid, "crane_rental")
        fr.set_service_area(self.c, self.op, self.cid, "LUZON", scope="ISLAND_GROUP")
        self.assertIn("crane_rental", fr.list_capabilities(self.c, self.op, self.cid))
        self.assertEqual(len(fr.list_service_areas(self.c, self.op, self.cid)), 1)

    def test_idempotent(self):
        fr.set_capability(self.c, self.op, self.cid, "rigging")
        r = fr.set_capability(self.c, self.op, self.cid, "rigging")
        self.assertTrue(r.get("idempotent"))


# --------------------------------------------------------------------------- #
class Bulk(Base):
    def test_dry_run_and_real_isolate_errors(self):
        rows = [
            {"plate_number": "B1", "vehicle_type": "TRUCK", "wheels": 10, "body": "wing_van", "payload_kg": 15000},
            {"plate_number": "B2", "vehicle_type": "TRUCK", "wheels": 6, "body": "dropside", "payload_kg": 8000},
            {"plate_number": "BAD", "vehicle_type": "UNKNOWN"},
        ]
        dry = fr.bulk_import(self.c, self.op, self.cid, rows, dry_run=True)
        self.assertEqual(dry["valid"], 2)
        self.assertEqual(dry["created"], 0)                 # dry run creates nothing
        real = fr.bulk_import(self.c, self.op, self.cid, rows)
        self.assertEqual(real["created"], 2)                # bad row didn't block the good ones
        self.assertFalse([r for r in real["results"] if r["index"] == 2][0]["ok"])


# --------------------------------------------------------------------------- #
class Isolation(Base):
    def test_tenant_isolation(self):
        r = fr.register_unit(self.c, self.op, self.cid, "ABC-001",
                             {"vehicle_type": "TRUCK", "wheels": 6, "body": "closed_van", "payload_kg": 4000})
        other = {"id": 99, "role": "ops", "perms": {"*"}, "tenant_id": 999999}
        with self.assertRaises(core.NotFoundError):
            fr.unit_spec(self.c, other, r["vehicle_id"])


if __name__ == "__main__":
    unittest.main()
