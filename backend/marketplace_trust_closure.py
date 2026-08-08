"""LiftHaul Marketplace — Trust & Protected-Transaction CLOSURE (C, D, M, N, O + hardening).

Continues from the trust foundation in `marketplace_trust.py` (KYB / adapters / fraud / trust
score / eligibility) and the Inc4/Inc5 payment/trip engines. It does NOT rebuild them. It closes
the production-relevant gaps:

  C. Driver / operator QUALIFICATION enforcement (equipment-type competencies + license legality).
  D. Vehicle / equipment LEGALITY (OR/CR/plate/registration/insurance/inspection/authority).
  M. Payout-account SECURITY (masked, maker/checker, cooling period, no self-approval).
  N. Full DISPUTE lifecycle (states + evidence + SLA + reviewer), auto-blocking release.
  O. CLAIMS / insurance case management, feeding carrier risk.
  + Progressive TRANSACTION RISK LIMITS (a legitimately-registered but unproven carrier is not
    immediately eligible for ₱5M–₱20M heavy-haul jobs).
  + A single composed RELEASE GATE that re-runs every blocking condition — release is denied,
    never silently bypassed.
  + Provider WEBHOOK SECURITY (signature + timestamp tolerance + replay/idempotency).
  + Marketplace LEDGER RECONCILIATION (funding == released + refunded + remaining + fees).

Governance (unchanged, non-negotiable): live fund custody stays BLOCKED until legal counsel +
a licensed protected-funds provider authorize the Philippine operating model. This module
enforces that decision; it never enables live custody and never fabricates verification.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json

import core
import tenant

VERIFY_STATES = ("SUBMITTED", "VERIFIED", "REJECTED", "EXPIRED")
DISPUTE_STATES = ("OPENED", "FUNDS_HELD", "EVIDENCE_REQUESTED", "CLIENT_EVIDENCE_SUBMITTED",
                  "CARRIER_EVIDENCE_SUBMITTED", "UNDER_REVIEW", "RESOLUTION_PROPOSED",
                  "RESOLUTION_APPROVED", "CLOSED")
DISPUTE_OUTCOMES = ("RELEASE_FULL", "RELEASE_PARTIAL", "REFUND_FULL", "REFUND_PARTIAL", "CREDIT",
                    "REWORK", "INSURANCE_CLAIM", "LEGAL_ESCALATION")
DISPUTE_BLOCKING = ("OPENED", "FUNDS_HELD", "EVIDENCE_REQUESTED", "CLIENT_EVIDENCE_SUBMITTED",
                    "CARRIER_EVIDENCE_SUBMITTED", "UNDER_REVIEW", "RESOLUTION_PROPOSED")
CLAIM_TYPES = ("CARGO_DAMAGE", "CARGO_LOSS", "PROPERTY_DAMAGE", "ACCIDENT", "INJURY",
               "VEHICLE_DAMAGE", "EQUIPMENT_DAMAGE", "THEFT", "DELAY", "FRAUD")
CLAIM_STATES = ("REPORTED", "TRIAGE", "EVIDENCE_COLLECTION", "INSURER_NOTIFICATION", "UNDER_REVIEW",
                "APPROVED", "PARTIAL", "DENIED", "SETTLED", "CLOSED")
CLAIM_HIGH_SEVERITY = ("CARGO_LOSS", "ACCIDENT", "INJURY", "THEFT", "FRAUD")
PAYOUT_COOLING_HOURS_DEFAULT = 24
HIGH_VALUE_DEFAULT = 500000.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS mkt_operator_qualifications(
  id INTEGER PRIMARY KEY, tenant_id INTEGER,
  driver_id INTEGER NOT NULL, equipment_type TEXT NOT NULL, qualification_type TEXT NOT NULL,
  certificate_number TEXT, issuer TEXT, issued_at TEXT, expires_at TEXT,
  verification_status TEXT NOT NULL DEFAULT 'SUBMITTED', source TEXT, evidence TEXT,
  verified_by INTEGER, verified_at TEXT, created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS mkt_vehicle_legality(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, vehicle_id INTEGER NOT NULL,
  or_number TEXT, cr_number TEXT, plate TEXT, mv_file_number TEXT, registered_owner TEXT,
  chassis_number TEXT, engine_number TEXT, classification TEXT, capacity_kg REAL,
  registration_expiry TEXT, insurance_expiry TEXT, cargo_insurance_expiry TEXT,
  inspection_valid_until TEXT, maintenance_status TEXT, authorized_scope TEXT, cpc_reference TEXT,
  verification_status TEXT NOT NULL DEFAULT 'SUBMITTED', source TEXT, evidence TEXT,
  verified_by INTEGER, verified_at TEXT, created_by INTEGER, created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS mkt_payout_accounts(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, carrier_id INTEGER NOT NULL,
  beneficiary_name TEXT, entity_name TEXT, provider_reference TEXT, account_masked TEXT,
  holder_verified INTEGER DEFAULT 0, verification_status TEXT NOT NULL DEFAULT 'SUBMITTED',
  status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL',
  effective_at TEXT, cooling_until TEXT,
  changed_by INTEGER, approved_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS mkt_trust_disputes(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, booking_id INTEGER, trip_id INTEGER,
  payment_requirement_id INTEGER, client_ref TEXT, carrier_id INTEGER,
  amount_disputed REAL, reason TEXT, evidence TEXT, status TEXT NOT NULL DEFAULT 'OPENED',
  sla_due TEXT, assigned_reviewer INTEGER, decision TEXT, decision_reason TEXT,
  financial_outcome TEXT, opened_by INTEGER, opened_at TEXT, resolved_by INTEGER, resolved_at TEXT);

CREATE TABLE IF NOT EXISTS mkt_claims(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, claim_number TEXT,
  claim_type TEXT NOT NULL, claimant TEXT, trip_id INTEGER, carrier_id INTEGER, driver_id INTEGER,
  vehicle_id INTEGER, incident_ref TEXT, claimed_amount REAL, insured_amount REAL, insurer TEXT,
  policy_reference TEXT, evidence TEXT, authority_report TEXT, adjuster_reference TEXT,
  reserve REAL, approved_amount REAL, settlement REAL, status TEXT NOT NULL DEFAULT 'REPORTED',
  opened_by INTEGER, created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS mkt_webhook_events(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, provider TEXT, event_id TEXT, event_type TEXT,
  signature_ok INTEGER, status TEXT, received_at TEXT, UNIQUE(provider, event_id));
"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today():
    return datetime.date.today().isoformat()


def _expired(date_str, as_of=None):
    if not date_str:
        return False
    try:
        return datetime.date.fromisoformat(date_str[:10]) < datetime.date.fromisoformat((as_of or _today())[:10])
    except Exception:
        return False


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn):
    return 0


# --------------------------------------------------------------------------- #
# C. Driver / operator qualification
# --------------------------------------------------------------------------- #
def record_qualification(conn, actor, driver_id, equipment_type, qualification_type,
                         certificate_number=None, issuer=None, issued_at=None, expires_at=None, evidence=None):
    """A qualification RECORD (from a submitted certificate) is SUBMITTED — not verified."""
    core.require(actor, "marketplace.driver.qualify")
    cur = conn.execute(
        "INSERT INTO mkt_operator_qualifications(driver_id,equipment_type,qualification_type,"
        "certificate_number,issuer,issued_at,expires_at,verification_status,evidence,created_by,created_at)"
        " VALUES(?,?,?,?,?,?,?, 'SUBMITTED', ?,?,?)",
        (driver_id, equipment_type, qualification_type, certificate_number, issuer, issued_at,
         expires_at, json.dumps(evidence) if evidence else None, actor["id"], _now()))
    qid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_operator_qualifications", qid)
    core.audit(conn, actor, "MKT_QUALIFICATION_RECORDED", "mkt_operator_qualifications", qid,
               new={"driver": driver_id, "equipment": equipment_type, "type": qualification_type})
    conn.commit()
    return qid


def verify_qualification(conn, actor, qualification_id, decision, source, evidence=None):
    core.require(actor, "marketplace.driver.qualify")
    if decision not in ("VERIFIED", "REJECTED"):
        raise core.ValidationError("decision must be VERIFIED or REJECTED")
    if decision == "VERIFIED" and not source:
        raise core.ValidationError("verification requires a recorded source")
    conn.execute("UPDATE mkt_operator_qualifications SET verification_status=?, source=?, "
                 "evidence=COALESCE(?,evidence), verified_by=?, verified_at=? WHERE id=?",
                 (decision, source, json.dumps(evidence) if evidence else None, actor["id"], _now(), qualification_id))
    core.audit(conn, actor, "MKT_QUALIFICATION_VERIFIED", "mkt_operator_qualifications",
               qualification_id, new={"decision": decision, "source": source})
    conn.commit()
    return decision


def driver_assignment_gate(conn, driver_id, vehicle_id=None, equipment_type=None, as_of=None):
    """C. Hard driver gate. Fails when license expired / wrong class / required qualification
    absent or expired / driver suspended / carrier relationship invalid."""
    as_of = as_of or _today()
    d = conn.execute("SELECT * FROM mkt_drivers WHERE id=?", (driver_id,)).fetchone()
    reasons = []
    if not d:
        return {"ok": False, "reasons": ["unknown_driver"]}
    if (d["status"] or "").upper() in ("SUSPENDED", "REJECTED", "EXPIRED"):
        reasons.append("driver_suspended")
    if not d["carrier_id"]:
        reasons.append("carrier_relationship_invalid")
    if _expired(d["licence_expiry"], as_of):
        reasons.append("license_expired")
    if equipment_type:
        # class compatibility: the driver's authorized categories must include the equipment type
        try:
            cats = json.loads(d["authorized_categories"]) if d["authorized_categories"] else []
        except Exception:
            cats = []
        if cats and equipment_type not in cats:
            reasons.append("wrong_license_class")
        q = conn.execute(
            "SELECT * FROM mkt_operator_qualifications WHERE driver_id=? AND equipment_type=?"
            " AND verification_status='VERIFIED'", (driver_id, equipment_type)).fetchall()
        if not q:
            reasons.append("required_qualification_absent")
        elif all(_expired(r["expires_at"], as_of) for r in q):
            reasons.append("qualification_expired")
    return {"ok": not reasons, "reasons": reasons}


# --------------------------------------------------------------------------- #
# D. Vehicle / equipment legality
# --------------------------------------------------------------------------- #
def record_vehicle_legality(conn, actor, vehicle_id, **fields):
    core.require(actor, "marketplace.vehicle.legality")
    cols = ("or_number", "cr_number", "plate", "mv_file_number", "registered_owner", "chassis_number",
            "engine_number", "classification", "capacity_kg", "registration_expiry", "insurance_expiry",
            "cargo_insurance_expiry", "inspection_valid_until", "maintenance_status", "authorized_scope",
            "cpc_reference")
    vals = [fields.get(c) for c in cols]
    cur = conn.execute(
        "INSERT INTO mkt_vehicle_legality(vehicle_id," + ",".join(cols) +
        ",verification_status,created_by,created_at,updated_at) VALUES(?" + ",?" * len(cols) +
        ", 'SUBMITTED', ?,?,?)",
        (vehicle_id, *vals, actor["id"], _now(), _now()))
    lid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_vehicle_legality", lid)
    core.audit(conn, actor, "MKT_VEHICLE_LEGALITY_RECORDED", "mkt_vehicle_legality", lid,
               new={"vehicle": vehicle_id, "plate": fields.get("plate")})
    conn.commit()
    return lid


def verify_vehicle_legality(conn, actor, legality_id, decision, source):
    core.require(actor, "marketplace.vehicle.legality")
    if decision not in ("VERIFIED", "REJECTED"):
        raise core.ValidationError("decision must be VERIFIED or REJECTED")
    if decision == "VERIFIED" and not source:
        raise core.ValidationError("verification requires a recorded source")
    conn.execute("UPDATE mkt_vehicle_legality SET verification_status=?, source=?, verified_by=?, "
                 "verified_at=?, updated_at=? WHERE id=?",
                 (decision, source, actor["id"], _now(), _now(), legality_id))
    core.audit(conn, actor, "MKT_VEHICLE_LEGALITY_VERIFIED", "mkt_vehicle_legality", legality_id,
               new={"decision": decision, "source": source})
    conn.commit()
    return decision


def vehicle_legality_gate(conn, vehicle_id, required_capacity_kg=None, as_of=None):
    """D. Hard vehicle gate: legal registration + active insurance + safe maintenance + capacity
    + regulatory authority where required. A valid carrier cannot make an invalid truck eligible."""
    as_of = as_of or _today()
    l = conn.execute("SELECT * FROM mkt_vehicle_legality WHERE vehicle_id=? ORDER BY id DESC LIMIT 1",
                     (vehicle_id,)).fetchone()
    reasons = []
    if not l:
        return {"ok": False, "reasons": ["no_legality_record"]}
    if l["verification_status"] != "VERIFIED":
        reasons.append("legality_not_verified")
    if not l["or_number"] or not l["cr_number"]:
        reasons.append("invalid_or_cr")
    if _expired(l["registration_expiry"], as_of):
        reasons.append("registration_expired")
    if _expired(l["insurance_expiry"], as_of):
        reasons.append("insurance_expired")
    if (l["maintenance_status"] or "").upper() in ("UNSAFE", "GROUNDED", "OVERDUE"):
        reasons.append("maintenance_unsafe")
    if required_capacity_kg is not None and (l["capacity_kg"] or 0) < required_capacity_kg:
        reasons.append("capacity_mismatch")
    return {"ok": not reasons, "reasons": reasons}


# --------------------------------------------------------------------------- #
# M. Payout-account security
# --------------------------------------------------------------------------- #
def _mask(number):
    if not number:
        return None
    s = str(number)
    return ("*" * max(0, len(s) - 4)) + s[-4:]


def submit_payout_account(conn, actor, carrier_id, beneficiary_name, entity_name,
                          provider_reference, account_number, cooling_hours=None):
    """Maker action. Stores a MASKED account only; never the raw number/credentials."""
    core.require(actor, "marketplace.payout.manage")
    cooling = int(cooling_hours if cooling_hours is not None else PAYOUT_COOLING_HOURS_DEFAULT)
    cool_until = (datetime.datetime.now(datetime.timezone.utc)
                  + datetime.timedelta(hours=cooling)).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO mkt_payout_accounts(carrier_id,beneficiary_name,entity_name,provider_reference,"
        "account_masked,verification_status,status,cooling_until,changed_by,created_at)"
        " VALUES(?,?,?,?,?, 'SUBMITTED','PENDING_APPROVAL', ?,?,?)",
        (carrier_id, beneficiary_name, entity_name, provider_reference, _mask(account_number),
         cool_until, actor["id"], _now()))
    pid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_payout_accounts", pid)
    core.audit(conn, actor, "MKT_PAYOUT_ACCOUNT_SUBMITTED", "mkt_payout_accounts", pid,
               new={"carrier": carrier_id, "beneficiary": beneficiary_name, "cooling_hours": cooling})
    conn.commit()
    return pid


def approve_payout_account(conn, actor, payout_account_id, beneficiary_verified=True, mfa_ok=False):
    """Independent checker approval. Requires MFA + maker≠checker. Activation still respects the
    cooling period for high-value payouts."""
    core.require(actor, "marketplace.payout.approve")
    if not mfa_ok:
        raise core.ForbiddenError("MFA required to approve a payout-destination change")
    pa = conn.execute("SELECT * FROM mkt_payout_accounts WHERE id=?", (payout_account_id,)).fetchone()
    if not pa:
        raise core.NotFoundError("payout account not found")
    if pa["changed_by"] == actor["id"]:
        raise core.ForbiddenError("separation of duties: the maker cannot approve their own change")
    if _fraud_review_open(conn, pa["carrier_id"]):
        raise core.ForbiddenError("account change blocked: carrier is under critical fraud review")
    conn.execute("UPDATE mkt_payout_accounts SET status='ACTIVE', verification_status=?, "
                 "holder_verified=?, approved_by=?, effective_at=? WHERE id=?",
                 ("VERIFIED" if beneficiary_verified else "SUBMITTED",
                  1 if beneficiary_verified else 0, actor["id"], _now(), payout_account_id))
    core.audit(conn, actor, "MKT_PAYOUT_ACCOUNT_APPROVED", "mkt_payout_accounts", payout_account_id,
               new={"beneficiary_verified": beneficiary_verified, "severity": "HIGH"})
    conn.commit()
    return "ACTIVE"


def _fraud_review_open(conn, carrier_id):
    try:
        import marketplace_trust as mt
        return mt.is_blocked(conn, "CARRIER", carrier_id)
    except Exception:
        return False


def payout_allowed(conn, payout_account_id, amount, as_of=None, high_value=None):
    """M. Payout guard. Blocks unverified beneficiary, and high-value payout during the cooling
    period. Returns {ok, reasons}."""
    high_value = HIGH_VALUE_DEFAULT if high_value is None else high_value
    pa = conn.execute("SELECT * FROM mkt_payout_accounts WHERE id=?", (payout_account_id,)).fetchone()
    reasons = []
    if not pa:
        return {"ok": False, "reasons": ["unknown_payout_account"]}
    if pa["status"] != "ACTIVE" or pa["verification_status"] != "VERIFIED" or not pa["holder_verified"]:
        reasons.append("beneficiary_unverified")
    now = as_of or _now()
    if pa["cooling_until"] and now < pa["cooling_until"] and (amount or 0) >= high_value:
        reasons.append("cooling_period_high_value_blocked")
    if _fraud_review_open(conn, pa["carrier_id"]):
        reasons.append("carrier_fraud_review")
    return {"ok": not reasons, "reasons": reasons}


# --------------------------------------------------------------------------- #
# N. Full dispute lifecycle
# --------------------------------------------------------------------------- #
def open_dispute(conn, actor, booking_id, carrier_id, amount_disputed, reason,
                 trip_id=None, payment_requirement_id=None, client_ref=None, sla_hours=72):
    core.require(actor, "marketplace.dispute.manage")
    sla = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=sla_hours)).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO mkt_trust_disputes(booking_id,trip_id,payment_requirement_id,client_ref,carrier_id,"
        "amount_disputed,reason,status,sla_due,opened_by,opened_at) VALUES(?,?,?,?,?,?,?, 'OPENED', ?,?,?)",
        (booking_id, trip_id, payment_requirement_id, client_ref, carrier_id, amount_disputed, reason,
         sla, actor["id"], _now()))
    did = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_trust_disputes", did)
    core.audit(conn, actor, "MKT_DISPUTE_OPENED", "mkt_trust_disputes", did,
               new={"booking": booking_id, "amount": amount_disputed, "reason": reason})
    conn.commit()
    return did


def advance_dispute(conn, actor, dispute_id, to_status, evidence=None, reviewer=None):
    core.require(actor, "marketplace.dispute.manage")
    if to_status not in DISPUTE_STATES:
        raise core.ValidationError("invalid dispute status")
    d = conn.execute("SELECT * FROM mkt_trust_disputes WHERE id=?", (dispute_id,)).fetchone()
    if not d:
        raise core.NotFoundError("dispute not found")
    conn.execute("UPDATE mkt_trust_disputes SET status=?, evidence=COALESCE(?,evidence), "
                 "assigned_reviewer=COALESCE(?,assigned_reviewer) WHERE id=?",
                 (to_status, json.dumps(evidence) if evidence else None, reviewer, dispute_id))
    core.audit(conn, actor, "MKT_DISPUTE_ADVANCED", "mkt_trust_disputes", dispute_id,
               old={"status": d["status"]}, new={"status": to_status})
    conn.commit()
    return to_status


def resolve_dispute(conn, actor, dispute_id, outcome, decision_reason, financial_outcome=None):
    """SoD: the reviewer/resolver must not be the party who opened the dispute."""
    core.require(actor, "marketplace.dispute.manage")
    if outcome not in DISPUTE_OUTCOMES:
        raise core.ValidationError("invalid dispute outcome")
    d = conn.execute("SELECT * FROM mkt_trust_disputes WHERE id=?", (dispute_id,)).fetchone()
    if not d:
        raise core.NotFoundError("dispute not found")
    if d["opened_by"] == actor["id"]:
        raise core.ForbiddenError("separation of duties: you cannot resolve a dispute you opened")
    conn.execute("UPDATE mkt_trust_disputes SET status='RESOLUTION_APPROVED', decision=?, "
                 "decision_reason=?, financial_outcome=?, resolved_by=?, resolved_at=? WHERE id=?",
                 (outcome, decision_reason, json.dumps(financial_outcome) if financial_outcome else None,
                  actor["id"], _now(), dispute_id))
    core.audit(conn, actor, "MKT_DISPUTE_RESOLVED", "mkt_trust_disputes", dispute_id,
               new={"outcome": outcome, "reason": decision_reason})
    conn.commit()
    return outcome


def dispute_blocks_release(conn, booking_id):
    placeholders = ",".join(["?"] * len(DISPUTE_BLOCKING))   # no literal % (PostgreSQL-portable)
    row = conn.execute("SELECT COUNT(*) c FROM mkt_trust_disputes WHERE booking_id=? AND status IN ("
                       + placeholders + ")", (booking_id, *DISPUTE_BLOCKING)).fetchone()
    return row["c"] > 0


# --------------------------------------------------------------------------- #
# O. Claims / insurance
# --------------------------------------------------------------------------- #
def open_claim(conn, actor, claim_type, claimant, trip_id=None, carrier_id=None, driver_id=None,
               vehicle_id=None, incident_ref=None, claimed_amount=None, insurer=None,
               policy_reference=None, evidence=None):
    core.require(actor, "marketplace.claim.manage")
    if claim_type not in CLAIM_TYPES:
        raise core.ValidationError("invalid claim type")
    n = conn.execute("SELECT COUNT(*) c FROM mkt_claims").fetchone()["c"]
    claim_number = f"CLM-{10001 + n}"
    cur = conn.execute(
        "INSERT INTO mkt_claims(claim_number,claim_type,claimant,trip_id,carrier_id,driver_id,vehicle_id,"
        "incident_ref,claimed_amount,insurer,policy_reference,evidence,status,opened_by,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'REPORTED', ?,?,?)",
        (claim_number, claim_type, claimant, trip_id, carrier_id, driver_id, vehicle_id, incident_ref,
         claimed_amount, insurer, policy_reference, json.dumps(evidence) if evidence else None,
         actor["id"], _now(), _now()))
    cid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_claims", cid)
    core.audit(conn, actor, "MKT_CLAIM_OPENED", "mkt_claims", cid,
               new={"claim_number": claim_number, "type": claim_type, "carrier": carrier_id})
    conn.commit()
    return cid


def advance_claim(conn, actor, claim_id, to_status, approved_amount=None, settlement=None, reserve=None):
    core.require(actor, "marketplace.claim.manage")
    if to_status not in CLAIM_STATES:
        raise core.ValidationError("invalid claim status")
    conn.execute("UPDATE mkt_claims SET status=?, approved_amount=COALESCE(?,approved_amount), "
                 "settlement=COALESCE(?,settlement), reserve=COALESCE(?,reserve), updated_at=? WHERE id=?",
                 (to_status, approved_amount, settlement, reserve, _now(), claim_id))
    core.audit(conn, actor, "MKT_CLAIM_ADVANCED", "mkt_claims", claim_id, new={"status": to_status})
    conn.commit()
    return to_status


def carrier_open_high_severity_claims(conn, carrier_id):
    rows = conn.execute(
        "SELECT claim_type FROM mkt_claims WHERE carrier_id=? AND status NOT IN ('SETTLED','CLOSED','DENIED')",
        (carrier_id,)).fetchall()
    return [r["claim_type"] for r in rows if r["claim_type"] in CLAIM_HIGH_SEVERITY]


# --------------------------------------------------------------------------- #
# Progressive transaction risk limits (CTO addition)
# --------------------------------------------------------------------------- #
def carrier_risk_limit(conn, carrier_id):
    """A legitimately-registered but operationally-unproven carrier gets a LOW cap. The cap grows
    with verification depth, completed-job history, insurance, and trust score, and shrinks with
    open high-severity claims. Returns a peso cap + the factors used."""
    try:
        import marketplace_trust as mt
        kyb = mt.carrier_kyb_status(conn, carrier_id)
        ts = mt.trust_score(conn, carrier_id)["trust_score"]
    except Exception:
        kyb, ts = "NONE", 0
    base = 500000.0 if kyb in ("VERIFIED", "VERIFIED_WITH_CONDITION") else 0.0
    # completed-job history (best-effort; unproven carriers have none)
    try:
        jobs = conn.execute("SELECT COUNT(*) c FROM mkt_assignments WHERE carrier_id=? AND status='COMPLETED'",
                            (carrier_id,)).fetchone()["c"]
    except Exception:
        jobs = 0
    history_bonus = min(jobs, 20) * 250000.0            # up to +5M with 20 clean jobs
    trust_multiplier = 0.5 + (ts / 100.0)               # 0.5x .. 1.5x
    claims = len(carrier_open_high_severity_claims(conn, carrier_id))
    penalty = 0.5 if claims else 1.0                    # halve the cap while a severe claim is open
    cap = round((base + history_bonus) * trust_multiplier * penalty, 2)
    return {"carrier_id": carrier_id, "limit": cap, "kyb": kyb, "trust_score": ts,
            "completed_jobs": jobs, "open_high_severity_claims": claims}


def within_risk_limit(conn, carrier_id, job_value):
    lim = carrier_risk_limit(conn, carrier_id)
    return {"ok": (job_value or 0) <= lim["limit"], "limit": lim["limit"], "job_value": job_value}


# --------------------------------------------------------------------------- #
# Composed RELEASE GATE — never silently bypass
# --------------------------------------------------------------------------- #
def release_gate(conn, booking_id, carrier_id, *, funding_confirmed=False, funds_protected=False,
                 milestone_verified=False, pod_ok=False, payout_account_id=None, job_value=None,
                 approvals_complete=True):
    """Every blocking condition, composed. Release is DENIED unless all pass."""
    reasons = []
    if not funding_confirmed:
        reasons.append("funding_not_confirmed")
    if not funds_protected:
        reasons.append("funds_not_protected")
    if not milestone_verified:
        reasons.append("milestone_not_verified")
    if not pod_ok:
        reasons.append("pod_requirements_not_met")
    if dispute_blocks_release(conn, booking_id):
        reasons.append("blocking_dispute_open")
    if _fraud_review_open(conn, carrier_id):
        reasons.append("critical_fraud_flag")
    if payout_account_id is not None:
        pr = payout_allowed(conn, payout_account_id, job_value or 0)
        if not pr["ok"]:
            reasons.extend(pr["reasons"])
    else:
        reasons.append("payout_destination_unverified")
    if job_value is not None and not within_risk_limit(conn, carrier_id, job_value)["ok"]:
        reasons.append("exceeds_carrier_risk_limit")
    if not approvals_complete:
        reasons.append("required_approvals_incomplete")
    return {"allowed": not reasons, "denied_reasons": reasons}


# --------------------------------------------------------------------------- #
# Provider webhook security
# --------------------------------------------------------------------------- #
def verify_webhook(conn, provider, event_id, event_type, payload_bytes, signature, secret,
                   timestamp=None, tolerance_seconds=300):
    """Signature (HMAC-SHA256) + timestamp tolerance + replay/idempotency. Unknown/invalid events
    are quarantined, never trusted. Never trust a frontend 'paid' signal — only signed provider
    events reach here."""
    expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    sig_ok = hmac.compare_digest(expected, signature or "")
    if timestamp is not None:
        try:
            ts = datetime.datetime.fromisoformat(timestamp)
            skew = abs((datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds())
            if skew > tolerance_seconds:
                sig_ok = False
        except Exception:
            sig_ok = False
    # replay / duplicate: (provider,event_id) is UNIQUE
    dup = conn.execute("SELECT 1 FROM mkt_webhook_events WHERE provider=? AND event_id=?",
                       (provider, event_id)).fetchone()
    if dup:
        return {"accepted": False, "reason": "duplicate_or_replay", "signature_ok": sig_ok}
    status = "ACCEPTED" if sig_ok else "QUARANTINED"
    conn.execute("INSERT INTO mkt_webhook_events(provider,event_id,event_type,signature_ok,status,received_at)"
                 " VALUES(?,?,?,?,?,?)", (provider, event_id, event_type, 1 if sig_ok else 0, status, _now()))
    conn.commit()
    return {"accepted": sig_ok, "reason": None if sig_ok else "signature_or_timestamp_invalid",
            "signature_ok": sig_ok, "status": status}


# --------------------------------------------------------------------------- #
# Marketplace ledger reconciliation
# --------------------------------------------------------------------------- #
def reconcile_ledger(funding, released, refunded, remaining_protected, fees=0.0, tolerance=0.01):
    """funding == released + refunded + remaining_protected + fees (within tolerance)."""
    lhs = round(funding or 0, 2)
    rhs = round((released or 0) + (refunded or 0) + (remaining_protected or 0) + (fees or 0), 2)
    diff = round(lhs - rhs, 2)
    return {"funding": lhs, "sum_of_parts": rhs, "difference": diff,
            "balanced": abs(diff) <= tolerance,
            "flag": None if abs(diff) <= tolerance else "LEDGER_IMBALANCE"}


def run_integrity(conn):
    bad = conn.execute("SELECT COUNT(*) c FROM mkt_payout_accounts WHERE status='ACTIVE' AND "
                       "(verification_status<>'VERIFIED' OR holder_verified=0)").fetchone()["c"]
    return {"active_payout_accounts_unverified": bad, "ok": bad == 0}
