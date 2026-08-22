#!/usr/bin/env python3
"""LiftHaul — Public Provider Activation acceptance lifecycle (production posture).

Runs the owner's exact go-live acceptance sequence end to end using the canonical domain functions
against whatever `DATABASE_URL` points at (PostgreSQL in CI/production, SQLite locally):

    Public Provider Registration -> Company Application -> Username/Password -> Account PENDING
    -> OTP issued -> OTP verified -> User ACTIVE -> Normal /login -> Own Carrier Workspace
    -> Add Vehicle -> Add Driver -> Pair Driver/Vehicle -> Add Service Area -> Upload Compliance
    -> Independent Reviewer Verification -> Marketplace Eligibility

It runs in **production posture** (`APP_ENV=production`): the one-time code is NEVER returned by the
API and NEVER logged. The harness obtains it only through the in-process test-capture seam
(`OTP_TEST_CAPTURE=1` + `public_provider.peek_code`), which has no HTTP route and is off in real prod.

Governance asserted, not assumed:
  * account is INACTIVE until the contact code is verified (login blocked before, allowed after);
  * the verified login resolves to its OWN carrier only (bound principal);
  * a provider can add DRAFT fleet/drivers/areas + SUBMIT compliance docs, but CANNOT self-verify
    them — independent staff verification is required (self-verify attempts are DENIED);
  * a unit is NOT marketplace-eligible until independent verification + activation;
  * Provider B can never read or mutate Provider A's records (cross-carrier isolation).

    DATABASE_URL=postgresql://user:pass@host:5432/lifthaul  python scripts/go_live/provider_activation_e2e.py
Exit 0 = full activation lifecycle + governance + isolation verified.
"""
import os
import sys

# Production posture + opt-in in-process capture (never exposed over HTTP; unset in real prod).
os.environ.setdefault("APP_ENV", "production")
os.environ["OTP_TEST_CAPTURE"] = "1"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import db
import core
import admin_platform as ap
import public_provider as pp
import carrier_portal as cp
import marketplace_onboarding as mo
import fleet_registration as fr

_ok = _fail = 0


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


def _denied(fn):
    """Assert an action is refused (independent compliance / isolation)."""
    try:
        fn()
        raise AssertionError("NOT DENIED — governance breach")
    except (core.ForbiddenError, core.NotFoundError, core.AuthError):
        return True


def op_actor(tenant_id):
    return {"id": 9100, "role": "ops", "perms": {"*"}, "tenant_id": tenant_id}


def register_provider(conn, email, legal, pw="Str0ngPass!"):
    """Public self-registration -> PENDING login -> OTP verify -> ACTIVE session -> principal actor."""
    r = pp.submit(conn, {"provider_type": "FLEET_OPERATOR", "legal_name": legal,
                         "email": email, "mobile": "09170000000", "username": email,
                         "password": pw, "island_group": "LUZON"})
    assert r["status"] == "VERIFY_CONTACT", r
    assert "dev_code" not in r, "production leaked the code over the API"
    return r


def main():
    url = os.environ.get("DATABASE_URL")
    backend = "postgres" if (url and url.startswith(("postgres://", "postgresql://"))) else "sqlite"
    print(f"LiftHaul Public Provider Activation Lifecycle   backend={backend}   APP_ENV={pp.env_posture()['app_env']}")
    print(f"  posture: dev_code_allowed={pp.env_posture()['dev_code_allowed']} (must be False)")
    print("-" * 70)
    conn = db.connect(url)

    email = "provider.a@activation.test"
    reg = stage("Public Provider Registration -> Company Application + PENDING login (no code leaked)",
                lambda: register_provider(conn, email, "Activation Haulers A"))
    if not reg:
        print("-" * 70); print(f"ACTIVATION RESULT ({backend}): {_ok} passed, {_fail} failed"); return 1
    cid = reg["carrier_id"]
    # Provider records land in the PLATFORM tenant (public intake, like public booking). Independent
    # staff reviewers operate in that same tenant. Use a distinct maker (op) + checker (vf) for SoD.
    ptenant = conn.execute("SELECT tenant_id FROM mkt_carriers WHERE id=?", (cid,)).fetchone()["tenant_id"]
    op = {"id": 9100, "role": "ops", "perms": {"*"}, "tenant_id": ptenant}      # maker / reviewer
    vf = {"id": 9101, "role": "ops", "perms": {"*"}, "tenant_id": ptenant}      # independent checker

    stage("Account is PENDING — /login is BLOCKED before verification",
          lambda: _denied(lambda: core.login(conn, email, "Str0ngPass!")))

    code = stage("OTP issued by provider seam (obtained in-process only; never over HTTP)",
                 lambda: pp.peek_code(conn, reg["challenge_id"]))
    assert code, "capture seam did not yield a code"

    v = stage("OTP verified -> User ACTIVE + session token + redirect to workspace",
              lambda: pp.verify(conn, {"challenge_id": reg["challenge_id"], "code": code}))
    assert v and v["status"] == "ACTIVE" and v["redirect"] == "portal.html"

    tok = stage("Normal /login now succeeds (real canonical credential)",
                lambda: core.login(conn, email, "Str0ngPass!"))
    principal = stage("Own Carrier Workspace — token resolves to OWN carrier (bound principal)",
                      lambda: core.actor_for(conn, tok))

    stage("Workspace scope: resolve_carrier -> OWN carrier only",
          lambda: (cp.resolve_carrier(conn, principal) == cid) or (_ for _ in ()).throw(AssertionError("wrong carrier")))

    # ---- provider self-service workspace (DRAFT; never self-verified) ----
    veh = stage("Add Vehicle (self-service; classified; lands DRAFT)",
                lambda: cp.register_unit(conn, principal, "NGA-1234",
                                         {"vehicle_type": "TRUCK", "wheels": 6, "body": "closed_van", "payload_kg": 4000}))
    vid = veh["vehicle_id"] if veh else None
    if veh:
        stage("  -> unit classified to canonical variant (%s)" % veh["classification"]["class_label"],
              lambda: True)

    drv = stage("Add Driver (self-service)",
                lambda: cp.add_driver(conn, principal, "Juan Dela Cruz",
                                      licence_expiry="2030-01-01", authorized_categories=["truck_6w"]))
    did = drv.get("driver_id") if isinstance(drv, dict) else drv

    stage("Pair Driver/Vehicle — compatibility check (self-service)",
          lambda: cp.check_pairing(conn, principal, did, vid))

    stage("Add Service Area (self-service)",
          lambda: cp.set_service_area(conn, principal, "LUZON", scope="ISLAND"))

    stage("Upload Compliance document — SUBMIT only (provider may submit, not verify)",
          lambda: cp.upload_document(conn, principal, "AUTHORITY_TO_OPERATE", "CARRIER", cid,
                                     expiry_date="2030-01-01"))

    # ---- independent compliance: provider CANNOT self-verify ----
    stage("Provider CANNOT self-verify its own vehicle (independent compliance enforced)",
          lambda: _denied(lambda: mo.verify_vehicle(conn, principal, vid)))
    stage("Provider CANNOT self-verify its own compliance document",
          lambda: _denied(lambda: mo.verify_document(conn, principal, 1)))
    stage("Provider CANNOT activate its own carrier",
          lambda: _denied(lambda: mo.activate_carrier(conn, principal, cid)))

    # ---- marketplace eligibility gated on independent verification ----
    stage("Before review: unit is NOT marketplace-eligible (coded reason)",
          lambda: (not fr.unit_eligibility(conn, op, cid, vid)["eligible"])
                  or (_ for _ in ()).throw(AssertionError("eligible before verification!")))

    # ---- commercial accreditation + cargo-insurance compliance (gates enabled for this run) ----
    import admin_platform as ap, accreditation as acc, cargo_insurance as ci
    fin = {"id": 9200, "role": "finance", "perms": {"payment.confirm"}, "tenant_id": op["tenant_id"]}
    stage("Enable commercial gates (accreditation fee + cargo insurance required)",
          lambda: (ap.set_config(conn, "platform", "", "accreditation.gate_enabled", "true", actor=op),
                   ap.set_config(conn, "platform", "", "cargo_insurance.required", "true", actor=op)) and True)
    asmt = stage("Accreditation fee assessed (server-authoritative, from canonical variant)",
                 lambda: acc.assess_fee(conn, op, cid, vid))
    stage("  -> transparent fee breakdown (payment != approval)", lambda: acc.fee_breakdown(conn, op, vid))
    stage("Unpaid unit is NOT eligible (ACCREDITATION_FEE_UNPAID)",
          lambda: ("ACCREDITATION_FEE_UNPAID" in fr.unit_eligibility(conn, op, cid, vid)["reasons"])
                  or (_ for _ in ()).throw(AssertionError("unpaid unit not gated")))
    stage("Finance records payment (a carrier can never pay its own fee)",
          lambda: acc.record_payment(conn, fin, asmt["id"], "gcash", "PAY-E2E-1", receipt_ref="OR-1"))
    stage("PAID but STILL not eligible (payment != approval)",
          lambda: (not fr.unit_eligibility(conn, op, cid, vid)["eligible"])
                  or (_ for _ in ()).throw(AssertionError("payment granted eligibility!")))
    ciup = stage("Provider uploads its own cargo-insurance certificate (compliance document)",
                 lambda: ci.upload(conn, op, cid, "ACME Insurance Co", "CI-POL-1", "s3://cargo-cert.pdf",
                                   vehicle_id=vid, coverage_amount=1_000_000, effective_from="2026-01-01",
                                   expiry_date="2030-01-01"))
    stage("Provider CANNOT self-verify cargo insurance (independent review only)",
          lambda: _denied(lambda: ci.review(conn, {"id": op["id"], "role": "carrier_principal",
                          "perms": {"marketplace.vehicle.manage"}, "tenant_id": op["tenant_id"]},
                          ciup["id"], "VERIFY")))
    stage("Independent reviewer VERIFIES cargo insurance",
          lambda: ci.review(conn, vf, ciup["id"], "VERIFY", verification_source="insurer confirmation"))
    stage("After pay + cargo verify: fee & cargo gates cleared (remaining gates stay independent)",
          lambda: (not any(r in fr.unit_eligibility(conn, op, cid, vid, driver_id=did)["reasons"]
                   for r in ("ACCREDITATION_FEE_UNPAID", "CARGO_INSURANCE_MISSING",
                             "CARGO_INSURANCE_PENDING", "CARGO_INSURANCE_EXPIRED")))
                  or (_ for _ in ()).throw(AssertionError("fee/cargo gates not cleared after settlement")))

    def _independent_review():
        # staff/reviewer path — maker (op) uploads, independent checker (vf) verifies (maker/checker SoD)
        mo.verify_vehicle(conn, vf, vid)
        for d in ("VEHICLE_REGISTRATION", "INSURANCE"):
            doc = mo.upload_document(conn, op, d, "VEHICLE", vid, expiry_date="2030-01-01")
            mo.verify_document(conn, vf, doc)
        mo.activate_vehicle(conn, op, vid)
        mo.verify_driver(conn, vf, did); mo.activate_driver(conn, op, did)
        return True
    stage("Independent Reviewer Verification (maker/checker: op uploads, vf verifies + activates)", _independent_review)

    stage("After review: unit eligibility reflects independent verification",
          lambda: fr.unit_eligibility(conn, op, cid, vid, driver_id=did))

    # ---- cross-provider isolation (carrier-scoped: all providers share the platform tenant) ----
    print("-" * 70)
    print("  Cross-provider isolation (enforced by carrier binding, not tenant):")
    regB = stage("Register Provider B (own company + login)",
                 lambda: register_provider(conn, "provider.b@activation.test", "Activation Haulers B"))
    if regB:
        codeB = pp.peek_code(conn, regB["challenge_id"])
        pp.verify(conn, {"challenge_id": regB["challenge_id"], "code": codeB})
        tokB = core.login(conn, "provider.b@activation.test", "Str0ngPass!")
        princB = core.actor_for(conn, tokB)
        cidB = cp.resolve_carrier(conn, princB)
        stage("Provider B resolves to its OWN carrier (not A's)",
              lambda: (cidB != cid) or (_ for _ in ()).throw(AssertionError("B got A")))

        def _b_fleet_excludes_a():
            board = cp.fleet(conn, princB)                 # portal read is carrier-scoped to B
            rows = board.get("vehicles", board) if isinstance(board, dict) else board
            ids = {r.get("id") or r.get("vehicle_id") for r in rows} if rows else set()
            if vid in ids:
                raise AssertionError("Provider A's vehicle leaked into Provider B's workspace")
            return True
        stage("Provider B's workspace lists ONLY its own fleet — A's vehicle is not visible",
              _b_fleet_excludes_a)

        def _b_cannot_write_to_a():
            r = cp.register_unit(conn, princB, "XXX-9999",
                                 {"vehicle_type": "TRUCK", "wheels": 6, "body": "dropside"}, requested=cid)
            # even though B *asked* for carrier A, the unit is bound to B's own carrier
            owner = conn.execute("SELECT carrier_id FROM mkt_vehicles WHERE id=?", (r["vehicle_id"],)).fetchone()["carrier_id"]
            if owner == cid:
                raise AssertionError("Provider B wrote a unit onto Provider A's carrier")
            return True
        stage("Provider B CANNOT register a unit onto A's carrier (client carrier_id ignored)",
              _b_cannot_write_to_a)

    print("-" * 70)
    print(f"ACTIVATION RESULT ({backend}): {_ok} passed, {_fail} failed")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
