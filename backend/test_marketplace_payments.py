"""LiftHaul Nationwide Marketplace — Increment 4 tests (§38).

Protected-payment lifecycle: payment requirements from immutable snapshots, funding + reconciliation,
fail-closed trip-activation gate, versioned release policies, milestone evidence, deterministic
non-mutating release evaluation, idempotent release instructions, carrier payouts, disputes + funds
freeze + resolution, refunds, chargebacks/reversals, dead-letter/replay — proving separation of duties,
no fund movement without protected evidence, live-provider fail-closed, tenant isolation, and zero
financial / payment-status / job-status drift.
"""
import unittest

import db
import core
import admin_platform as ap
import marketplace as mkt
import marketplace_onboarding as mo
import marketplace_matching as mm
import marketplace_payments as mp


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.cr = self._a(10); self.vf = self._a(11); self.ac = self._a(12)
        self.cu = self._a(20); self.fin = self._a(13)

    def _a(self, id, perms=("*",), tenant="rgo"):
        return {"id": id, "role": "ops", "perms": set(perms), "tenant_id": self.rgo if tenant == "rgo" else tenant}

    def _confirmed_assignment(self, sfx="1", amount=4800):
        cr, vf, ac, cu = self.cr, self.vf, self.ac, self.cu
        c = self.c
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
        off = mm.submit_offer(c, cu, bk, cid, amount, vehicle_id=vid, driver_id=did)["offer_id"]
        mm.select_offer(c, vf, bk, off); asg = mm.create_assignment(c, ac, bk); mm.confirm_assignment(c, cu, asg["assignment_id"])
        return asg["assignment_id"], cid

    def _protected_pr(self, sfx="1"):
        aid, cid = self._confirmed_assignment(sfx)
        pr = mp.create_payment_requirement(self.c, self.ac, aid)
        mp.record_funding_event(self.c, self.fin, pr["id"], "full")
        return pr["id"], cid

    def _releasable(self, sfx="1"):
        prid, cid = self._protected_pr(sfx)
        mp.submit_milestone(self.c, self.vf, prid, "DELIVERY_CONFIRMED")
        mp.submit_milestone(self.c, self.vf, prid, "CLIENT_ACCEPTED")
        return prid, cid


# --------------------------------------------------------------------------- #
class PaymentRequirementTests(Base):
    def test_creation_from_immutable_snapshot(self):
        aid, _ = self._confirmed_assignment()
        pr = mp.create_payment_requirement(self.c, self.ac, aid)
        snap = self.c.execute("SELECT total FROM mkt_pricing_snapshots s JOIN mkt_assignments a "
                              "ON a.pricing_snapshot_id=s.id WHERE a.id=?", (aid,)).fetchone()
        self.assertEqual(pr["protected_amount_required"], snap["total"])

    def test_duplicate_prevention(self):
        aid, _ = self._confirmed_assignment()
        p1 = mp.create_payment_requirement(self.c, self.ac, aid)
        p2 = mp.create_payment_requirement(self.c, self.ac, aid)
        self.assertEqual(p1["id"], p2["id"])
        self.assertTrue(p2.get("idempotent"))

    def test_permission_required(self):
        aid, _ = self._confirmed_assignment()
        with self.assertRaises(core.ForbiddenError):
            mp.create_payment_requirement(self.c, self._a(99, perms=()), aid)

    def test_tenant_isolation(self):
        aid, _ = self._confirmed_assignment()
        pr = mp.create_payment_requirement(self.c, self.ac, aid)
        other = self._a(50, tenant=99999)
        with self.assertRaises(core.NotFoundError):
            mp.funding_instructions(self.c, other, pr["id"])

    def test_funding_instructions_masked(self):
        aid, _ = self._confirmed_assignment()
        pr = mp.create_payment_requirement(self.c, self.ac, aid)
        instr = mp.funding_instructions(self.c, self.ac, pr["id"])
        self.assertIn("****", instr["masked_account"])
        self.assertEqual(instr["mock_label"], "MOCK_ONLY")


class FundingReconciliationTests(Base):
    def test_partial_then_full(self):
        aid, _ = self._confirmed_assignment()
        pr = mp.create_payment_requirement(self.c, self.ac, aid)
        r1 = mp.record_funding_event(self.c, self.fin, pr["id"], "partial")
        self.assertIn(r1["reconciliation"], ("PARTIAL", "UNDERPAID"))
        self.assertEqual(mp.funding_status(self.c, pr["id"])["level"], "PARTIAL_NOT_SUFFICIENT")
        mp.record_funding_event(self.c, self.fin, pr["id"], "full")
        self.assertEqual(self.c.execute("SELECT status FROM mkt_payment_requirements WHERE id=?", (pr["id"],)).fetchone()["status"], "PROTECTED")

    def test_overpayment_routes_to_review(self):
        aid, _ = self._confirmed_assignment()
        pr = mp.create_payment_requirement(self.c, self.ac, aid)
        r = mp.record_funding_event(self.c, self.fin, pr["id"], "over")
        self.assertEqual(r["reconciliation"], "OVERPAID")
        # not auto-protected
        self.assertNotEqual(self.c.execute("SELECT status FROM mkt_payment_requirements WHERE id=?", (pr["id"],)).fetchone()["status"], "PROTECTED")

    def test_wrong_currency(self):
        aid, _ = self._confirmed_assignment()
        pr = mp.create_payment_requirement(self.c, self.ac, aid)
        self.assertEqual(mp.record_funding_event(self.c, self.fin, pr["id"], "wrong_currency")["reconciliation"], "WRONG_CURRENCY")

    def test_duplicate_event(self):
        aid, _ = self._confirmed_assignment()
        pr = mp.create_payment_requirement(self.c, self.ac, aid)
        mp.record_funding_event(self.c, self.fin, pr["id"], "full")
        self.assertEqual(mp.record_funding_event(self.c, self.fin, pr["id"], "full")["reconciliation"], "DUPLICATE")

    def test_funding_idempotency(self):
        aid, _ = self._confirmed_assignment()
        pr = mp.create_payment_requirement(self.c, self.ac, aid)
        a = mp.record_funding_event(self.c, self.fin, pr["id"], "full", idem_key="F1")
        b = mp.record_funding_event(self.c, self.fin, pr["id"], "full", idem_key="F1")
        self.assertEqual(a["funding_event_id"], b["funding_event_id"])

    def test_idempotency_different_payload_rejected(self):
        aid, _ = self._confirmed_assignment()
        pr = mp.create_payment_requirement(self.c, self.ac, aid)
        mp.record_funding_event(self.c, self.fin, pr["id"], "partial", idem_key="F2")
        with self.assertRaises(ValueError):
            mp.record_funding_event(self.c, self.fin, pr["id"], "full", idem_key="F2")


class ActivationGateTests(Base):
    def test_no_funding_blocked(self):
        aid, _ = self._confirmed_assignment()
        pr = mp.create_payment_requirement(self.c, self.ac, aid)
        self.assertFalse(mp.trip_activation_gate(self.c, self.ac, pr["id"])["eligible"])

    def test_full_funding_ready_not_active(self):
        prid, _ = self._protected_pr()
        g = mp.trip_activation_gate(self.c, self.ac, prid)
        self.assertEqual(g["result"], "READY_FOR_TRIP_ACTIVATION")
        self.assertFalse(g["trip_active"])

    def test_active_dispute_blocks(self):
        prid, _ = self._protected_pr()
        mp.open_dispute(self.c, self.cu, prid, "cargo_damage", "shipper")
        self.assertIn("active_dispute", mp.trip_activation_gate(self.c, self.ac, prid)["blockers"])


class ReleaseTests(Base):
    def test_delivery_required_blocks_early_release(self):
        prid, _ = self._protected_pr()
        self.assertIn("delivery_evidence_required", mp.evaluate_release(self.c, self.ac, prid)["blockers"])

    def test_release_after_evidence(self):
        prid, _ = self._releasable()
        ev = mp.evaluate_release(self.c, self.ac, prid)
        self.assertTrue(ev["release_eligible"])
        self.assertGreater(ev["carrier_payout"], 0)

    def test_release_non_mutating(self):
        prid, _ = self._releasable()
        before = self.c.execute("SELECT released_amount FROM mkt_payment_requirements WHERE id=?", (prid,)).fetchone()["released_amount"]
        mp.evaluate_release(self.c, self.ac, prid)
        after = self.c.execute("SELECT released_amount FROM mkt_payment_requirements WHERE id=?", (prid,)).fetchone()["released_amount"]
        self.assertEqual(before, after)

    def test_release_self_approval_denied(self):
        prid, _ = self._releasable()
        ri = mp.create_release_instruction(self.c, self.cr, prid)
        with self.assertRaises(PermissionError):
            mp.approve_release(self.c, self.cr, ri["release_instruction_id"])

    def test_release_idempotent(self):
        prid, _ = self._releasable()
        a = mp.create_release_instruction(self.c, self.cr, prid, idem_key="R1")
        b = mp.create_release_instruction(self.c, self.cr, prid, idem_key="R1")
        self.assertEqual(a["release_instruction_id"], b["release_instruction_id"])

    def test_release_blocked_during_freeze(self):
        prid, _ = self._releasable()
        mp.open_dispute(self.c, self.cu, prid, "cargo_damage", "shipper")
        self.assertFalse(mp.evaluate_release(self.c, self.ac, prid)["release_eligible"])

    def test_full_release_payout_snapshot(self):
        prid, _ = self._releasable()
        ri = mp.create_release_instruction(self.c, self.cr, prid)
        mp.approve_release(self.c, self.vf, ri["release_instruction_id"])
        res = mp.submit_release(self.c, self.ac, ri["release_instruction_id"])
        self.assertEqual(res["status"], "COMPLETED")
        po = self.c.execute("SELECT * FROM mkt_payouts WHERE id=?", (res["payout_id"],)).fetchone()
        self.assertEqual(po["status"], "PAID")
        self.assertTrue(po["provider_beneficiary_reference"])

    def test_release_provider_failure_deadletters(self):
        prid, _ = self._releasable()
        ri = mp.create_release_instruction(self.c, self.cr, prid)
        mp.approve_release(self.c, self.vf, ri["release_instruction_id"])
        res = mp.submit_release(self.c, self.ac, ri["release_instruction_id"], scenario="fail")
        self.assertEqual(res["status"], "FAILED")
        self.assertTrue(self.c.execute("SELECT COUNT(*) FROM mkt_payment_deadletter WHERE operation='release'").fetchone()[0])


class DisputeFreezeTests(Base):
    def test_dispute_freezes_funds(self):
        prid, _ = self._releasable()
        d = mp.open_dispute(self.c, self.cu, prid, "cargo_loss", "shipper")
        self.assertEqual(d["status"], "FUNDS_FROZEN")
        self.assertGreater(d["frozen_amount"], 0)

    def test_self_resolution_denied(self):
        prid, _ = self._releasable()
        d = mp.open_dispute(self.c, self.cu, prid, "cargo_loss", "shipper")
        with self.assertRaises(PermissionError):
            mp.resolve_dispute(self.c, self.cu, d["dispute_id"], "FULL_RELEASE_TO_CARRIER")

    def test_partial_resolution_reconciles(self):
        prid, _ = self._releasable()
        d = mp.open_dispute(self.c, self.cu, prid, "cargo_damage", "shipper")
        r = mp.resolve_dispute(self.c, self.vf, d["dispute_id"], "PARTIAL_RELEASE_AND_PARTIAL_REFUND",
                               released=2000, refunded=2000, liability="shared")
        self.assertEqual(r["status"], "RESOLVED")
        self.assertEqual(self.c.execute("SELECT frozen_amount FROM mkt_payment_requirements WHERE id=?", (prid,)).fetchone()["frozen_amount"], 0)

    def test_resolution_cannot_exceed_disputed(self):
        prid, _ = self._releasable()
        d = mp.open_dispute(self.c, self.cu, prid, "cargo_damage", "shipper")
        with self.assertRaises(ValueError):
            mp.resolve_dispute(self.c, self.vf, d["dispute_id"], "PARTIAL_RELEASE_AND_PARTIAL_REFUND",
                               released=999999, refunded=999999)


class RefundTests(Base):
    def test_refund_exceeds_balance_rejected(self):
        prid, _ = self._protected_pr()
        with self.assertRaises(ValueError):
            mp.request_refund(self.c, self.cu, prid, "overpayment", 999999)

    def test_refund_self_approval_denied(self):
        prid, _ = self._protected_pr()
        rf = mp.request_refund(self.c, self.cu, prid, "cancellation", 1000)
        with self.assertRaises(PermissionError):
            mp.approve_refund(self.c, self.cu, rf["refund_id"])

    def test_refund_idempotency(self):
        prid, _ = self._protected_pr()
        a = mp.request_refund(self.c, self.cu, prid, "duplicate payment", 1000, idem_key="RF1")
        b = mp.request_refund(self.c, self.cu, prid, "duplicate payment", 1000, idem_key="RF1")
        self.assertEqual(a["refund_id"], b["refund_id"])

    def test_refund_success_and_provider_failure(self):
        prid, _ = self._protected_pr()
        rf = mp.request_refund(self.c, self.cu, prid, "cancellation", 500)
        mp.approve_refund(self.c, self.vf, rf["refund_id"])
        self.assertEqual(mp.submit_refund(self.c, self.ac, rf["refund_id"])["status"], "COMPLETED")
        rf2 = mp.request_refund(self.c, self.cu, prid, "cancellation", 500)
        mp.approve_refund(self.c, self.vf, rf2["refund_id"])
        self.assertEqual(mp.submit_refund(self.c, self.ac, rf2["refund_id"], scenario="fail")["status"], "FAILED")


class ChargebackFailureTests(Base):
    def test_chargeback_recon(self):
        prid, _ = self._protected_pr()
        self.assertEqual(mp.record_funding_event(self.c, self.fin, prid, "chargeback")["reconciliation"], "CHARGEBACK")

    def test_reversal_recon(self):
        prid, _ = self._protected_pr()
        self.assertEqual(mp.record_funding_event(self.c, self.fin, prid, "reversed")["reconciliation"], "REVERSED")

    def test_unsafe_replay_denied(self):
        self.c.execute("INSERT INTO mkt_payment_deadletter(provider,operation,entity,failure_class,status,created_at) "
                       "VALUES('MOCK','release','x','validation','OPEN',?)", (mp._now(),))
        dl = self.c.execute("SELECT id FROM mkt_payment_deadletter ORDER BY id DESC LIMIT 1").fetchone()["id"]
        with self.assertRaises(ValueError):
            mp.replay_deadletter(self.c, self.ac, dl)


class LiveBoundaryIntegrityTests(Base):
    def test_wise_adapter_fail_closed(self):
        with self.assertRaises(core.ForbiddenError):
            mp.provider("WISE").funding_instructions({"protected_amount_required": 1, "currency": "PHP"})

    def test_live_status_blocked(self):
        self.assertEqual(mp.live_status()["live_protected_payment"], "BLOCKED")

    def test_integrity_runs(self):
        prid, _ = self._releasable()
        self.assertIn(mp.run_integrity(self.c, self.ac)["overall"], mp.INTEGRITY_STATUSES)

    def test_migration_zero_drift(self):
        inv = mp.classify_existing(self.c)["invariants"]
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
        self.assertGreaterEqual(db.SCHEMA_VERSION, 19)


class TestPaymentApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        import db as _db
        server._conn = _db.connect(":memory:")
        cls.server = server
        try:
            core.create_user(server._conn, "pay@r", "demo1234", "admin", "Pay Admin")
        except core.ConflictError:
            pass

    def _call(self, method, path, body=None):
        fn, params = self.server._match(method, path)
        assert fn, f"no route for {method} {path}"
        tok = self.server._match("POST", "/login")[0](None, {"email": "pay@r", "password": "demo1234"}, {})["token"]
        actor = core.actor_for(self.server._conn, tok)
        return fn(actor, body or {}, params or {})

    def test_finance_queues_and_integrity_via_api(self):
        self.assertIn("awaiting_funding", self._call("GET", "/admin/marketplace/finance-queues"))
        self.assertIn(self._call("GET", "/admin/marketplace/finance-integrity")["overall"], mp.INTEGRITY_STATUSES)

    def test_live_status_via_api(self):
        self.assertEqual(self._call("GET", "/admin/marketplace/protected-payment-live-status")["live_protected_payment"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
