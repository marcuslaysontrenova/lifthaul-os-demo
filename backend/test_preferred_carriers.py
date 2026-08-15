"""Preferred Carriers / Dedicated Capacity — a shipper preference layer over the existing matching.

Proves: tiered preferences (PREFERRED/DEDICATED/EXCLUSIVE/BLOCKED); one preference per shipper-carrier
pair; apply_preferences REORDERS/FILTERS only (never adds an ineligible carrier, never overrides a hard
gate); BLOCKED excludes; EXCLUSIVE restricts the pool; DEDICATED/EXCLUSIVE outrank PREFERRED; the
generate_candidates hook actually shapes matching; dedicated-capacity commitments with honest
usage counting; RBAC; tenant isolation; integrity.
"""
import unittest

import db
import core
import admin_platform as ap
import marketplace as mkt
import marketplace_onboarding as mo
import marketplace_matching as mm
import preferred_carriers as pc


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.op = self._a(10)
        self.sid = self._shipper()
        self.c1 = self._carrier("C1", "P1")
        self.c2 = self._carrier("C2", "P2")
        self.c3 = self._carrier("C3", "P3")
        self._lane("MM-CAV")

    def _a(self, id, perms=("*",)):
        return {"id": id, "role": "ops", "perms": set(perms), "tenant_id": self.rgo}

    def _shipper(self, reg="S1"):
        s = mo.create_shipper_application(self.c, self.op, "CORPORATION", "Acme", registration_type="SEC",
                                          registration_number=reg, registered_address="Mkt",
                                          contract_accepted=1, privacy_accepted=1)
        mo.submit_shipper(self.c, self.op, s); mo.verify_shipper(self.c, self._a(11), s)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION"):
            d = mo.upload_document(self.c, self.op, dt, "SHIPPER", s, expiry_date="2027-01-01")
            mo.verify_document(self.c, self._a(11), d)
        mo.activate_shipper(self.c, self._a(12), s); return s

    def _carrier(self, reg, plate):
        cid = mo.create_carrier_application(self.c, self.op, "FLEET_OPERATOR", "Car" + reg, registration_type="SEC",
                                            registration_number=reg, operating_address="M", preferred_lanes=["CAVITE"])
        mo.submit_carrier(self.c, self.op, cid); mo.verify_carrier(self.c, self._a(11), cid)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION", "AUTHORITY_TO_OPERATE", "INSURANCE"):
            d = mo.upload_document(self.c, self.op, dt, "CARRIER", cid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self._a(11), d)
        mo.activate_carrier(self.c, self._a(12), cid)
        v = mo.register_vehicle(self.c, self.op, cid, "truck_6w", plate); mo.verify_vehicle(self.c, self._a(11), v)
        for dt in ("VEHICLE_REGISTRATION", "INSURANCE"):
            d = mo.upload_document(self.c, self.op, dt, "VEHICLE", v, expiry_date="2027-01-01")
            mo.verify_document(self.c, self._a(11), d)
        mo.activate_vehicle(self.c, self._a(12), v)
        dd = mo.register_driver(self.c, self.op, cid, "D" + reg, licence_expiry="2027-01-01", authorized_categories=["truck_6w"])
        mo.verify_driver(self.c, self._a(11), dd); mo.activate_driver(self.c, self._a(12), dd)
        return cid

    def _lane(self, code):
        lane = [l for l in mkt.list_lanes(self.c) if l["code"] == code][0]
        mkt.assess_lane(self.c, self.op, lane["id"], verified_carriers=5, backup_capacity=1,
                        price_model_validated=1, ops_support=1, payment_capable=1, dispute_process=1, monitoring=1)
        mkt.activate_lane(self.c, self._a(11), lane["id"], target="ACTIVE"); return lane

    def _candidates(self):
        bk = mm.create_booking(self.c, self.op, self.sid, "general", "METRO_MANILA", "CAVITE",
                               weight_kg=5000, volume_cbm=10, pickup_address="A", delivery_address="B")
        mm.validate_booking(self.c, self._a(11), bk); mm.select_pricing_mode(self.c, self._a(11), bk)
        mm.price_booking(self.c, self._a(11), bk)
        return mm.generate_candidates(self.c, self.op, bk)["candidates"]


# --------------------------------------------------------------------------- #
class Preferences(Base):
    def test_set_and_one_per_pair(self):
        pc.set_preference(self.c, self.op, self.sid, self.c1, "PREFERRED")
        pc.set_preference(self.c, self.op, self.sid, self.c1, "DEDICATED")   # upsert, not a 2nd row
        n = self.c.execute("SELECT COUNT(*) c FROM carrier_preferences WHERE shipper_id=? AND carrier_id=?",
                           (self.sid, self.c1)).fetchone()["c"]
        self.assertEqual(n, 1)

    def test_invalid_tier(self):
        with self.assertRaises(core.ValidationError):
            pc.set_preference(self.c, self.op, self.sid, self.c1, "FAVORITE")

    def test_unknown_carrier(self):
        with self.assertRaises(core.NotFoundError):
            pc.set_preference(self.c, self.op, self.sid, 999999, "PREFERRED")

    def test_rbac(self):
        weak = self._a(20, perms=("marketplace.preference.view",))
        with self.assertRaises(core.ForbiddenError):
            pc.set_preference(self.c, weak, self.sid, self.c1, "PREFERRED")


# --------------------------------------------------------------------------- #
class ApplyPreferences(Base):
    def test_preferred_boosted_blocked_excluded(self):
        pc.set_preference(self.c, self.op, self.sid, self.c3, "PREFERRED", priority=5)
        pc.set_preference(self.c, self.op, self.sid, self.c1, "BLOCKED")
        cands = self._candidates()
        ids = [x["carrier_id"] for x in cands]
        self.assertNotIn(self.c1, ids)          # blocked -> excluded
        self.assertEqual(ids[0], self.c3)       # preferred -> top
        self.assertEqual(cands[0]["preference_tier"], "PREFERRED")

    def test_exclusive_restricts_pool(self):
        pc.set_preference(self.c, self.op, self.sid, self.c2, "EXCLUSIVE")
        ids = [x["carrier_id"] for x in self._candidates()]
        self.assertEqual(ids, [self.c2])        # only the exclusive carrier competes

    def test_dedicated_outranks_preferred(self):
        pc.set_preference(self.c, self.op, self.sid, self.c1, "PREFERRED")
        pc.set_preference(self.c, self.op, self.sid, self.c2, "DEDICATED")
        ids = [x["carrier_id"] for x in self._candidates()]
        self.assertEqual(ids[0], self.c2)

    def test_apply_never_adds_ineligible(self):
        # a carrier NOT in the eligible ranked list is never introduced by a preference
        ranked = [{"carrier_id": self.c1, "score": 0.5}]
        pc.set_preference(self.c, self.op, self.sid, self.c2, "DEDICATED")
        out = pc.apply_preferences(self.c, self.sid, ranked, self.rgo)
        self.assertEqual([x["carrier_id"] for x in out], [self.c1])   # c2 not injected

    def test_no_prefs_is_identity(self):
        ranked = [{"carrier_id": self.c1, "score": 0.5}, {"carrier_id": self.c2, "score": 0.4}]
        out = pc.apply_preferences(self.c, self.sid, ranked, self.rgo)
        self.assertEqual(out, ranked)


# --------------------------------------------------------------------------- #
class Capacity(Base):
    def test_reserve_and_status(self):
        cap = pc.reserve_capacity(self.c, self.op, self.sid, self.c1, "truck_6w", 3,
                                  period_start="2026-08-01", period_end="2026-12-31")
        st = pc.capacity_status(self.c, self.op, cap["capacity_id"])
        self.assertEqual(st["committed_units"], 3)
        self.assertEqual(st["used_units"], 0)
        self.assertEqual(st["available_units"], 3)

    def test_reserve_implies_dedicated_preference(self):
        pc.reserve_capacity(self.c, self.op, self.sid, self.c1, "truck_6w", 2)
        prefs = {p["carrier_id"]: p["tier"] for p in pc.list_preferences(self.c, self.op, self.sid)}
        self.assertEqual(prefs.get(self.c1), "DEDICATED")

    def test_reserve_requires_active_carrier(self):
        mo.suspend_carrier(self.c, self.op, self.c1, "audit")
        with self.assertRaises(core.ConflictError):
            pc.reserve_capacity(self.c, self.op, self.sid, self.c1, "truck_6w", 2)

    def test_units_must_be_positive(self):
        with self.assertRaises(core.ValidationError):
            pc.reserve_capacity(self.c, self.op, self.sid, self.c1, "truck_6w", 0)

    def test_usage_counts_real_assignments(self):
        cap = pc.reserve_capacity(self.c, self.op, self.sid, self.c1, "truck_6w", 3,
                                  period_start="2026-08-01", period_end="2026-12-31")
        vid = self.c.execute("SELECT id FROM mkt_vehicles WHERE carrier_id=?", (self.c1,)).fetchone()["id"]
        self.c.execute("INSERT INTO mkt_assignments(tenant_id,booking_id,shipper_id,carrier_id,vehicle_id,"
                       "status,assigned_at) VALUES(?,?,?,?,?, 'CONFIRMED', '2026-09-01T10:00:00')",
                       (self.rgo, 1, self.sid, self.c1, vid))
        self.c.commit()
        st = pc.capacity_status(self.c, self.op, cap["capacity_id"])
        self.assertEqual(st["used_units"], 1)
        self.assertEqual(st["available_units"], 2)

    def test_cancel(self):
        cap = pc.reserve_capacity(self.c, self.op, self.sid, self.c1, "truck_6w", 2)
        self.assertEqual(pc.cancel_capacity(self.c, self.op, cap["capacity_id"])["status"], "CANCELLED")


# --------------------------------------------------------------------------- #
class IsolationIntegrity(Base):
    def test_tenant_isolation(self):
        pc.set_preference(self.c, self.op, self.sid, self.c1, "PREFERRED")
        other = {"id": 99, "role": "ops", "perms": {"*"}, "tenant_id": 999999}
        self.assertEqual(pc.list_preferences(self.c, other, self.sid), [])

    def test_integrity_clean(self):
        pc.set_preference(self.c, self.op, self.sid, self.c1, "PREFERRED")
        pc.reserve_capacity(self.c, self.op, self.sid, self.c2, "truck_6w", 2)
        self.assertTrue(pc.run_integrity(self.c, self.op)["ok"])


if __name__ == "__main__":
    unittest.main()
