"""Driver Mobile App — a driver-facing surface over the EXISTING trip / POD / OTP domains.

Proves: identity-scoped driver binding (a driver only ever sees/acts on its OWN trips); delegated trip
execution (advance / gps / POD / accept) over the canonical trip functions; two hard safety rules — a
driver can never self-verify compliance (role holds no operational marketplace.* perm, /admin/* is 403)
and a driver never issues or sees a delivery OTP (can only VERIFY a code the recipient gives); the
elevation allow-list can never include an OTP issue/override perm; RBAC; tenant isolation; integrity.
"""
import unittest

import db
import core
import admin_platform as ap
import marketplace as mkt
import marketplace_onboarding as mo
import marketplace_matching as mm
import marketplace_trips as tp
import delivery_verification as dv
import driver_app as da


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.op = self._a(10); self.cu = self._a(20)
        self.cid = self._carrier()
        self.vid = self._vehicle(self.cid)
        self.drv = self._driver(self.cid, "Juan")
        self.sid = self._shipper()
        self._lane("MM-CAV")
        self.tid, self.bk = self._trip()

    def _a(self, id, perms=("*",)):
        return {"id": id, "role": "ops", "perms": set(perms), "tenant_id": self.rgo}

    def _principal(self, user_id, driver_id):
        da.bind_principal(self.c, self.op, user_id, driver_id)
        return {"id": user_id, "role": "driver_principal", "perms": set(core.PERMISSIONS["driver_principal"]),
                "tenant_id": self.rgo}

    def _shipper(self):
        s = mo.create_shipper_application(self.c, self.op, "CORPORATION", "Acme", registration_type="SEC",
                                          registration_number="S1", registered_address="Mkt",
                                          contract_accepted=1, privacy_accepted=1)
        mo.submit_shipper(self.c, self.op, s); mo.verify_shipper(self.c, self._a(11), s)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION"):
            d = mo.upload_document(self.c, self.op, dt, "SHIPPER", s, expiry_date="2027-01-01")
            mo.verify_document(self.c, self._a(11), d)
        mo.activate_shipper(self.c, self._a(12), s); return s

    def _carrier(self, reg="C1"):
        cid = mo.create_carrier_application(self.c, self.op, "FLEET_OPERATOR", "Cr", registration_type="SEC",
                                            registration_number=reg, operating_address="M", preferred_lanes=["CAVITE"])
        mo.submit_carrier(self.c, self.op, cid); mo.verify_carrier(self.c, self._a(11), cid)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION", "AUTHORITY_TO_OPERATE", "INSURANCE"):
            d = mo.upload_document(self.c, self.op, dt, "CARRIER", cid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self._a(11), d)
        mo.activate_carrier(self.c, self._a(12), cid); return cid

    def _vehicle(self, cid, plate="P1"):
        v = mo.register_vehicle(self.c, self.op, cid, "truck_6w", plate); mo.verify_vehicle(self.c, self._a(11), v)
        for dt in ("VEHICLE_REGISTRATION", "INSURANCE"):
            d = mo.upload_document(self.c, self.op, dt, "VEHICLE", v, expiry_date="2027-01-01")
            mo.verify_document(self.c, self._a(11), d)
        mo.activate_vehicle(self.c, self._a(12), v); return v

    def _driver(self, cid, name):
        d = mo.register_driver(self.c, self.op, cid, name, licence_expiry="2027-01-01", authorized_categories=["truck_6w"])
        mo.verify_driver(self.c, self._a(11), d); mo.activate_driver(self.c, self._a(12), d); return d

    def _lane(self, code):
        lane = [l for l in mkt.list_lanes(self.c) if l["code"] == code][0]
        mkt.assess_lane(self.c, self.op, lane["id"], verified_carriers=5, backup_capacity=1,
                        price_model_validated=1, ops_support=1, payment_capable=1, dispute_process=1, monitoring=1)
        mkt.activate_lane(self.c, self._a(11), lane["id"], target="ACTIVE")

    def _trip(self, driver_id=None):
        driver_id = driver_id or self.drv
        bk = mm.create_booking(self.c, self.op, self.sid, "general", "METRO_MANILA", "CAVITE",
                               weight_kg=5000, volume_cbm=10, pickup_address="A", delivery_address="B")
        mm.validate_booking(self.c, self._a(11), bk); mm.select_pricing_mode(self.c, self._a(11), bk)
        mm.price_booking(self.c, self._a(11), bk)
        mm.generate_candidates(self.c, self.op, bk); mm.create_broadcast(self.c, self.op, bk, wave=1)
        off = mm.submit_offer(self.c, self.cu, bk, self.cid, 4800, vehicle_id=self.vid, driver_id=driver_id)["offer_id"]
        mm.evaluate_offers(self.c, self.op, bk); mm.select_offer(self.c, self.op, bk, off)
        aid = mm.create_assignment(self.c, self.op, bk)["assignment_id"]
        tid = tp.create_trip(self.c, self.op, aid)["trip_id"]
        return tid, bk

    def _activate(self, tid):
        self.c.execute("UPDATE mkt_trips SET status='ACTIVATED' WHERE id=?", (tid,)); self.c.commit()


# --------------------------------------------------------------------------- #
class Binding(Base):
    def test_resolve_and_my_trips(self):
        p = self._principal(90, self.drv)
        self.assertEqual(da.resolve_driver(self.c, p), self.drv)
        self.assertIn(self.tid, [t["id"] for t in da.my_trips(self.c, p)["trips"]])

    def test_unbound_denied(self):
        p = {"id": 77, "role": "driver_principal", "perms": set(core.PERMISSIONS["driver_principal"]), "tenant_id": self.rgo}
        with self.assertRaises(core.ForbiddenError):
            da.my_trips(self.c, p)

    def test_binding_requires_authority(self):
        weak = {"id": 5, "role": "x", "perms": {"driver.app.view"}, "tenant_id": self.rgo}
        with self.assertRaises(core.ForbiddenError):
            da.bind_principal(self.c, weak, 90, self.drv)


# --------------------------------------------------------------------------- #
class OwnTripsOnly(Base):
    def test_cross_driver_blocked(self):
        d2 = self._driver(self.cid, "Pedro")
        other = self._principal(91, d2)
        with self.assertRaises(core.ForbiddenError):
            da.trip_detail(self.c, other, self.tid)      # not their trip
        with self.assertRaises(core.ForbiddenError):
            da.advance(self.c, other, self.tid, "EN_ROUTE_PICKUP")


# --------------------------------------------------------------------------- #
class Execution(Base):
    def test_advance_and_ping_and_pod(self):
        p = self._principal(90, self.drv)
        self._activate(self.tid)
        self.assertEqual(da.advance(self.c, p, self.tid, "EN_ROUTE_PICKUP")["to"], "EN_ROUTE_PICKUP")
        da.ping(self.c, p, self.tid, progress=25, lat=14.6, lng=121.1)
        self.assertEqual(da.submit_pod(self.c, p, self.tid, "POD", evidence_types=["photo"])["status"], "SUBMITTED")

    def test_detail_shape(self):
        p = self._principal(90, self.drv)
        d = da.trip_detail(self.c, p, self.tid)
        self.assertEqual(set(d.keys()), {"trip", "timeline", "delivery_verification"})


# --------------------------------------------------------------------------- #
class OtpSafety(Base):
    def test_driver_can_verify_but_never_issue(self):
        p = self._principal(90, self.drv)
        dv.set_recipient(self.c, self.op, self.bk, "Maria", mobile="09171234567")
        dv.issue_otp(self.c, self.op, self.bk)                 # ops issues
        with self.assertRaises(core.ForbiddenError):
            dv.issue_otp(self.c, p, self.bk)                   # driver cannot issue
        with self.assertRaises(core.ForbiddenError):
            da.verify_recipient_otp(self.c, p, self.tid, "000000")   # wrong code rejected (no leak)

    def test_elevation_never_reaches_issue_or_override(self):
        p = self._principal(90, self.drv)
        for forbidden in ("delivery.verification.issue", "delivery.verification.resend",
                          "delivery.verification.override", "marketplace.trip.manage"):
            with self.assertRaises(core.ForbiddenError):
                da._svc(p, forbidden)

    def test_role_cannot_reach_admin_trip_control(self):
        p = self._principal(90, self.drv)
        with self.assertRaises(core.ForbiddenError):
            tp.cancel_trip(self.c, p, self.tid, "nope")        # role lacks marketplace.trip.manage


# --------------------------------------------------------------------------- #
class Integrity(Base):
    def test_integrity_clean(self):
        self._principal(90, self.drv)
        self.assertTrue(da.run_integrity(self.c, self.op)["ok"])


if __name__ == "__main__":
    unittest.main()
