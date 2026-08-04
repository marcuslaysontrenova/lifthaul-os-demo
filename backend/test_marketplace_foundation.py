"""LiftHaul OS — Nationwide Marketplace program, Increment 1 tests.

Proves the deterministic marketplace foundation and its two hard safety invariants:

  * cargo->vehicle eligibility is DETERMINISTIC and computed BEFORE any AI ranking
    (prohibited / payload / volume / refrigeration / oversized / hazmat / dimension gates);
  * a lane may PROMISE service only after passing every activation criterion under
    separation of duties — an inactive lane ACCEPTS INTEREST but PROMISES NOTHING;

plus governed taxonomy lifecycle + permission enforcement + zero drift on the existing
Phase 1-10 financial/operational surface.
"""
import unittest

import db
import core
import marketplace as m


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.a = {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": None}       # platform authority
        self.assessor = {"id": 7, "role": "ops", "perms": {"marketplace.lane.*"}, "tenant_id": None}
        self.approver = {"id": 8, "role": "ops", "perms": {"marketplace.lane.*"}, "tenant_id": None}

    def _actor(self, perms, id=9):
        return {"id": id, "role": "ops", "perms": set(perms), "tenant_id": None}


# --------------------------------------------------------------------------- #
# Taxonomy lifecycle + governance
# --------------------------------------------------------------------------- #
class TaxonomyTests(Base):
    def test_seed_populates_active_taxonomy(self):
        self.assertGreaterEqual(len(m.list_vehicle_categories(self.c, active_only=True)), 15)
        self.assertGreaterEqual(len(m.list_cargo_types(self.c, active_only=True)), 10)

    def test_vehicle_has_immutable_checksum(self):
        v = m.list_vehicle_categories(self.c)[0]
        self.assertTrue(v["checksum"])

    def test_duplicate_vehicle_code_rejected(self):
        with self.assertRaises(ValueError):
            m.create_vehicle_category(self.c, self.a, "truck_6w", "dup", "MEDIUM_HEAVY")

    def test_duplicate_cargo_code_rejected(self):
        with self.assertRaises(ValueError):
            m.create_cargo_type(self.c, self.a, "general", "dup", "GENERAL")

    def test_invalid_class_group_rejected(self):
        with self.assertRaises(ValueError):
            m.create_vehicle_category(self.c, self.a, "x", "x", "NOPE")

    def test_vehicle_manage_permission_enforced(self):
        with self.assertRaises(core.ForbiddenError):
            m.create_vehicle_category(self.c, self._actor(set()), "x", "x", "MEDIUM_HEAVY")

    def test_cargo_manage_permission_enforced(self):
        with self.assertRaises(core.ForbiddenError):
            m.create_cargo_type(self.c, self._actor(set()), "x", "x", "GENERAL")

    def test_status_lifecycle(self):
        vid = m.create_vehicle_category(self.c, self.a, "test_v", "Test", "LIGHT_COMMERCIAL",
                                        payload_kg=500, volume_cbm=2)
        self.assertEqual([v for v in m.list_vehicle_categories(self.c) if v["id"] == vid][0]["status"], "DRAFT")
        m.set_vehicle_status(self.c, self.a, vid, "ACTIVE")
        self.assertIn("test_v", [v["code"] for v in m.list_vehicle_categories(self.c, active_only=True)])


# --------------------------------------------------------------------------- #
# DETERMINISTIC cargo -> vehicle eligibility (the "before AI" invariant)
# --------------------------------------------------------------------------- #
class EligibilityTests(Base):
    def test_prohibited_cargo_yields_empty_pool(self):
        r = m.eligible_vehicles(self.c, "prohibited")
        self.assertEqual(r["eligible"], [])
        self.assertEqual(r["blocked"], "cargo_prohibited")

    def test_payload_gate(self):
        # 9 tonnes excludes every light vehicle; only heavy trucks qualify
        r = m.eligible_vehicles(self.c, "general", weight_kg=9000, volume_cbm=0)
        codes = [v["code"] for v in r["eligible"]]
        self.assertNotIn("elf_4w", codes)
        self.assertIn("truck_10w", codes)
        for v in r["eligible"]:
            self.assertGreaterEqual(v["payload_kg"], 9000)

    def test_volume_gate(self):
        r = m.eligible_vehicles(self.c, "general", weight_kg=100, volume_cbm=35)
        for v in r["eligible"]:
            self.assertGreaterEqual(v["volume_cbm"], 35)

    def test_refrigeration_required_for_chilled(self):
        r = m.eligible_vehicles(self.c, "perishable_chilled", weight_kg=500, volume_cbm=2)
        self.assertTrue(r["eligible"])
        for v in r["eligible"]:
            self.assertTrue(v["refrigerated"])

    def test_oversized_machinery_requires_special_handling(self):
        r = m.eligible_vehicles(self.c, "machinery", weight_kg=5000, volume_cbm=0)
        for v in r["eligible"]:
            self.assertTrue(v["lifting_capable"] or v["body_type"] in ("flatbed", "lowbed", "container_chassis"))
        self.assertTrue(r["eligible"])

    def test_hazardous_requires_hazmat_vehicle(self):
        # no seeded vehicle allows hazmat -> empty eligible pool, every rejection cites hazmat
        r = m.eligible_vehicles(self.c, "hazardous", weight_kg=100, volume_cbm=1)
        self.assertEqual(r["eligible"], [])
        self.assertTrue(all("hazmat_not_permitted" in x["reasons"] for x in r["rejected"]))

    def test_dimension_gate(self):
        # a 9m-long item cannot fit a small van opening but fits a 12-wheeler
        small = m.is_vehicle_eligible(self.c, "general", "small_van", weight_kg=100, volume_cbm=1,
                                      dims=(900, 100, 100))
        big = m.is_vehicle_eligible(self.c, "general", "truck_12w", weight_kg=100, volume_cbm=1,
                                    dims=(900, 100, 100))
        self.assertFalse(small["eligible"])
        self.assertIn("item_too_long", small["reasons"])
        self.assertTrue(big["eligible"])

    def test_inactive_vehicle_never_eligible(self):
        vid = m.create_vehicle_category(self.c, self.a, "draft_v", "Draft", "MEDIUM_HEAVY",
                                        payload_kg=99999, volume_cbm=999)
        # left in DRAFT -> even though huge capacity, never eligible
        r = m.eligible_vehicles(self.c, "general", weight_kg=100, volume_cbm=1)
        self.assertNotIn("draft_v", [v["code"] for v in r["eligible"]])

    def test_eligibility_is_deterministic(self):
        a = m.eligible_vehicles(self.c, "general", weight_kg=1000, volume_cbm=2)
        b = m.eligible_vehicles(self.c, "general", weight_kg=1000, volume_cbm=2)
        self.assertEqual([v["code"] for v in a["eligible"]], [v["code"] for v in b["eligible"]])

    def test_specific_vehicle_guard_matches_pool(self):
        pool = {v["code"] for v in m.eligible_vehicles(self.c, "general", weight_kg=1000, volume_cbm=2)["eligible"]}
        for code in ("l300_van", "elf_4w", "small_van"):
            guard = m.is_vehicle_eligible(self.c, "general", code, weight_kg=1000, volume_cbm=2)
            self.assertEqual(guard["eligible"], code in pool)


# --------------------------------------------------------------------------- #
# Lane coverage + activation gate + serviceability promise boundary
# --------------------------------------------------------------------------- #
class LaneTests(Base):
    def _ready_lane(self):
        lid = m.create_lane(self.c, self.a, "T1", "LUZON", "LUZON", "ZONE_A", "ZONE_B")
        m.assess_lane(self.c, self.assessor, lid, verified_carriers=5, backup_capacity=1,
                      price_model_validated=1, ops_support=1, payment_capable=1,
                      dispute_process=1, monitoring=1)
        return lid

    def test_seeded_pilot_lanes_promise_nothing(self):
        for l in m.list_lanes(self.c):
            s = m.serviceability(self.c, l["origin_zone"], l["dest_zone"])
            self.assertFalse(s["promises_service"], f"{l['code']} must not promise service while {l['status']}")

    def test_unknown_lane_accepts_interest_but_never_promises(self):
        s = m.serviceability(self.c, "METRO_MANILA", "PALAWAN")
        self.assertFalse(s["found"])
        self.assertTrue(s["accepts_interest"])
        self.assertFalse(s["promises_service"])

    def test_inter_island_lane_flags_sea_leg(self):
        lid = m.create_lane(self.c, self.a, "L2V", "LUZON", "VISAYAS", "MANILA_PORT", "CEBU_PORT")
        s = m.serviceability(self.c, "MANILA_PORT", "CEBU_PORT")
        self.assertTrue(s["requires_sea_leg"])

    def test_activation_blocked_until_all_criteria_met(self):
        lid = m.create_lane(self.c, self.a, "T2", "LUZON", "LUZON", "Z1", "Z2")
        m.assess_lane(self.c, self.assessor, lid, verified_carriers=1)  # below min, others false
        st = m.lane_activation_status(self.c, lid)
        self.assertFalse(st["ready"])
        self.assertIn("verified_carriers", st["unmet"])
        self.assertIn("payment_capable", st["unmet"])
        with self.assertRaises(ValueError):
            m.activate_lane(self.c, self.approver, lid)

    def test_activation_succeeds_when_ready_under_sod(self):
        lid = self._ready_lane()
        self.assertTrue(m.lane_activation_status(self.c, lid)["ready"])
        m.activate_lane(self.c, self.approver, lid, target="ACTIVE")
        s = m.serviceability(self.c, "ZONE_A", "ZONE_B")
        self.assertEqual(s["status"], "ACTIVE")
        self.assertTrue(s["promises_service"])

    def test_separation_of_duties_blocks_self_approval(self):
        lid = self._ready_lane()   # assessed by self.assessor (id 7)
        with self.assertRaises(PermissionError):
            m.activate_lane(self.c, self.assessor, lid)   # same actor cannot approve

    def test_activation_requires_permission(self):
        lid = self._ready_lane()
        with self.assertRaises(core.ForbiddenError):
            m.activate_lane(self.c, self._actor(set(), id=99), lid)

    def test_pilot_target_promises_service(self):
        lid = self._ready_lane()
        m.activate_lane(self.c, self.approver, lid, target="PILOT")
        self.assertTrue(m.serviceability(self.c, "ZONE_A", "ZONE_B")["promises_service"])

    def test_suspend_revokes_promise(self):
        lid = self._ready_lane()
        m.activate_lane(self.c, self.approver, lid, target="ACTIVE")
        m.set_lane_status(self.c, self.a, lid, "SUSPENDED")
        self.assertFalse(m.serviceability(self.c, "ZONE_A", "ZONE_B")["promises_service"])

    def test_set_lane_status_cannot_jump_to_active(self):
        lid = m.create_lane(self.c, self.a, "T3", "LUZON", "LUZON", "Z3", "Z4")
        with self.assertRaises(ValueError):
            m.set_lane_status(self.c, self.a, lid, "ACTIVE")

    def test_lane_manage_permission_enforced(self):
        with self.assertRaises(core.ForbiddenError):
            m.create_lane(self.c, self._actor(set()), "X", "LUZON", "LUZON", "a", "b")


# --------------------------------------------------------------------------- #
# Zero drift: the marketplace foundation must not touch Phase 1-10 financials
# --------------------------------------------------------------------------- #
class ZeroDriftTests(Base):
    def test_marketplace_does_not_change_freight_financials(self):
        import admin_platform as ap
        rgo = ap.get_tenant(self.c, "RGO")["id"]
        a = {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": rgo}
        cid = core.create_customer(self.c, a, "Mkt Fin Co")
        bid = core.create_booking(self.c, a, cid, "CRANE_RENTAL", "x", 1)
        core.review_booking(self.c, a, bid); core.ready_for_quotation(self.c, a, bid)
        qid = core.create_quotation(self.c, a, bid,
                                    [{"kind": "crane", "description": "x", "qty": 2, "days": 1, "rate": 300000}])
        row = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        # canonical freight math (tax 12% => 72000, total 672000) — unchanged by the marketplace layer
        self.assertEqual((row["tax"], row["total"]), (72000, 672000))

    def test_schema_version_bumped(self):
        self.assertGreaterEqual(db.SCHEMA_VERSION, 16)


if __name__ == "__main__":
    unittest.main()
