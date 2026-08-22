"""LiftHaul Enterprise — Cargo Insurance Compliance (provider document gate).

Cargo insurance is a PROVIDER COMPLIANCE DOCUMENT, not a product. LiftHaul is not an insurer or broker
and never quotes, prices, binds, or decides claims. The trucking/hauling company obtains cargo insurance
from its own licensed insurer/broker and uploads the certificate; LiftHaul stores, independently verifies,
monitors expiry, and uses it as one marketplace-eligibility gate.

This is a compliance extension over the canonical carrier/vehicle domains — it does NOT create a new
document, insurance, or claims model, and it is deliberately SEPARATE from vehicle insurance (an OR/CR-
style vehicle document). Vehicle insurance can never satisfy the cargo-insurance requirement.

States (§13): NOT_REQUIRED, MISSING, SUBMITTED, UNDER_REVIEW, VERIFIED, REJECTED, EXPIRING, EXPIRED.
Requirement + expiring window are configuration-driven; the eligibility gate is OFF (not required) by
default so activating it is a deliberate per-tenant policy choice.
"""
from __future__ import annotations

import datetime

import core
import tenant

STATES = ("NOT_REQUIRED", "MISSING", "SUBMITTED", "UNDER_REVIEW", "VERIFIED", "REJECTED",
          "EXPIRING", "EXPIRED")

P_UPLOAD = "marketplace.vehicle.manage"       # provider-side (also held via carrier portal _svc)
P_REVIEW = "marketplace.insurance.manage"     # independent staff reviewer (provider cannot self-verify)
P_VIEW = "marketplace.fleet.view"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cargo_insurance(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, carrier_id INTEGER NOT NULL, vehicle_id INTEGER,
  insurer TEXT, policy_ref TEXT, insured_company TEXT, coverage_type TEXT, coverage_amount REAL,
  effective_from TEXT, expiry_date TEXT, vehicle_scope TEXT, cargo_scope TEXT, document_ref TEXT,
  verification_source TEXT, verification_evidence TEXT, reviewer INTEGER, verified_at TEXT,
  rejection_reason TEXT, status TEXT NOT NULL DEFAULT 'SUBMITTED', created_by INTEGER,
  created_at TEXT, updated_at TEXT);
"""


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    return


def _now():
    return core.now()


def _today():
    return datetime.date.today().isoformat()


def _cfg(conn, key, default=None):
    try:
        import admin_platform as ap
        v, _ = ap.resolve_config(conn, key)
        return v if v is not None else default
    except Exception:
        return default


def required(conn):
    """Whether cargo insurance is a required marketplace-eligibility gate (config; default off)."""
    return str(_cfg(conn, "cargo_insurance.required", "false")).lower() == "true"


def _expiring_days(conn):
    try:
        return int(_cfg(conn, "cargo_insurance.expiring_days", "30") or 30)
    except Exception:
        return 30


# --------------------------------------------------------------------------- #
# upload / replace  (provider) — never self-verified
# --------------------------------------------------------------------------- #
def upload(conn, actor, carrier_id, insurer, policy_ref, document_ref, *, vehicle_id=None,
           insured_company=None, coverage_type="CARGO", coverage_amount=None, effective_from=None,
           expiry_date=None, vehicle_scope=None, cargo_scope=None):
    """Provider uploads its own insurer's cargo-insurance certificate. Lands SUBMITTED for independent
    review. A new upload for the same scope supersedes the prior one. Never sets VERIFIED."""
    core.require(actor, P_UPLOAD)
    if not (str(insurer or "").strip() and str(policy_ref or "").strip() and str(document_ref or "").strip()):
        raise core.ValidationError("insurer, policy/certificate number and the uploaded document are required")
    conn.execute("UPDATE cargo_insurance SET status='SUPERSEDED', updated_at=? WHERE carrier_id=? AND "
                 "(vehicle_id IS ? OR vehicle_id=?) AND status IN ('SUBMITTED','UNDER_REVIEW','REJECTED')",
                 (_now(), carrier_id, vehicle_id, vehicle_id))
    now = _now()
    cur = conn.execute(
        "INSERT INTO cargo_insurance(carrier_id,vehicle_id,insurer,policy_ref,insured_company,coverage_type,"
        "coverage_amount,effective_from,expiry_date,vehicle_scope,cargo_scope,document_ref,status,"
        "created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'SUBMITTED',?,?,?)",
        (carrier_id, vehicle_id, str(insurer)[:200], str(policy_ref)[:120],
         (str(insured_company)[:200] if insured_company else None), str(coverage_type)[:40],
         (float(coverage_amount) if coverage_amount not in (None, "") else None), effective_from, expiry_date,
         (str(vehicle_scope)[:80] if vehicle_scope else ("VEHICLE" if vehicle_id else "FLEET")),
         (str(cargo_scope)[:200] if cargo_scope else None), str(document_ref)[:400], actor.get("id"), now, now))
    cid = cur.lastrowid
    tenant.stamp(conn, actor, "cargo_insurance", cid)
    core.audit(conn, actor, "CARGO_INSURANCE_UPLOADED", "cargo_insurance", cid, None,
               {"carrier_id": carrier_id, "vehicle_id": vehicle_id, "insurer": insurer, "policy_ref": policy_ref})
    conn.commit()
    return {"id": cid, "status": "SUBMITTED",
            "note": "Cargo-insurance certificate submitted for independent review. LiftHaul does not "
                    "underwrite, price, or process insurance."}


def review(conn, actor, ci_id, decision, *, verification_source=None, verification_evidence=None,
           rejection_reason=None):
    """Independent reviewer verifies/rejects. Requires the insurance-review permission which a provider/
    carrier does not hold, so a company can never self-verify its own cargo insurance."""
    core.require(actor, P_REVIEW)
    r = conn.execute("SELECT * FROM cargo_insurance WHERE id=?", (ci_id,)).fetchone()
    if not r:
        raise core.NotFoundError("cargo insurance record not found")
    tenant.guard(actor, r)
    if r["status"] not in ("SUBMITTED", "UNDER_REVIEW"):
        raise core.ConflictError(f"record is {r['status']} — not pending review")
    d = str(decision or "").upper()
    if d not in ("VERIFY", "REJECT"):
        raise core.ValidationError("decision must be VERIFY or REJECT")
    if d == "VERIFY":
        conn.execute("UPDATE cargo_insurance SET status='VERIFIED',reviewer=?,verified_at=?,"
                     "verification_source=?,verification_evidence=?,updated_at=? WHERE id=?",
                     (actor.get("id"), _now(), verification_source, verification_evidence, _now(), ci_id))
        core.audit(conn, actor, "CARGO_INSURANCE_VERIFIED", "cargo_insurance", ci_id, None,
                   {"source": verification_source})
        status = "VERIFIED"
    else:
        conn.execute("UPDATE cargo_insurance SET status='REJECTED',reviewer=?,rejection_reason=?,"
                     "updated_at=? WHERE id=?", (actor.get("id"), str(rejection_reason or "")[:300], _now(), ci_id))
        core.audit(conn, actor, "CARGO_INSURANCE_REJECTED", "cargo_insurance", ci_id, None,
                   {"reason": rejection_reason})
        status = "REJECTED"
    conn.commit()
    return {"id": ci_id, "status": status}


# --------------------------------------------------------------------------- #
# state resolution + eligibility gate
# --------------------------------------------------------------------------- #
def _current(conn, carrier_id, vehicle_id):
    """Most relevant live record: a vehicle-specific one wins over a fleet-wide (vehicle_id NULL) one."""
    if vehicle_id is not None:
        r = conn.execute("SELECT * FROM cargo_insurance WHERE carrier_id=? AND vehicle_id=? AND "
                         "status!='SUPERSEDED' ORDER BY id DESC LIMIT 1", (carrier_id, vehicle_id)).fetchone()
        if r:
            return dict(r)
    r = conn.execute("SELECT * FROM cargo_insurance WHERE carrier_id=? AND vehicle_id IS NULL AND "
                     "status!='SUPERSEDED' ORDER BY id DESC LIMIT 1", (carrier_id,)).fetchone()
    return dict(r) if r else None


def status_for(conn, carrier_id, vehicle_id=None):
    """Effective cargo-insurance state, applying expiry to a VERIFIED record."""
    if not required(conn):
        return "NOT_REQUIRED"
    rec = _current(conn, carrier_id, vehicle_id)
    if rec is None:
        return "MISSING"
    st = rec["status"]
    if st == "VERIFIED" and rec.get("expiry_date"):
        exp = str(rec["expiry_date"])[:10]
        today = _today()
        if exp < today:
            return "EXPIRED"
        try:
            days = (datetime.date.fromisoformat(exp) - datetime.date.fromisoformat(today)).days
            if days <= _expiring_days(conn):
                return "EXPIRING"
        except Exception:
            pass
    return st


def eligibility_gate(conn, carrier_id, vehicle_id=None):
    """Return 'PASS' or a coded eligibility reason. EXPIRING still passes (valid, but flagged in readiness)."""
    st = status_for(conn, carrier_id, vehicle_id)
    if st in ("NOT_REQUIRED", "VERIFIED", "EXPIRING"):
        return "PASS"
    return {"MISSING": "CARGO_INSURANCE_MISSING", "EXPIRED": "CARGO_INSURANCE_EXPIRED",
            "REJECTED": "CARGO_INSURANCE_REJECTED"}.get(st, "CARGO_INSURANCE_PENDING")


def summary(conn, carrier_id, vehicle_id=None):
    rec = _current(conn, carrier_id, vehicle_id)
    return {"carrier_id": carrier_id, "vehicle_id": vehicle_id, "required": required(conn),
            "status": status_for(conn, carrier_id, vehicle_id),
            "insurer": rec["insurer"] if rec else None, "policy_ref": rec["policy_ref"] if rec else None,
            "expiry_date": rec["expiry_date"] if rec else None,
            "separate_from_vehicle_insurance": True}


def expiring_queue(conn, actor):
    """Compliance queue: verified cargo-insurance records that are EXPIRING or EXPIRED."""
    core.require(actor, P_REVIEW)
    frag, params = tenant.predicate(actor)
    rows = conn.execute("SELECT * FROM cargo_insurance WHERE status='VERIFIED' AND expiry_date IS NOT NULL"
                        + frag + " ORDER BY expiry_date ASC", list(params)).fetchall()
    out = []
    for r in rows:
        exp = str(r["expiry_date"])[:10]
        state = "EXPIRED" if exp < _today() else None
        if state is None:
            try:
                days = (datetime.date.fromisoformat(exp) - datetime.date.fromisoformat(_today())).days
                state = "EXPIRING" if days <= _expiring_days(conn) else None
            except Exception:
                state = None
        if state:
            d = dict(r); d["effective_state"] = state; out.append(d)
    return {"expiring": out, "count": len(out)}
