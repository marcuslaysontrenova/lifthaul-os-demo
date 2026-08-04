"""LiftHaul Nationwide Marketplace — Increment 5 (core) tests.

Trip execution engine, provider-neutral GPS + geofencing, and proof of delivery — proving that a trip
activates ONLY when Increment-4 protected funding is confirmed (no trip-active before payment), that the
state machine is governed + audited, that POD is required before completion, that delivery evidence
bridges to Increment-4 conditional release, that live GPS is fail-closed, and that there is zero
financial / payment-status drift.
"""
import unittest

import db
import core
import admin_platform as ap
import marketplace as mkt
import marketplace_onboarding as mo
import marketplace_matching as mm
import marketplace_payments as mp
import marketplace_trips as mt


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.cr = self._a(10); self.vf = self._a(11); self.ac = self._a(12)
        self.cu = self._a(20); self.fin = self._a(13); self.drv = self._a(30)

    def _a(self, id, perms=("*",), tenant="rgo"):
        return {"id": id, "role": "ops", "perms": set(perms), "tenant_id": self.rgo if tenant == "rgo" else tenant}

    def _confirmed_assignment(self, sfx="1"):
        c, cr, vf, ac, cu = self.c, self.cr, self.vf, self.ac, self.cu
        sid = mo.create_shipper_application(c, cr, "CORPORATION", "Acme" + sfx, registration_type="SEC",
                                            registration_number="S" + sfx, registered_address="Makati",
                                            contract_accepted=1, privacy_accepted=1)
        mo.submit_shipper(c, cr, sid); mo.verify_shipper(c, vf, sid)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION"):
            mo.verify_document(c, vf, mo.upload_document(c, cr, dt, "SHIPPER", sid, expiry_date="2027-01-01"))
        mo.activate_shipper(c, ac, sid)
        cid = mo.create_carrier_application(c, cr, "FLEET_OPERATOR", "H" + sfx, registration_type="SEC",
                                            registration_number="C" + sfx, operating_address="M", preferred_lanes=["CAVITE"])
        mo.submit_carrier(c, cr, cid); mo.verify_carrier(c, vf, cid)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION", "AUTHORITY_TO_OPERATE", "INSURANCE"):
            mo.verify_document(c, vf, mo.upload_document(c, cr, dt, "CARRIER", cid, expiry_date="2027-01-01"))
        mo.activate_carrier(c, ac, cid)
        vid = mo.register_vehicle(c, cr, cid, "truck_6w", "P" + sfx); mo.verify_vehicle(c, vf, vid)
        for dt in ("VEHICLE_REGISTRATION", "INSURANCE"):
            mo.verify_document(c, vf, mo.upload_document(c, cr, dt, "VEHICLE", vid, expiry_date="2027-01-01"))
        mo.activate_vehicle(c, ac, vid)
        did = mo.register_driver(c, cr, cid, "D" + sfx, licence_expiry="2027-01-01", authorized_categories=["truck_6w"])
        mo.verify_driver(c, vf, did); mo.activate_driver(c, ac, did)
        lane = [l for l in mkt.list_lanes(c) if l["code"] == "MM-CAV"][0]
        if lane["status"] != "ACTIVE":
            mkt.assess_lane(c, cr, lane["id"], verified_carriers=5, backup_capacity=1, price_model_validated=1,
                            ops_support=1, payment_capable=1, dispute_process=1, monitoring=1)
            mkt.activate_lane(c, vf, lane["id"], target="ACTIVE")
        bk = mm.create_booking(c, cr, sid, "general", "METRO_MANILA", "CAVITE", weight_kg=5000, volume_cbm=10,
                               pickup_address="A", delivery_address="B")
        mm.validate_booking(c, vf, bk); mm.select_pricing_mode(c, vf, bk); mm.price_booking(c, vf, bk)
        mm.generate_candidates(c, ac, bk); mm.create_broadcast(c, ac, bk, wave=1)
        off = mm.submit_offer(c, cu, bk, cid, 4800, vehicle_id=vid, driver_id=did)["offer_id"]
        mm.select_offer(c, vf, bk, off); asg = mm.create_assignment(c, ac, bk); mm.confirm_assignment(c, cu, asg["assignment_id"])
        return asg["assignment_id"]

    def _protected_trip(self, sfx="1"):
        aid = self._confirmed_assignment(sfx)
        prid = mp.create_payment_requirement(self.c, self.ac, aid)["id"]
        tid = mt.create_trip(self.c, self.cr, aid)["trip_id"]
        mp.record_funding_event(self.c, self.fin, prid, "full")
        return tid, prid, aid

    def _drive_to(self, tid, upto):
        seq = ["EN_ROUTE_PICKUP", "ARRIVED_PICKUP", "LOADING", "LOADED", "DEPARTED", "IN_TRANSIT",
               "ARRIVED_DESTINATION", "UNLOADING", "DELIVERED"]
        for st in seq:
            mt.advance_trip(self.c, self.drv, tid, st)
            if st == upto:
                return


# --------------------------------------------------------------------------- #
class TripActivationTests(Base):
    def test_activation_blocked_before_funding(self):
        aid = self._confirmed_assignment()
        mp.create_payment_requirement(self.c, self.ac, aid)
        tid = mt.create_trip(self.c, self.cr, aid)["trip_id"]
        with self.assertRaises(ValueError):
            mt.activate_trip(self.c, self.ac, tid)   # payment gate not eligible

    def test_activation_after_protected_funding(self):
        tid, prid, _ = self._protected_trip()
        self.assertEqual(mt.activate_trip(self.c, self.ac, tid)["status"], "ACTIVATED")

    def test_activation_requires_permission(self):
        tid, prid, _ = self._protected_trip()
        with self.assertRaises(core.ForbiddenError):
            mt.activate_trip(self.c, self._a(99, perms=()), tid)

    def test_duplicate_trip_prevented(self):
        aid = self._confirmed_assignment()
        mt.create_trip(self.c, self.cr, aid)
        with self.assertRaises(ValueError):
            mt.create_trip(self.c, self.cr, aid)

    def test_tenant_isolation(self):
        tid, prid, _ = self._protected_trip()
        with self.assertRaises(core.NotFoundError):
            mt.activate_trip(self.c, self._a(50, tenant=99999), tid)


class StateMachineTests(Base):
    def test_illegal_transition_rejected(self):
        tid, prid, _ = self._protected_trip()
        mt.activate_trip(self.c, self.ac, tid)
        with self.assertRaises(ValueError):
            mt.advance_trip(self.c, self.drv, tid, "DELIVERED")   # skips the chain

    def test_cannot_advance_to_activated(self):
        tid, prid, _ = self._protected_trip()
        mt.activate_trip(self.c, self.ac, tid)
        with self.assertRaises(ValueError):
            mt.advance_trip(self.c, self.drv, tid, "ACTIVATED")

    def test_pod_required_before_pod_submitted(self):
        tid, prid, _ = self._protected_trip()
        mt.activate_trip(self.c, self.ac, tid)
        self._drive_to(tid, "DELIVERED")
        with self.assertRaises(ValueError):
            mt.advance_trip(self.c, self.drv, tid, "POD_SUBMITTED")

    def test_full_lifecycle_completes(self):
        tid, prid, _ = self._protected_trip()
        mt.activate_trip(self.c, self.ac, tid)
        self._drive_to(tid, "DELIVERED")
        mt.submit_proof(self.c, self.drv, tid, "POD", evidence_types=["photo", "signature"], signature_ref="s")
        mt.advance_trip(self.c, self.drv, tid, "POD_SUBMITTED")
        mt.accept_delivery(self.c, self.cu, tid)
        mt.advance_trip(self.c, self.drv, tid, "COMPLETED")
        self.assertEqual([t for t in mt.list_trips(self.c, self.ac) if t["id"] == tid][0]["status"], "COMPLETED")

    def test_timeline_is_audited(self):
        tid, prid, _ = self._protected_trip()
        mt.activate_trip(self.c, self.ac, tid)
        mt.advance_trip(self.c, self.drv, tid, "EN_ROUTE_PICKUP")
        self.assertGreaterEqual(len(mt.trip_timeline(self.c, self.ac, tid)), 2)


class MilestoneBridgeTests(Base):
    def test_delivery_makes_release_eligible(self):
        tid, prid, _ = self._protected_trip()
        # before delivery, release is blocked on delivery evidence
        self.assertFalse(mp.evaluate_release(self.c, self.ac, prid)["release_eligible"])
        mt.activate_trip(self.c, self.ac, tid)
        self._drive_to(tid, "DELIVERED")
        mt.submit_proof(self.c, self.drv, tid, "POD", evidence_types=["photo"], signature_ref="s")
        mt.advance_trip(self.c, self.drv, tid, "POD_SUBMITTED")   # emits DELIVERY_CONFIRMED
        mt.accept_delivery(self.c, self.cu, tid)                  # emits CLIENT_ACCEPTED
        # execution evidence now unlocks Increment-4 release
        self.assertTrue(mp.evaluate_release(self.c, self.ac, prid)["release_eligible"])

    def test_release_then_payout_end_to_end(self):
        tid, prid, _ = self._protected_trip()
        mt.activate_trip(self.c, self.ac, tid)
        self._drive_to(tid, "DELIVERED")
        mt.submit_proof(self.c, self.drv, tid, "POD", evidence_types=["photo"], signature_ref="s")
        mt.advance_trip(self.c, self.drv, tid, "POD_SUBMITTED")
        mt.accept_delivery(self.c, self.cu, tid)
        ri = mp.create_release_instruction(self.c, self.cr, prid)
        mp.approve_release(self.c, self.vf, ri["release_instruction_id"])
        self.assertEqual(mp.submit_release(self.c, self.ac, ri["release_instruction_id"])["status"], "COMPLETED")


class GpsGeofenceTests(Base):
    def test_gps_ping_updates_progress_and_eta(self):
        tid, prid, _ = self._protected_trip()
        mt.activate_trip(self.c, self.ac, tid)
        mt.advance_trip(self.c, self.drv, tid, "EN_ROUTE_PICKUP")
        r = mt.record_gps_ping(self.c, self.drv, tid, progress=0.5)
        self.assertTrue(0 <= r["progress_pct"] <= 100)
        self.assertTrue(r["eta"])

    def test_gps_ping_rejected_on_terminal_trip(self):
        tid, prid, _ = self._protected_trip()
        with self.assertRaises(ValueError):
            mt.record_gps_ping(self.c, self.drv, tid)   # still CREATED

    def test_geofence_enter_event(self):
        tid, prid, _ = self._protected_trip()
        mt.activate_trip(self.c, self.ac, tid)
        mt.advance_trip(self.c, self.drv, tid, "EN_ROUTE_PICKUP")
        gf = mt.define_geofence(self.c, self.ac, "DEST", "DESTINATION", 14.4791, 120.8969, radius_m=100000)
        mt.record_gps_ping(self.c, self.drv, tid, progress=1.0)   # near destination
        self.assertEqual(mt.evaluate_geofence(self.c, self.drv, tid, gf)["event"], "ENTER")

    def test_live_gps_fail_closed(self):
        with self.assertRaises(core.ForbiddenError):
            mt.gps_provider("GOOGLE").position({}, 0.5)

    def test_gps_live_status_blocked(self):
        self.assertEqual(mt.gps_live_status()["live_gps"], "BLOCKED")


class PodExceptionIntegrityTests(Base):
    def test_pod_multi_evidence_hashed_and_labelled(self):
        tid, prid, _ = self._protected_trip()
        mt.activate_trip(self.c, self.ac, tid)
        self._drive_to(tid, "DELIVERED")
        r = mt.submit_proof(self.c, self.drv, tid, "POD", evidence_types=["photo", "signature", "otp"],
                            signature_ref="s", otp="1234", gps_lat=14.48, gps_lng=120.9)
        row = self.c.execute("SELECT * FROM mkt_pods WHERE id=?", (r["pod_id"],)).fetchone()
        self.assertTrue(row["evidence_hash"])
        self.assertEqual(row["mock_label"], "MOCK_ONLY")

    def test_exception_open_and_resolve(self):
        tid, prid, _ = self._protected_trip()
        mt.activate_trip(self.c, self.ac, tid)
        e = mt.open_exception(self.c, self.ac, tid, "BREAKDOWN", severity="HIGH", description="engine")
        self.assertEqual(mt.resolve_exception(self.c, self.ac, e["exception_id"], "replacement dispatched")["status"], "RESOLVED")

    def test_integrity_runs(self):
        tid, prid, _ = self._protected_trip()
        self.assertIn(mt.run_integrity(self.c, self.ac)["overall"], mt.INTEGRITY_STATUSES)

    def test_operations_dashboard(self):
        tid, prid, _ = self._protected_trip()
        self.assertIn("active", mt.operations_dashboard(self.c, self.ac))

    def test_migration_zero_drift(self):
        inv = mt.classify_existing(self.c)["invariants"]
        self.assertTrue(all(v == 0 for v in inv.values()))

    def test_no_financial_drift(self):
        a = {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": self.rgo}
        cid = core.create_customer(self.c, a, "Drift Co")
        bid = core.create_booking(self.c, a, cid, "CRANE_RENTAL", "x", 1)
        core.review_booking(self.c, a, bid); core.ready_for_quotation(self.c, a, bid)
        qid = core.create_quotation(self.c, a, bid, [{"kind": "crane", "description": "x", "qty": 2, "days": 1, "rate": 300000}])
        row = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        self.assertEqual((row["tax"], row["total"]), (72000, 672000))

    def test_schema_version(self):
        self.assertGreaterEqual(db.SCHEMA_VERSION, 20)


class TestTripApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        import db as _db
        server._conn = _db.connect(":memory:")
        cls.server = server
        try:
            core.create_user(server._conn, "trip@r", "demo1234", "admin", "Trip Admin")
        except core.ConflictError:
            pass

    def _call(self, method, path, body=None):
        fn, params = self.server._match(method, path)
        assert fn, f"no route for {method} {path}"
        tok = self.server._match("POST", "/login")[0](None, {"email": "trip@r", "password": "demo1234"}, {})["token"]
        actor = core.actor_for(self.server._conn, tok)
        return fn(actor, body or {}, params or {})

    def test_trips_and_dashboard_via_api(self):
        self.assertIn("trips", self._call("GET", "/admin/marketplace/trips"))
        self.assertIn("active", self._call("GET", "/admin/marketplace/operations-dashboard"))

    def test_gps_live_status_via_api(self):
        self.assertEqual(self._call("GET", "/admin/marketplace/gps-live-status")["live_gps"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
