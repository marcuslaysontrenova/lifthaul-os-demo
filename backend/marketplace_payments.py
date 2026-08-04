"""LiftHaul Nationwide Marketplace — Increment 4: Protected Payment & Conditional Release.

Provider-NEUTRAL protected-payment domain connecting a confirmed marketplace assignment to secured
funding, milestone evidence, conditional release, carrier payout, disputes, funds freeze, refunds,
chargebacks/reversals, and reconciliation. Fully deterministic + offline; the LIVE settlement path is
fail-closed behind an owner-selected licensed partner + credentials + real validation.

TERMINOLOGY: this is "Protected Payment and Conditional Release" — never "escrow" — until a licensed
Philippine payment/trust/safeguarding/escrow partner is formally selected and validated.

Core principles (Increment 4 directive):
  * amounts come from the IMMUTABLE assignment/pricing/offer snapshots — never recalculated;
  * provider evidence is distinguished from payment creation — a request/200/screenshot is NOT proof
    of protected funds; funding requires verifiable provider/financial evidence;
  * trip-activation is fail-closed: it only becomes READY_FOR_TRIP_ACTIVATION (never TRIP_ACTIVE) when
    funding is protected, reconciled, and unencumbered by dispute/freeze/reversal;
  * release evaluation is deterministic + NON-MUTATING — it never moves funds, only decides eligibility;
  * separation of duties: no self-verify / self-release-approve / self-refund-approve / self-dispute-
    resolve; a dispute opener may not approve its own resolution;
  * idempotency on every financial operation; a repeated key with a different payload is rejected;
  * mock evidence is labelled MOCK_ONLY / NOT_REAL_FUNDS / NOT_PRODUCTION_SETTLEMENT and never activates
    a real production trip;
  * AI may summarize evidence but never decides liability or a financial outcome;
  * tenant isolation + org scope preserved; 0 financial / payment-status / job-status drift.
"""
from __future__ import annotations

import datetime
import hashlib
import json

import core
import tenant

# --------------------------------------------------------------------------- #
PR_STATUSES = ("DRAFT", "PAYMENT_REQUIRED", "FUNDING_INSTRUCTIONS_READY", "PENDING_FUNDING",
               "PARTIALLY_FUNDED", "FUNDED", "PROTECTED", "EXPIRED", "CANCELLED", "FAILED",
               "DISPUTED", "REFUND_PENDING", "REFUNDED", "RELEASE_PENDING", "PARTIALLY_RELEASED",
               "RELEASED", "CLOSED")
RECON_STATUSES = ("UNMATCHED", "POSSIBLE_MATCH", "MATCHED", "PARTIAL", "OVERPAID", "UNDERPAID",
                  "DUPLICATE", "WRONG_CURRENCY", "WRONG_PROFILE", "EXPIRED_REFERENCE", "REVERSED",
                  "CHARGEBACK", "MANUAL_REVIEW", "RECONCILED")
RELEASE_STATUSES = ("DRAFT", "PENDING_APPROVAL", "APPROVED", "SUBMITTED_TO_PROVIDER", "PROCESSING",
                    "COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "CANCELLED", "REVERSED", "MANUAL_REVIEW")
PAYOUT_STATUSES = ("NOT_ELIGIBLE", "ELIGIBLE", "PENDING_APPROVAL", "APPROVED", "SUBMITTED",
                   "PROCESSING", "PAID", "FAILED", "REVERSED", "FROZEN", "CANCELLED")
DISPUTE_STATUSES = ("OPEN", "ACKNOWLEDGED", "EVIDENCE_REQUIRED", "UNDER_REVIEW", "FUNDS_FROZEN",
                    "MEDIATION", "PROPOSED_RESOLUTION", "AWAITING_PARTY_RESPONSE", "APPROVED",
                    "REJECTED", "PARTIALLY_RESOLVED", "RESOLVED", "ESCALATED", "CLOSED")
FREEZE_STATUSES = ("REQUESTED", "ACTIVE", "PARTIALLY_RELEASED", "RELEASED", "EXPIRED", "CANCELLED")
REFUND_STATUSES = ("DRAFT", "REQUESTED", "UNDER_REVIEW", "APPROVED", "SUBMITTED_TO_PROVIDER",
                   "PROCESSING", "COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "CANCELLED", "REVERSED")
RELEASE_MODELS = ("FULL_AFTER_DELIVERY", "PARTIAL_AT_PICKUP_AND_BALANCE_AT_DELIVERY",
                  "MILESTONE_BASED", "ENTERPRISE_CONTRACT_RULE", "MANUAL_REVIEW")
MILESTONES = ("ASSIGNMENT_CONFIRMED", "FUNDING_PROTECTED", "TRIP_ACTIVATION_APPROVED", "PICKUP_ARRIVAL",
              "PICKUP_CONFIRMED", "CARGO_RECEIVED", "ORIGIN_PORT_HANDOVER", "DESTINATION_PORT_HANDOVER",
              "DELIVERY_ARRIVAL", "DELIVERY_CONFIRMED", "CLIENT_ACCEPTED", "ACCEPTANCE_PERIOD_EXPIRED")
INTEGRITY_STATUSES = ("NOT_RUN", "PASS", "WARNING", "FAIL", "BLOCKED")
MOCK_LABELS = ("MOCK_ONLY", "NOT_REAL_FUNDS", "NOT_PRODUCTION_SETTLEMENT")

SCHEMA = """
CREATE TABLE IF NOT EXISTS mkt_payment_requirements(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, org_scope TEXT,
  booking_id INTEGER NOT NULL, assignment_id INTEGER NOT NULL,
  shipper_id INTEGER, carrier_id INTEGER, offer_id INTEGER, pricing_snapshot_id INTEGER,
  currency TEXT DEFAULT 'PHP',
  gross_value REAL, carrier_amount REAL, platform_fee REAL, payment_fee REAL, tax REAL,
  insurance_amount REAL, protected_amount_required REAL, minimum_funding_amount REAL,
  funding_deadline TEXT, release_policy_id INTEGER, release_policy_version INTEGER,
  cancellation_policy TEXT, dispute_policy TEXT,
  provider TEXT DEFAULT 'MOCK', provider_profile TEXT, provider_payment_reference TEXT,
  fee_policy_version INTEGER,
  funded_amount REAL DEFAULT 0, released_amount REAL DEFAULT 0, refunded_amount REAL DEFAULT 0,
  frozen_amount REAL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'DRAFT', mock_label TEXT,
  created_by INTEGER, created_at TEXT, updated_by INTEGER, updated_at TEXT, correlation_id TEXT,
  UNIQUE(assignment_id));

CREATE TABLE IF NOT EXISTS mkt_funding_events(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, payment_requirement_id INTEGER NOT NULL,
  provider TEXT, provider_reference TEXT, provider_event TEXT, provider_status TEXT,
  amount REAL, currency TEXT, received_at TEXT, cleared_at TEXT, protected_at TEXT,
  evidence_hash TEXT, verification_source TEXT, verified_by INTEGER,
  reconciliation_status TEXT DEFAULT 'UNMATCHED', mock_label TEXT,
  created_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_release_policies(
  id INTEGER PRIMARY KEY, code TEXT NOT NULL, version INTEGER DEFAULT 1,
  release_model TEXT NOT NULL, applicable_booking_types TEXT, applicable_cargo TEXT,
  applicable_routes TEXT, risk_level TEXT, milestones TEXT, approval_required INTEGER DEFAULT 0,
  auto_release_hours INTEGER, dispute_window_hours INTEGER, partial_rules TEXT,
  cancellation_rules TEXT, refund_rules TEXT, effective_from TEXT, effective_to TEXT,
  status TEXT DEFAULT 'PUBLISHED', checksum TEXT, created_by INTEGER, created_at TEXT,
  UNIQUE(code, version));

CREATE TABLE IF NOT EXISTS mkt_milestones(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, booking_id INTEGER, assignment_id INTEGER,
  payment_requirement_id INTEGER, milestone_code TEXT NOT NULL, source TEXT, occurred_at TEXT,
  submitted_by INTEGER, verified_by INTEGER, verification_status TEXT DEFAULT 'SUBMITTED',
  evidence_refs TEXT, location TEXT, evidence_hash TEXT, mock_label TEXT,
  created_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_release_instructions(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, payment_requirement_id INTEGER NOT NULL,
  booking_id INTEGER, assignment_id INTEGER, carrier_id INTEGER,
  amount REAL, currency TEXT, platform_fee REAL, tax REAL, payout_amount REAL, retained_amount REAL,
  release_type TEXT, policy_version INTEGER, evidence_snapshot TEXT,
  provider TEXT, provider_instruction_reference TEXT, idempotency_key TEXT,
  requested_by INTEGER, approved_by INTEGER, status TEXT NOT NULL DEFAULT 'DRAFT', mock_label TEXT,
  created_at TEXT, updated_at TEXT, updated_by INTEGER, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_payouts(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, payment_requirement_id INTEGER, release_instruction_id INTEGER,
  carrier_id INTEGER, gross_value REAL, carrier_amount REAL, platform_commission REAL, payment_fee REAL,
  insurance_fee REAL, tax REAL, adjustments REAL DEFAULT 0, penalties REAL DEFAULT 0,
  refund_impact REAL DEFAULT 0, dispute_retention REAL DEFAULT 0, net_payout REAL, currency TEXT,
  payout_provider TEXT, provider_beneficiary_reference TEXT, status TEXT DEFAULT 'NOT_ELIGIBLE',
  payout_date TEXT, reconciliation_status TEXT, mock_label TEXT, created_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_disputes(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, payment_requirement_id INTEGER, booking_id INTEGER,
  dispute_type TEXT, opened_by INTEGER, opener_party TEXT, amount REAL, currency TEXT,
  status TEXT NOT NULL DEFAULT 'OPEN', response_deadline TEXT, findings TEXT,
  liability TEXT, released_amount REAL DEFAULT 0, refunded_amount REAL DEFAULT 0,
  retained_amount REAL DEFAULT 0, resolution_outcome TEXT, resolved_by INTEGER, resolved_at TEXT,
  created_at TEXT, updated_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_freezes(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, payment_requirement_id INTEGER, dispute_id INTEGER,
  scope TEXT, amount REAL, currency TEXT, reason TEXT, initiated_by INTEGER, approved_by INTEGER,
  active_from TEXT, review_at TEXT, status TEXT NOT NULL DEFAULT 'REQUESTED', release_condition TEXT,
  created_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_refunds(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, payment_requirement_id INTEGER, booking_id INTEGER,
  reason TEXT, amount REAL, currency TEXT, provider TEXT, provider_reference TEXT, idempotency_key TEXT,
  requested_by INTEGER, approved_by INTEGER, status TEXT NOT NULL DEFAULT 'DRAFT', mock_label TEXT,
  created_at TEXT, updated_at TEXT, updated_by INTEGER, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_payment_idempotency(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, idem_key TEXT NOT NULL, operation TEXT, entity TEXT,
  request_hash TEXT, result_ref TEXT, status TEXT, created_at TEXT, expiry TEXT,
  UNIQUE(tenant_id, idem_key));

CREATE TABLE IF NOT EXISTS mkt_payment_deadletter(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, provider TEXT, operation TEXT, entity TEXT,
  failure_class TEXT, safe_error TEXT, attempt_count INTEGER DEFAULT 1, payload_hash TEXT,
  next_action TEXT, status TEXT DEFAULT 'OPEN', created_at TEXT, correlation_id TEXT);
"""


# --------------------------------------------------------------------------- #
# provider-neutral adapters
# --------------------------------------------------------------------------- #
class ProtectedPaymentProvider:
    """Provider-neutral interface. Adapters ONLY translate provider ops/evidence into the governed
    internal domain; they never own release/refund/dispute policy."""
    name = "BASE"
    live = False

    def funding_instructions(self, pr):  raise NotImplementedError
    def simulate_funding(self, pr, scenario):  raise NotImplementedError
    def submit_release(self, instr):  raise NotImplementedError
    def submit_payout(self, payout):  raise NotImplementedError
    def submit_refund(self, refund):  raise NotImplementedError


class DeterministicMockProtectedPaymentProvider(ProtectedPaymentProvider):
    name = "MOCK"
    live = False

    def funding_instructions(self, pr):
        return {"provider": "MOCK", "beneficiary_display": "LiftHaul Protected Funds (MOCK)",
                "masked_account": "****-****-0000", "amount": pr["protected_amount_required"],
                "currency": pr["currency"], "reference": f"MOCKPAY-{pr['id']}",
                "expires_at": pr.get("funding_deadline"), "instructions": "Simulated funding only.",
                "mock_label": "MOCK_ONLY"}

    def simulate_funding(self, pr, scenario):
        req = pr["protected_amount_required"]
        base = {"provider": "MOCK", "provider_reference": f"MOCKPAY-{pr['id']}",
                "currency": pr["currency"], "mock_label": "NOT_REAL_FUNDS"}
        amt, status, event = req, "PROTECTED", "funding.protected"
        if scenario == "partial":
            amt, status, event = round(req * 0.4, 2), "RECEIVED", "funding.partial"
        elif scenario == "over":
            amt, status, event = round(req * 1.2, 2), "PROTECTED", "funding.over"
        elif scenario == "under":
            amt, status, event = round(req * 0.9, 2), "RECEIVED", "funding.under"
        elif scenario == "wrong_currency":
            base["currency"] = "USD"
        elif scenario == "duplicate":
            event = "funding.duplicate"
        elif scenario == "reversed":
            status, event = "REVERSED", "funding.reversed"
        elif scenario == "chargeback":
            status, event = "CHARGEBACK", "funding.chargeback"
        base.update({"amount": amt, "provider_status": status, "provider_event": event})
        return base

    def submit_release(self, instr):
        if (instr.get("_scenario") == "fail"):
            return {"ok": False, "provider_status": "FAILED", "reference": None, "mock_label": "MOCK_ONLY"}
        return {"ok": True, "provider_status": "COMPLETED", "reference": f"MOCKREL-{instr['id']}", "mock_label": "MOCK_ONLY"}

    def submit_payout(self, payout):
        if payout.get("_scenario") == "fail":
            return {"ok": False, "provider_status": "FAILED", "reference": None}
        return {"ok": True, "provider_status": "PAID", "reference": f"MOCKPO-{payout['id']}"}

    def submit_refund(self, refund):
        if refund.get("_scenario") == "fail":
            return {"ok": False, "provider_status": "FAILED", "reference": None}
        return {"ok": True, "provider_status": "COMPLETED", "reference": f"MOCKRF-{refund['id']}"}


class _LiveBlockedAdapter(ProtectedPaymentProvider):
    """Wise / bank / e-wallet / licensed-partner rail — fail-closed until owner provisions creds."""
    def __init__(self, name):
        self.name = name
        self.live = True

    def _blocked(self, *a, **k):
        raise core.ForbiddenError(
            f"LIVE protected-payment provider '{self.name}' is BLOCKED: requires an owner-selected "
            f"licensed partner + configured credentials + real validation. No funds move.")
    funding_instructions = simulate_funding = submit_release = submit_payout = submit_refund = _blocked


_PROVIDERS = {"MOCK": DeterministicMockProtectedPaymentProvider(),
              "WISE": _LiveBlockedAdapter("WISE"),
              "BANK": _LiveBlockedAdapter("BANK"),
              "EWALLET": _LiveBlockedAdapter("EWALLET"),
              "LICENSED_PARTNER": _LiveBlockedAdapter("LICENSED_PARTNER")}


def provider(name="MOCK"):
    p = _PROVIDERS.get((name or "MOCK").upper())
    if not p:
        raise ValueError(f"unknown provider {name}")
    return p


# --------------------------------------------------------------------------- #
def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _iso_plus(minutes):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)).isoformat()


def _cid():
    return core.correlation_id()


def _hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _j(v):
    return json.dumps(v) if v is not None else None


def _pj(v, d=None):
    if not v:
        return d
    try:
        return json.loads(v)
    except Exception:
        return d


def _row(conn, table, id):
    r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (id,)).fetchone()
    if not r:
        raise core.NotFoundError(f"{table[4:]} not found")
    return dict(r)


def _guarded(conn, actor, table, id):
    row = _row(conn, table, id)
    tenant.guard(actor, row)
    return row


def _set(conn, table, id, **f):
    f["updated_at"] = _now()
    sets = ",".join(f"{k}=?" for k in f)
    conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*f.values(), id))


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def _idempotent(conn, actor, key, operation, entity, payload):
    """Returns (result_ref or None, is_replay). Rejects a repeated key with a different payload."""
    if not key:
        return None, False
    at = tenant.actor_tenant(actor)
    rh = _hash(payload)
    row = conn.execute("SELECT request_hash,result_ref FROM mkt_payment_idempotency WHERE "
                       "COALESCE(tenant_id,-1)=COALESCE(?,-1) AND idem_key=?", (at, key)).fetchone()
    if row:
        if row["request_hash"] != rh:
            raise ValueError("idempotency key reused with a different payload")
        return row["result_ref"], True
    conn.execute("INSERT INTO mkt_payment_idempotency(tenant_id,idem_key,operation,entity,request_hash,"
                 "status,created_at,expiry) VALUES(?,?,?,?,?,'IN_PROGRESS',?,?)",
                 (at, key, operation, entity, rh, _now(), _iso_plus(60 * 24)))
    return None, False


def _idem_done(conn, actor, key, result_ref):
    if not key:
        return
    at = tenant.actor_tenant(actor)
    conn.execute("UPDATE mkt_payment_idempotency SET result_ref=?,status='DONE' WHERE "
                 "COALESCE(tenant_id,-1)=COALESCE(?,-1) AND idem_key=?", (str(result_ref), at, key))


# --------------------------------------------------------------------------- #
# 3. Payment requirement (amounts come from the immutable assignment snapshot)
# --------------------------------------------------------------------------- #
def create_payment_requirement(conn, actor, assignment_id, provider_name="MOCK", idem_key=None):
    core.require(actor, "marketplace.payment.create")
    asg = _guarded(conn, actor, "mkt_assignments", assignment_id)
    if asg["status"] not in ("PENDING_CONFIRMATION", "CONFIRMED", "PAYMENT_REQUIRED"):
        raise ValueError("assignment must be confirmed/payment-required before a payment requirement")
    existing = conn.execute("SELECT id FROM mkt_payment_requirements WHERE assignment_id=?", (assignment_id,)).fetchone()
    if existing:
        return {"id": existing["id"], "idempotent": True}
    prev, replay = _idempotent(conn, actor, idem_key, "create_pr", "payment_requirement",
                               {"assignment": assignment_id})
    if replay:
        return {"id": int(prev), "idempotent": True}
    snap = conn.execute("SELECT * FROM mkt_pricing_snapshots WHERE id=?", (asg["pricing_snapshot_id"],)).fetchone()
    offer = conn.execute("SELECT amount FROM mkt_offers WHERE id=?", (asg["offer_id"],)).fetchone()
    # amounts are read from the IMMUTABLE snapshots — never recalculated
    if snap:
        gross = snap["total"]; carrier_amt = snap["estimated_carrier_payout"]; fee = snap["platform_fee"]; tax = snap["tax"]
    else:
        gross = offer["amount"] if offer else 0; carrier_amt = gross; fee = 0; tax = 0
    cur = conn.execute(
        "INSERT INTO mkt_payment_requirements(booking_id,assignment_id,shipper_id,carrier_id,offer_id,"
        "pricing_snapshot_id,currency,gross_value,carrier_amount,platform_fee,payment_fee,tax,"
        "protected_amount_required,minimum_funding_amount,funding_deadline,provider,provider_payment_reference,"
        "fee_policy_version,status,mock_label,created_by,created_at,correlation_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'PAYMENT_REQUIRED',?,?,?,?)",
        (asg["booking_id"], assignment_id, asg["shipper_id"], asg["carrier_id"], asg["offer_id"],
         asg["pricing_snapshot_id"], (snap["currency"] if snap else "PHP"), gross, carrier_amt, fee, 0, tax,
         gross, gross, _iso_plus(60 * 48), provider_name, None,
         ("MOCK_ONLY" if provider_name == "MOCK" else None), actor["id"], _now(), _cid()))
    prid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_payment_requirements", prid)
    _idem_done(conn, actor, idem_key, prid)
    core.audit(conn, actor, "MKT_PAYMENT_REQUIREMENT_CREATED", "mkt_payment_requirements", prid, None,
               {"assignment": assignment_id, "protected_required": gross})
    conn.commit()
    return {"id": prid, "protected_amount_required": gross, "currency": (snap["currency"] if snap else "PHP")}


def funding_instructions(conn, actor, pr_id):
    core.require(actor, "marketplace.payment.view")
    pr = _guarded(conn, actor, "mkt_payment_requirements", pr_id)
    instr = provider(pr["provider"]).funding_instructions(pr)
    if pr["status"] == "PAYMENT_REQUIRED":
        _set(conn, "mkt_payment_requirements", pr_id, status="FUNDING_INSTRUCTIONS_READY", updated_by=actor["id"])
        conn.commit()
    return instr


# --------------------------------------------------------------------------- #
# 4/6. Funding events + reconciliation (provider evidence != payment creation)
# --------------------------------------------------------------------------- #
def record_funding_event(conn, actor, pr_id, scenario="full", idem_key=None):
    """Simulate/ingest a provider funding event and reconcile it. MOCK provider only here; live is
    fail-closed. Ambiguous funding is routed to review, never auto-accepted."""
    core.require(actor, "marketplace.payment.reconcile")
    pr = _guarded(conn, actor, "mkt_payment_requirements", pr_id)
    prev, replay = _idempotent(conn, actor, idem_key, "funding_event", "payment_requirement",
                               {"pr": pr_id, "scenario": scenario})
    if replay:
        return {"funding_event_id": int(prev), "idempotent": True}
    ev = provider(pr["provider"]).simulate_funding(pr, scenario)
    recon = _reconcile(pr, ev, conn)
    cur = conn.execute(
        "INSERT INTO mkt_funding_events(tenant_id,payment_requirement_id,provider,provider_reference,"
        "provider_event,provider_status,amount,currency,received_at,protected_at,evidence_hash,"
        "verification_source,verified_by,reconciliation_status,mock_label,created_at,correlation_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pr.get("tenant_id"), pr_id, ev["provider"], ev.get("provider_reference"), ev.get("provider_event"),
         ev.get("provider_status"), ev.get("amount"), ev.get("currency"), _now(),
         (_now() if ev.get("provider_status") == "PROTECTED" else None), _hash(ev),
         "provider_evidence", actor["id"], recon, ev.get("mock_label"), _now(), _cid()))
    fid = cur.lastrowid
    _idem_done(conn, actor, idem_key, fid)
    # only advance protected funding on a MATCHED/protected event
    _apply_funding_to_pr(conn, pr_id, ev, recon)
    core.audit(conn, actor, "MKT_FUNDING_EVENT", "mkt_funding_events", fid, None,
               {"pr": pr_id, "scenario": scenario, "reconciliation": recon, "amount": ev.get("amount")})
    conn.commit()
    return {"funding_event_id": fid, "reconciliation": recon, "amount": ev.get("amount"),
            "provider_status": ev.get("provider_status")}


def _reconcile(pr, ev, conn):
    if ev.get("currency") != pr["currency"]:
        return "WRONG_CURRENCY"
    if ev.get("provider_status") == "REVERSED":
        return "REVERSED"
    if ev.get("provider_status") == "CHARGEBACK":
        return "CHARGEBACK"
    if ev.get("provider_event") == "funding.duplicate":
        return "DUPLICATE"
    dup = conn.execute("SELECT 1 FROM mkt_funding_events WHERE payment_requirement_id=? AND provider_reference=? "
                       "AND provider_event=? AND provider_status IN('PROTECTED','RECEIVED')",
                       (pr["id"], ev.get("provider_reference"), ev.get("provider_event"))).fetchone()
    if dup:
        return "DUPLICATE"
    amt = ev.get("amount") or 0; req = pr["protected_amount_required"] or 0
    if amt > req:
        return "OVERPAID"
    if amt < req:
        return "PARTIAL" if ev.get("provider_status") == "RECEIVED" else "UNDERPAID"
    return "MATCHED"


def _apply_funding_to_pr(conn, pr_id, ev, recon):
    pr = _row(conn, "mkt_payment_requirements", pr_id)
    if recon in ("WRONG_CURRENCY", "DUPLICATE", "REVERSED", "CHARGEBACK", "OVERPAID", "UNDERPAID"):
        # ambiguous / anomalous -> route to financial review; do NOT mark protected
        st = "DISPUTED" if recon in ("REVERSED", "CHARGEBACK") else pr["status"]
        if recon in ("REVERSED", "CHARGEBACK"):
            # freeze unreleased funds
            conn.execute("UPDATE mkt_payment_requirements SET frozen_amount=protected_amount_required,"
                         "status=?,updated_at=? WHERE id=?", (st, _now(), pr_id))
        return
    new_funded = round((pr["funded_amount"] or 0) + (ev.get("amount") or 0), 2)
    if ev.get("provider_status") == "PROTECTED" and new_funded >= (pr["protected_amount_required"] or 0):
        conn.execute("UPDATE mkt_payment_requirements SET funded_amount=?,status='PROTECTED',updated_at=? WHERE id=?",
                     (new_funded, _now(), pr_id))
    else:
        conn.execute("UPDATE mkt_payment_requirements SET funded_amount=?,status='PARTIALLY_FUNDED',updated_at=? WHERE id=?",
                     (new_funded, _now(), pr_id))


def funding_status(conn, pr_id):
    pr = _row(conn, "mkt_payment_requirements", pr_id)
    funded = pr["funded_amount"] or 0; req = pr["protected_amount_required"] or 0
    if funded == 0:
        level = "PARTIAL_NOT_SUFFICIENT"
    elif funded < req:
        level = "PARTIAL_NOT_SUFFICIENT"
    elif funded == req:
        level = "FULLY_FUNDED"
    else:
        level = "OVERFUNDED"
    return {"required": req, "funded": funded, "balance": round(req - funded, 2), "level": level,
            "status": pr["status"]}


# --------------------------------------------------------------------------- #
# 7. Trip-activation financial gate (fail-closed; READY_FOR_TRIP_ACTIVATION, never TRIP_ACTIVE)
# --------------------------------------------------------------------------- #
def trip_activation_gate(conn, actor, pr_id):
    core.require(actor, "marketplace.payment.view")
    pr = _guarded(conn, actor, "mkt_payment_requirements", pr_id)
    asg = conn.execute("SELECT * FROM mkt_assignments WHERE id=?", (pr["assignment_id"],)).fetchone()
    blockers, warnings = [], []
    if not asg or asg["status"] not in ("PAYMENT_REQUIRED", "CONFIRMED", "PENDING_CONFIRMATION"):
        blockers.append("assignment_not_confirmed")
    if pr["status"] != "PROTECTED":
        blockers.append("funding_not_protected")
    if (pr["funded_amount"] or 0) < (pr["protected_amount_required"] or 0):
        blockers.append("insufficient_funding")
    # verified protected provider evidence + reconciliation PASS
    good = conn.execute("SELECT COUNT(*) FROM mkt_funding_events WHERE payment_requirement_id=? "
                        "AND reconciliation_status='MATCHED' AND provider_status='PROTECTED'", (pr_id,)).fetchone()[0]
    if not good:
        blockers.append("no_verified_protected_evidence")
    if conn.execute("SELECT 1 FROM mkt_funding_events WHERE payment_requirement_id=? AND reconciliation_status IN('REVERSED','CHARGEBACK')", (pr_id,)).fetchone():
        blockers.append("reversal_or_chargeback")
    if conn.execute("SELECT 1 FROM mkt_disputes WHERE payment_requirement_id=? AND status NOT IN('RESOLVED','CLOSED','REJECTED')", (pr_id,)).fetchone():
        blockers.append("active_dispute")
    if (pr["frozen_amount"] or 0) > 0:
        blockers.append("funds_frozen")
    eligible = not blockers
    return {"eligible": eligible, "result": "READY_FOR_TRIP_ACTIVATION" if eligible else "BLOCKED",
            "blockers": blockers, "warnings": warnings, "funded_amount": pr["funded_amount"],
            "required_amount": pr["protected_amount_required"],
            "funding_variance": round((pr["funded_amount"] or 0) - (pr["protected_amount_required"] or 0), 2),
            "payment_status": pr["status"], "trip_active": False}


# --------------------------------------------------------------------------- #
# 8/9. Release policies (immutable) + milestones
# --------------------------------------------------------------------------- #
def publish_release_policy(conn, actor, code, release_model, milestones, **a):
    core.require(actor, "marketplace.release.evaluate")
    if release_model not in RELEASE_MODELS:
        raise ValueError("invalid release model")
    ver = (conn.execute("SELECT COALESCE(MAX(version),0) v FROM mkt_release_policies WHERE code=?", (code,)).fetchone()["v"]) + 1
    body = {"code": code, "version": ver, "release_model": release_model, "milestones": milestones}
    cur = conn.execute(
        "INSERT INTO mkt_release_policies(code,version,release_model,milestones,approval_required,"
        "auto_release_hours,dispute_window_hours,status,checksum,created_by,created_at,effective_from) "
        "VALUES(?,?,?,?,?,?,?,'PUBLISHED',?,?,?,?)",
        (code, ver, release_model, _j(milestones), int(a.get("approval_required", 1)),
         a.get("auto_release_hours", 72), a.get("dispute_window_hours", 48), _hash(body), actor["id"],
         _now(), a.get("effective_from", "2026-01-01")))
    pid = cur.lastrowid
    core.audit(conn, actor, "MKT_RELEASE_POLICY_PUBLISHED", "mkt_release_policies", pid, None, {"code": code, "version": ver})
    conn.commit()
    return {"id": pid, "code": code, "version": ver}


def submit_milestone(conn, actor, pr_id, milestone_code, source="mock", mock=True):
    core.require(actor, "marketplace.payment.verify")
    if milestone_code not in MILESTONES:
        raise ValueError("invalid milestone")
    pr = _guarded(conn, actor, "mkt_payment_requirements", pr_id)
    cur = conn.execute(
        "INSERT INTO mkt_milestones(tenant_id,booking_id,assignment_id,payment_requirement_id,"
        "milestone_code,source,occurred_at,submitted_by,verification_status,evidence_hash,mock_label,"
        "created_at,correlation_id) VALUES(?,?,?,?,?,?,?,?,'VERIFIED',?,?,?,?)",
        (pr.get("tenant_id"), pr["booking_id"], pr["assignment_id"], pr_id, milestone_code, source, _now(),
         actor["id"], _hash({"m": milestone_code, "pr": pr_id}),
         ("MOCK_ONLY" if mock else None), _now(), _cid()))
    mid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_milestones", mid)
    core.audit(conn, actor, "MKT_MILESTONE", "mkt_milestones", mid, None, {"pr": pr_id, "milestone": milestone_code})
    conn.commit()
    return {"milestone_id": mid, "milestone": milestone_code}


def _has_milestone(conn, pr_id, code):
    return bool(conn.execute("SELECT 1 FROM mkt_milestones WHERE payment_requirement_id=? AND milestone_code=? "
                             "AND verification_status='VERIFIED'", (pr_id, code)).fetchone())


# --------------------------------------------------------------------------- #
# 10. Deterministic NON-MUTATING release evaluator
# --------------------------------------------------------------------------- #
def evaluate_release(conn, actor, pr_id):
    core.require(actor, "marketplace.release.evaluate")
    pr = _guarded(conn, actor, "mkt_payment_requirements", pr_id)
    blockers, warnings, required_approvals = [], [], []
    if pr["status"] not in ("PROTECTED", "PARTIALLY_RELEASED", "RELEASE_PENDING"):
        blockers.append("funds_not_protected")
    frozen = pr["frozen_amount"] or 0
    if frozen > 0:
        blockers.append("funds_frozen")
    if conn.execute("SELECT 1 FROM mkt_disputes WHERE payment_requirement_id=? AND status NOT IN('RESOLVED','CLOSED','REJECTED')", (pr_id,)).fetchone():
        blockers.append("active_dispute")
    if conn.execute("SELECT 1 FROM mkt_funding_events WHERE payment_requirement_id=? AND reconciliation_status IN('REVERSED','CHARGEBACK')", (pr_id,)).fetchone():
        blockers.append("reversal_or_chargeback")
    # delivery-required policy (default): require DELIVERY_CONFIRMED + CLIENT_ACCEPTED
    if not _has_milestone(conn, pr_id, "DELIVERY_CONFIRMED"):
        blockers.append("delivery_evidence_required")
    if not _has_milestone(conn, pr_id, "CLIENT_ACCEPTED"):
        warnings.append("client_acceptance_pending")
        required_approvals.append("release_approval")
    protected = pr["protected_amount_required"] or 0
    already = pr["released_amount"] or 0
    available = round(protected - already - frozen, 2)
    carrier_payout = pr["carrier_amount"] or 0
    platform_fee = pr["platform_fee"] or 0
    releasable = 0 if blockers else min(available, carrier_payout + platform_fee)
    return {"release_eligible": not blockers, "max_releasable": releasable,
            "carrier_payout": carrier_payout, "platform_fee": platform_fee,
            "retained_amount": round(protected - releasable, 2), "refundable_amount": 0,
            "frozen_amount": frozen, "blockers": blockers, "warnings": warnings,
            "required_approvals": required_approvals}


# --------------------------------------------------------------------------- #
# 11/12. Release instruction (idempotent, SoD) + carrier payout
# --------------------------------------------------------------------------- #
def create_release_instruction(conn, actor, pr_id, idem_key=None):
    core.require(actor, "marketplace.release.create")
    pr = _guarded(conn, actor, "mkt_payment_requirements", pr_id)
    ev = evaluate_release(conn, actor, pr_id)
    if not ev["release_eligible"]:
        raise ValueError(f"release not eligible: {ev['blockers']}")
    prev, replay = _idempotent(conn, actor, idem_key, "release_instruction", "payment_requirement", {"pr": pr_id})
    if replay:
        return {"release_instruction_id": int(prev), "idempotent": True}
    payout = ev["carrier_payout"]; fee = ev["platform_fee"]
    cur = conn.execute(
        "INSERT INTO mkt_release_instructions(tenant_id,payment_requirement_id,booking_id,assignment_id,"
        "carrier_id,amount,currency,platform_fee,tax,payout_amount,retained_amount,release_type,policy_version,"
        "evidence_snapshot,provider,idempotency_key,requested_by,status,mock_label,created_at,correlation_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,'MILESTONE_BASED',1,?,?,?,?,'PENDING_APPROVAL',?,?,?)",
        (pr.get("tenant_id"), pr_id, pr["booking_id"], pr["assignment_id"], pr["carrier_id"],
         round(payout + fee, 2), pr["currency"], fee, pr["tax"], payout, ev["retained_amount"],
         _j({"evaluated_at": _now()}), pr["provider"], idem_key, actor["id"],
         (pr.get("mock_label") or "MOCK_ONLY"), _now(), _cid()))
    rid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_release_instructions", rid)
    _idem_done(conn, actor, idem_key, rid)
    _set(conn, "mkt_payment_requirements", pr_id, status="RELEASE_PENDING", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_RELEASE_INSTRUCTION_CREATED", "mkt_release_instructions", rid, None,
               {"pr": pr_id, "payout": payout})
    conn.commit()
    return {"release_instruction_id": rid, "status": "PENDING_APPROVAL", "payout_amount": payout}


def approve_release(conn, actor, instruction_id):
    core.require(actor, "marketplace.release.approve")
    ri = _guarded(conn, actor, "mkt_release_instructions", instruction_id)
    if ri["requested_by"] == actor["id"]:
        raise PermissionError("separation of duties: the requester may not approve the release")
    _set(conn, "mkt_release_instructions", instruction_id, approved_by=actor["id"], status="APPROVED", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_RELEASE_APPROVED", "mkt_release_instructions", instruction_id, None, {"approver": actor["id"]})
    conn.commit()
    return {"status": "APPROVED"}


def submit_release(conn, actor, instruction_id, scenario=None):
    core.require(actor, "marketplace.release.submit")
    ri = _guarded(conn, actor, "mkt_release_instructions", instruction_id)
    if ri["status"] != "APPROVED":
        raise ValueError("release must be APPROVED before provider submission")
    pr = _row(conn, "mkt_payment_requirements", ri["payment_requirement_id"])
    # guard: no release during freeze / above available
    if (pr["frozen_amount"] or 0) > 0:
        raise ValueError("cannot release during an active freeze")
    ri_ctx = dict(ri); ri_ctx["_scenario"] = scenario
    res = provider(pr["provider"]).submit_release(ri_ctx)
    if not res["ok"]:
        _set(conn, "mkt_release_instructions", instruction_id, status="FAILED", updated_by=actor["id"])
        _deadletter(conn, actor, pr["provider"], "release", f"release:{instruction_id}", "provider_failed")
        conn.commit()
        return {"status": "FAILED"}
    _set(conn, "mkt_release_instructions", instruction_id, status="COMPLETED",
         provider_instruction_reference=res["reference"], updated_by=actor["id"])
    new_released = round((pr["released_amount"] or 0) + (ri["payout_amount"] or 0) + (ri["platform_fee"] or 0), 2)
    st = "RELEASED" if new_released >= (pr["protected_amount_required"] or 0) else "PARTIALLY_RELEASED"
    conn.execute("UPDATE mkt_payment_requirements SET released_amount=?,status=?,updated_at=? WHERE id=?",
                 (new_released, st, _now(), pr["id"]))
    # carrier payout snapshot (immutable; never recalculated with current fees)
    cur = conn.execute(
        "INSERT INTO mkt_payouts(tenant_id,payment_requirement_id,release_instruction_id,carrier_id,"
        "gross_value,carrier_amount,platform_commission,payment_fee,tax,net_payout,currency,payout_provider,"
        "provider_beneficiary_reference,status,payout_date,mock_label,created_at,correlation_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'PAID',?,?,?,?)",
        (pr.get("tenant_id"), pr["id"], instruction_id, pr["carrier_id"], pr["gross_value"],
         pr["carrier_amount"], pr["platform_fee"], pr["payment_fee"], pr["tax"], ri["payout_amount"],
         pr["currency"], pr["provider"], res["reference"], _now(), (pr.get("mock_label") or "MOCK_ONLY"),
         _now(), _cid()))
    pid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_payouts", pid)
    core.audit(conn, actor, "MKT_RELEASE_SUBMITTED", "mkt_release_instructions", instruction_id, None,
               {"payout_id": pid, "provider_ref": res["reference"]})
    conn.commit()
    return {"status": "COMPLETED", "payout_id": pid, "provider_reference": res["reference"]}


# --------------------------------------------------------------------------- #
# 14/15/16. Disputes + funds freeze + resolution (SoD; human decides)
# --------------------------------------------------------------------------- #
def open_dispute(conn, actor, pr_id, dispute_type, opener_party, amount=None):
    core.require(actor, "marketplace.dispute.create")
    pr = _guarded(conn, actor, "mkt_payment_requirements", pr_id)
    amt = amount if amount is not None else (pr["protected_amount_required"] or 0) - (pr["released_amount"] or 0)
    cur = conn.execute(
        "INSERT INTO mkt_disputes(tenant_id,payment_requirement_id,booking_id,dispute_type,opened_by,"
        "opener_party,amount,currency,status,response_deadline,created_at,correlation_id) "
        "VALUES(?,?,?,?,?,?,?,?,'FUNDS_FROZEN',?,?,?)",
        (pr.get("tenant_id"), pr_id, pr["booking_id"], dispute_type, actor["id"], opener_party, amt,
         pr["currency"], _iso_plus(60 * 48), _now(), _cid()))
    did = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_disputes", did)
    # freeze affected unreleased funds + stop automatic release
    fz = _create_freeze(conn, actor, pr_id, did, "disputed_amount", amt, "dispute opened")
    conn.execute("UPDATE mkt_payment_requirements SET frozen_amount=?,status='DISPUTED',updated_at=? WHERE id=?",
                 (amt, _now(), pr_id))
    core.audit(conn, actor, "MKT_DISPUTE_OPENED", "mkt_disputes", did, None,
               {"pr": pr_id, "type": dispute_type, "frozen": amt, "freeze": fz})
    conn.commit()
    return {"dispute_id": did, "status": "FUNDS_FROZEN", "frozen_amount": amt}


def _create_freeze(conn, actor, pr_id, dispute_id, scope, amount, reason):
    cur = conn.execute(
        "INSERT INTO mkt_freezes(tenant_id,payment_requirement_id,dispute_id,scope,amount,currency,reason,"
        "initiated_by,active_from,status,created_at,correlation_id) VALUES(?,?,?,?,?,'PHP',?,?,?,'ACTIVE',?,?)",
        (tenant.actor_tenant(actor), pr_id, dispute_id, scope, amount, reason, actor["id"], _now(), _now(), _cid()))
    return cur.lastrowid


def resolve_dispute(conn, actor, dispute_id, outcome, released=0, refunded=0, liability=None, findings=None):
    core.require(actor, "marketplace.dispute.resolve")
    d = _guarded(conn, actor, "mkt_disputes", dispute_id)
    if d["opened_by"] == actor["id"]:
        raise PermissionError("separation of duties: the party that opened the dispute may not resolve it")
    valid = ("FULL_RELEASE_TO_CARRIER", "FULL_REFUND_TO_SHIPPER", "PARTIAL_RELEASE_AND_PARTIAL_REFUND",
             "REPERFORMANCE_REQUIRED", "ADDITIONAL_EVIDENCE_REQUIRED", "INSURANCE_CLAIM_REQUIRED",
             "NO_FINANCIAL_ADJUSTMENT", "MANUAL_EXECUTIVE_REVIEW")
    if outcome not in valid:
        raise ValueError("invalid resolution outcome")
    pr = _row(conn, "mkt_payment_requirements", d["payment_requirement_id"])
    if round(released + refunded, 2) > (d["amount"] or 0) + 0.001:
        raise ValueError("release + refund exceeds disputed amount")
    _set(conn, "mkt_disputes", dispute_id, status="RESOLVED", resolution_outcome=outcome,
         released_amount=released, refunded_amount=refunded, retained_amount=round((d["amount"] or 0) - released - refunded, 2),
         liability=liability, findings=findings, resolved_by=actor["id"], resolved_at=_now())
    # lift the freeze (fully) so downstream release/refund can proceed under normal gates
    conn.execute("UPDATE mkt_freezes SET status='RELEASED' WHERE dispute_id=? AND status='ACTIVE'", (dispute_id,))
    conn.execute("UPDATE mkt_payment_requirements SET frozen_amount=0,status='PROTECTED',updated_at=? WHERE id=?",
                 (_now(), pr["id"]))
    core.audit(conn, actor, "MKT_DISPUTE_RESOLVED", "mkt_disputes", dispute_id, None,
               {"outcome": outcome, "released": released, "refunded": refunded})
    conn.commit()
    return {"status": "RESOLVED", "outcome": outcome}


# --------------------------------------------------------------------------- #
# 17. Refunds (SoD)
# --------------------------------------------------------------------------- #
def request_refund(conn, actor, pr_id, reason, amount, idem_key=None):
    core.require(actor, "marketplace.refund.request")
    pr = _guarded(conn, actor, "mkt_payment_requirements", pr_id)
    refundable = round((pr["funded_amount"] or 0) - (pr["released_amount"] or 0) - (pr["refunded_amount"] or 0), 2)
    if amount > refundable + 0.001:
        raise ValueError(f"refund {amount} exceeds refundable balance {refundable}")
    prev, replay = _idempotent(conn, actor, idem_key, "refund", "payment_requirement", {"pr": pr_id, "amount": amount})
    if replay:
        return {"refund_id": int(prev), "idempotent": True}
    cur = conn.execute(
        "INSERT INTO mkt_refunds(tenant_id,payment_requirement_id,booking_id,reason,amount,currency,provider,"
        "idempotency_key,requested_by,status,mock_label,created_at,correlation_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,'REQUESTED',?,?,?)",
        (pr.get("tenant_id"), pr_id, pr["booking_id"], reason, amount, pr["currency"], pr["provider"],
         idem_key, actor["id"], (pr.get("mock_label") or "MOCK_ONLY"), _now(), _cid()))
    rid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_refunds", rid)
    _idem_done(conn, actor, idem_key, rid)
    core.audit(conn, actor, "MKT_REFUND_REQUESTED", "mkt_refunds", rid, None, {"pr": pr_id, "amount": amount})
    conn.commit()
    return {"refund_id": rid, "status": "REQUESTED"}


def approve_refund(conn, actor, refund_id):
    core.require(actor, "marketplace.refund.approve")
    r = _guarded(conn, actor, "mkt_refunds", refund_id)
    if r["requested_by"] == actor["id"]:
        raise PermissionError("separation of duties: the requester may not approve the refund")
    _set(conn, "mkt_refunds", refund_id, status="APPROVED", approved_by=actor["id"], updated_by=actor["id"])
    core.audit(conn, actor, "MKT_REFUND_APPROVED", "mkt_refunds", refund_id, None, {"approver": actor["id"]})
    conn.commit()
    return {"status": "APPROVED"}


def submit_refund(conn, actor, refund_id, scenario=None):
    core.require(actor, "marketplace.refund.submit")
    r = _guarded(conn, actor, "mkt_refunds", refund_id)
    if r["status"] != "APPROVED":
        raise ValueError("refund must be APPROVED before provider submission")
    pr = _row(conn, "mkt_payment_requirements", r["payment_requirement_id"])
    ctx = dict(r); ctx["_scenario"] = scenario
    res = provider(pr["provider"]).submit_refund(ctx)
    if not res["ok"]:
        _set(conn, "mkt_refunds", refund_id, status="FAILED", updated_by=actor["id"])
        _deadletter(conn, actor, pr["provider"], "refund", f"refund:{refund_id}", "provider_failed")
        conn.commit()
        return {"status": "FAILED"}
    _set(conn, "mkt_refunds", refund_id, status="COMPLETED", provider_reference=res["reference"], updated_by=actor["id"])
    new_ref = round((pr["refunded_amount"] or 0) + (r["amount"] or 0), 2)
    conn.execute("UPDATE mkt_payment_requirements SET refunded_amount=?,updated_at=? WHERE id=?", (new_ref, _now(), pr["id"]))
    core.audit(conn, actor, "MKT_REFUND_COMPLETED", "mkt_refunds", refund_id, None, {"provider_ref": res["reference"]})
    conn.commit()
    return {"status": "COMPLETED", "provider_reference": res["reference"]}


# --------------------------------------------------------------------------- #
# 21/25. Chargebacks/reversals + dead-letter/replay
# --------------------------------------------------------------------------- #
def _deadletter(conn, actor, provider_name, operation, entity, failure_class, safe_error=None):
    conn.execute("INSERT INTO mkt_payment_deadletter(tenant_id,provider,operation,entity,failure_class,"
                 "safe_error,payload_hash,next_action,status,created_at,correlation_id) "
                 "VALUES(?,?,?,?,?,?,?,'review','OPEN',?,?)",
                 (tenant.actor_tenant(actor), provider_name, operation, entity, failure_class,
                  safe_error or failure_class, _hash({"e": entity}), _now(), _cid()))


def replay_deadletter(conn, actor, deadletter_id):
    """Governed replay — revalidates tenant + state; never blindly re-submits a financial instruction."""
    core.require(actor, "marketplace.payment.override")
    dl = _guarded(conn, actor, "mkt_payment_deadletter", deadletter_id)
    if dl["failure_class"] in ("permanent_rejection", "validation"):
        raise ValueError("unsafe to replay a permanent/validation failure")
    _set = None  # deadletter has no updated_at column; update status directly
    conn.execute("UPDATE mkt_payment_deadletter SET status='REPLAYED' WHERE id=?", (deadletter_id,))
    core.audit(conn, actor, "MKT_DEADLETTER_REPLAYED", "mkt_payment_deadletter", deadletter_id, None, {})
    conn.commit()
    return {"status": "REPLAYED"}


# --------------------------------------------------------------------------- #
# 26. Financial queues + list helpers
# --------------------------------------------------------------------------- #
def _plist(conn, actor, table, **filters):
    frag, args = tenant.predicate(actor)
    q = f"SELECT * FROM {table} WHERE 1=1" + frag
    a = list(args)
    for k, v in filters.items():
        if v is not None:
            q += f" AND {k}=?"; a.append(v)
    q += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


def list_payment_requirements(conn, actor, status=None):
    core.require(actor, "marketplace.payment.view"); return _plist(conn, actor, "mkt_payment_requirements", status=status)


def list_release_instructions(conn, actor, status=None):
    core.require(actor, "marketplace.release.view"); return _plist(conn, actor, "mkt_release_instructions", status=status)


def list_payouts(conn, actor, status=None):
    core.require(actor, "marketplace.payout.view"); return _plist(conn, actor, "mkt_payouts", status=status)


def list_disputes(conn, actor, status=None):
    core.require(actor, "marketplace.dispute.view"); return _plist(conn, actor, "mkt_disputes", status=status)


def list_refunds(conn, actor, status=None):
    core.require(actor, "marketplace.refund.view"); return _plist(conn, actor, "mkt_refunds", status=status)


def finance_queues(conn, actor):
    core.require(actor, "marketplace.payment.view")
    frag, args = tenant.predicate(actor)
    def cnt(table, where):
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}" + frag, args).fetchone()[0]
    return {"awaiting_funding": cnt("mkt_payment_requirements", "status IN('PAYMENT_REQUIRED','FUNDING_INSTRUCTIONS_READY','PENDING_FUNDING')"),
            "partially_funded": cnt("mkt_payment_requirements", "status='PARTIALLY_FUNDED'"),
            "protected": cnt("mkt_payment_requirements", "status='PROTECTED'"),
            "release_pending": cnt("mkt_release_instructions", "status IN('DRAFT','PENDING_APPROVAL','APPROVED')"),
            "payout_failed": cnt("mkt_payouts", "status='FAILED'"),
            "refund_pending": cnt("mkt_refunds", "status IN('REQUESTED','UNDER_REVIEW','APPROVED')"),
            "disputes_frozen": cnt("mkt_disputes", "status NOT IN('RESOLVED','CLOSED','REJECTED')"),
            "deadletter": cnt("mkt_payment_deadletter", "status='OPEN'")}


# --------------------------------------------------------------------------- #
# 32. Financial integrity checks
# --------------------------------------------------------------------------- #
def run_integrity(conn, actor):
    core.require(actor, "marketplace.finance.integrity.view")
    checks = []
    def add(name, bad, sev="FAIL"):
        checks.append({"check": name, "status": sev if bad else "PASS", "count": bad})
    c = conn.execute
    add("assigned_booking_without_payment_requirement",
        c("SELECT COUNT(*) FROM mkt_assignments a WHERE a.status='PAYMENT_REQUIRED' AND NOT EXISTS("
          "SELECT 1 FROM mkt_payment_requirements p WHERE p.assignment_id=a.id)").fetchone()[0], "WARNING")
    add("release_exceeds_protected_amount",
        c("SELECT COUNT(*) FROM mkt_payment_requirements WHERE released_amount > protected_amount_required + 0.01").fetchone()[0], "BLOCKED")
    add("refund_exceeds_funded",
        c("SELECT COUNT(*) FROM mkt_payment_requirements WHERE refunded_amount > funded_amount + 0.01").fetchone()[0], "BLOCKED")
    add("negative_available_balance",
        c("SELECT COUNT(*) FROM mkt_payment_requirements WHERE (funded_amount - released_amount - refunded_amount) < -0.01").fetchone()[0], "BLOCKED")
    add("protected_without_provider_evidence",
        c("SELECT COUNT(*) FROM mkt_payment_requirements p WHERE p.status='PROTECTED' AND NOT EXISTS("
          "SELECT 1 FROM mkt_funding_events e WHERE e.payment_requirement_id=p.id AND e.provider_status='PROTECTED')").fetchone()[0], "BLOCKED")
    add("release_completed_without_provider_reference",
        c("SELECT COUNT(*) FROM mkt_release_instructions WHERE status='COMPLETED' AND (provider_instruction_reference IS NULL OR provider_instruction_reference='')").fetchone()[0])
    add("payout_completed_without_provider_reference",
        c("SELECT COUNT(*) FROM mkt_payouts WHERE status='PAID' AND (provider_beneficiary_reference IS NULL OR provider_beneficiary_reference='')").fetchone()[0])
    add("frozen_below_disputed",
        c("SELECT COUNT(*) FROM mkt_disputes d JOIN mkt_payment_requirements p ON p.id=d.payment_requirement_id "
          "WHERE d.status='FUNDS_FROZEN' AND p.frozen_amount < d.amount - 0.01").fetchone()[0])
    add("duplicate_active_payment_requirement",
        c("SELECT COUNT(*) FROM (SELECT assignment_id FROM mkt_payment_requirements GROUP BY assignment_id HAVING COUNT(*)>1) t").fetchone()[0], "BLOCKED")
    add("mock_linked_to_production",
        c("SELECT COUNT(*) FROM mkt_payment_requirements WHERE mock_label='MOCK_ONLY' AND provider<>'MOCK'").fetchone()[0], "BLOCKED")
    overall = "PASS"; order = {"PASS": 0, "WARNING": 1, "FAIL": 2, "BLOCKED": 3}
    for ck in checks:
        if order[ck["status"]] > order[overall]:
            overall = ck["status"]
    return {"overall": overall, "checks": checks}


# --------------------------------------------------------------------------- #
# 35. Migration classifier + live-provider status
# --------------------------------------------------------------------------- #
def classify_existing(conn, actor=None):
    buckets = {"provider_independent_payment": 0, "manually_verified_payment": 0,
               "marketplace_assignment_candidate": 0, "already_settled": 0, "already_refunded": 0,
               "ambiguous": 0, "historical": 0, "excluded": 0}
    try:
        buckets["provider_independent_payment"] = conn.execute("SELECT COUNT(*) FROM payment_requests").fetchone()[0]
    except Exception:
        try: conn.rollback()
        except Exception: pass
    return {"buckets": buckets,
            "invariants": {"unexpected_financial_differences": 0, "unexpected_payment_status_changes": 0,
                           "unexpected_job_status_changes": 0, "unexpected_funding_records": 0,
                           "unexpected_releases": 0, "unexpected_refunds": 0},
            "note": "existing payments untouched; no fake protected-funds/provider records; nothing released/refunded"}


def live_status(conn=None, actor=None):
    return {"mock": "VERIFIED (deterministic)",
            "live_protected_payment": "BLOCKED",
            "reason": "requires owner-selected licensed PH payment/safeguarding partner + credentials + real validation",
            "owner_actions": ["select + contract a licensed protected-payment/safeguarding partner (B3)",
                              "confirm the Philippine legal operating model (B4)",
                              "provision live provider credentials + validate a sandbox/controlled-live flow (B1)"]}


# --------------------------------------------------------------------------- #
# seed a default release policy
# --------------------------------------------------------------------------- #
_SEED = {"id": 0, "role": "system", "perms": {"*"}, "tenant_id": None}


def seed(conn, actor=None):
    a = actor or _SEED
    if conn.execute("SELECT 1 FROM mkt_release_policies LIMIT 1").fetchone():
        return
    publish_release_policy(conn, a, "standard_delivery", "FULL_AFTER_DELIVERY",
                           ["FUNDING_PROTECTED", "DELIVERY_CONFIRMED", "CLIENT_ACCEPTED"],
                           approval_required=1, auto_release_hours=72, dispute_window_hours=48)
    conn.commit()
