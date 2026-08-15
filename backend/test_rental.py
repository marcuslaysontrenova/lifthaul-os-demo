"""Hourly / Daily / Project Rental — duration-and-usage revenue over the existing spine.

Proves: governed effective-dated + versioned rental rates; deterministic billing; minimum-billing
enforcement; overtime + standby + mobilization math; the overtime-approval gate; honest usage capture
(no negatives, no fabrication); one-invoice-per-agreement; Protected Payment reuse (MOCK, funds never
fabricated); lifecycle guards (confirm needs carrier+vehicle, activate re-runs the driver/vehicle gate);
RBAC; tenant isolation.
"""
import unittest

import db
import core
import admin_platform as ap
import marketplace_onboarding as mo
import rental as rt


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.op = self._a(10)
        self.cid = self._carrier()
        self.vid = self._vehicle(self.cid)
        self.did = self._driver(self.cid)

    def _a(self, id, perms=("*",)):
        return {"id": id, "role": "ops", "perms": set(perms), "tenant_id": self.rgo}

    def _carrier(self, reg="C1"):
        cid = mo.create_carrier_application(self.c, self.op, "FLEET_OPERATOR", "Cranes",
                                            registration_type="SEC", registration_number=reg, operating_address="M")
        mo.submit_carrier(self.c, self.op, cid); mo.verify_carrier(self.c, self._a(11), cid)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION", "AUTHORITY_TO_OPERATE", "INSURANCE"):
            d = mo.upload_document(self.c, self.op, dt, "CARRIER", cid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self._a(11), d)
        mo.activate_carrier(self.c, self._a(12), cid); return cid

    def _vehicle(self, cid, plate="PLT-1", cat="truck_6w"):
        v = mo.register_vehicle(self.c, self.op, cid, cat, plate); mo.verify_vehicle(self.c, self._a(11), v)
        for dt in ("VEHICLE_REGISTRATION", "INSURANCE"):
            d = mo.upload_document(self.c, self.op, dt, "VEHICLE", v, expiry_date="2027-01-01")
            mo.verify_document(self.c, self._a(11), d)
        mo.activate_vehicle(self.c, self._a(12), v); return v

    def _driver(self, cid, cat="truck_6w"):
        d = mo.register_driver(self.c, self.op, cid, "Juan", licence_expiry="2027-01-01", authorized_categories=[cat])
        mo.verify_driver(self.c, self._a(11), d); mo.activate_driver(self.c, self._a(12), d); return d

    def _agreement(self, unit="DAILY", qty=3, **kw):
        q = rt.quote_rental(self.c, self.op, "truck_6w", unit, qty, carrier_id=self.cid,
                            vehicle_id=self.vid, driver_id=self.did, customer_id=1, **kw)
        return q["agreement_id"]


# --------------------------------------------------------------------------- #
class Rates(Base):
    def test_seed_present(self):
        self.assertGreaterEqual(len(rt.list_rental_rates(self.c, self.op)), 1)

    def test_versioning_supersedes(self):
        r1 = rt.set_rental_rate(self.c, self.op, "truck_6w", "DAILY", 9000)
        r2 = rt.set_rental_rate(self.c, self.op, "truck_6w", "DAILY", 9500)
        self.assertEqual(r2["version"], r1["version"] + 1)
        active = self.c.execute("SELECT COUNT(*) c FROM rental_rate_cards WHERE vehicle_category='truck_6w' "
                                "AND rate_unit='DAILY' AND active=1").fetchone()["c"]
        self.assertEqual(active, 1)

    def test_rate_manage_rbac(self):
        weak = self._a(20, perms=("marketplace.rental.view",))
        with self.assertRaises(core.ForbiddenError):
            rt.set_rental_rate(self.c, weak, "truck_6w", "DAILY", 9000)

    def test_invalid_unit(self):
        with self.assertRaises(core.ValidationError):
            rt.set_rental_rate(self.c, self.op, "truck_6w", "PER_KM", 9000)


# --------------------------------------------------------------------------- #
class Billing(Base):
    def setUp(self):
        super().setUp()
        rt.set_rental_rate(self.c, self.op, "truck_6w", "DAILY", 9000, min_billing_qty=2,
                           overtime_multiplier=1.5, standby_rate=1000, mobilization_fee=3000)

    def test_min_billing_enforced(self):
        aid = self._agreement(qty=1)
        rt.confirm_rental(self.c, self.op, aid); rt.activate_rental(self.c, self.op, aid)
        rt.record_usage(self.c, self.op, aid, 1)   # used 1 day, min is 2
        f = rt.finalize_rental(self.c, self.op, aid)
        self.assertEqual(f["billed_quantity"], 2)
        self.assertEqual(f["base_amount"], 18000)   # 2 * 9000

    def test_full_billing_math(self):
        aid = self._agreement(qty=3)
        rt.confirm_rental(self.c, self.op, aid); rt.activate_rental(self.c, self.op, aid)
        rt.record_usage(self.c, self.op, aid, 4, standby_quantity=1)
        f = rt.finalize_rental(self.c, self.op, aid)
        self.assertEqual(f["base_amount"], 36000)         # 4 * 9000
        self.assertEqual(f["standby_amount"], 1000)       # 1 * 1000
        self.assertEqual(f["mobilization_amount"], 3000)
        self.assertEqual(f["subtotal"], 40000)
        self.assertEqual(f["platform_fee"], 4000)         # 10%
        self.assertEqual(f["carrier_payout"], 36000)

    def test_protected_payment_reused(self):
        aid = self._agreement(qty=2)
        rt.confirm_rental(self.c, self.op, aid); rt.activate_rental(self.c, self.op, aid)
        rt.record_usage(self.c, self.op, aid, 2)
        f = rt.finalize_rental(self.c, self.op, aid)
        self.assertIsNotNone(f["protected_tx_id"])
        tx = self.c.execute("SELECT provider,contract_amount FROM mkt_protected_tx WHERE id=?",
                            (f["protected_tx_id"],)).fetchone()
        self.assertEqual(tx["provider"], "MOCK")          # never a live rail
        self.assertEqual(tx["contract_amount"], f["total"])

    def test_one_invoice_per_agreement(self):
        aid = self._agreement(qty=2)
        rt.confirm_rental(self.c, self.op, aid); rt.activate_rental(self.c, self.op, aid)
        rt.record_usage(self.c, self.op, aid, 2)
        rt.finalize_rental(self.c, self.op, aid)
        with self.assertRaises(core.ConflictError):
            rt.finalize_rental(self.c, self.op, aid)


# --------------------------------------------------------------------------- #
class Overtime(Base):
    def test_overtime_gate_blocks_large_charge(self):
        rt.set_rental_rate(self.c, self.op, "truck_6w", "HOURLY", 4500, overtime_multiplier=1.5)
        aid = self._agreement(unit="HOURLY", qty=8)
        rt.confirm_rental(self.c, self.op, aid); rt.activate_rental(self.c, self.op, aid)
        rt.record_usage(self.c, self.op, aid, 8, overtime_quantity=20)   # 20*4500*1.5 = 135000 > 50000
        weak = self._a(20, perms=("marketplace.rental.view", "marketplace.rental.manage",
                                  "marketplace.rental.usage.record", "marketplace.rental.billing.finalize"))
        with self.assertRaises(core.ForbiddenError):
            rt.finalize_rental(self.c, weak, aid)
        # with the approval perm it succeeds
        f = rt.finalize_rental(self.c, self.op, aid)
        self.assertEqual(f["overtime_amount"], 135000)


# --------------------------------------------------------------------------- #
class Usage(Base):
    def test_negative_usage_rejected(self):
        aid = self._agreement(qty=2)
        rt.confirm_rental(self.c, self.op, aid); rt.activate_rental(self.c, self.op, aid)
        with self.assertRaises(core.ValidationError):
            rt.record_usage(self.c, self.op, aid, -1)

    def test_meter_order_validated(self):
        aid = self._agreement(qty=2)
        rt.confirm_rental(self.c, self.op, aid); rt.activate_rental(self.c, self.op, aid)
        with self.assertRaises(core.ValidationError):
            rt.record_usage(self.c, self.op, aid, 2, meter_start=100, meter_end=50)

    def test_usage_only_when_active(self):
        aid = self._agreement(qty=2)   # QUOTED
        with self.assertRaises(core.ConflictError):
            rt.record_usage(self.c, self.op, aid, 1)


# --------------------------------------------------------------------------- #
class Lifecycle(Base):
    def test_confirm_requires_carrier_and_vehicle(self):
        q = rt.quote_rental(self.c, self.op, "truck_6w", "DAILY", 2)   # no carrier/vehicle
        with self.assertRaises(core.ValidationError):
            rt.confirm_rental(self.c, self.op, q["agreement_id"])

    def test_activate_reruns_eligibility_gate(self):
        aid = self._agreement(qty=2)
        rt.confirm_rental(self.c, self.op, aid)
        mo.set_driver_status(self.c, self.op, self.did, "SUSPENDED")   # now ineligible
        with self.assertRaises(core.ConflictError):
            rt.activate_rental(self.c, self.op, aid)

    def test_cancel(self):
        aid = self._agreement(qty=2)
        self.assertEqual(rt.cancel_rental(self.c, self.op, aid, "customer withdrew")["status"], "CANCELLED")

    def test_tenant_isolation(self):
        aid = self._agreement(qty=2)
        other = {"id": 99, "role": "ops", "perms": {"*"}, "tenant_id": 999999}
        with self.assertRaises(core.NotFoundError):
            rt.get_agreement(self.c, other, aid)


# --------------------------------------------------------------------------- #
class Integrity(Base):
    def test_integrity_clean(self):
        aid = self._agreement(qty=2)
        rt.confirm_rental(self.c, self.op, aid); rt.activate_rental(self.c, self.op, aid)
        rt.record_usage(self.c, self.op, aid, 2)
        rt.finalize_rental(self.c, self.op, aid)
        self.assertTrue(rt.run_integrity(self.c, self.op)["ok"])


if __name__ == "__main__":
    unittest.main()
