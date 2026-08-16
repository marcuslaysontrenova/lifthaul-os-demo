"""Driver / Vehicle Availability — operational-readiness overlay over the existing vehicle/driver/trip
domains, plus the consolidated Carrier Operations dashboard and governed reassignment closure.

Proves: effective availability is COMPUTED (composes canonical status + block + active trip + declared),
never a second source of truth; declared status upsert (one current per resource); scheduled blocks;
availability board + counts; the carrier dashboard KPIs (company/KYB/LTFRB/eligibility + vehicle & driver
availability); governed reassignment closure (setting an on-assignment resource UNAVAILABLE surfaces the
impacted work + a reassignment hint but never auto-reassigns); RBAC; tenant isolation; integrity.
"""
import unittest

import db
import core
import admin_platform as ap
import marketplace_onboarding as mo
import availability as av
import carrier_portal as cp


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.op = self._a(10)
        self.cid = self._carrier()
        self.v1 = self._vehicle("V-1"); self.v2 = self._vehicle("V-2")
        self.d1 = self._driver("Juan"); self.d2 = self._driver("Pedro")

    def _a(self, id, perms=("*",)):
        return {"id": id, "role": "ops", "perms": set(perms), "tenant_id": self.rgo}

    def _carrier(self, reg="C1"):
        cid = mo.create_carrier_application(self.c, self.op, "FLEET_OPERATOR", "ABC", registration_type="SEC",
                                            registration_number=reg, operating_address="M")
        mo.submit_carrier(self.c, self.op, cid); mo.verify_carrier(self.c, self._a(11), cid)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION", "AUTHORITY_TO_OPERATE", "INSURANCE"):
            d = mo.upload_document(self.c, self.op, dt, "CARRIER", cid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self._a(11), d)
        mo.activate_carrier(self.c, self._a(12), cid); return cid

    def _vehicle(self, plate):
        v = mo.register_vehicle(self.c, self.op, self.cid, "truck_6w", plate); mo.verify_vehicle(self.c, self._a(11), v)
        for dt in ("VEHICLE_REGISTRATION", "INSURANCE"):
            d = mo.upload_document(self.c, self.op, dt, "VEHICLE", v, expiry_date="2027-01-01")
            mo.verify_document(self.c, self._a(11), d)
        mo.activate_vehicle(self.c, self._a(12), v); return v

    def _driver(self, name):
        d = mo.register_driver(self.c, self.op, self.cid, name, licence_expiry="2027-01-01", authorized_categories=["truck_6w"])
        mo.verify_driver(self.c, self._a(11), d); mo.activate_driver(self.c, self._a(12), d); return d

    def _principal(self, uid):
        cp.bind_principal(self.c, self.op, uid, self.cid)
        return {"id": uid, "role": "carrier_principal", "perms": set(core.PERMISSIONS["carrier_principal"]),
                "tenant_id": self.rgo}


# --------------------------------------------------------------------------- #
class Compute(Base):
    def test_default_available(self):
        st = av.compute_status(self.c, "VEHICLE", self.v1)
        self.assertEqual(st["effective"], "AVAILABLE")
        self.assertTrue(st["available"])

    def test_declared_off_duty(self):
        av.set_availability(self.c, self.op, "DRIVER", self.d1, "OFF_DUTY", reason="rest")
        self.assertEqual(av.compute_status(self.c, "DRIVER", self.d1)["effective"], "OFF_DUTY")

    def test_canonical_maintenance_wins(self):
        mo.set_vehicle_status(self.c, self.op, self.v2, "MAINTENANCE")
        # even if declared AVAILABLE, canonical maintenance is not available
        av.set_availability(self.c, self.op, "VEHICLE", self.v2, "AVAILABLE")
        st = av.compute_status(self.c, "VEHICLE", self.v2)
        self.assertEqual(st["effective"], "MAINTENANCE")
        self.assertFalse(st["available"])

    def test_block_makes_unavailable(self):
        av.add_block(self.c, self.op, "VEHICLE", self.v1, "LEAVE", "2020-01-01", "2030-01-01")
        self.assertEqual(av.compute_status(self.c, "VEHICLE", self.v1)["effective"], "BLOCKED")

    def test_on_active_trip(self):
        self.c.execute("INSERT INTO mkt_trips(tenant_id,assignment_id,vehicle_id,driver_id,status) "
                       "VALUES(?,?,?,?, 'IN_TRANSIT')", (self.rgo, 1, self.v1, self.d1))
        self.c.commit()
        self.assertEqual(av.compute_status(self.c, "VEHICLE", self.v1)["effective"], "ON_TRIP")


# --------------------------------------------------------------------------- #
class Declared(Base):
    def test_upsert_one_current_per_resource(self):
        av.set_availability(self.c, self.op, "DRIVER", self.d1, "UNAVAILABLE")
        av.set_availability(self.c, self.op, "DRIVER", self.d1, "AVAILABLE")
        n = self.c.execute("SELECT COUNT(*) c FROM resource_availability WHERE resource_type='DRIVER' AND resource_id=?",
                           (self.d1,)).fetchone()["c"]
        self.assertEqual(n, 1)

    def test_invalid_status(self):
        with self.assertRaises(core.ValidationError):
            av.set_availability(self.c, self.op, "DRIVER", self.d1, "SLEEPING")

    def test_rbac(self):
        weak = self._a(20, perms=("marketplace.availability.view",))
        with self.assertRaises(core.ForbiddenError):
            av.set_availability(self.c, weak, "DRIVER", self.d1, "OFF_DUTY")

    def test_clear_block_restores(self):
        b = av.add_block(self.c, self.op, "VEHICLE", self.v1, "LEAVE", "2020-01-01", "2030-01-01")["block_id"]
        self.assertEqual(av.compute_status(self.c, "VEHICLE", self.v1)["effective"], "BLOCKED")
        av.clear_block(self.c, self.op, b)
        self.assertEqual(av.compute_status(self.c, "VEHICLE", self.v1)["effective"], "AVAILABLE")


# --------------------------------------------------------------------------- #
class BoardAndDashboard(Base):
    def test_board_counts(self):
        mo.set_vehicle_status(self.c, self.op, self.v2, "MAINTENANCE")
        av.set_availability(self.c, self.op, "DRIVER", self.d1, "OFF_DUTY")
        bd = av.availability_board(self.c, self.op, self.cid)
        self.assertEqual(bd["vehicles"]["available"], 1)     # v1 available, v2 maintenance
        self.assertEqual(bd["drivers"]["available"], 1)      # d2 available, d1 off duty

    def test_carrier_dashboard_kpis(self):
        mo.set_vehicle_status(self.c, self.op, self.v2, "MAINTENANCE")
        av.set_availability(self.c, self.op, "DRIVER", self.d1, "OFF_DUTY")
        p = self._principal(90)
        d = cp.dashboard(self.c, p)
        self.assertEqual(d["vehicles"]["total"], 2)
        self.assertEqual(d["vehicles"]["on_hold"], 1)        # v2 maintenance
        self.assertEqual(d["drivers"]["total"], 2)
        self.assertEqual(d["drivers"]["unavailable"], 1)     # d1 off duty
        for key in ("kyb_status", "ltfrb_cpc_valid", "marketplace_status", "company_status"):
            self.assertIn(key, d)


# --------------------------------------------------------------------------- #
class ReassignmentClosure(Base):
    def test_unavailable_on_active_work_surfaces_hint(self):
        # an active assignment using the driver (direct insert; the full flow is exercised elsewhere)
        self.c.execute("INSERT INTO mkt_assignments(tenant_id,booking_id,carrier_id,vehicle_id,driver_id,status) "
                       "VALUES(?,?,?,?,?, 'CONFIRMED')", (self.rgo, 1, self.cid, self.v1, self.d1))
        self.c.commit()
        r = av.set_availability(self.c, self.op, "DRIVER", self.d1, "UNAVAILABLE", reason="sick")
        self.assertIn("impacted_active_work", r)
        self.assertEqual(r["reassignment_hint"], "DRIVER_UNAVAILABLE")   # a HINT — never auto-reassigns

    def test_no_hint_when_idle(self):
        r = av.set_availability(self.c, self.op, "DRIVER", self.d1, "OFF_DUTY")
        self.assertNotIn("impacted_active_work", r)


# --------------------------------------------------------------------------- #
class PortalAndIsolation(Base):
    def test_portal_own_resource_only(self):
        other = self._carrier("C2")
        ov = mo.register_vehicle(self.c, self.op, other, "truck_6w", "OTH-1")
        p = self._principal(90)
        with self.assertRaises(core.ForbiddenError):
            cp.set_availability(self.c, p, "VEHICLE", ov, "UNAVAILABLE")

    def test_tenant_isolation(self):
        other = {"id": 99, "role": "ops", "perms": {"*"}, "tenant_id": 999999}
        with self.assertRaises(core.NotFoundError):
            av.set_availability(self.c, other, "VEHICLE", self.v1, "UNAVAILABLE")

    def test_integrity(self):
        av.set_availability(self.c, self.op, "DRIVER", self.d1, "OFF_DUTY")
        self.assertTrue(av.run_integrity(self.c, self.op)["ok"])


if __name__ == "__main__":
    unittest.main()
