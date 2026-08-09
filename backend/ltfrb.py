"""LiftHaul Marketplace — LTFRB carrier transport-authority compliance (regulatory closure C/D/E).

Extends the existing trust layer (KYB, vehicle legality) with the carrier-level LTFRB authority the
Philippine model requires for for-hire trucking. Reuses — does not rebuild — mkt_vehicle_legality
(OR/CR/CPC per vehicle) and marketplace_trust (fraud/eligibility). A carrier without valid, verified
LTFRB authority (where applicable) is HARD-BLOCKED from new assignment. LTFRB verification is never
fabricated: with no legally-accessible live LTFRB API, verification returns MANUAL_VERIFICATION_REQUIRED
and a human records the source + evidence.
"""
from __future__ import annotations

import datetime
import json

import core
import tenant

AUTH_STATES = ("SUBMITTED", "MANUAL_VERIFICATION_REQUIRED", "VERIFIED", "REJECTED", "EXPIRED", "SUSPENDED")
ISLAND_GROUPS = ("LUZON", "VISAYAS", "MINDANAO")

SCHEMA = """
CREATE TABLE IF NOT EXISTS mkt_ltfrb_authority(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, carrier_id INTEGER NOT NULL,
  authority_type TEXT DEFAULT 'TRUCK_FOR_HIRE_CPC', cpc_number TEXT, case_reference TEXT,
  area_of_operation TEXT,               -- JSON list of island groups / regions
  authorized_units TEXT,                -- JSON list of authorized plate numbers
  port_authority TEXT, special_permits TEXT, garage_evidence TEXT, hauling_contract_evidence TEXT,
  issue_date TEXT, expiry_date TEXT,
  status TEXT NOT NULL DEFAULT 'SUBMITTED',
  verification_source TEXT, verification_result TEXT, evidence TEXT,
  verified_by INTEGER, verified_at TEXT, created_by INTEGER, created_at TEXT, updated_at TEXT);
"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today():
    return datetime.date.today().isoformat()


def _expired(d, as_of=None):
    if not d:
        return False
    try:
        return datetime.date.fromisoformat(d[:10]) < datetime.date.fromisoformat((as_of or _today())[:10])
    except Exception:
        return False


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn):
    return 0


# --------------------------------------------------------------------------- #
# C. Carrier LTFRB authority records
# --------------------------------------------------------------------------- #
def record_authority(conn, actor, carrier_id, *, cpc_number=None, case_reference=None,
                     area_of_operation=None, authorized_units=None, port_authority=None,
                     special_permits=None, garage_evidence=None, hauling_contract_evidence=None,
                     issue_date=None, expiry_date=None, authority_type="TRUCK_FOR_HIRE_CPC"):
    """Record an LTFRB authority from submitted documents. Status SUBMITTED — not verified."""
    core.require(actor, "marketplace.ltfrb.manage")
    for g in (area_of_operation or []):
        if g not in ISLAND_GROUPS and g.upper() not in ISLAND_GROUPS:
            pass  # regions/cities allowed too; island-group validation is advisory
    cur = conn.execute(
        "INSERT INTO mkt_ltfrb_authority(carrier_id,authority_type,cpc_number,case_reference,"
        "area_of_operation,authorized_units,port_authority,special_permits,garage_evidence,"
        "hauling_contract_evidence,issue_date,expiry_date,status,created_by,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'SUBMITTED', ?,?,?)",
        (carrier_id, authority_type, cpc_number, case_reference,
         json.dumps(area_of_operation) if area_of_operation else None,
         json.dumps(authorized_units) if authorized_units else None, port_authority, special_permits,
         garage_evidence, hauling_contract_evidence, issue_date, expiry_date, actor["id"], _now(), _now()))
    aid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_ltfrb_authority", aid)
    core.audit(conn, actor, "LTFRB_AUTHORITY_RECORDED", "mkt_ltfrb_authority", aid,
               new={"carrier": carrier_id, "cpc": cpc_number})
    conn.commit()
    return aid


# --------------------------------------------------------------------------- #
# D. Verification workflow (never fabricate)
# --------------------------------------------------------------------------- #
def run_ltfrb_adapter(cpc_number, case_reference):
    """Provider-neutral LTFRB lookup. No legally-accessible live API is configured, so this honestly
    returns MANUAL_VERIFICATION_REQUIRED — verification is never fabricated."""
    return {"status": "MANUAL_VERIFICATION_REQUIRED", "source": "LTFRB:no-public-api",
            "note": "requires manual verification against an official LTFRB source"}


def check_authority(conn, actor, authority_id):
    core.require(actor, "marketplace.ltfrb.manage")
    a = conn.execute("SELECT * FROM mkt_ltfrb_authority WHERE id=?", (authority_id,)).fetchone()
    if not a:
        raise core.NotFoundError("LTFRB authority not found")
    res = run_ltfrb_adapter(a["cpc_number"], a["case_reference"])
    conn.execute("UPDATE mkt_ltfrb_authority SET status='MANUAL_VERIFICATION_REQUIRED',"
                 " verification_source=?, updated_at=? WHERE id=?", (res["source"], _now(), authority_id))
    core.audit(conn, actor, "LTFRB_ADAPTER_CHECK", "mkt_ltfrb_authority", authority_id,
               new={"result": res["status"]})
    conn.commit()
    return {"authority_id": authority_id, "adapter_result": res}


def verify_authority(conn, actor, authority_id, decision, source, evidence=None):
    """Human verification with a mandatory recorded source — no fabricated verification."""
    core.require(actor, "marketplace.ltfrb.manage")
    if decision not in ("VERIFIED", "REJECTED"):
        raise core.ValidationError("decision must be VERIFIED or REJECTED")
    if decision == "VERIFIED" and not source:
        raise core.ValidationError("LTFRB verification requires a recorded official source")
    conn.execute("UPDATE mkt_ltfrb_authority SET status=?, verification_source=?, verification_result=?,"
                 " evidence=COALESCE(?,evidence), verified_by=?, verified_at=?, updated_at=? WHERE id=?",
                 (decision, source, decision, json.dumps(evidence) if evidence else None,
                  actor["id"], _now(), _now(), authority_id))
    core.audit(conn, actor, "LTFRB_AUTHORITY_VERIFIED", "mkt_ltfrb_authority", authority_id,
               new={"decision": decision, "source": source})
    conn.commit()
    return decision


def expire_due(conn, as_of=None):
    as_of = as_of or _today()
    rows = conn.execute("SELECT id,expiry_date,status FROM mkt_ltfrb_authority WHERE status='VERIFIED'"
                        " AND expiry_date IS NOT NULL").fetchall()
    n = 0
    for r in rows:
        if _expired(r["expiry_date"], as_of):
            conn.execute("UPDATE mkt_ltfrb_authority SET status='EXPIRED', updated_at=? WHERE id=?", (_now(), r["id"]))
            n += 1
    conn.commit()
    return {"expired": n}


# --------------------------------------------------------------------------- #
# C. Hard carrier / assignment gate
# --------------------------------------------------------------------------- #
def _active_authority(conn, carrier_id):
    return conn.execute("SELECT * FROM mkt_ltfrb_authority WHERE carrier_id=? AND authority_type="
                        "'TRUCK_FOR_HIRE_CPC' ORDER BY id DESC LIMIT 1", (carrier_id,)).fetchone()


def carrier_authority_gate(conn, carrier_id, as_of=None):
    """A carrier requires a VERIFIED, unexpired LTFRB CPC to take new for-hire assignments."""
    as_of = as_of or _today()
    a = _active_authority(conn, carrier_id)
    reasons = []
    if not a:
        return {"ok": False, "reasons": ["no_ltfrb_authority_on_file"]}
    if a["status"] != "VERIFIED":
        reasons.append(f"cpc_not_verified({a['status']})")
    if _expired(a["expiry_date"], as_of):
        reasons.append("cpc_expired")
    return {"ok": not reasons, "reasons": reasons, "cpc_number": a["cpc_number"]}


def assignment_authority_gate(conn, carrier_id, vehicle_plate=None, area=None, as_of=None):
    """Hard assignment gate: valid CPC AND (if given) the vehicle is an authorized unit AND the area
    is within the carrier's area of operation. Any failure blocks the assignment."""
    base = carrier_authority_gate(conn, carrier_id, as_of)
    reasons = list(base["reasons"])
    a = _active_authority(conn, carrier_id)
    if a:
        if vehicle_plate is not None:
            units = json.loads(a["authorized_units"]) if a["authorized_units"] else []
            if units and vehicle_plate not in units:
                reasons.append("vehicle_not_authorized_unit")
        if area is not None:
            aoo = json.loads(a["area_of_operation"]) if a["area_of_operation"] else []
            if aoo and area not in aoo and area.upper() not in [x.upper() for x in aoo]:
                reasons.append("area_outside_authority")
    return {"ok": not reasons, "reasons": reasons}


# --------------------------------------------------------------------------- #
# E. Regulatory dashboard aggregation
# --------------------------------------------------------------------------- #
def expiring_cpcs(conn, actor, within_days=60, as_of=None):
    core.require(actor, "marketplace.ltfrb.view")
    as_of = as_of or _today()
    out = []
    frag, params = tenant.predicate(actor)
    for r in conn.execute("SELECT id,carrier_id,cpc_number,expiry_date,status FROM mkt_ltfrb_authority"
                          " WHERE status='VERIFIED' AND expiry_date IS NOT NULL" + frag, params).fetchall():
        try:
            days = (datetime.date.fromisoformat(r["expiry_date"][:10]) - datetime.date.fromisoformat(as_of[:10])).days
        except Exception:
            continue
        if days <= within_days:
            tier = ("EXPIRED" if days < 0 else "CRITICAL" if days <= 7 else "HIGH" if days <= 15
                    else "WARNING" if days <= 30 else "NOTICE")
            out.append({"authority_id": r["id"], "carrier_id": r["carrier_id"], "cpc_number": r["cpc_number"],
                        "days_to_expiry": days, "tier": tier})
    return out


def pending_verification(conn, actor):
    core.require(actor, "marketplace.ltfrb.view")
    frag, params = tenant.predicate(actor)
    return [dict(r) for r in conn.execute(
        "SELECT id,carrier_id,cpc_number,status FROM mkt_ltfrb_authority WHERE status IN "
        "('SUBMITTED','MANUAL_VERIFICATION_REQUIRED')" + frag + " ORDER BY id", params).fetchall()]


def regulatory_summary(conn, actor):
    """E. Feeds the Regulatory Compliance dashboard: BSP readiness (docs), provider readiness, LTFRB
    carrier compliance, CPC expiries, pending manual verification, exceptions."""
    core.require(actor, "marketplace.ltfrb.view") if core.can(actor, "marketplace.ltfrb.view") else core.require(actor, "marketplace.trust.view")
    import marketplace_payments as pay
    frag, params = tenant.predicate(actor)
    total = conn.execute("SELECT COUNT(*) c FROM mkt_ltfrb_authority WHERE 1=1" + frag, params).fetchone()["c"]
    verified = conn.execute("SELECT COUNT(*) c FROM mkt_ltfrb_authority WHERE status='VERIFIED'" + frag, params).fetchone()["c"]
    expiring = expiring_cpcs(conn, actor)
    pending = pending_verification(conn, actor)
    return {
        "bsp": {"status": "REGULATORY CLASSIFICATION / APPLICATION PREPARATION",
                "package": "docs/regulatory/bsp/", "registered": False},
        "payment_provider": {"live_funds_enabled": pay.live_funds_enabled(conn),
                             "status": "PENDING — regulated provider certification required"},
        "ltfrb": {"authorities_total": total, "verified": verified,
                  "pending_manual_verification": len(pending),
                  "expiring_cpcs": len(expiring), "expiring_detail": expiring},
        "exceptions": pending,
        "live_protected_funds_enabled": pay.live_funds_enabled(conn)}
