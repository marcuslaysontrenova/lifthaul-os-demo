#!/usr/bin/env python3
"""LiftHaul — Production acceptance lifecycle (Gates 3 + 4 on the REAL stack).

Runs ONE complete controlled synthetic lifecycle end to end using the canonical domain functions
(the same code the 1,185 automated tests exercise) against whatever database `DATABASE_URL` points at:
PostgreSQL in CI/production, SQLite locally. Then proves two-tenant isolation.

    Tenant A:  Shipper -> Carrier -> Vehicle -> Driver -> Lane
               -> Booking -> Validate -> Price -> Matching -> Offer -> Select -> Assignment -> Confirm
               -> Protected Payment (MOCK): FUNDING_CONFIRMED -> FUNDS_PROTECTED -> TRIP_AUTHORIZED
                  -> SERVICE_IN_PROGRESS -> DELIVERY_EVIDENCE_PENDING -> DISPUTE_WINDOW
                  -> RELEASE_ELIGIBLE -> RELEASE_APPROVED -> RELEASE_REQUESTED -> RELEASE_CONFIRMED -> SETTLED
               -> Secure Delivery recipient OTP issued
               -> Carrier settlement projection
    Tenant B:  creates its own records and attempts to read Tenant A's booking/carrier/vehicle
               -> EVERY cross-tenant attempt DENIED (404 no-leak).

Live funds stay OFF (provider = MOCK); no real money moves. Run:
    DATABASE_URL=postgresql://user:pass@host:5432/lifthaul  python scripts/go_live/pg_lifecycle.py
Exit 0 = full lifecycle + isolation verified.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import db
import core
import admin_platform as ap
import marketplace as mkt
import marketplace_onboarding as mo
import marketplace_matching as mm
import protected_payment as pp
import marketplace_trust as tr
import marketplace_trust_closure as tc
import delivery_verification as dv

_ok = _fail = 0
_backend = None


def stage(name, fn):
    global _ok, _fail
    try:
        r = fn()
        _ok += 1
        print(f"  [PASS] {name}")
        return r
    except Exception as e:
        _fail += 1
        print(f"  [FAIL] {name}  -> {type(e).__name__}: {e}")
        return None


def actor(uid, tenant_id, perms=("*",)):
    return {"id": uid, "role": "ops", "perms": set(perms), "tenant_id": tenant_id}


def onboard_carrier(c, op, vf, ac, rgo, reg):
    cid = mo.create_carrier_application(c, op, "FLEET_OPERATOR", "Verified Carrier " + reg,
                                        registration_type="SEC", registration_number=reg,
                                        operating_address="Manila", preferred_lanes=["CAVITE"])
    mo.submit_carrier(c, op, cid); mo.verify_carrier(c, vf, cid)
    for d in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION", "AUTHORITY_TO_OPERATE", "INSURANCE"):
        doc = mo.upload_document(c, op, d, "CARRIER", cid, expiry_date="2030-01-01"); mo.verify_document(c, vf, doc)
    mo.activate_carrier(c, ac, cid)
    # KYB (maker/checker; no self-verify) — a "Verified Carrier" needs verified business KYB, which
    # also gives it a non-zero Protected-Payment release risk limit
    k = tr.submit_kyb(c, op, "CARRIER", cid, "SEC", reg, "Verified Carrier " + reg)
    tr.verify_kyb(c, vf, k, "VERIFIED", "manual")
    vid = mo.register_vehicle(c, op, cid, "truck_6w", "ACC-" + reg); mo.verify_vehicle(c, vf, vid)
    for d in ("VEHICLE_REGISTRATION", "INSURANCE"):
        doc = mo.upload_document(c, op, d, "VEHICLE", vid, expiry_date="2030-01-01"); mo.verify_document(c, vf, doc)
    mo.activate_vehicle(c, ac, vid)
    did = mo.register_driver(c, op, cid, "Juan " + reg, licence_expiry="2030-01-01", authorized_categories=["truck_6w"])
    mo.verify_driver(c, vf, did); mo.activate_driver(c, ac, did)
    return cid, vid, did


def main():
    url = os.environ.get("DATABASE_URL")
    backend = "postgres" if (url and url.startswith(("postgres://", "postgresql://"))) else "sqlite"
    print(f"LiftHaul Production Acceptance Lifecycle   backend={backend}")
    print("-" * 66)
    c = db.connect(url)
    rgo = ap.get_tenant(c, "RGO")["id"]
    op, vf, ac, cu = actor(9001, rgo), actor(9002, rgo), actor(9003, rgo), actor(9004, rgo)

    # ---- Onboarding (verified carrier + vehicle + driver + shipper + lane) ----
    def _shipper():
        s = mo.create_shipper_application(c, op, "CORPORATION", "Acme Shipper", registration_type="SEC",
                                          registration_number="ACC-S1", registered_address="Makati",
                                          contract_accepted=1, privacy_accepted=1)
        mo.submit_shipper(c, op, s); mo.verify_shipper(c, vf, s)
        for d in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION"):
            doc = mo.upload_document(c, op, d, "SHIPPER", s, expiry_date="2030-01-01"); mo.verify_document(c, vf, doc)
        mo.activate_shipper(c, ac, s); return s
    sid = stage("Onboard + verify + activate SHIPPER", _shipper)
    res = stage("Onboard + verify + activate CARRIER + VEHICLE + DRIVER",
                lambda: onboard_carrier(c, op, vf, ac, rgo, "CarA"))
    cid, vid, did = res if res else (None, None, None)

    def _lane():
        lane = [l for l in mkt.list_lanes(c) if l["code"] == "MM-CAV"][0]
        mkt.assess_lane(c, op, lane["id"], verified_carriers=5, backup_capacity=1, price_model_validated=1,
                        ops_support=1, payment_capable=1, dispute_process=1, monitoring=1)
        mkt.activate_lane(c, vf, lane["id"], target="ACTIVE"); return lane["id"]
    stage("Activate LANE (governed)", _lane)

    # ---- Booking -> price -> match -> offer -> assign ----
    bk = stage("Create BOOKING", lambda: mm.create_booking(c, op, sid, "general", "METRO_MANILA", "CAVITE",
                                                            weight_kg=5000, volume_cbm=10, pickup_address="A", delivery_address="B"))
    stage("Validate booking", lambda: mm.validate_booking(c, vf, bk))
    stage("Select pricing mode", lambda: mm.select_pricing_mode(c, vf, bk))
    stage("Price booking (immutable snapshot)", lambda: mm.price_booking(c, vf, bk))
    stage("Generate candidates", lambda: mm.generate_candidates(c, ac, bk))
    stage("Broadcast to carriers", lambda: mm.create_broadcast(c, ac, bk, wave=1))
    off = stage("Carrier submits OFFER", lambda: mm.submit_offer(c, cu, bk, cid, 4800, vehicle_id=vid, driver_id=did)["offer_id"])
    stage("Evaluate offers", lambda: mm.evaluate_offers(c, ac, bk))
    stage("Select offer (SoD: not the offer's creator)", lambda: mm.select_offer(c, ac, bk, off))
    aid = stage("Create ASSIGNMENT", lambda: mm.create_assignment(c, ac, bk)["assignment_id"])
    stage("Carrier confirms assignment", lambda: mm.confirm_assignment(c, ac, aid))

    # ---- Protected Payment lifecycle (MOCK; live custody OFF) ----
    pa_maker, pa_checker = actor(9010, rgo), actor(9011, rgo)
    stage("Submit payout account (maker)",
          lambda: tc.submit_payout_account(c, pa_maker, cid, "Juan Cruz", "Verified Carrier CarA", "prov_ref", "1234567890"))
    pa = c.execute("SELECT id FROM mkt_payout_accounts WHERE carrier_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
    pa = pa["id"] if pa else None
    stage("Approve payout account (checker; maker/checker SoD)",
          lambda: tc.approve_payout_account(c, pa_checker, pa, beneficiary_verified=True, mfa_ok=True))
    tx = stage("Create Protected Payment transaction (provider=MOCK)",
               lambda: pp.create_transaction(c, op, booking_id=bk, carrier_id=cid, contract_amount=4800,
                                             protected_amount=4800, platform_fee=480, carrier_payable=4320,
                                             job_id=bk, provider_name="MOCK"))
    seq = ["PAYMENT_INTENT_CREATED", "AWAITING_CUSTOMER_FUNDS", "CUSTOMER_FUNDED", "FUNDING_CONFIRMED",
           "FUNDS_PROTECTED", "TRIP_AUTHORIZED", "SERVICE_IN_PROGRESS", "DELIVERY_EVIDENCE_PENDING",
           "DISPUTE_WINDOW", "RELEASE_ELIGIBLE", "RELEASE_APPROVAL_PENDING", "RELEASE_APPROVED",
           "RELEASE_REQUESTED", "RELEASE_CONFIRMED", "SETTLED"]
    def _run_states():
        st = None
        for s in seq:
            kw = {}
            if s in ("RELEASE_APPROVAL_PENDING", "RELEASE_APPROVED", "RELEASE_REQUESTED", "RELEASE_CONFIRMED"):
                kw = {"payout_account_id": pa, "job_value": 4800}
            st = pp.transition(c, op, tx, s, **kw)
        return st
    stage("Protected Payment: FUNDING -> FUNDS_PROTECTED -> ... -> SETTLED (MOCK, no live custody)", _run_states)
    settled = c.execute("SELECT state FROM mkt_protected_tx WHERE id=?", (tx,)).fetchone()
    stage("Settlement reached (state=SETTLED)", lambda: (_ for _ in ()).throw(AssertionError(settled["state"]))
          if settled["state"] != "SETTLED" else True)
    stage("Carrier settlement projection (no customer credentials)",
          lambda: pp.carrier_settlement(c, op, tx))

    # ---- Secure delivery recipient verification (OTP path; driver never sees the code) ----
    stage("Set delivery recipient", lambda: dv.set_recipient(c, op, bk, "Maria Santos", mobile="09171234567"))
    stage("Issue recipient OTP (hashed, single-use)", lambda: dv.issue_otp(c, op, bk))
    stage("Delivery-verification status readable", lambda: dv.status(c, bk))

    # ---- Two-tenant isolation proof ----
    print("-" * 66)
    print("  Two-tenant isolation:")
    tB = stage("Create Tenant B (synthetic company)", lambda: ap.create_tenant(c, "ACCB", "Synthetic Company B"))
    opB = actor(9500, tB)
    stage("Tenant B creates its own carrier", lambda: onboard_carrier(c, opB, actor(9501, tB), actor(9502, tB), tB, "CarB"))

    def _denied(label, fn):
        try:
            fn()
            raise AssertionError(f"CROSS-TENANT LEAK: {label} was readable by Tenant B")
        except (core.NotFoundError, core.ForbiddenError):
            return True
    stage("Tenant B CANNOT read Tenant A booking (404 no-leak)",
          lambda: _denied("booking", lambda: mm._guarded(c, opB, "mkt_bookings", bk)))
    stage("Tenant B CANNOT read Tenant A carrier (404 no-leak)",
          lambda: _denied("carrier", lambda: mo._guarded(c, opB, "mkt_carriers", cid)))
    stage("Tenant B CANNOT read Tenant A vehicle (404 no-leak)",
          lambda: _denied("vehicle", lambda: mo._guarded(c, opB, "mkt_vehicles", vid)))
    stage("Tenant B CANNOT read Tenant A protected payment (404 no-leak)",
          lambda: _denied("protected_tx", lambda: pp._tx(c, opB, tx)))

    print("-" * 66)
    print(f"ACCEPTANCE RESULT ({backend}): {_ok} passed, {_fail} failed")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
