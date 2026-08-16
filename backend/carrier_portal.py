"""Carrier / Fleet Owner Portal — secure self-service surface over the EXISTING carrier ecosystem.

This module does NOT introduce a parallel carrier, vehicle, driver, compliance, payment, trust or
marketplace domain. It is a thin, governed *access + aggregation* layer that:

  1. Binds a portal login to exactly one carrier (`carrier_principals`) — identity-derived, never
     client-supplied. A principal can only ever act on its own carrier_id.
  2. Reads the canonical domains (onboarding, KYB/trust, LTFRB authority, compliance documents,
     protected payment, disputes/claims, goods protection, trips/POD, notifications) and composes a
     carrier-facing operating picture, including the operational-eligibility summary panel.
  3. Exposes the carrier-ALLOWED self-service writes (register vehicle/driver, upload a document,
     submit a payout account, submit/withdraw an offer, accept/decline an assignment, submit POD)
     by delegating to the existing governed domain functions.

Governance invariant — a carrier can manage its own fleet but can NEVER self-verify regulated
compliance. The portal never calls verify_carrier / verify_vehicle / verify_driver / verify_document /
approve_payout_account / verify_kyb / verify_authority / activate_* / override_compliance. Those
require reviewer permissions the `carrier_principal` role does not hold, AND the underlying domain
functions additionally reject self-verification (`created_by == actor`). Uploading a CPC yields
SUBMITTED; only a Compliance Reviewer can move it to VERIFIED.

Reuse mechanism — `_svc(actor, *perms)` returns a shallow copy of the carrier's own actor with the
MINIMAL operational permission needed for one specific self-service call added to its permission set.
This preserves the carrier's identity for audit (`created_by`, actor id) and tenant for isolation,
while never granting any verify/approve/activate/override permission. The elevation set is a closed
allow-list defined in `_SELF_SERVICE`.
"""
from __future__ import annotations

import datetime

import core
import tenant
import marketplace_onboarding as ob
import marketplace_trust as tr
import marketplace_trust_closure as tc
import marketplace_matching as mm
import marketplace_trips as tp
import protected_payment as pp
import goods_protection as gp
import ltfrb
import notifications_engine as ne
import driver_reassignment as dr
import fleet_registration as fr


# --------------------------------------------------------------------------- #
# Portal permissions (distinct from operational marketplace.* perms). The
# carrier_principal role holds ONLY these — so a carrier token hitting an
# /admin/* route fails core.require and gets 403. No verify/approve/activate.
# --------------------------------------------------------------------------- #
PORTAL_PERMISSIONS = [
    "carrier.portal.view",              # read the whole portal (own carrier only)
    "carrier.portal.fleet.manage",      # register vehicle/driver, set maintenance, pairing check
    "carrier.portal.compliance.submit", # upload document (-> SUBMITTED), submit payout account
    "carrier.portal.offers.manage",     # submit/withdraw offer, accept/decline assignment
    "carrier.portal.trips.execute",     # submit proof of delivery / evidence
    "carrier.portal.settings.manage",   # bind/unbind own operators, account settings
]

# The closed allow-list of operational permissions the portal is permitted to elevate into, per
# self-service action. NOTHING here grants verification, activation, approval or override.
_SELF_SERVICE = {
    "add_vehicle":      ("marketplace.vehicle.manage",),
    "add_driver":       ("marketplace.driver.manage",),
    "set_vehicle_hold": ("marketplace.vehicle.manage",),
    "upload_document":  ("marketplace.compliance.manage",),
    "submit_payout":    ("marketplace.payout.manage",),
    "submit_offer":     ("marketplace.offer.create",),
    "withdraw_offer":   ("marketplace.offer.manage",),
    "respond_assignment": ("marketplace.assignment.confirm",),
    "submit_pod":       ("marketplace.pod.submit",),
    "open_reassignment":   ("marketplace.reassignment.open",),
    "propose_substitute":  ("marketplace.reassignment.substitute", "marketplace.assignment.confirm"),
    "register_unit":       ("marketplace.vehicle.manage",),
    "fleet_view":          ("marketplace.fleet.view",),
    "fleet_manage":        ("marketplace.fleet.manage",),
}

# Permissions the portal must NEVER elevate into (defence-in-depth: a hard assertion, so a future
# edit that wires a verification path through the portal fails loudly in tests).
_FORBIDDEN_ELEVATION = {
    "marketplace.carrier.verify", "marketplace.carrier.activate",
    "marketplace.vehicle.verify", "marketplace.vehicle.activate",
    "marketplace.driver.verify", "marketplace.driver.activate",
    "marketplace.compliance.verify", "marketplace.compliance.override",
    "marketplace.payout.approve", "marketplace.kyb.manage",
    "marketplace.ltfrb.manage",
}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _today():
    return datetime.date.today().isoformat()


def _svc(actor, *perms):
    """Minimal, auditable elevation: the carrier's own actor + exactly the operational permission(s)
    needed for ONE self-service call. Never grants a verify/approve/activate/override permission."""
    for p in perms:
        if p in _FORBIDDEN_ELEVATION:
            raise core.ForbiddenError(f"portal may never elevate into '{p}'")
    base = set(actor.get("perms") or core.PERMISSIONS.get(actor.get("role"), set()))
    out = dict(actor)
    out["perms"] = base | set(perms)
    return out


# --------------------------------------------------------------------------- #
# Schema — the ONLY new table: the principal binding (access concept, not a domain)
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS carrier_principals(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  user_id INTEGER NOT NULL,
  carrier_id INTEGER NOT NULL,
  portal_role TEXT NOT NULL DEFAULT 'CARRIER_OWNER',
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_by INTEGER, created_at TEXT,
  revoked_by INTEGER, revoked_at TEXT,
  UNIQUE(user_id, carrier_id));
"""


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    return  # no default bindings — carriers are bound explicitly by an operator


# --------------------------------------------------------------------------- #
# Principal binding + resolver (identity-derived scoping)
# --------------------------------------------------------------------------- #
def bind_principal(conn, actor, user_id, carrier_id, portal_role="CARRIER_OWNER"):
    """Operator action: link a login to a carrier so it can self-serve. Requires the same authority
    as managing a carrier application. A carrier cannot bind itself to another carrier."""
    core.require(actor, "marketplace.carrier.application.manage")
    ob._guarded(conn, actor, "mkt_carriers", carrier_id)   # 404 no-leak cross-tenant
    existing = conn.execute("SELECT id,status FROM carrier_principals WHERE user_id=? AND carrier_id=?",
                            (user_id, carrier_id)).fetchone()
    if existing:
        conn.execute("UPDATE carrier_principals SET status='ACTIVE',portal_role=?,revoked_by=NULL,"
                     "revoked_at=NULL WHERE id=?", (portal_role, existing["id"]))
        pid = existing["id"]
    else:
        cur = conn.execute("INSERT INTO carrier_principals(user_id,carrier_id,portal_role,status,"
                           "created_by,created_at) VALUES(?,?,?,'ACTIVE',?,?)",
                           (user_id, carrier_id, portal_role, actor["id"], _now()))
        pid = cur.lastrowid
        tenant.stamp(conn, actor, "carrier_principals", pid)
    core.audit(conn, actor, "CARRIER_PRINCIPAL_BOUND", "carrier_principals", pid, None,
               {"user_id": user_id, "carrier_id": carrier_id, "portal_role": portal_role})
    conn.commit()
    return pid


def revoke_principal(conn, actor, principal_id, reason=None):
    core.require(actor, "marketplace.carrier.application.manage")
    row = conn.execute("SELECT * FROM carrier_principals WHERE id=?", (principal_id,)).fetchone()
    if not row:
        raise core.NotFoundError("principal not found")
    tenant.guard(actor, row)
    conn.execute("UPDATE carrier_principals SET status='REVOKED',revoked_by=?,revoked_at=? WHERE id=?",
                 (actor["id"], _now(), principal_id))
    core.audit(conn, actor, "CARRIER_PRINCIPAL_REVOKED", "carrier_principals", principal_id, None,
               {"reason": reason})
    conn.commit()
    return {"status": "REVOKED"}


def _binding(conn, actor):
    return conn.execute("SELECT * FROM carrier_principals WHERE user_id=? AND status='ACTIVE' "
                        "ORDER BY id DESC LIMIT 1", (actor["id"],)).fetchone()


def resolve_carrier(conn, actor, requested=None, write=False):
    """Return the carrier_id this actor may act on.

      * A bound carrier principal always resolves to its OWN carrier_id; any client-supplied
        `requested` value is ignored (cannot be used to reach another carrier).
      * For READS only, an operator holding `marketplace.carrier.application.view` may pass an
        explicit `requested` carrier_id (support view). Writes require a principal binding.
    """
    b = _binding(conn, actor)
    if b:
        return b["carrier_id"]
    if not write and requested is not None and core.can(actor, "marketplace.carrier.application.view"):
        ob._guarded(conn, actor, "mkt_carriers", int(requested))   # tenant-guarded
        return int(requested)
    raise core.ForbiddenError("no active carrier binding for this user")


def is_principal(conn, actor):
    return _binding(conn, actor) is not None


# --------------------------------------------------------------------------- #
# READ — operational picture (composes existing domains; no new state)
# --------------------------------------------------------------------------- #
def _carrier(conn, carrier_id):
    r = conn.execute("SELECT * FROM mkt_carriers WHERE id=?", (carrier_id,)).fetchone()
    if not r:
        raise core.NotFoundError("carrier not found")
    return dict(r)


def _days_to(date_str, as_of=None):
    if not date_str:
        return None
    try:
        return (datetime.date.fromisoformat(str(date_str)[:10]) -
                datetime.date.fromisoformat((as_of or _today())[:10])).days
    except Exception:
        return None


def profile(conn, actor, requested=None):
    core.require(actor, "carrier.portal.view")
    cid = resolve_carrier(conn, actor, requested)
    c = _carrier(conn, cid)
    kyb = tr.carrier_kyb_status(conn, cid)
    return {
        "carrier_id": cid,
        "legal_name": c["legal_name"], "trade_name": c.get("trade_name"),
        "carrier_type": c["carrier_type"], "status": c["status"],
        "operating_address": c.get("operating_address"),
        "registration_type": c.get("registration_type"),
        "registration_number": c.get("registration_number"),
        "kyb_status": kyb,
        "service_areas": ob._pj(c.get("service_areas"), []),
        "vehicle_categories": ob._pj(c.get("vehicle_categories"), []),
    }


def compliance(conn, actor, requested=None, expiring_days=30):
    """Company-level regulatory + document status, plus an expiry watch. Read-only."""
    core.require(actor, "carrier.portal.view")
    cid = resolve_carrier(conn, actor, requested)
    docs = ob.list_documents(conn, _svc(actor, "marketplace.compliance.view"),
                             subject_type="CARRIER", subject_id=cid)
    # LTFRB / CPC
    gate = ltfrb.carrier_authority_gate(conn, cid)
    auth = ltfrb._active_authority(conn, cid)
    cpc = None
    if auth:
        cpc = {"cpc_number": auth["cpc_number"], "status": auth["status"],
               "expiry_date": auth["expiry_date"], "days_to_expiry": _days_to(auth["expiry_date"])}
    # expiry watch across all carrier-subject documents
    watch = []
    for d in docs:
        dtx = _days_to(d.get("expiry_date"))
        if dtx is not None and dtx <= expiring_days:
            watch.append({"document_type": d["document_type"], "status": d["status"],
                          "expiry_date": d["expiry_date"], "days_to_expiry": dtx})
    return {
        "carrier_id": cid,
        "kyb_status": tr.carrier_kyb_status(conn, cid),
        "ltfrb": {"ok": gate["ok"], "reasons": gate["reasons"], "authority": cpc},
        "documents": [{"id": d["id"], "document_type": d["document_type"], "status": d["status"],
                       "issuing_authority": d.get("issuing_authority"),
                       "expiry_date": d.get("expiry_date"), "days_to_expiry": _days_to(d.get("expiry_date"))}
                      for d in docs],
        "expiring_soon": sorted(watch, key=lambda x: x["days_to_expiry"]),
    }


def _vehicle_eligibility(conn, v):
    """Deterministic per-vehicle eligibility using the EXISTING compliance + legality gates."""
    reasons = []
    status = v["status"]
    if status != ob.VEHICLE_ELIGIBLE_STATUS:
        reasons.append(f"status_{status.lower()}")
    ev = ob.evaluate_compliance(conn, "VEHICLE", v["id"])
    reasons += list(ev["blockers"])
    lg = tc.vehicle_legality_gate(conn, v["id"])
    if not lg.get("ok", True):
        reasons += ["legality:" + r for r in lg.get("reasons", [])]
    return {"eligible": not reasons, "reasons": reasons, "status": status}


def fleet(conn, actor, requested=None):
    core.require(actor, "carrier.portal.view")
    cid = resolve_carrier(conn, actor, requested)
    vs = ob.list_vehicles(conn, _svc(actor, "marketplace.vehicle.view"), carrier_id=cid)
    out = []
    for v in vs:
        el = _vehicle_eligibility(conn, v)
        out.append({"id": v["id"], "plate_number": v["plate_number"], "category_code": v["category_code"],
                    "status": v["status"], "payload_kg": v.get("payload_kg"),
                    "eligible": el["eligible"], "reasons": el["reasons"]})
    return {"carrier_id": cid, "vehicles": out}


def _driver_eligibility(conn, d):
    reasons = []
    status = d["status"]
    if status != "ACTIVE":
        reasons.append(f"status_{status.lower()}")
    gate = tc.driver_assignment_gate(conn, d["id"])
    if not gate["ok"]:
        reasons += gate["reasons"]
    dtx = _days_to(d.get("licence_expiry"))
    license_expiring = dtx is not None and 0 <= dtx <= 30
    ev = ob.evaluate_compliance(conn, "DRIVER", d["id"])
    reasons += list(ev["blockers"])
    return {"eligible": not reasons, "reasons": sorted(set(reasons)),
            "license_expiring": license_expiring, "days_to_licence_expiry": dtx}


def drivers(conn, actor, requested=None):
    core.require(actor, "carrier.portal.view")
    cid = resolve_carrier(conn, actor, requested)
    ds = ob.list_drivers(conn, _svc(actor, "marketplace.driver.view"), carrier_id=cid)
    out = []
    for d in ds:
        el = _driver_eligibility(conn, d)
        out.append({"id": d["id"], "full_name": d["full_name"], "status": d["status"],
                    "licence_class": d.get("licence_class"), "licence_expiry": d.get("licence_expiry"),
                    "eligible": el["eligible"], "reasons": el["reasons"],
                    "license_expiring": el["license_expiring"]})
    return {"carrier_id": cid, "drivers": out}


def overview(conn, actor, requested=None):
    """The carrier operating dashboard — the eligibility summary panel. A carrier can immediately see
    why a company, vehicle or driver cannot accept work."""
    core.require(actor, "carrier.portal.view")
    cid = resolve_carrier(conn, actor, requested)
    c = _carrier(conn, cid)

    # COMPANY
    kyb = tr.carrier_kyb_status(conn, cid)
    permit = ob.evaluate_compliance(conn, "CARRIER", cid)
    ltfrb_gate = ltfrb.carrier_authority_gate(conn, cid)
    elig = tr.assess_eligibility(conn, None, cid)
    company = {
        "kyb_verified": kyb in ("VERIFIED", "VERIFIED_WITH_CONDITION"),
        "kyb_status": kyb,
        "business_permit_valid": not permit["blockers"],
        "business_permit_blockers": permit["blockers"],
        "ltfrb_authority_valid": ltfrb_gate["ok"],
        "ltfrb_reasons": ltfrb_gate["reasons"],
    }

    # FLEET
    vs = ob.list_vehicles(conn, _svc(actor, "marketplace.vehicle.view"), carrier_id=cid)
    fleet_eligible = maint = reg_expired = 0
    for v in vs:
        el = _vehicle_eligibility(conn, v)
        if el["eligible"]:
            fleet_eligible += 1
        if v["status"] == "MAINTENANCE":
            maint += 1
        if v["status"] == "EXPIRED" or any("expired" in r.lower() for r in el["reasons"]):
            reg_expired += 1

    # DRIVERS
    ds = ob.list_drivers(conn, _svc(actor, "marketplace.driver.view"), carrier_id=cid)
    drv_eligible = lic_expiring = qual_missing = 0
    for d in ds:
        el = _driver_eligibility(conn, d)
        if el["eligible"]:
            drv_eligible += 1
        if el["license_expiring"]:
            lic_expiring += 1
        if any("qualification" in r.lower() for r in el["reasons"]):
            qual_missing += 1

    # MARKETPLACE STATUS — the hard gate result
    marketplace_active = bool(elig["eligible"])
    reasons = list(elig["hard_reasons"])
    return {
        "carrier_id": cid, "legal_name": c["legal_name"], "status": c["status"],
        "company": company,
        "fleet": {"total": len(vs), "eligible": fleet_eligible,
                  "maintenance_hold": maint, "registration_expired": reg_expired},
        "drivers": {"total": len(ds), "eligible": drv_eligible,
                    "license_expiring": lic_expiring, "qualification_missing": qual_missing},
        "marketplace_status": "ACTIVE" if marketplace_active else "BLOCKED",
        "marketplace_reasons": reasons,
        "trust_score": elig.get("trust_score"),
    }


# --------------------------------------------------------------------------- #
# READ — marketplace work, trips, finance, cases, notifications
# --------------------------------------------------------------------------- #
def invitations(conn, actor, requested=None):
    """Broadcasts/offers relevant to this carrier + the carrier's own submitted offers."""
    core.require(actor, "carrier.portal.view")
    cid = resolve_carrier(conn, actor, requested)
    offers = [o for o in mm.list_offers(conn, _svc(actor, "marketplace.offer.view"))
              if o.get("carrier_id") == cid]
    return {"carrier_id": cid, "offers": offers}


def assignments(conn, actor, requested=None):
    core.require(actor, "carrier.portal.view")
    cid = resolve_carrier(conn, actor, requested)
    rows = [a for a in mm.list_assignments(conn, _svc(actor, "marketplace.assignment.view"))
            if a.get("carrier_id") == cid]
    return {"carrier_id": cid, "assignments": rows}


def trips(conn, actor, requested=None):
    core.require(actor, "carrier.portal.view")
    cid = resolve_carrier(conn, actor, requested)
    allt = tp.list_trips(conn, _svc(actor, "marketplace.trip.view"))
    mine = []
    for t in allt:
        a = conn.execute("SELECT carrier_id FROM mkt_assignments WHERE id=?", (t.get("assignment_id"),)).fetchone()
        if a and a["carrier_id"] == cid:
            mine.append(t)
    return {"carrier_id": cid, "trips": mine}


def finance(conn, actor, requested=None):
    """Earnings + Protected Payment status + payout-account status. Carrier projection only —
    never customer payment credentials or competing-carrier data."""
    core.require(actor, "carrier.portal.view")
    cid = resolve_carrier(conn, actor, requested)
    txs = conn.execute("SELECT id FROM mkt_protected_tx WHERE carrier_id=? ORDER BY id DESC", (cid,)).fetchall()
    settlements = []
    earned = held = released = 0.0
    for r in txs:
        s = pp.carrier_settlement(conn, _svc(actor, "marketplace.payment.view"), r["id"])
        settlements.append(s)
        released += s.get("released_amount") or 0
        held += s.get("held_amount") or 0
        earned += s.get("carrier_payable") or 0
    payout = conn.execute(
        "SELECT id,beneficiary_name,entity_name,account_masked,status,created_at FROM mkt_payout_accounts "
        "WHERE carrier_id=? ORDER BY id DESC", (cid,)).fetchall()
    return {
        "carrier_id": cid,
        "totals": {"earned": round(earned, 2), "released": round(released, 2), "held": round(held, 2)},
        "settlements": settlements,
        "payout_accounts": [dict(p) for p in payout],   # account already masked at rest
    }


def cases(conn, actor, requested=None):
    """Disputes + claims + goods-protection case visibility for this carrier."""
    core.require(actor, "carrier.portal.view")
    cid = resolve_carrier(conn, actor, requested)
    disputes = [dict(r) for r in conn.execute(
        "SELECT id,booking_id,status,amount_disputed,reason,opened_at FROM mkt_trust_disputes WHERE carrier_id=? "
        "ORDER BY id DESC", (cid,)).fetchall()]
    claims = [dict(r) for r in conn.execute(
        "SELECT id,claim_type,status,claimant,approved_amount,created_at FROM mkt_claims WHERE carrier_id=? "
        "ORDER BY id DESC", (cid,)).fetchall()]
    return {"carrier_id": cid, "disputes": disputes, "claims": claims,
            "open_high_severity_claims": tc.carrier_open_high_severity_claims(conn, cid)}


def notifications(conn, actor, requested=None):
    """Communication history addressed to this portal user (recipients masked)."""
    core.require(actor, "carrier.portal.view")
    resolve_carrier(conn, actor, requested)   # enforce binding
    return ne.customer_history(conn, actor.get("email") or "")


def performance(conn, actor, requested=None):
    core.require(actor, "carrier.portal.view")
    cid = resolve_carrier(conn, actor, requested)
    return tr.trust_score(conn, cid)


# --------------------------------------------------------------------------- #
# WRITE — carrier-allowed self-service (delegates to governed domain funcs).
# NONE of these can verify/approve/activate — only submit for review.
# --------------------------------------------------------------------------- #
def add_vehicle(conn, actor, category_code, plate_number, requested=None, **attrs):
    core.require(actor, "carrier.portal.fleet.manage")
    cid = resolve_carrier(conn, actor, requested, write=True)
    vid = ob.register_vehicle(conn, _svc(actor, *_SELF_SERVICE["add_vehicle"]), cid, category_code, plate_number, **attrs)
    return {"vehicle_id": vid, "status": "DRAFT",
            "note": "registered as DRAFT — a reviewer must verify + activate before it can accept work"}


def add_driver(conn, actor, full_name, requested=None, **attrs):
    core.require(actor, "carrier.portal.fleet.manage")
    cid = resolve_carrier(conn, actor, requested, write=True)
    did = ob.register_driver(conn, _svc(actor, *_SELF_SERVICE["add_driver"]), cid, full_name, **attrs)
    return {"driver_id": did, "status": "APPLICATION",
            "note": "registered as APPLICATION — a reviewer must verify + activate before assignment"}


def set_vehicle_maintenance(conn, actor, vehicle_id, on=True, reason=None, requested=None):
    """A carrier may take its OWN vehicle in/out of a maintenance hold (operational availability),
    but cannot mark a vehicle ACTIVE/APPROVED — that stays a reviewer action."""
    core.require(actor, "carrier.portal.fleet.manage")
    cid = resolve_carrier(conn, actor, requested, write=True)
    v = ob._guarded(conn, actor, "mkt_vehicles", vehicle_id)
    if v["carrier_id"] != cid:
        raise core.ForbiddenError("vehicle does not belong to your carrier")
    if not on and v["status"] != "MAINTENANCE":
        raise core.ValidationError("vehicle is not on maintenance hold")
    target = "MAINTENANCE" if on else "ACTIVE" if v.get("activated_at") else "APPROVED" if v.get("verified_at") else "DRAFT"
    return ob.set_vehicle_status(conn, _svc(actor, *_SELF_SERVICE["set_vehicle_hold"]), vehicle_id, target, reason=reason)


def check_pairing(conn, actor, driver_id, vehicle_id, requested=None):
    """Deterministic driver<->vehicle pairing check (read-only gate, reuses can_assign_driver)."""
    core.require(actor, "carrier.portal.view")
    cid = resolve_carrier(conn, actor, requested)
    for tbl, _id in (("mkt_drivers", driver_id), ("mkt_vehicles", vehicle_id)):
        row = conn.execute(f"SELECT carrier_id FROM {tbl} WHERE id=?", (_id,)).fetchone()
        if not row or row["carrier_id"] != cid:
            raise core.ForbiddenError("driver/vehicle does not belong to your carrier")
    return ob.can_assign_driver(conn, driver_id, vehicle_id)


def upload_document(conn, actor, document_type, subject_type, subject_id, requested=None, **attrs):
    """Upload a compliance document -> UPLOADED/SUBMITTED. A carrier can NEVER set it VERIFIED."""
    core.require(actor, "carrier.portal.compliance.submit")
    cid = resolve_carrier(conn, actor, requested, write=True)
    subject_type = subject_type.upper()
    if subject_type == "CARRIER" and int(subject_id) != cid:
        raise core.ForbiddenError("cannot upload documents for another carrier")
    if subject_type in ("VEHICLE", "DRIVER"):
        owner = conn.execute(f"SELECT carrier_id FROM mkt_{subject_type.lower()}s WHERE id=?", (subject_id,)).fetchone()
        if not owner or owner["carrier_id"] != cid:
            raise core.ForbiddenError("subject does not belong to your carrier")
    did = ob.upload_document(conn, _svc(actor, *_SELF_SERVICE["upload_document"]), document_type, subject_type, subject_id, **attrs)
    return {"document_id": did, "status": "UPLOADED",
            "note": "submitted for review — only a Compliance Reviewer can mark it VERIFIED"}


def submit_payout_account(conn, actor, beneficiary_name, entity_name, provider_reference, account_number,
                          cooling_hours=None, requested=None):
    """Submit a payout account -> pending approval. The domain stores a MASKED account only; approval
    is a separate finance action (approve_payout_account) the carrier can never perform."""
    core.require(actor, "carrier.portal.compliance.submit")
    cid = resolve_carrier(conn, actor, requested, write=True)
    pid = tc.submit_payout_account(conn, _svc(actor, *_SELF_SERVICE["submit_payout"]), cid,
                                   beneficiary_name, entity_name, provider_reference, account_number,
                                   cooling_hours=cooling_hours)
    return {"payout_account_id": pid, "status": "SUBMITTED",
            "note": "submitted — a finance reviewer must approve before any payout"}


def submit_offer(conn, actor, booking_id, amount, vehicle_id=None, driver_id=None, requested=None, **attrs):
    core.require(actor, "carrier.portal.offers.manage")
    cid = resolve_carrier(conn, actor, requested, write=True)
    return mm.submit_offer(conn, _svc(actor, *_SELF_SERVICE["submit_offer"]), booking_id, cid, amount,
                           vehicle_id=vehicle_id, driver_id=driver_id, **attrs)


def withdraw_offer(conn, actor, offer_id, requested=None):
    core.require(actor, "carrier.portal.offers.manage")
    cid = resolve_carrier(conn, actor, requested, write=True)
    o = conn.execute("SELECT carrier_id FROM mkt_offers WHERE id=?", (offer_id,)).fetchone()
    if not o or o["carrier_id"] != cid:
        raise core.ForbiddenError("offer does not belong to your carrier")
    return mm.withdraw_offer(conn, _svc(actor, *_SELF_SERVICE["withdraw_offer"]), offer_id)


def respond_assignment(conn, actor, assignment_id, decision, requested=None):
    """Accept or decline an assignment offered to this carrier."""
    core.require(actor, "carrier.portal.offers.manage")
    cid = resolve_carrier(conn, actor, requested, write=True)
    a = conn.execute("SELECT carrier_id FROM mkt_assignments WHERE id=?", (assignment_id,)).fetchone()
    if not a or a["carrier_id"] != cid:
        raise core.ForbiddenError("assignment does not belong to your carrier")
    if decision not in ("accept", "reject"):
        raise core.ValidationError("decision must be 'accept' or 'reject'")
    return mm.confirm_assignment(conn, _svc(actor, *_SELF_SERVICE["respond_assignment"]), assignment_id, decision=decision)


def submit_pod(conn, actor, trip_id, kind="POD", evidence_types=None, requested=None, **attrs):
    """Submit proof-of-delivery evidence for a trip the carrier is executing. OTP remains on the
    authorized delivery-verification path — never entered here."""
    core.require(actor, "carrier.portal.trips.execute")
    cid = resolve_carrier(conn, actor, requested, write=True)
    a = conn.execute("SELECT a.carrier_id FROM mkt_trips t JOIN mkt_assignments a ON a.id=t.assignment_id "
                     "WHERE t.id=?", (trip_id,)).fetchone()
    if not a or a["carrier_id"] != cid:
        raise core.ForbiddenError("trip does not belong to your carrier")
    return tp.submit_proof(conn, _svc(actor, *_SELF_SERVICE["submit_pod"]), trip_id, kind, evidence_types=evidence_types, **attrs)


def _own_assignment(conn, cid, assignment_id):
    a = conn.execute("SELECT carrier_id FROM mkt_assignments WHERE id=?", (assignment_id,)).fetchone()
    if not a or a["carrier_id"] != cid:
        raise core.ForbiddenError("assignment does not belong to your carrier")


def open_reassignment(conn, actor, assignment_id, reason, evidence=None, requested=None):
    """A carrier may open an INTRA-CARRIER reassignment on its own assignment (e.g. driver sick, vehicle
    breakdown). It can never re-match the work to another carrier — that is an operator action."""
    core.require(actor, "carrier.portal.reassign")
    cid = resolve_carrier(conn, actor, requested, write=True)
    _own_assignment(conn, cid, assignment_id)
    return dr.open_reassignment(conn, _svc(actor, *_SELF_SERVICE["open_reassignment"]), assignment_id,
                                reason, evidence=evidence, scope="INTRA_CARRIER")


def propose_substitute(conn, actor, reassignment_id, new_driver_id=None, new_vehicle_id=None, requested=None):
    core.require(actor, "carrier.portal.reassign")
    cid = resolve_carrier(conn, actor, requested, write=True)
    case = conn.execute("SELECT carrier_id FROM mkt_reassignments WHERE id=?", (reassignment_id,)).fetchone()
    if not case or case["carrier_id"] != cid:
        raise core.ForbiddenError("reassignment does not belong to your carrier")
    return dr.propose_substitute(conn, _svc(actor, *_SELF_SERVICE["propose_substitute"]), reassignment_id,
                                 new_driver_id=new_driver_id, new_vehicle_id=new_vehicle_id)


# --------------------------------------------------------------------------- #
# Fleet Registration Workspace (register units by spec; classified canonically; DRAFT only)
# --------------------------------------------------------------------------- #
def register_unit(conn, actor, plate_number, specs, requested=None):
    """Register a fleet unit from provider specs into the carrier's OWN fleet. Classified canonically;
    lands DRAFT (a reviewer verifies — the carrier never self-activates)."""
    core.require(actor, "carrier.portal.fleet.manage")
    cid = resolve_carrier(conn, actor, requested, write=True)
    return fr.register_unit(conn, _svc(actor, *_SELF_SERVICE["register_unit"]), cid, plate_number, specs)


def fleet_dashboard(conn, actor, requested=None):
    core.require(actor, "carrier.portal.view")
    cid = resolve_carrier(conn, actor, requested)
    return fr.fleet_dashboard(conn, _svc(actor, *_SELF_SERVICE["fleet_view"]), cid)


def set_service_area(conn, actor, area_code, scope="REGION", requested=None):
    core.require(actor, "carrier.portal.fleet.manage")
    cid = resolve_carrier(conn, actor, requested, write=True)
    return fr.set_service_area(conn, _svc(actor, *_SELF_SERVICE["fleet_manage"]), cid, area_code, scope=scope)


def set_capability(conn, actor, capability, requested=None):
    core.require(actor, "carrier.portal.fleet.manage")
    cid = resolve_carrier(conn, actor, requested, write=True)
    return fr.set_capability(conn, _svc(actor, *_SELF_SERVICE["fleet_manage"]), cid, capability)


def classify_unit(conn, actor, specs, requested=None):
    core.require(actor, "carrier.portal.view")
    resolve_carrier(conn, actor, requested)
    return fr.classify(conn, specs, tenant_id=actor.get("tenant_id"))


# --------------------------------------------------------------------------- #
# Integrity
# --------------------------------------------------------------------------- #
def run_integrity(conn, actor):
    core.require(actor, "carrier.portal.view")
    checks = []
    orphan = conn.execute("SELECT COUNT(*) c FROM carrier_principals p LEFT JOIN mkt_carriers c "
                          "ON c.id=p.carrier_id WHERE c.id IS NULL").fetchone()["c"]
    checks.append({"check": "no_orphan_principal_binding", "ok": orphan == 0, "count": orphan})
    dup = conn.execute("SELECT COUNT(*) c FROM (SELECT user_id,carrier_id,COUNT(*) n FROM carrier_principals "
                       "GROUP BY user_id,carrier_id HAVING n>1)").fetchone()["c"]
    checks.append({"check": "no_duplicate_binding", "ok": dup == 0, "count": dup})
    return {"ok": all(x["ok"] for x in checks), "checks": checks}
