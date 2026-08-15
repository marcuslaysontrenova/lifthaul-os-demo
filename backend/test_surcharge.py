"""Dynamic Surcharge Engine — governed effective-dated surcharge rules over the existing pricing.

Proves: versioned/effective-dated rules; PERCENT/FLAT/PER_KM math; applies_when matching (zone /
category / weekday / date range / distance band); stackable vs non-stackable selection; CONFIG-GATED
integration (price_booking is UNCHANGED when disabled, surcharges apply transparently when enabled);
immutable application audit; operator preview; RBAC; tenant isolation; integrity.
"""
import unittest

import db
import core
import admin_platform as ap
import marketplace as mkt
import marketplace_onboarding as mo
import marketplace_matching as mm
import surcharge as sc


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.op = self._a(10)

    def _a(self, id, perms=("*",)):
        return {"id": id, "role": "ops", "perms": set(perms), "tenant_id": self.rgo}


# --------------------------------------------------------------------------- #
class Rules(Base):
    def test_versioning(self):
        r1 = sc.set_rule(self.c, self.op, "FUEL", "Fuel", "FUEL", "PERCENT", 10)
        r2 = sc.set_rule(self.c, self.op, "FUEL", "Fuel", "FUEL", "PERCENT", 12)
        self.assertEqual(r2["version"], r1["version"] + 1)
        active = self.c.execute("SELECT COUNT(*) c FROM surcharge_rules WHERE code='FUEL' AND active=1").fetchone()["c"]
        self.assertEqual(active, 1)

    def test_invalid_type_and_basis(self):
        with self.assertRaises(core.ValidationError):
            sc.set_rule(self.c, self.op, "X", "x", "BOGUS", "PERCENT", 5)
        with self.assertRaises(core.ValidationError):
            sc.set_rule(self.c, self.op, "X", "x", "FUEL", "BOGUS", 5)

    def test_negative_rate_rejected(self):
        with self.assertRaises(core.ValidationError):
            sc.set_rule(self.c, self.op, "X", "x", "FUEL", "PERCENT", -1)

    def test_rbac(self):
        weak = self._a(20, perms=("marketplace.surcharge.view",))
        with self.assertRaises(core.ForbiddenError):
            sc.set_rule(self.c, weak, "FUEL", "f", "FUEL", "PERCENT", 10)


# --------------------------------------------------------------------------- #
class Evaluate(Base):
    def test_percent_flat_perkm(self):
        sc.set_rule(self.c, self.op, "P", "p", "FUEL", "PERCENT", 10, priority=3)
        sc.set_rule(self.c, self.op, "F", "f", "SPECIAL", "FLAT", 250, priority=2)
        sc.set_rule(self.c, self.op, "K", "k", "CONGESTION", "PER_KM", 5, priority=1)
        ev = sc.evaluate(self.c, {}, 1000, distance_km=20, tenant_id=self.rgo)
        amounts = {s["code"]: s["amount"] for s in ev["surcharges"]}
        self.assertEqual(amounts["P"], 100)     # 10% of 1000
        self.assertEqual(amounts["F"], 250)
        self.assertEqual(amounts["K"], 100)     # 5 * 20
        self.assertEqual(ev["total"], 450)

    def test_zone_condition(self):
        sc.set_rule(self.c, self.op, "MM", "m", "ZONE", "FLAT", 500, applies_when={"origin_zones": ["METRO_MANILA"]})
        self.assertEqual(sc.evaluate(self.c, {"origin_zone": "METRO_MANILA"}, 1000, tenant_id=self.rgo)["total"], 500)
        self.assertEqual(sc.evaluate(self.c, {"origin_zone": "CEBU"}, 1000, tenant_id=self.rgo)["total"], 0)

    def test_weekday_and_date_window(self):
        sc.set_rule(self.c, self.op, "SUN", "s", "PEAK", "FLAT", 300, applies_when={"weekdays": [6]})   # Sunday
        self.assertEqual(sc.evaluate(self.c, {}, 1000, tenant_id=self.rgo, as_of="2026-08-16")["total"], 300)  # a Sunday
        self.assertEqual(sc.evaluate(self.c, {}, 1000, tenant_id=self.rgo, as_of="2026-08-17")["total"], 0)    # Monday
        sc.set_rule(self.c, self.op, "HOL", "h", "HOLIDAY", "FLAT", 400, applies_when={"date_from": "2026-12-24", "date_to": "2026-12-26"})
        # 2026-12-25 is a Friday -> SUN(weekday 6) not matched; only HOL 400 applies (date window)
        ev = sc.evaluate(self.c, {}, 1000, tenant_id=self.rgo, as_of="2026-12-25")
        self.assertEqual({s["code"] for s in ev["surcharges"]}, {"HOL"})
        self.assertEqual(ev["total"], 400)

    def test_distance_band(self):
        sc.set_rule(self.c, self.op, "LONG", "l", "SPECIAL", "FLAT", 800, applies_when={"min_distance_km": 100})
        self.assertEqual(sc.evaluate(self.c, {}, 1000, distance_km=150, tenant_id=self.rgo)["total"], 800)
        self.assertEqual(sc.evaluate(self.c, {}, 1000, distance_km=50, tenant_id=self.rgo)["total"], 0)

    def test_non_stackable_takes_highest_priority(self):
        sc.set_rule(self.c, self.op, "A", "a", "DEMAND", "FLAT", 100, priority=1, stackable=False)
        sc.set_rule(self.c, self.op, "B", "b", "DEMAND", "FLAT", 300, priority=9, stackable=False)
        sc.set_rule(self.c, self.op, "C", "c", "FUEL", "FLAT", 50, priority=2, stackable=True)
        ev = sc.evaluate(self.c, {}, 1000, tenant_id=self.rgo)
        codes = {s["code"] for s in ev["surcharges"]}
        self.assertIn("B", codes)      # highest-priority non-stackable
        self.assertNotIn("A", codes)   # excluded
        self.assertIn("C", codes)      # stackable still applies
        self.assertEqual(ev["total"], 350)


# --------------------------------------------------------------------------- #
class Integration(Base):
    def setUp(self):
        super().setUp()
        self.sid = self._shipper()
        self._lane("MM-CAV")

    def _shipper(self):
        s = mo.create_shipper_application(self.c, self.op, "CORPORATION", "Acme", registration_type="SEC",
                                          registration_number="S1", registered_address="Mkt",
                                          contract_accepted=1, privacy_accepted=1)
        mo.submit_shipper(self.c, self.op, s); mo.verify_shipper(self.c, self._a(11), s)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION"):
            d = mo.upload_document(self.c, self.op, dt, "SHIPPER", s, expiry_date="2027-01-01")
            mo.verify_document(self.c, self._a(11), d)
        mo.activate_shipper(self.c, self._a(12), s); return s

    def _lane(self, code):
        lane = [l for l in mkt.list_lanes(self.c) if l["code"] == code][0]
        mkt.assess_lane(self.c, self.op, lane["id"], verified_carriers=5, backup_capacity=1,
                        price_model_validated=1, ops_support=1, payment_capable=1, dispute_process=1, monitoring=1)
        mkt.activate_lane(self.c, self._a(11), lane["id"], target="ACTIVE")

    def _price(self):
        bk = mm.create_booking(self.c, self.op, self.sid, "general", "METRO_MANILA", "CAVITE",
                               weight_kg=5000, volume_cbm=10, pickup_address="A", delivery_address="B")
        mm.validate_booking(self.c, self._a(11), bk); mm.select_pricing_mode(self.c, self._a(11), bk)
        return bk, mm.price_booking(self.c, self._a(11), bk)

    def test_disabled_by_default_pricing_unchanged(self):
        sc.set_rule(self.c, self.op, "FUEL", "f", "FUEL", "PERCENT", 10)   # rule exists but engine OFF
        _, p = self._price()
        self.assertFalse(any(x["component"].startswith("surcharge_") for x in p["components"]))

    def test_enabled_applies_transparently(self):
        sc.set_rule(self.c, self.op, "FUEL", "f", "FUEL", "PERCENT", 10, priority=5)
        sc.set_rule(self.c, self.op, "MM", "m", "ZONE", "FLAT", 500, applies_when={"origin_zones": ["METRO_MANILA"]})
        ap.set_config(self.c, "platform", "", "marketplace.surcharge_enabled", "true", actor=self.op)
        bk, p = self._price()
        codes = [x["component"] for x in p["components"] if x["component"].startswith("surcharge_")]
        self.assertIn("surcharge_FUEL", codes)
        self.assertIn("surcharge_MM", codes)
        apps = sc.applications_for(self.c, self.op, bk)
        self.assertEqual({a["rule_code"] for a in apps}, {"FUEL", "MM"})   # immutable audit persisted


# --------------------------------------------------------------------------- #
class Misc(Base):
    def test_quote_preview_no_persist(self):
        sc.set_rule(self.c, self.op, "FUEL", "f", "FUEL", "PERCENT", 10)
        q = sc.quote(self.c, self.op, 2000, origin_zone="METRO_MANILA")
        self.assertEqual(q["total"], 200)
        self.assertEqual(self.c.execute("SELECT COUNT(*) c FROM surcharge_applications").fetchone()["c"], 0)

    def test_tenant_isolation(self):
        sc.set_rule(self.c, self.op, "FUEL", "f", "FUEL", "PERCENT", 10)
        other = {"id": 99, "role": "ops", "perms": {"*"}, "tenant_id": 999999}
        self.assertEqual(sc.list_rules(self.c, other), [])

    def test_integrity_clean(self):
        sc.set_rule(self.c, self.op, "FUEL", "f", "FUEL", "PERCENT", 10)
        self.assertTrue(sc.run_integrity(self.c, self.op)["ok"])


if __name__ == "__main__":
    unittest.main()
