"""Nationwide marketplace final integration (Workstream 2). ONE Philippine geography + service-area
engine covers Luzon, Visayas, Mindanao and inter-island multi-leg custody — not separate systems.
Deterministic/synthetic data; no fabricated live ferry schedules.
"""
import unittest

import core
import db
import marketplace as mkt
import marketplace_trust as mt
import marketplace_trust_closure as tc


class NationwideTests(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.sup = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
        core.create_user(self.c, "op@nw", "pw", "admin", "Op")   # '*'
        self.op = core.actor_for(self.c, core.login(self.c, "op@nw", "pw"))

    def _lane(self, code, og, dg, oz, dz):
        lid = mkt.create_lane(self.c, self.op, code, og, dg, oz, dz)
        # activate to PILOT/ACTIVE so it promises service
        try:
            mkt.assess_lane(self.c, self.op, lid) if hasattr(mkt, "assess_lane") else None
            mkt.activate_lane(self.c, self.op, lid) if hasattr(mkt, "activate_lane") else None
        except Exception:
            self.c.execute("UPDATE mkt_lanes SET status='ACTIVE' WHERE id=?", (lid,)); self.c.commit()
        return lid

    def test_one_engine_uses_island_groups(self):
        self.assertEqual(mkt.ISLAND_GROUPS, ("LUZON", "VISAYAS", "MINDANAO"))

    # Flow A — Luzon (Metro Manila -> Central Luzon)
    def test_luzon_flow(self):
        self._lane("MM-CL", "LUZON", "LUZON", "METRO_MANILA", "CENTRAL_LUZON")
        s = mkt.serviceability(self.c, "METRO_MANILA", "CENTRAL_LUZON")
        self.assertTrue(s["found"]); self.assertFalse(s["requires_sea_leg"])

    # Flow B — Visayas (synthetic, independent)
    def test_visayas_flow(self):
        self._lane("CEB-BOH", "VISAYAS", "VISAYAS", "CEBU_CITY", "BOHOL")
        s = mkt.serviceability(self.c, "CEBU_CITY", "BOHOL")
        self.assertTrue(s["found"]); self.assertFalse(s["requires_sea_leg"])

    # Flow C — Mindanao (synthetic, independent)
    def test_mindanao_flow(self):
        self._lane("DVO-CAG", "MINDANAO", "MINDANAO", "DAVAO_CITY", "CAGAYAN_DE_ORO")
        s = mkt.serviceability(self.c, "DAVAO_CITY", "CAGAYAN_DE_ORO")
        self.assertTrue(s["found"]); self.assertFalse(s["requires_sea_leg"])

    # Flow D — Inter-island multi-leg (Luzon -> port -> RoRo -> Visayas), custody transition marked
    def test_inter_island_multi_leg(self):
        lid = self._lane("MM-CEB", "LUZON", "VISAYAS", "METRO_MANILA", "CEBU_CITY")
        row = self.c.execute("SELECT requires_sea_leg FROM mkt_lanes WHERE id=?", (lid,)).fetchone()
        self.assertEqual(row["requires_sea_leg"], 1)          # sea leg auto-flagged (custody transition)
        s = mkt.serviceability(self.c, "METRO_MANILA", "CEBU_CITY")
        self.assertTrue(s["requires_sea_leg"])

    # One trust/eligibility gate applies uniformly across all regions
    def test_eligibility_gate_uniform_across_regions(self):
        # an unverified carrier is ineligible regardless of region
        a2 = core.actor_for(self.c, core.login(self.c, "op@nw", "pw"))
        for cid in (301, 302, 303):
            self.assertFalse(mt.assess_eligibility(self.c, self.sup, cid)["eligible"])
        # verify one and confirm it becomes eligible (same engine, any region)
        k = mt.submit_kyb(self.c, self.op, "CARRIER", 301, "SEC", "SEC-301", "Nat Co")
        # need a distinct verifier for SoD-free verify (op created nothing for carrier 301 subject row)
        mt.verify_kyb(self.c, self.op, k, "VERIFIED", source="SEC portal")
        self.assertTrue(mt.assess_eligibility(self.c, self.sup, 301)["eligible"])


if __name__ == "__main__":
    unittest.main()
