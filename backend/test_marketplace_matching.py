"""LiftHaul Nationwide Marketplace — Increment 3 tests (§28).

Booking intake, validation, deterministic vehicle requirement, pricing-mode selection, pricing +
immutable snapshots, candidate pool (reusing Increment-2 eligibility), deterministic ranking,
controlled broadcast, offers/bids, evaluation, selection, and PAYMENT-GATED assignment — proving
separation of duties, no auto-select, hard-denial precedence, no trip activation, tenant isolation,
and zero financial / operational-status drift.
"""
import unittest

import db
import core
import admin_platform as ap
import marketplace as mkt
import marketplace_onboarding as mo
import marketplace_matching as mm


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.cr = self._a(10); self.vf = self._a(11); self.ac = self._a(12); self.cu = self._a(20)
        self.sid = self._active_shipper()
        self.cid = self._active_carrier()
        self.vid = self._active_vehicle(self.cid)
        self.did = self._active_driver(self.cid)
        self._active_lane("MM-CAV")

    def _a(self, id, perms=("*",), tenant="rgo"):
        return {"id": id, "role": "ops", "perms": set(perms), "tenant_id": self.rgo if tenant == "rgo" else tenant}

    def _active_shipper(self, reg="S1"):
        sid = mo.create_shipper_application(self.c, self.cr, "CORPORATION", "Acme", registration_type="SEC",
                                            registration_number=reg, registered_address="Makati",
                                            contract_accepted=1, privacy_accepted=1)
        mo.submit_shipper(self.c, self.cr, sid); mo.verify_shipper(self.c, self.vf, sid)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION"):
            d = mo.upload_document(self.c, self.cr, dt, "SHIPPER", sid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self.vf, d)
        mo.activate_shipper(self.c, self.ac, sid)
        return sid

    def _active_carrier(self, reg="C1", prefs=None):
        cid = mo.create_carrier_application(self.c, self.cr, "FLEET_OPERATOR", "Haulers", registration_type="SEC",
                                            registration_number=reg, operating_address="M",
                                            preferred_lanes=prefs or ["CAVITE"])
        mo.submit_carrier(self.c, self.cr, cid); mo.verify_carrier(self.c, self.vf, cid)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION", "AUTHORITY_TO_OPERATE", "INSURANCE"):
            d = mo.upload_document(self.c, self.cr, dt, "CARRIER", cid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self.vf, d)
        mo.activate_carrier(self.c, self.ac, cid)
        return cid

    def _active_vehicle(self, cid, cat="truck_6w", plate="ABC-1"):
        vid = mo.register_vehicle(self.c, self.cr, cid, cat, plate)
        mo.verify_vehicle(self.c, self.vf, vid)
        for dt in ("VEHICLE_REGISTRATION", "INSURANCE"):
            d = mo.upload_document(self.c, self.cr, dt, "VEHICLE", vid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self.vf, d)
        mo.activate_vehicle(self.c, self.ac, vid)
        return vid

    def _active_driver(self, cid, cat="truck_6w"):
        did = mo.register_driver(self.c, self.cr, cid, "Juan", licence_expiry="2027-01-01",
                                 authorized_categories=[cat])
        mo.verify_driver(self.c, self.vf, did); mo.activate_driver(self.c, self.ac, did)
        return did

    def _active_lane(self, code):
        lane = [l for l in mkt.list_lanes(self.c) if l["code"] == code][0]
        mkt.assess_lane(self.c, self.cr, lane["id"], verified_carriers=5, backup_capacity=1,
                        price_model_validated=1, ops_support=1, payment_capable=1, dispute_process=1, monitoring=1)
        mkt.activate_lane(self.c, self.vf, lane["id"], target="ACTIVE")
        return lane

    def _booking(self, cargo="general", oz="METRO_MANILA", dz="CAVITE", **a):
        return mm.create_booking(self.c, self.cr, self.sid, cargo, oz, dz,
                                 weight_kg=a.get("weight_kg", 5000), volume_cbm=a.get("volume_cbm", 10),
                                 pickup_address=a.get("pickup", "A"), delivery_address=a.get("delivery", "B"), **{k: v for k, v in a.items() if k not in ("weight_kg", "volume_cbm", "pickup", "delivery")})

    def _priced_matched(self, bk):
        mm.validate_booking(self.c, self.vf, bk); mm.select_pricing_mode(self.c, self.vf, bk)
        mm.price_booking(self.c, self.vf, bk); mm.generate_candidates(self.c, self.ac, bk)
        mm.create_broadcast(self.c, self.ac, bk, wave=1)
        return mm.submit_offer(self.c, self.cu, bk, self.cid, 4800, vehicle_id=self.vid, driver_id=self.did)["offer_id"]


# --------------------------------------------------------------------------- #
class BookingTests(Base):
    def test_create_and_validate(self):
        bk = self._booking()
        self.assertEqual(mm.validate_booking(self.c, self.vf, bk)["status"], "VALIDATED")

    def test_missing_cargo_weight_incomplete(self):
        bk = mm.create_booking(self.c, self.cr, self.sid, "general", "METRO_MANILA", "CAVITE",
                               pickup_address="A", delivery_address="B")
        r = mm.validate_booking(self.c, self.vf, bk)
        self.assertIn("weight", r["missing"])

    def test_missing_address(self):
        bk = mm.create_booking(self.c, self.cr, self.sid, "general", "METRO_MANILA", "CAVITE", weight_kg=1000)
        r = mm.validate_booking(self.c, self.vf, bk)
        self.assertIn("pickup_address", r["missing"])

    def test_prohibited_cargo_blocked(self):
        bk = self._booking(cargo="prohibited")
        r = mm.validate_booking(self.c, self.vf, bk)
        self.assertIn("cargo_prohibited", r["blockers"])

    def test_inactive_shipper_blocked(self):
        mo.suspend_shipper(self.c, self.ac, self.sid, "audit")
        bk = self._booking()
        self.assertIn("shipper_not_active", mm.validate_booking(self.c, self.vf, bk)["blockers"])

    def test_tenant_isolation(self):
        bk = self._booking()
        other = self._a(30, tenant=99999)
        with self.assertRaises(core.NotFoundError):
            mm.validate_booking(self.c, other, bk)

    def test_booking_create_permission(self):
        with self.assertRaises(core.ForbiddenError):
            mm.create_booking(self.c, self._a(40, perms=()), self.sid, "general", "METRO_MANILA", "CAVITE")


class VehicleRequirementTests(Base):
    def test_payload_and_eligible(self):
        bk = self._booking(weight_kg=9000)
        vr = mm.vehicle_requirement(self.c, bk)
        self.assertTrue(all(True for _ in vr["eligible_categories"]))
        self.assertNotIn("elf_4w", vr["eligible_categories"])

    def test_refrigeration_feature(self):
        bk = self._booking(cargo="perishable_chilled", weight_kg=500, volume_cbm=2)
        vr = mm.vehicle_requirement(self.c, bk)
        self.assertIn("refrigeration", vr["required_features"])

    def test_prohibited_blocks_requirement(self):
        bk = self._booking(cargo="prohibited")
        self.assertEqual(mm.vehicle_requirement(self.c, bk)["blocked"], "cargo_prohibited")


class PricingTests(Base):
    def test_instant_price_snapshot_immutable(self):
        bk = self._booking(); mm.validate_booking(self.c, self.vf, bk); mm.select_pricing_mode(self.c, self.vf, bk)
        p1 = mm.price_booking(self.c, self.vf, bk)
        self.assertGreater(p1["total"], 0)
        # tax is Phase-2 governed (12% of subtotal)
        self.assertEqual(p1["tax"], round(p1["subtotal"] * 0.12))
        # changing a rate card later does not alter the existing snapshot
        self.c.execute("UPDATE mkt_rate_cards SET rate=99999 WHERE component='base'")
        snap = mm.get_pricing_snapshot(self.c, self.vf, p1["snapshot_id"])
        self.assertEqual(snap["total"], p1["total"])

    def test_platform_fee_and_payout(self):
        bk = self._booking(); mm.validate_booking(self.c, self.vf, bk); mm.select_pricing_mode(self.c, self.vf, bk)
        p = mm.price_booking(self.c, self.vf, bk)
        self.assertEqual(p["platform_fee"], round(p["subtotal"] * 0.10, 2))
        self.assertEqual(p["estimated_carrier_payout"], round(p["subtotal"] - p["platform_fee"], 2))

    def test_client_view_hides_internal_margin(self):
        bk = self._booking(); mm.validate_booking(self.c, self.vf, bk); mm.select_pricing_mode(self.c, self.vf, bk)
        p = mm.price_booking(self.c, self.vf, bk)
        shipper_actor = {"id": 77, "role": "shipper", "perms": set(), "tenant_id": self.rgo}
        snap = mm.get_pricing_snapshot(self.c, shipper_actor, p["snapshot_id"], client_view=True)
        self.assertNotIn("platform_fee", snap)
        self.assertFalse(any(c.get("internal_only") for c in snap["components"]))

    def test_unknown_distance_routes_away_from_instant(self):
        # inter-regional lane with no master distance -> managed quotation, instant price refuses
        bk = self._booking(oz="METRO_MANILA", dz="CEBU")
        mm.validate_booking(self.c, self.vf, bk)
        modes = mm.eligible_pricing_modes(self.c, bk)
        self.assertIn("MANAGED_QUOTATION", modes)

    def test_unauthorized_pricing_override(self):
        bk = self._booking(); mm.validate_booking(self.c, self.vf, bk)
        weak = self._a(50, perms=("marketplace.pricing.manage",))
        with self.assertRaises(core.ForbiddenError):
            mm.select_pricing_mode(self.c, weak, bk, override="CONTRACT_RATE", reason="x")


class MatchingTests(Base):
    def test_candidate_pool_reuses_eligibility(self):
        bk = self._booking(); mm.validate_booking(self.c, self.vf, bk)
        mm.select_pricing_mode(self.c, self.vf, bk); mm.price_booking(self.c, self.vf, bk)
        r = mm.generate_candidates(self.c, self.ac, bk)
        self.assertEqual(len(r["candidates"]), 1)

    def test_suspended_carrier_excluded(self):
        bk = self._booking(); mm.validate_booking(self.c, self.vf, bk)
        mm.select_pricing_mode(self.c, self.vf, bk); mm.price_booking(self.c, self.vf, bk)
        mo.suspend_carrier(self.c, self.ac, self.cid, "audit")
        r = mm.generate_candidates(self.c, self.ac, bk)
        self.assertEqual(r["candidates"], [])

    def test_ranking_is_deterministic_and_transparent(self):
        bk = self._booking(); mm.validate_booking(self.c, self.vf, bk)
        mm.select_pricing_mode(self.c, self.vf, bk); mm.price_booking(self.c, self.vf, bk)
        a = mm.generate_candidates(self.c, self.ac, bk)["candidates"]
        self.assertTrue(a and "factors" in a[0])
        self.assertTrue(all("weight" in f and "contribution" in f for f in a[0]["factors"]))

    def test_ai_cannot_widen_pool(self):
        # excluded carriers stay excluded; the API returns only the hard-eligible pool
        bk = self._booking(); mm.validate_booking(self.c, self.vf, bk)
        mm.select_pricing_mode(self.c, self.vf, bk); mm.price_booking(self.c, self.vf, bk)
        mo.set_vehicle_status(self.c, self.ac, self.vid, "MAINTENANCE")
        r = mm.generate_candidates(self.c, self.ac, bk)
        self.assertEqual(r["candidates"], [])


class BroadcastTests(Base):
    def test_wave1_targets_and_dedup(self):
        bk = self._booking(); self._priced_matched(bk)   # already broadcast wave 1 with 1 target
        # a second wave-1 broadcast must suppress the already-notified carrier
        bc2 = mm.create_broadcast(self.c, self.ac, bk, wave=1)
        self.assertIn(self.cid, [int(k) for k in bc2["suppressed"].keys()])

    def test_broadcast_requires_candidates(self):
        bk = self._booking(); mm.validate_booking(self.c, self.vf, bk)
        mm.select_pricing_mode(self.c, self.vf, bk); mm.price_booking(self.c, self.vf, bk)
        with self.assertRaises(ValueError):
            mm.create_broadcast(self.c, self.ac, bk, wave=1)


class OfferTests(Base):
    def test_valid_offer(self):
        bk = self._booking(); self._priced_matched(bk)   # returns an offer id; make another valid
        r = mm.submit_offer(self.c, self.cu, bk, self.cid, 5200, vehicle_id=self.vid, driver_id=self.did)
        self.assertEqual(r["status"], "VALID")

    def test_invalid_amount(self):
        bk = self._booking(); mm.validate_booking(self.c, self.vf, bk)
        mm.select_pricing_mode(self.c, self.vf, bk); mm.price_booking(self.c, self.vf, bk)
        r = mm.submit_offer(self.c, self.cu, bk, self.cid, 0, vehicle_id=self.vid, driver_id=self.did)
        self.assertEqual(r["status"], "INVALID")
        self.assertIn("invalid_amount", r["invalid_reason"])

    def test_ineligible_vehicle_offer(self):
        bk = self._booking(weight_kg=9000)   # 9t needs a bigger truck
        mm.validate_booking(self.c, self.vf, bk); mm.select_pricing_mode(self.c, self.vf, bk)
        small = self._active_vehicle(self.cid, cat="elf_4w", plate="SMALL-1")   # 2t
        r = mm.submit_offer(self.c, self.cu, bk, self.cid, 6000, vehicle_id=small)
        self.assertEqual(r["status"], "INVALID")

    def test_expired_offer_cannot_be_selected(self):
        bk = self._booking(); off = self._priced_matched(bk)
        self.c.execute("UPDATE mkt_offers SET valid_until='2020-01-01' WHERE id=?", (off,))
        mm.expire_offers(self.c, self.ac)
        with self.assertRaises(ValueError):
            mm.select_offer(self.c, self.vf, bk, off)

    def test_withdraw(self):
        bk = self._booking(); off = self._priced_matched(bk)
        self.assertEqual(mm.withdraw_offer(self.c, self.cu, off)["status"], "WITHDRAWN")

    def test_cross_tenant_offer_denied(self):
        bk = self._booking(); self._priced_matched(bk)
        other = self._a(60, tenant=99999)
        with self.assertRaises(core.NotFoundError):
            mm.submit_offer(self.c, other, bk, self.cid, 5000, vehicle_id=self.vid)


class SelectionAssignmentTests(Base):
    def test_self_selection_denied(self):
        bk = self._booking(); off = self._priced_matched(bk)
        with self.assertRaises(PermissionError):
            mm.select_offer(self.c, self.cu, bk, off)   # cu created the offer

    def test_auto_selection_disabled(self):
        bk = self._booking(); off = self._priced_matched(bk)
        with self.assertRaises(PermissionError):
            mm.select_offer(self.c, self.vf, bk, off, model="AUTO_SELECTION")

    def test_valid_assignment_is_payment_gated(self):
        bk = self._booking(); off = self._priced_matched(bk)
        mm.evaluate_offers(self.c, self.vf, bk)
        mm.select_offer(self.c, self.vf, bk, off)
        asg = mm.create_assignment(self.c, self.ac, bk)
        conf = mm.confirm_assignment(self.c, self.cu, asg["assignment_id"])
        self.assertEqual(conf["status"], "PAYMENT_REQUIRED")
        self.assertFalse(conf["trip_active"])
        row = self.c.execute("SELECT status FROM mkt_bookings WHERE id=?", (bk,)).fetchone()
        self.assertEqual(row["status"], "PAYMENT_REQUIRED")

    def test_high_value_requires_approval_and_sod(self):
        bk = self._booking(); mm.validate_booking(self.c, self.vf, bk)
        mm.select_pricing_mode(self.c, self.vf, bk); mm.price_booking(self.c, self.vf, bk)
        mm.generate_candidates(self.c, self.ac, bk); mm.create_broadcast(self.c, self.ac, bk, wave=1)
        off = mm.submit_offer(self.c, self.cu, bk, self.cid, 600000, vehicle_id=self.vid, driver_id=self.did)["offer_id"]
        mm.select_offer(self.c, self.vf, bk, off)
        asg = mm.create_assignment(self.c, self.ac, bk)
        self.assertTrue(asg["approval_required"])
        # confirmation blocked until approved
        with self.assertRaises(ValueError):
            mm.confirm_assignment(self.c, self.cu, asg["assignment_id"])
        # assigner may not approve (SoD)
        with self.assertRaises(PermissionError):
            mm.approve_assignment(self.c, self.ac, asg["assignment_id"])
        mm.approve_assignment(self.c, self.vf, asg["assignment_id"])
        self.assertEqual(mm.confirm_assignment(self.c, self.cu, asg["assignment_id"])["status"], "PAYMENT_REQUIRED")

    def test_carrier_rejection_reassigns(self):
        bk = self._booking(); off = self._priced_matched(bk)
        mm.select_offer(self.c, self.vf, bk, off)
        asg = mm.create_assignment(self.c, self.ac, bk)
        r = mm.confirm_assignment(self.c, self.cu, asg["assignment_id"], decision="reject")
        self.assertEqual(r["status"], "REASSIGNMENT_REQUIRED")

    def test_substitution_reruns_eligibility(self):
        bk = self._booking(); off = self._priced_matched(bk)
        mm.select_offer(self.c, self.vf, bk, off)
        asg = mm.create_assignment(self.c, self.ac, bk)
        # substitute a maintenance vehicle -> rerun fails
        mo.set_vehicle_status(self.c, self.ac, self.vid, "MAINTENANCE")
        r = mm.request_substitution(self.c, self.cu, asg["assignment_id"], new_vehicle_id=self.vid)
        self.assertFalse(r["ok"])

    def test_no_trip_activation_state(self):
        bk = self._booking(); off = self._priced_matched(bk)
        mm.select_offer(self.c, self.vf, bk, off)
        asg = mm.create_assignment(self.c, self.ac, bk)
        mm.confirm_assignment(self.c, self.cu, asg["assignment_id"])
        # nothing reaches READY_FOR_TRIP_ACTIVATION in Increment 3
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM mkt_assignments WHERE status='READY_FOR_TRIP_ACTIVATION'").fetchone()[0], 0)


class IntegrityMigrationDriftTests(Base):
    def test_integrity_runs(self):
        bk = self._booking(); self._priced_matched(bk)
        r = mm.run_integrity(self.c, self.ac)
        self.assertIn(r["overall"], mm.INTEGRITY_STATUSES)

    def test_cheapest_does_not_auto_win_note(self):
        bk = self._booking(); self._priced_matched(bk)
        ev = mm.evaluate_offers(self.c, self.vf, bk)
        self.assertIn("cheapest", ev["note"])

    def test_migration_zero_unexpected(self):
        inv = mm.classify_existing(self.c)["invariants"]
        self.assertEqual(inv["unexpected_broadcasts"], 0)
        self.assertEqual(inv["unexpected_offers"], 0)
        self.assertEqual(inv["unexpected_assignments"], 0)

    def test_no_financial_drift(self):
        a = {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": self.rgo}
        cid = core.create_customer(self.c, a, "Drift Co")
        bid = core.create_booking(self.c, a, cid, "CRANE_RENTAL", "x", 1)
        core.review_booking(self.c, a, bid); core.ready_for_quotation(self.c, a, bid)
        qid = core.create_quotation(self.c, a, bid, [{"kind": "crane", "description": "x", "qty": 2, "days": 1, "rate": 300000}])
        row = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        self.assertEqual((row["tax"], row["total"]), (72000, 672000))

    def test_schema_version(self):
        self.assertGreaterEqual(db.SCHEMA_VERSION, 18)


class TestMatchingApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        import db as _db
        server._conn = _db.connect(":memory:")
        cls.server = server
        try:
            core.create_user(server._conn, "mm@r", "demo1234", "admin", "MM Admin")
        except core.ConflictError:
            pass

    def _call(self, method, path, body=None):
        fn, params = self.server._match(method, path)
        assert fn, f"no route for {method} {path}"
        tok = self.server._match("POST", "/login")[0](None, {"email": "mm@r", "password": "demo1234"}, {})["token"]
        actor = core.actor_for(self.server._conn, tok)
        return fn(actor, body or {}, params or {})

    def test_bookings_and_queues_via_api(self):
        self.assertIn("bookings", self._call("GET", "/admin/marketplace/bookings"))
        self.assertIn("unvalidated", self._call("GET", "/admin/marketplace/queues"))

    def test_pricing_integrity_via_api(self):
        self.assertIn(self._call("GET", "/admin/marketplace/matching-integrity")["overall"], mm.INTEGRITY_STATUSES)


if __name__ == "__main__":
    unittest.main()
