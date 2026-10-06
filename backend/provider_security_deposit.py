"""Refundable provider-security-deposit controls for LiftHaul.

This is a *liability* and safeguarding sub-ledger, never revenue, an earnings-wallet balance,
insurance, or proof that LiftHaul may hold customer/provider funds itself.  Real money must remain
with a contracted BSP-supervised/registered payment provider unless Philippine counsel approves a
different structure.

The seeded policy is deliberately DRAFT with a zero amount.  It cannot affect onboarding or
assignments until two-person approval records the legal memo, provider contract, terms version,
refund SLA and a non-zero risk-based amount.
"""
from __future__ import annotations

import datetime
import json

import core
import tenant

POLICY_STATES = ("DRAFT", "ACTIVE", "RETIRED")
DEPOSIT_STATES = ("PENDING_FUNDING", "FUNDED", "BELOW_MINIMUM", "HELD",
                  "REFUND_PENDING", "REFUNDED")
PERMITTED_USES = {
    "RESOLVED_CARGO_LOSS",
    "RESOLVED_CARGO_DAMAGE",
    "RESOLVED_PROPERTY_DAMAGE",
    "RESOLVED_PLATFORM_OBLIGATION",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS mkt_provider_deposit_policies(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, code TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  risk_band TEXT NOT NULL DEFAULT 'STANDARD', provider_scope TEXT NOT NULL DEFAULT 'CARRIER',
  required_amount REAL NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'PHP',
  refund_sla_business_days INTEGER NOT NULL DEFAULT 30,
  permitted_uses TEXT NOT NULL, provider_name TEXT,
  legal_memo_ref TEXT, provider_contract_ref TEXT, tax_memo_ref TEXT, terms_version TEXT,
  status TEXT NOT NULL DEFAULT 'DRAFT', created_by INTEGER, approved_by INTEGER,
  effective_from TEXT, created_at TEXT, updated_at TEXT,
  UNIQUE(tenant_id, code, version));

CREATE TABLE IF NOT EXISTS mkt_provider_security_deposits(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, carrier_id INTEGER NOT NULL,
  policy_id INTEGER NOT NULL, required_amount REAL NOT NULL, funded_amount REAL NOT NULL DEFAULT 0,
  applied_amount REAL NOT NULL DEFAULT 0, refunded_amount REAL NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT 'PHP', provider_name TEXT NOT NULL,
  provider_account_reference TEXT, status TEXT NOT NULL DEFAULT 'PENDING_FUNDING',
  funded_at TEXT, refund_due_at TEXT, created_at TEXT, updated_at TEXT,
  UNIQUE(tenant_id, carrier_id, policy_id));

CREATE TABLE IF NOT EXISTS mkt_provider_deposit_events(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, deposit_id INTEGER NOT NULL,
  event_type TEXT NOT NULL, amount REAL NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'PHP',
  idempotency_key TEXT NOT NULL, provider_reference TEXT, claim_id INTEGER, dispute_id INTEGER,
  reason TEXT, evidence TEXT, created_by INTEGER, approved_by INTEGER, created_at TEXT,
  UNIQUE(tenant_id, idempotency_key));

CREATE TABLE IF NOT EXISTS mkt_provider_deposit_applications(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, deposit_id INTEGER NOT NULL,
  use_code TEXT NOT NULL, amount REAL NOT NULL, claim_id INTEGER, dispute_id INTEGER,
  notice_reference TEXT NOT NULL, evidence TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL', requested_by INTEGER NOT NULL,
  approved_by INTEGER, provider_reference TEXT, requested_at TEXT, applied_at TEXT);

CREATE TABLE IF NOT EXISTS mkt_provider_deposit_refunds(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, deposit_id INTEGER NOT NULL,
  amount REAL NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
  requested_by INTEGER NOT NULL, approved_by INTEGER, provider_reference TEXT,
  requested_at TEXT, due_at TEXT, paid_at TEXT);
"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _add_business_days(start, days):
    current = start
    remaining = int(days)
    while remaining > 0:
        current += datetime.timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn):
    """Create design intent only. This does not authorize collection."""
    if not conn.execute(
        "SELECT 1 FROM mkt_provider_deposit_policies WHERE tenant_id IS NULL AND code=? AND version=1",
        ("LH_REFUNDABLE_PROVIDER_SECURITY",)).fetchone():
        conn.execute(
            "INSERT INTO mkt_provider_deposit_policies(tenant_id,code,version,risk_band,provider_scope,"
            "required_amount,currency,refund_sla_business_days,permitted_uses,status,created_at,updated_at) "
            "VALUES(NULL,?,1,'STANDARD','CARRIER',0,'PHP',30,?,'DRAFT',?,?)",
            ("LH_REFUNDABLE_PROVIDER_SECURITY", json.dumps(sorted(PERMITTED_USES)), _now(), _now()))
    conn.commit()


def propose_policy(conn, actor, *, code, version, risk_band, provider_scope, required_amount,
                   refund_sla_business_days, provider_name, terms_version,
                   permitted_uses=None):
    core.require(actor, "marketplace.payout.manage")
    amount = round(float(required_amount), 2)
    if amount <= 0:
        raise core.ValidationError("a deposit policy requires a positive, risk-approved amount")
    uses = set(permitted_uses or PERMITTED_USES)
    if not uses or not uses.issubset(PERMITTED_USES):
        raise core.ValidationError("deposit policy contains an unapproved use")
    cur = conn.execute(
        "INSERT INTO mkt_provider_deposit_policies(tenant_id,code,version,risk_band,provider_scope,"
        "required_amount,currency,refund_sla_business_days,permitted_uses,provider_name,terms_version,"
        "status,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,'PHP',?,?,?,?, 'DRAFT',?,?,?)",
        (actor.get("tenant_id"), code, int(version), risk_band, provider_scope, amount,
         int(refund_sla_business_days), json.dumps(sorted(uses)), str(provider_name).upper(),
         terms_version, actor["id"], _now(), _now()))
    pid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_provider_deposit_policies", pid)
    core.audit(conn, actor, "MKT_PROVIDER_DEPOSIT_POLICY_PROPOSED", "mkt_provider_deposit_policies", pid,
               new={"code": code, "version": version, "risk_band": risk_band, "amount": amount})
    conn.commit()
    return pid


def activate_policy(conn, actor, policy_id, *, legal_memo_ref, provider_contract_ref,
                    tax_memo_ref, terms_version):
    """Two-person, evidence-backed activation. A competitor policy is never sufficient evidence."""
    core.require(actor, "marketplace.payout.approve")
    row = conn.execute("SELECT * FROM mkt_provider_deposit_policies WHERE id=?", (int(policy_id),)).fetchone()
    if not row:
        raise core.NotFoundError("deposit policy not found")
    if row["status"] != "DRAFT":
        raise core.ValidationError("only a draft deposit policy can be activated")
    if row["created_by"] == actor["id"]:
        raise core.ForbiddenError("separation of duties: proposer cannot activate deposit policy")
    if float(row["required_amount"] or 0) <= 0:
        raise core.ValidationError("deposit amount has not been approved")
    if not all((legal_memo_ref, provider_contract_ref, tax_memo_ref, terms_version, row["provider_name"])):
        raise core.ValidationError("legal, provider, tax and accepted-terms evidence are required")
    if str(row["provider_name"]).upper() == "MOCK":
        import public_provider as environment
        if environment.env_posture()["treated_as_production"]:
            raise core.ForbiddenError("MOCK deposit provider is forbidden in production")
    conn.execute(
        "UPDATE mkt_provider_deposit_policies SET legal_memo_ref=?,provider_contract_ref=?,tax_memo_ref=?,"
        "terms_version=?,status='ACTIVE',approved_by=?,effective_from=?,updated_at=? WHERE id=?",
        (legal_memo_ref, provider_contract_ref, tax_memo_ref, terms_version, actor["id"], _now(), _now(), row["id"]))
    core.audit(conn, actor, "MKT_PROVIDER_DEPOSIT_POLICY_ACTIVATED", "mkt_provider_deposit_policies", row["id"],
               new={"legal_memo_ref": legal_memo_ref, "provider_contract_ref": provider_contract_ref,
                    "tax_memo_ref": tax_memo_ref, "terms_version": terms_version})
    conn.commit()
    return "ACTIVE"


def active_policy(conn, tenant_id, risk_band="STANDARD"):
    row = conn.execute(
        "SELECT * FROM mkt_provider_deposit_policies WHERE (tenant_id=? OR tenant_id IS NULL) "
        "AND risk_band=? AND status='ACTIVE' ORDER BY CASE WHEN tenant_id=? THEN 0 ELSE 1 END,version DESC LIMIT 1",
        (tenant_id, risk_band, tenant_id)).fetchone()
    return dict(row) if row else None


def ensure_requirement(conn, actor, carrier_id, risk_band="STANDARD"):
    policy = active_policy(conn, actor.get("tenant_id"), risk_band)
    if not policy:
        return {"required": False, "status": "POLICY_INACTIVE",
                "note": "No deposit may be collected until an approved policy is active."}
    row = conn.execute(
        "SELECT * FROM mkt_provider_security_deposits WHERE tenant_id=? AND carrier_id=? AND policy_id=?",
        (actor.get("tenant_id"), int(carrier_id), policy["id"])).fetchone()
    if not row:
        cur = conn.execute(
            "INSERT INTO mkt_provider_security_deposits(tenant_id,carrier_id,policy_id,required_amount,"
            "currency,provider_name,status,created_at,updated_at) VALUES(?,?,?,?,?,?, 'PENDING_FUNDING',?,?)",
            (actor.get("tenant_id"), int(carrier_id), policy["id"], policy["required_amount"],
             policy["currency"], policy["provider_name"], _now(), _now()))
        tenant.stamp(conn, actor, "mkt_provider_security_deposits", cur.lastrowid)
        conn.commit()
        row = conn.execute("SELECT * FROM mkt_provider_security_deposits WHERE id=?", (cur.lastrowid,)).fetchone()
    return {"required": True, **dict(row), "policy_code": policy["code"],
            "refund_sla_business_days": policy["refund_sla_business_days"]}


def record_funding(conn, actor, carrier_id, amount, *, provider_reference, idempotency_key,
                   provider_account_reference=None, risk_band="STANDARD"):
    """Record provider-verified funding. A screenshot or manual assertion is not sufficient."""
    core.require(actor, "marketplace.payout.approve")
    if not provider_reference:
        raise core.ValidationError("verified payment-provider reference required")
    req = ensure_requirement(conn, actor, carrier_id, risk_band)
    if not req.get("required"):
        raise core.ForbiddenError("deposit collection is not authorized")
    prior = conn.execute(
        "SELECT * FROM mkt_provider_deposit_events WHERE tenant_id=? AND idempotency_key=?",
        (actor.get("tenant_id"), str(idempotency_key))).fetchone()
    if prior:
        return dict(prior)
    amount = round(float(amount), 2)
    if amount <= 0:
        raise core.ValidationError("funding amount must be positive")
    new_funded = round(float(req["funded_amount"]) + amount, 2)
    available = new_funded - float(req["applied_amount"]) - float(req["refunded_amount"])
    state = "FUNDED" if available >= float(req["required_amount"]) else "PENDING_FUNDING"
    conn.execute(
        "UPDATE mkt_provider_security_deposits SET funded_amount=?,provider_account_reference=COALESCE(?,provider_account_reference),"
        "status=?,funded_at=?,updated_at=? WHERE id=?",
        (new_funded, provider_account_reference, state, _now(), _now(), req["id"]))
    cur = conn.execute(
        "INSERT INTO mkt_provider_deposit_events(tenant_id,deposit_id,event_type,amount,currency,idempotency_key,"
        "provider_reference,created_by,created_at) VALUES(?,?,'FUNDING_VERIFIED',?,?,?,?,?,?)",
        (actor.get("tenant_id"), req["id"], amount, req["currency"], str(idempotency_key),
         provider_reference, actor["id"], _now()))
    tenant.stamp(conn, actor, "mkt_provider_deposit_events", cur.lastrowid)
    core.audit(conn, actor, "MKT_PROVIDER_DEPOSIT_FUNDED", "mkt_provider_security_deposits", req["id"],
               new={"amount": amount, "provider_reference": provider_reference, "status": state})
    conn.commit()
    return dict(conn.execute("SELECT * FROM mkt_provider_deposit_events WHERE id=?", (cur.lastrowid,)).fetchone())


def gate(conn, tenant_id, carrier_id, risk_band="STANDARD"):
    policy = active_policy(conn, tenant_id, risk_band)
    if not policy:
        return {"ok": True, "required": False, "reasons": ["deposit_policy_inactive"]}
    row = conn.execute(
        "SELECT * FROM mkt_provider_security_deposits WHERE tenant_id=? AND carrier_id=? AND policy_id=?",
        (tenant_id, int(carrier_id), policy["id"])).fetchone()
    if not row:
        return {"ok": False, "required": True, "reasons": ["deposit_not_funded"]}
    available = round(float(row["funded_amount"]) - float(row["applied_amount"]) - float(row["refunded_amount"]), 2)
    reasons = []
    if row["status"] in ("HELD", "REFUND_PENDING", "REFUNDED"):
        reasons.append("deposit_not_operational")
    if available < float(row["required_amount"]):
        reasons.append("deposit_below_minimum")
    return {"ok": not reasons, "required": True, "reasons": reasons,
            "required_amount": row["required_amount"], "available_amount": available,
            "status": row["status"]}


def request_application(conn, actor, deposit_id, *, use_code, amount, notice_reference,
                        evidence, claim_id=None, dispute_id=None):
    """Request a deduction only after an adjudicated claim/dispute and documented due notice."""
    core.require(actor, "marketplace.payout.manage")
    if use_code not in PERMITTED_USES:
        raise core.ValidationError("deposit use is not permitted")
    if not notice_reference or not evidence or not (claim_id or dispute_id):
        raise core.ValidationError("resolved case, due notice and evidence are required")
    dep = conn.execute("SELECT * FROM mkt_provider_security_deposits WHERE id=?", (int(deposit_id),)).fetchone()
    if not dep:
        raise core.NotFoundError("security deposit not found")
    if dep["tenant_id"] != actor.get("tenant_id"):
        raise core.ForbiddenError("security deposit belongs to another tenant")
    amount = round(float(amount), 2)
    available = float(dep["funded_amount"]) - float(dep["applied_amount"]) - float(dep["refunded_amount"])
    if amount <= 0 or amount > available:
        raise core.ValidationError("application exceeds available refundable deposit")
    if claim_id:
        case = conn.execute("SELECT * FROM mkt_claims WHERE id=? AND carrier_id=?", (claim_id, dep["carrier_id"])).fetchone()
        if not case or case["status"] not in ("APPROVED", "PARTIAL", "SETTLED", "CLOSED"):
            raise core.ValidationError("claim has not reached an eligible adjudicated state")
        ceiling = float(case["approved_amount"] or case["settlement"] or 0)
        if ceiling <= 0 or amount > ceiling:
            raise core.ValidationError("application exceeds adjudicated claim amount")
    if dispute_id:
        case = conn.execute("SELECT * FROM mkt_trust_disputes WHERE id=? AND carrier_id=?", (dispute_id, dep["carrier_id"])).fetchone()
        if not case or case["status"] not in ("RESOLUTION_APPROVED", "CLOSED"):
            raise core.ValidationError("dispute has not reached an eligible adjudicated state")
    cur = conn.execute(
        "INSERT INTO mkt_provider_deposit_applications(tenant_id,deposit_id,use_code,amount,claim_id,dispute_id,"
        "notice_reference,evidence,status,requested_by,requested_at) VALUES(?,?,?,?,?,?,?,?, 'PENDING_APPROVAL',?,?)",
        (dep["tenant_id"], dep["id"], use_code, amount, claim_id, dispute_id,
         notice_reference, json.dumps(evidence, sort_keys=True), actor["id"], _now()))
    tenant.stamp(conn, actor, "mkt_provider_deposit_applications", cur.lastrowid)
    conn.commit()
    return cur.lastrowid


def confirm_application(conn, actor, application_id, *, provider_reference):
    """Checker confirms actual provider movement; approval alone never reduces the deposit."""
    core.require(actor, "marketplace.payout.approve")
    app = conn.execute("SELECT * FROM mkt_provider_deposit_applications WHERE id=?", (int(application_id),)).fetchone()
    if not app:
        raise core.NotFoundError("deposit application not found")
    if app["tenant_id"] != actor.get("tenant_id"):
        raise core.ForbiddenError("deposit application belongs to another tenant")
    if app["status"] == "APPLIED":
        return "APPLIED"
    if app["requested_by"] == actor["id"]:
        raise core.ForbiddenError("separation of duties: requester cannot confirm a deduction")
    if not provider_reference:
        raise core.ValidationError("provider confirmation reference required")
    dep = conn.execute("SELECT * FROM mkt_provider_security_deposits WHERE id=?", (app["deposit_id"],)).fetchone()
    new_applied = round(float(dep["applied_amount"]) + float(app["amount"]), 2)
    available = float(dep["funded_amount"]) - new_applied - float(dep["refunded_amount"])
    state = "FUNDED" if available >= float(dep["required_amount"]) else "BELOW_MINIMUM"
    conn.execute("UPDATE mkt_provider_security_deposits SET applied_amount=?,status=?,updated_at=? WHERE id=?",
                 (new_applied, state, _now(), dep["id"]))
    conn.execute("UPDATE mkt_provider_deposit_applications SET status='APPLIED',approved_by=?,provider_reference=?,"
                 "applied_at=? WHERE id=?", (actor["id"], provider_reference, _now(), app["id"]))
    event_key = f"deposit-application:{app['id']}"
    conn.execute(
        "INSERT INTO mkt_provider_deposit_events(tenant_id,deposit_id,event_type,amount,currency,idempotency_key,"
        "provider_reference,claim_id,dispute_id,reason,evidence,created_by,approved_by,created_at) "
        "VALUES(?,?,'APPLIED',?,'PHP',?,?,?,?,?,?,?, ?,?)",
        (dep["tenant_id"], dep["id"], app["amount"], event_key, provider_reference,
         app["claim_id"], app["dispute_id"], app["use_code"], app["evidence"],
         app["requested_by"], actor["id"], _now()))
    core.audit(conn, actor, "MKT_PROVIDER_DEPOSIT_APPLIED", "mkt_provider_security_deposits", dep["id"],
               new={"amount": app["amount"], "use_code": app["use_code"], "status": state})
    conn.commit()
    return "APPLIED"


def request_refund(conn, actor, carrier_id):
    """Request the unused balance. Open cases may pause only until a documented decision."""
    core.require(actor, "marketplace.payout.manage")
    dep = conn.execute(
        "SELECT * FROM mkt_provider_security_deposits WHERE tenant_id=? AND carrier_id=? "
        "AND status IN('FUNDED','BELOW_MINIMUM','HELD') ORDER BY id DESC LIMIT 1",
        (actor.get("tenant_id"), int(carrier_id))).fetchone()
    if not dep:
        raise core.NotFoundError("refundable security deposit not found")
    carrier = conn.execute("SELECT status FROM mkt_carriers WHERE id=?", (int(carrier_id),)).fetchone()
    if carrier and carrier["status"] == "ACTIVE":
        raise core.ValidationError("offboarding or suspension must begin before a deposit refund")
    open_claims = conn.execute(
        "SELECT COUNT(*) c FROM mkt_claims WHERE carrier_id=? AND status NOT IN('DENIED','SETTLED','CLOSED')",
        (int(carrier_id),)).fetchone()["c"]
    open_disputes = conn.execute(
        "SELECT COUNT(*) c FROM mkt_trust_disputes WHERE carrier_id=? "
        "AND status NOT IN('RESOLUTION_APPROVED','CLOSED')", (int(carrier_id),)).fetchone()["c"]
    if open_claims or open_disputes:
        raise core.ValidationError("refund paused by an open claim or dispute; show case status and deadline")
    amount = round(float(dep["funded_amount"]) - float(dep["applied_amount"]) - float(dep["refunded_amount"]), 2)
    if amount <= 0:
        raise core.ValidationError("no refundable balance")
    pol = conn.execute("SELECT refund_sla_business_days FROM mkt_provider_deposit_policies WHERE id=?",
                       (dep["policy_id"],)).fetchone()
    due = _add_business_days(datetime.datetime.now(datetime.timezone.utc), int(pol[0]))
    cur = conn.execute(
        "INSERT INTO mkt_provider_deposit_refunds(tenant_id,deposit_id,amount,status,requested_by,requested_at,due_at) "
        "VALUES(?,?,?,'PENDING_REVIEW',?,?,?)",
        (dep["tenant_id"], dep["id"], amount, actor["id"], _now(), due.isoformat(timespec="seconds")))
    tenant.stamp(conn, actor, "mkt_provider_deposit_refunds", cur.lastrowid)
    conn.execute("UPDATE mkt_provider_security_deposits SET status='REFUND_PENDING',refund_due_at=?,updated_at=? WHERE id=?",
                 (due.isoformat(timespec="seconds"), _now(), dep["id"]))
    conn.commit()
    return dict(conn.execute("SELECT * FROM mkt_provider_deposit_refunds WHERE id=?", (cur.lastrowid,)).fetchone())


def confirm_refund(conn, actor, refund_id, *, provider_reference):
    core.require(actor, "marketplace.payout.approve")
    row = conn.execute("SELECT * FROM mkt_provider_deposit_refunds WHERE id=?", (int(refund_id),)).fetchone()
    if not row:
        raise core.NotFoundError("deposit refund not found")
    if row["tenant_id"] != actor.get("tenant_id"):
        raise core.ForbiddenError("deposit refund belongs to another tenant")
    if row["status"] == "PAID":
        return "PAID"
    if row["requested_by"] == actor["id"]:
        raise core.ForbiddenError("separation of duties: requester cannot confirm refund")
    if not provider_reference:
        raise core.ValidationError("provider refund reference required")
    dep = conn.execute("SELECT * FROM mkt_provider_security_deposits WHERE id=?", (row["deposit_id"],)).fetchone()
    new_refunded = round(float(dep["refunded_amount"]) + float(row["amount"]), 2)
    conn.execute("UPDATE mkt_provider_deposit_refunds SET status='PAID',approved_by=?,provider_reference=?,paid_at=? WHERE id=?",
                 (actor["id"], provider_reference, _now(), row["id"]))
    conn.execute("UPDATE mkt_provider_security_deposits SET refunded_amount=?,status='REFUNDED',updated_at=? WHERE id=?",
                 (new_refunded, _now(), dep["id"]))
    conn.execute(
        "INSERT INTO mkt_provider_deposit_events(tenant_id,deposit_id,event_type,amount,currency,idempotency_key,"
        "provider_reference,created_by,approved_by,created_at) VALUES(?,?,'REFUNDED',?,'PHP',?,?,?,?,?)",
        (dep["tenant_id"], dep["id"], row["amount"], f"deposit-refund:{row['id']}", provider_reference,
         row["requested_by"], actor["id"], _now()))
    core.audit(conn, actor, "MKT_PROVIDER_DEPOSIT_REFUNDED", "mkt_provider_security_deposits", dep["id"],
               new={"amount": row["amount"], "provider_reference": provider_reference})
    conn.commit()
    return "PAID"


def statement(conn, actor, carrier_id):
    dep = conn.execute(
        "SELECT * FROM mkt_provider_security_deposits WHERE tenant_id=? AND carrier_id=? ORDER BY id DESC LIMIT 1",
        (actor.get("tenant_id"), int(carrier_id))).fetchone()
    if not dep:
        return {"required": False, "status": "NOT_ESTABLISHED", "events": []}
    events = conn.execute("SELECT * FROM mkt_provider_deposit_events WHERE deposit_id=? ORDER BY id", (dep["id"],)).fetchall()
    available = round(float(dep["funded_amount"]) - float(dep["applied_amount"]) - float(dep["refunded_amount"]), 2)
    return {**dict(dep), "available_amount": available, "events": [dict(e) for e in events],
            "classification": "REFUNDABLE_LIABILITY_NOT_REVENUE",
            "notice": "This security deposit is not insurance and does not cap provider liability."}
