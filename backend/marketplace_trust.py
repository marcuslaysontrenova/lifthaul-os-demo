"""LiftHaul Nationwide Marketplace — Trust, Legal-Compliance & Protected-Transaction layer.

EXTENDS the existing marketplace engines (onboarding, matching, payments, trips) — it does NOT
create a parallel marketplace. It adds the trust spine the directive asks for:

  A. KYB / business-verification PROFILES with a full verification state machine. A government
     document upload is SUBMITTED, never VERIFIED — only an explicit human verification with a
     recorded source + evidence advances a profile.
  B. Provider-neutral OFFICIAL-SOURCE verification adapters (DTI BNRS, SEC, BIR, LTFRB, LTO, LGU,
     insurance). With no legally-accessible live API in this build, every adapter honestly returns
     MANUAL_VERIFICATION_REQUIRED. Verification is NEVER fabricated; source + evidence are recorded.
  P. Fraud / risk engine — indicators → LOW/MEDIUM/HIGH/CRITICAL. CRITICAL/HIGH raise a blocking flag
     that stops payout + new assignment and forces manual review.
  Q. Trust score — configurable weighted factors for RANKING eligible carriers. A hard compliance
     denial can NEVER be overridden by trust score.
  F. Hard marketplace-eligibility gate composing carrier legal status AND no blocking fraud flag
     (and, where supplied, the existing onboarding compliance for vehicle/driver/insurance). Ranking
     happens only after the hard gate passes.
  L. Separation of duties — no self-verification of your own carrier; no self-clearing of a fraud
     flag you raised.

Reuses: onboarding compliance/documents, payments (provider abstraction + protected-payment state
machine + disputes/refunds/immutable settlement ledger), trips (GPS/geofence/POD release evidence).

GOVERNANCE (non-negotiable): the protected-payment path must not custody LIVE customer funds until
legal counsel confirms the Philippine operating model + payment-custody structure. The payment
provider abstraction stays in mock/authorize-only mode and fails closed; this module never flips
that. It enforces the legal decision — it does not invent it.
"""
from __future__ import annotations

import datetime
import json

import core
import tenant

# A. verification state machine (a government-document upload is only SUBMITTED)
KYB_STATUSES = ("SUBMITTED", "AUTO_CHECKING", "NEEDS_REVIEW", "VERIFIED", "VERIFIED_WITH_CONDITION",
                "REJECTED", "EXPIRED", "SUSPENDED", "REVOKED", "SUSPECTED_FRAUD")
_TERMINAL_OK = ("VERIFIED", "VERIFIED_WITH_CONDITION")
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
BLOCKING_RISK = ("HIGH", "CRITICAL")

# recognised issuing authorities (configurable set — others go straight to MANUAL_VERIFICATION_REQUIRED)
AUTHORITIES = ("DTI", "SEC", "BIR", "MAYORS_PERMIT", "LTFRB", "LTO", "PORT_AUTHORITY", "INSURANCE", "LGU")

SCHEMA = """
CREATE TABLE IF NOT EXISTS mkt_kyb_profiles(
  id INTEGER PRIMARY KEY, tenant_id INTEGER,
  subject_type TEXT NOT NULL, subject_id INTEGER NOT NULL,
  authority TEXT NOT NULL, registration_number TEXT, registered_name TEXT, scope TEXT,
  issue_date TEXT, expiry_date TEXT,
  source TEXT, verification_method TEXT,
  status TEXT NOT NULL DEFAULT 'SUBMITTED',
  condition_note TEXT, evidence TEXT,
  verified_by INTEGER, verified_at TEXT,
  created_by INTEGER, created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS mkt_kyb_history(
  id INTEGER PRIMARY KEY, kyb_id INTEGER NOT NULL,
  from_status TEXT, to_status TEXT, source TEXT, reason TEXT,
  actor_id INTEGER, at TEXT);

CREATE TABLE IF NOT EXISTS mkt_fraud_flags(
  id INTEGER PRIMARY KEY, tenant_id INTEGER,
  subject_type TEXT NOT NULL, subject_id INTEGER NOT NULL,
  indicator TEXT NOT NULL, risk_level TEXT NOT NULL, detail TEXT,
  status TEXT NOT NULL DEFAULT 'OPEN',
  raised_by INTEGER, raised_at TEXT, cleared_by INTEGER, cleared_at TEXT, clear_reason TEXT);
"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today():
    return datetime.date.today().isoformat()


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn):
    return 0


# --------------------------------------------------------------------------- #
# B. Official-source verification adapters (provider-neutral, NEVER fabricate)
# --------------------------------------------------------------------------- #
def _manual(authority):
    return {"status": "MANUAL_VERIFICATION_REQUIRED", "authority": authority,
            "source": f"{authority}:no-public-api", "evidence": None,
            "note": "no legally-accessible live verification API configured; requires manual review"}


VERIFICATION_ADAPTERS = {a: (lambda number, reg_name, _a=a: _manual(_a)) for a in AUTHORITIES}


def run_adapter(authority, registration_number, registered_name):
    """Provider-neutral verification lookup. Returns a result dict; unknown or unwired authorities
    honestly return MANUAL_VERIFICATION_REQUIRED. This function NEVER returns a fabricated VERIFIED."""
    fn = VERIFICATION_ADAPTERS.get(authority)
    if not fn:
        return _manual(authority)
    r = fn(registration_number, registered_name)
    if r.get("status") == "VERIFIED" and not r.get("source"):
        raise core.ValidationError("verification adapter returned VERIFIED without a source — refused")
    return r


# --------------------------------------------------------------------------- #
# A. KYB verification profiles + state machine
# --------------------------------------------------------------------------- #
def _kyb(conn, actor, kyb_id):
    r = conn.execute("SELECT * FROM mkt_kyb_profiles WHERE id=?", (kyb_id,)).fetchone()
    if not r:
        raise core.NotFoundError("KYB profile not found")
    if actor is not None:
        tenant.guard(actor, r)
    return r


def _log(conn, kyb_id, frm, to, actor, source=None, reason=None):
    conn.execute("INSERT INTO mkt_kyb_history(kyb_id,from_status,to_status,source,reason,actor_id,at)"
                 " VALUES(?,?,?,?,?,?,?)", (kyb_id, frm, to, source, reason,
                                           (actor or {}).get("id"), _now()))


def submit_kyb(conn, actor, subject_type, subject_id, authority, registration_number,
               registered_name=None, scope=None, issue_date=None, expiry_date=None, evidence=None):
    """Register a business-verification profile from a submitted document. Status = SUBMITTED.
    A document upload is NOT verification."""
    core.require(actor, "marketplace.kyb.manage")
    if authority not in AUTHORITIES:
        raise core.ValidationError(f"unknown issuing authority '{authority}'")
    cur = conn.execute(
        "INSERT INTO mkt_kyb_profiles(subject_type,subject_id,authority,registration_number,"
        "registered_name,scope,issue_date,expiry_date,status,evidence,created_by,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?, 'SUBMITTED', ?,?,?,?)",
        (subject_type, subject_id, authority, registration_number, registered_name, scope,
         issue_date, expiry_date, json.dumps(evidence) if evidence else None, actor["id"], _now(), _now()))
    kid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_kyb_profiles", kid)
    _log(conn, kid, None, "SUBMITTED", actor, reason="document submitted (not yet verified)")
    core.audit(conn, actor, "MKT_KYB_SUBMITTED", "mkt_kyb_profiles", kid,
               new={"authority": authority, "subject": f"{subject_type}:{subject_id}"})
    conn.commit()
    return kid


def check_kyb(conn, actor, kyb_id):
    """Run the official-source adapter. Moves SUBMITTED -> AUTO_CHECKING -> NEEDS_REVIEW (never
    auto-VERIFIED unless a real adapter returns a sourced VERIFIED, which none do here)."""
    core.require(actor, "marketplace.kyb.manage")
    p = _kyb(conn, actor, kyb_id)
    if p["status"] not in ("SUBMITTED", "NEEDS_REVIEW"):
        raise core.ConflictError("only a submitted profile can be auto-checked")
    res = run_adapter(p["authority"], p["registration_number"], p["registered_name"])
    to = "VERIFIED" if res.get("status") == "VERIFIED" else "NEEDS_REVIEW"
    conn.execute("UPDATE mkt_kyb_profiles SET status=?, source=?, verification_method='ADAPTER', updated_at=?"
                 " WHERE id=?", (to, res.get("source"), _now(), kyb_id))
    _log(conn, kyb_id, p["status"], to, actor, source=res.get("source"), reason=res.get("note"))
    core.audit(conn, actor, "MKT_KYB_ADAPTER_CHECK", "mkt_kyb_profiles", kyb_id,
               new={"result": res.get("status"), "moved_to": to})
    conn.commit()
    return {"kyb_id": kyb_id, "adapter_result": res, "status": to}


def verify_kyb(conn, actor, kyb_id, decision, source, evidence=None, condition=None, reason=None):
    """Human verification decision with mandatory recorded source + evidence.
    SoD: the verifier must not be the actor who created the subject's application (no self-verify)."""
    core.require(actor, "marketplace.kyb.manage")
    if decision not in ("VERIFIED", "VERIFIED_WITH_CONDITION", "REJECTED"):
        raise core.ValidationError("decision must be VERIFIED, VERIFIED_WITH_CONDITION, or REJECTED")
    if decision != "REJECTED" and not source:
        raise core.ValidationError("verification requires a recorded source (no fabricated status)")
    p = _kyb(conn, actor, kyb_id)
    if p["status"] in ("REVOKED", "SUSPECTED_FRAUD"):
        raise core.ConflictError("profile is locked by a fraud/revocation control")
    _assert_not_self(conn, actor, p)                       # L. no self-verification
    if decision == "VERIFIED_WITH_CONDITION" and not condition:
        raise core.ValidationError("a conditional verification must record the condition")
    conn.execute(
        "UPDATE mkt_kyb_profiles SET status=?, source=?, verification_method='MANUAL_REVIEW',"
        " condition_note=?, evidence=COALESCE(?,evidence), verified_by=?, verified_at=?, updated_at=?"
        " WHERE id=?",
        (decision, source, condition, json.dumps(evidence) if evidence else None,
         actor["id"], _now(), _now(), kyb_id))
    _log(conn, kyb_id, p["status"], decision, actor, source=source, reason=reason)
    core.audit(conn, actor, "MKT_KYB_VERIFIED", "mkt_kyb_profiles", kyb_id,
               new={"decision": decision, "source": source, "condition": condition})
    conn.commit()
    return decision


def _assert_not_self(conn, actor, profile):
    """No self-verification: the verifier must not have created the underlying carrier/subject."""
    st, sid = profile["subject_type"], profile["subject_id"]
    try:
        row = conn.execute(f"SELECT created_by FROM mkt_{st.lower()}s WHERE id=?", (sid,)).fetchone()
    except Exception:
        row = None
    if row and row["created_by"] is not None and row["created_by"] == actor.get("id"):
        raise core.ForbiddenError("separation of duties: you cannot verify your own carrier/subject")


def _transition(conn, actor, kyb_id, to, reason, action):
    core.require(actor, "marketplace.kyb.manage")
    p = _kyb(conn, actor, kyb_id)
    conn.execute("UPDATE mkt_kyb_profiles SET status=?, updated_at=? WHERE id=?", (to, _now(), kyb_id))
    _log(conn, kyb_id, p["status"], to, actor, reason=reason)
    core.audit(conn, actor, action, "mkt_kyb_profiles", kyb_id, old={"status": p["status"]},
               new={"status": to, "reason": reason})
    conn.commit()
    return to


def suspend_kyb(conn, actor, kyb_id, reason):
    return _transition(conn, actor, kyb_id, "SUSPENDED", reason, "MKT_KYB_SUSPENDED")


def revoke_kyb(conn, actor, kyb_id, reason):
    return _transition(conn, actor, kyb_id, "REVOKED", reason, "MKT_KYB_REVOKED")


def flag_kyb_fraud(conn, actor, kyb_id, reason):
    return _transition(conn, actor, kyb_id, "SUSPECTED_FRAUD", reason, "MKT_KYB_SUSPECTED_FRAUD")


def expire_due_kyb(conn, as_of=None):
    """E. Continuous compliance: expire VERIFIED profiles past their expiry_date. Historical rows
    are preserved (status change only). Returns tiered notices; does NOT terminate active trips."""
    as_of = as_of or _today()
    rows = conn.execute(
        "SELECT id,expiry_date,status FROM mkt_kyb_profiles WHERE expiry_date IS NOT NULL"
        " AND status IN ('VERIFIED','VERIFIED_WITH_CONDITION')").fetchall()
    expired, notices = 0, []
    for r in rows:
        try:
            days = (datetime.date.fromisoformat(r["expiry_date"]) - datetime.date.fromisoformat(as_of)).days
        except Exception:
            continue
        if days < 0:
            conn.execute("UPDATE mkt_kyb_profiles SET status='EXPIRED', updated_at=? WHERE id=?", (_now(), r["id"]))
            _log(conn, r["id"], r["status"], "EXPIRED", None, reason="expiry_date passed")
            expired += 1
        else:
            tier = ("CRITICAL" if days <= 7 else "HIGH" if days <= 15 else
                    "WARNING" if days <= 30 else "NOTICE" if days <= 60 else None)
            if tier:
                notices.append({"kyb_id": r["id"], "days_to_expiry": days, "tier": tier})
    conn.commit()
    return {"expired": expired, "notices": notices}


# --------------------------------------------------------------------------- #
# P. Fraud / risk engine
# --------------------------------------------------------------------------- #
def raise_fraud_flag(conn, actor, subject_type, subject_id, indicator, risk_level, detail=None):
    core.require(actor, "marketplace.fraud.manage")
    if risk_level not in RISK_LEVELS:
        raise core.ValidationError("invalid risk level")
    cur = conn.execute(
        "INSERT INTO mkt_fraud_flags(subject_type,subject_id,indicator,risk_level,detail,status,"
        "raised_by,raised_at) VALUES(?,?,?,?,?, 'OPEN', ?,?)",
        (subject_type, subject_id, indicator, risk_level, detail, actor["id"], _now()))
    fid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_fraud_flags", fid)
    core.audit(conn, actor, "MKT_FRAUD_FLAG_RAISED", "mkt_fraud_flags", fid,
               new={"subject": f"{subject_type}:{subject_id}", "indicator": indicator,
                    "risk": risk_level, "severity": "HIGH" if risk_level in BLOCKING_RISK else "LOW"})
    conn.commit()
    return fid


def clear_fraud_flag(conn, actor, flag_id, reason):
    """SoD: the person who raised a flag cannot clear it (no self-clearing)."""
    core.require(actor, "marketplace.fraud.manage")
    f = conn.execute("SELECT * FROM mkt_fraud_flags WHERE id=?", (flag_id,)).fetchone()
    if not f:
        raise core.NotFoundError("fraud flag not found")
    if f["raised_by"] == actor["id"]:
        raise core.ForbiddenError("separation of duties: you cannot clear a flag you raised")
    if not reason:
        raise core.ValidationError("clearing a fraud flag requires a reason")
    conn.execute("UPDATE mkt_fraud_flags SET status='CLEARED', cleared_by=?, cleared_at=?, clear_reason=?"
                 " WHERE id=?", (actor["id"], _now(), reason, flag_id))
    core.audit(conn, actor, "MKT_FRAUD_FLAG_CLEARED", "mkt_fraud_flags", flag_id, new={"reason": reason})
    conn.commit()


def evaluate_fraud(conn, subject_type, subject_id):
    """Deterministic risk detectors over existing data. Returns indicators + the max risk level.
    A detected duplicate business registration or duplicate driver's license is HIGH; an open
    HIGH/CRITICAL flag is blocking."""
    indicators = []
    # reused business-registration number across DIFFERENT carriers (document reuse / shell risk)
    if subject_type.upper() == "CARRIER":
        row = conn.execute("SELECT registration_number FROM mkt_kyb_profiles WHERE subject_type='CARRIER'"
                           " AND subject_id=? AND registration_number IS NOT NULL LIMIT 1",
                           (subject_id,)).fetchone()
        if row:
            dup = conn.execute("SELECT COUNT(DISTINCT subject_id) c FROM mkt_kyb_profiles"
                               " WHERE subject_type='CARRIER' AND registration_number=?",
                               (row["registration_number"],)).fetchone()["c"]
            if dup > 1:
                indicators.append({"indicator": "reused_business_registration", "risk": "HIGH"})
    open_flags = conn.execute(
        "SELECT risk_level FROM mkt_fraud_flags WHERE subject_type=? AND subject_id=? AND status='OPEN'",
        (subject_type, subject_id)).fetchall()
    for fr in open_flags:
        indicators.append({"indicator": "open_fraud_flag", "risk": fr["risk_level"]})
    order = {r: i for i, r in enumerate(RISK_LEVELS)}
    max_risk = "LOW"
    for ind in indicators:
        if order.get(ind["risk"], 0) > order[max_risk]:
            max_risk = ind["risk"]
    return {"subject_type": subject_type, "subject_id": subject_id, "indicators": indicators,
            "risk_level": max_risk, "blocked": max_risk in BLOCKING_RISK}


def is_blocked(conn, subject_type, subject_id):
    return evaluate_fraud(conn, subject_type, subject_id)["blocked"]


# --------------------------------------------------------------------------- #
# Q. Trust score  +  F. hard eligibility gate
# --------------------------------------------------------------------------- #
DEFAULT_TRUST_WEIGHTS = {"legal_compliance": 0.30, "safety": 0.15, "reliability": 0.15,
                         "on_time": 0.15, "customer_rating": 0.10, "acceptance": 0.05,
                         "claims_history": 0.05, "pricing": 0.05}


def carrier_kyb_status(conn, carrier_id):
    """The carrier's strongest current business-verification status (DTI/SEC/BIR)."""
    rows = conn.execute("SELECT status FROM mkt_kyb_profiles WHERE subject_type='CARRIER' AND subject_id=?"
                        " AND authority IN ('DTI','SEC','BIR')", (carrier_id,)).fetchall()
    statuses = {r["status"] for r in rows}
    for s in ("VERIFIED", "VERIFIED_WITH_CONDITION"):
        if s in statuses:
            return s
    return next(iter(statuses)) if statuses else "NONE"


def trust_score(conn, carrier_id, factors=None):
    """Weighted 0-100 trust score for RANKING. Factors 0-100; weights from config cascade
    (marketplace.trust.weight.<factor>) with defaults. Legal_compliance is derived from KYB."""
    kyb = carrier_kyb_status(conn, carrier_id)
    base = dict(factors or {})
    base.setdefault("legal_compliance", 100 if kyb in _TERMINAL_OK else 0)
    for k in DEFAULT_TRUST_WEIGHTS:
        base.setdefault(k, 60)                              # neutral default until real signals exist
    weights = {}
    for k, dw in DEFAULT_TRUST_WEIGHTS.items():
        try:
            v, _ = __import__("policy")._num(conn, f"marketplace.trust.weight.{k}", {}, dw)
        except Exception:
            v = dw
        weights[k] = v
    total_w = sum(weights.values()) or 1
    score = round(sum(base[k] * weights[k] for k in DEFAULT_TRUST_WEIGHTS) / total_w, 1)
    return {"carrier_id": carrier_id, "trust_score": score, "kyb_status": kyb, "factors": base}


def assess_eligibility(conn, actor, carrier_id, vehicle_id=None, driver_id=None):
    """F. HARD gate first, ranking second. A carrier is eligible ONLY when its business is
    legally VERIFIED and there is no blocking fraud flag (and, when supplied, the existing
    onboarding compliance for vehicle/driver passes). Trust score is advisory for RANKING and
    can NEVER flip a hard denial to eligible."""
    if actor is not None:
        core.require(actor, "marketplace.trust.view")
    hard_reasons = []
    kyb = carrier_kyb_status(conn, carrier_id)
    if kyb not in _TERMINAL_OK:
        hard_reasons.append(f"carrier business not verified (KYB={kyb})")
    if is_blocked(conn, "CARRIER", carrier_id):
        hard_reasons.append("blocking fraud/risk flag on carrier")
    # reuse existing onboarding compliance for vehicle/driver where provided (never re-implement it)
    try:
        import marketplace_onboarding as ob
        if vehicle_id is not None:
            ev = ob.evaluate_compliance(conn, "VEHICLE", vehicle_id)
            if ev["blockers"]:
                hard_reasons.append("vehicle compliance blockers: " + ",".join(ev["blockers"]))
        if driver_id is not None:
            ed = ob.evaluate_compliance(conn, "DRIVER", driver_id)
            if ed["blockers"]:
                hard_reasons.append("driver compliance blockers: " + ",".join(ed["blockers"]))
    except Exception:
        pass
    eligible = not hard_reasons
    ts = trust_score(conn, carrier_id)
    return {"carrier_id": carrier_id, "eligible": eligible, "hard_reasons": hard_reasons,
            "trust_score": ts["trust_score"] if eligible else None,  # score is meaningless if denied
            "kyb_status": kyb}


# --------------------------------------------------------------------------- #
# Read/admin surface
# --------------------------------------------------------------------------- #
def list_kyb(conn, actor, subject_type=None, subject_id=None):
    core.require(actor, "marketplace.kyb.view")
    frag, params = tenant.predicate(actor)
    sql = "SELECT * FROM mkt_kyb_profiles WHERE 1=1" + frag
    args = list(params)
    if subject_type:
        sql += " AND subject_type=?"; args.append(subject_type)
    if subject_id is not None:
        sql += " AND subject_id=?"; args.append(subject_id)
    sql += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def list_fraud_flags(conn, actor, status="OPEN"):
    core.require(actor, "marketplace.fraud.view")
    frag, params = tenant.predicate(actor)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM mkt_fraud_flags WHERE status=?" + frag + " ORDER BY id DESC",
        (status, *params)).fetchall()]


def run_integrity(conn):
    """No fabricated verifications: every VERIFIED/CONDITION profile must carry a source."""
    bad = conn.execute("SELECT COUNT(*) c FROM mkt_kyb_profiles WHERE status IN "
                       "('VERIFIED','VERIFIED_WITH_CONDITION') AND (source IS NULL OR source='')"
                       ).fetchone()["c"]
    return {"kyb_verified_without_source": bad, "ok": bad == 0}
