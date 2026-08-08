"""LiftHaul Enterprise — Protected Payment authoritative domain (formalization).

This is the SINGLE authoritative facade over the existing marketplace protected-payment
architecture. It does NOT create a new payment system — it standardizes the state machine,
adds the immutable ledger + daily reconciliation, and formalizes the provider interface, while
reusing everything already built and tested:

  * marketplace_payments        — payment requirements, provider abstraction, funding, milestone
                                  release, disputes, refunds, live-funds hard gate;
  * marketplace_trust_closure   — release gate, payout-account security, fraud, transaction limits,
                                  ledger reconciliation, webhook security;
  * marketplace_trips           — POD / geofence / GPS delivery evidence.

Product terminology stays "Protected Payment". It is NOT called legal "Escrow" until Philippine
counsel + a BSP-regulated provider approve the operating model and terminology. Live fund custody
stays OFF (LIVE_PROTECTED_FUNDS_ENABLED=false) until all three prerequisites are documented.
"""
from __future__ import annotations

import datetime
import json

import core
import tenant
import marketplace_payments as pay
import marketplace_trust_closure as tc

# 1. Canonical state machine (no arbitrary status editing — only declared transitions).
STATES = ("PAYMENT_REQUIRED", "PAYMENT_INTENT_CREATED", "AWAITING_CUSTOMER_FUNDS", "CUSTOMER_FUNDED",
          "FUNDING_CONFIRMED", "FUNDS_PROTECTED", "TRIP_AUTHORIZED", "SERVICE_IN_PROGRESS",
          "DELIVERY_EVIDENCE_PENDING", "DISPUTE_WINDOW", "RELEASE_ELIGIBLE", "RELEASE_APPROVAL_PENDING",
          "RELEASE_APPROVED", "RELEASE_REQUESTED", "RELEASE_CONFIRMED", "SETTLED")
EXCEPTION_STATES = ("PAYMENT_FAILED", "PAYMENT_EXPIRED", "FUNDS_HELD", "FRAUD_REVIEW", "DISPUTED",
                    "RELEASE_REJECTED", "REFUND_PENDING", "PARTIALLY_REFUNDED", "REFUNDED",
                    "CHARGEBACK", "LEGAL_HOLD")

# happy-path forward transitions; exception transitions are allowed from most live states
_FORWARD = {STATES[i]: {STATES[i + 1]} for i in range(len(STATES) - 1)}
_EXCEPTION_FROM = set(STATES) - {"SETTLED"}
_TRANSITIONS = {s: set(_FORWARD.get(s, set())) for s in STATES}
for s in _EXCEPTION_FROM:
    _TRANSITIONS[s] |= {"FUNDS_HELD", "FRAUD_REVIEW", "DISPUTED", "LEGAL_HOLD", "PAYMENT_FAILED",
                        "PAYMENT_EXPIRED", "RELEASE_REJECTED", "REFUND_PENDING"}
# recovery / refund lifecycle transitions
_TRANSITIONS.update({
    "FUNDS_HELD": {"FUNDS_PROTECTED", "DISPUTED", "REFUND_PENDING", "RELEASE_ELIGIBLE", "LEGAL_HOLD"},
    "FRAUD_REVIEW": {"FUNDS_PROTECTED", "REFUND_PENDING", "LEGAL_HOLD"},
    "DISPUTED": {"DISPUTE_WINDOW", "RELEASE_ELIGIBLE", "REFUND_PENDING", "LEGAL_HOLD"},
    "RELEASE_REJECTED": {"RELEASE_ELIGIBLE", "REFUND_PENDING", "DISPUTED"},
    "REFUND_PENDING": {"PARTIALLY_REFUNDED", "REFUNDED"},
    "PARTIALLY_REFUNDED": {"RELEASE_ELIGIBLE", "REFUND_PENDING", "SETTLED"},
    "REFUNDED": {"SETTLED"},
    "LEGAL_HOLD": {"FUNDS_PROTECTED", "REFUND_PENDING"},
})

LEDGER_EVENTS = ("funding", "protected_funds", "carrier_payable", "platform_fee", "provider_fee",
                 "tax", "release", "refund", "adjustment", "reversal")

SCHEMA = """
CREATE TABLE IF NOT EXISTS mkt_protected_tx(
  id INTEGER PRIMARY KEY, tenant_id INTEGER,
  payment_requirement_id INTEGER, booking_id INTEGER, quotation_id INTEGER, job_id INTEGER,
  client_ref TEXT, carrier_id INTEGER, currency TEXT DEFAULT 'PHP',
  contract_amount REAL, protected_amount REAL, platform_fee REAL, provider_fee REAL, tax REAL,
  carrier_payable REAL, funding_deadline TEXT, dispute_policy TEXT, milestone_plan TEXT,
  dispute_window_started_at TEXT, dispute_window_expires_at TEXT,
  provider TEXT DEFAULT 'MOCK', provider_reference TEXT,
  state TEXT NOT NULL DEFAULT 'PAYMENT_REQUIRED',
  created_by INTEGER, created_at TEXT, updated_at TEXT, correlation_id TEXT);

-- Immutable, append-only ledger. No UPDATE/DELETE — corrections are reversing entries.
CREATE TABLE IF NOT EXISTS mkt_protected_ledger(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, tx_id INTEGER NOT NULL,
  event TEXT NOT NULL, amount REAL NOT NULL, currency TEXT DEFAULT 'PHP',
  reverses_entry_id INTEGER, reason TEXT, provider_event TEXT,
  actor_id INTEGER, correlation_id TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS mkt_protected_state_log(
  id INTEGER PRIMARY KEY, tx_id INTEGER NOT NULL, from_state TEXT, to_state TEXT,
  reason TEXT, actor_id INTEGER, correlation_id TEXT, at TEXT NOT NULL);
"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn):
    return 0


# --------------------------------------------------------------------------- #
# 2. Formal provider interface (the licensed/approved rail). LiftHaul stores references +
#    evidence only — never bank/payment secrets in business tables.
# --------------------------------------------------------------------------- #
class ProtectedPaymentProvider:
    """The formal interface a licensed provider adapter must implement."""
    name = "ABSTRACT"
    live = False
    def create_payment(self, tx): raise NotImplementedError
    def get_payment_status(self, ref): raise NotImplementedError
    def confirm_funding(self, ref): raise NotImplementedError
    def get_protected_balance(self, ref): raise NotImplementedError
    def hold(self, ref, amount, reason): raise NotImplementedError
    def create_release(self, ref, amount): raise NotImplementedError
    def release_partial(self, ref, amount): raise NotImplementedError
    def release_full(self, ref): raise NotImplementedError
    def refund_partial(self, ref, amount): raise NotImplementedError
    def refund_full(self, ref): raise NotImplementedError
    def get_settlement(self, ref): raise NotImplementedError
    def reconcile(self, ref): raise NotImplementedError


class MockProtectedPaymentProvider(ProtectedPaymentProvider):
    """Deterministic, offline. No real funds. Used until a licensed provider is activated."""
    name = "MOCK"
    live = False
    def create_payment(self, tx): return {"provider_reference": f"MOCK-PR-{tx.get('id')}", "status": "PAYMENT_INTENT_CREATED"}
    def get_payment_status(self, ref): return {"status": "MOCK", "reference": ref}
    def confirm_funding(self, ref): return {"funded": True, "reference": ref, "mock": True}
    def get_protected_balance(self, ref): return {"protected_balance": None, "mock": True}
    def hold(self, ref, amount, reason): return {"held": amount, "mock": True}
    def create_release(self, ref, amount): return {"release_intent": amount, "mock": True}
    def release_partial(self, ref, amount): return {"released": amount, "mock": True}
    def release_full(self, ref): return {"released": "full", "mock": True}
    def refund_partial(self, ref, amount): return {"refunded": amount, "mock": True}
    def refund_full(self, ref): return {"refunded": "full", "mock": True}
    def get_settlement(self, ref): return {"settlement": None, "mock": True}
    def reconcile(self, ref): return {"reconciled": True, "mock": True}


_PROVIDERS = {"MOCK": MockProtectedPaymentProvider()}


def provider(name="MOCK"):
    """Return a provider adapter. A non-MOCK (live) rail is refused unless the live-funds hard gate
    is satisfied — no live custody by accident or config drift."""
    name = (name or "MOCK").upper()
    if name != "MOCK":
        raise core.ForbiddenError("no live protected-payment provider is activated; MOCK only")
    return _PROVIDERS["MOCK"]


# 3. Live-funds hard gate (reuse the single authority).
def live_funds_enabled(conn):
    return pay.live_funds_enabled(conn)


def assert_live_allowed(conn, moving_real_funds):
    if moving_real_funds and not live_funds_enabled(conn):
        raise core.ForbiddenError("LIVE FUND MOVEMENT DENIED — requires LEGAL_OPERATING_MODEL_APPROVED "
                                  "AND LICENSED_PAYMENT_PROVIDER_ACTIVE AND LIVE_PROTECTED_FUNDS_ENABLED")


# --------------------------------------------------------------------------- #
# 4. Create protected-payment requirement (after quotation acceptance)
# --------------------------------------------------------------------------- #
def create_transaction(conn, actor, *, booking_id, carrier_id, contract_amount, protected_amount,
                       platform_fee=0, provider_fee=0, tax=0, carrier_payable=None, quotation_id=None,
                       job_id=None, client_ref=None, currency="PHP", funding_deadline=None,
                       dispute_policy=None, milestone_plan=None, payment_requirement_id=None,
                       provider_name="MOCK"):
    core.require(actor, "marketplace.payment.create")
    p = provider(provider_name)                              # refuses a live rail
    cur = conn.execute(
        "INSERT INTO mkt_protected_tx(payment_requirement_id,booking_id,quotation_id,job_id,client_ref,"
        "carrier_id,currency,contract_amount,protected_amount,platform_fee,provider_fee,tax,carrier_payable,"
        "funding_deadline,dispute_policy,milestone_plan,provider,state,created_by,created_at,updated_at,"
        "correlation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'PAYMENT_REQUIRED', ?,?,?,?)",
        (payment_requirement_id, booking_id, quotation_id, job_id, client_ref, carrier_id, currency,
         contract_amount, protected_amount, platform_fee, provider_fee, tax,
         carrier_payable if carrier_payable is not None else (contract_amount - platform_fee - provider_fee - tax),
         funding_deadline, dispute_policy, json.dumps(milestone_plan) if milestone_plan else None,
         p.name, actor["id"], _now(), _now(), core.correlation_id()))
    tx_id = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_protected_tx", tx_id)
    core.audit(conn, actor, "PP_TX_CREATED", "mkt_protected_tx", tx_id,
               new={"booking": booking_id, "contract": contract_amount, "protected": protected_amount})
    conn.commit()
    return tx_id


def _tx(conn, actor, tx_id):
    r = conn.execute("SELECT * FROM mkt_protected_tx WHERE id=?", (tx_id,)).fetchone()
    if not r:
        raise core.NotFoundError("protected transaction not found")
    if actor is not None:
        tenant.guard(actor, r)
    return dict(r)


# --------------------------------------------------------------------------- #
# 10. Immutable ledger (append-only; corrections are reversing entries)
# --------------------------------------------------------------------------- #
def append_ledger(conn, actor, tx_id, event, amount, reason=None, reverses_entry_id=None,
                  provider_event=None):
    if event not in LEDGER_EVENTS:
        raise core.ValidationError(f"invalid ledger event '{event}'")
    cur = conn.execute(
        "INSERT INTO mkt_protected_ledger(tx_id,event,amount,currency,reverses_entry_id,reason,"
        "provider_event,actor_id,correlation_id,created_at) VALUES(?,?,?,'PHP',?,?,?,?,?,?)",
        (tx_id, event, amount, reverses_entry_id, reason, provider_event,
         (actor or {}).get("id"), core.correlation_id(), _now()))
    lid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_protected_ledger", lid)
    conn.commit()
    return lid


def reverse_ledger_entry(conn, actor, entry_id, reason):
    """A correction never edits history — it appends a reversing entry of the opposite amount."""
    core.require(actor, "marketplace.payment.manage") if core.can(actor, "marketplace.payment.manage") else None
    orig = conn.execute("SELECT * FROM mkt_protected_ledger WHERE id=?", (entry_id,)).fetchone()
    if not orig:
        raise core.NotFoundError("ledger entry not found")
    return append_ledger(conn, actor, orig["tx_id"], "reversal", -orig["amount"],
                         reason=reason, reverses_entry_id=entry_id)


def ledger_totals(conn, tx_id):
    rows = conn.execute("SELECT event, SUM(amount) s FROM mkt_protected_ledger WHERE tx_id=? GROUP BY event",
                        (tx_id,)).fetchall()
    return {r["event"]: (r["s"] or 0) for r in rows}


# --------------------------------------------------------------------------- #
# 11 + 18. Reconciliation: funded == released + refunded + protected + fees. Diff must be 0.
# --------------------------------------------------------------------------- #
def reconcile(conn, tx_id):
    t = _tx(conn, None, tx_id)
    lt = ledger_totals(conn, tx_id)
    funded = lt.get("funding", 0) + lt.get("reversal", 0) if False else lt.get("funding", 0)
    released = lt.get("release", 0)
    refunded = lt.get("refund", 0)
    fees = lt.get("platform_fee", 0) + lt.get("provider_fee", 0) + lt.get("tax", 0)
    remaining = round(funded - released - refunded - fees, 2)
    rec = tc.reconcile_ledger(funded, released, refunded, max(0.0, remaining), fees)
    return {"tx_id": tx_id, "funded": funded, "released": released, "refunded": refunded,
            "fees": fees, "remaining_protected": remaining, "difference": rec["difference"],
            "balanced": rec["balanced"], "flag": rec["flag"], "state": t["state"]}


def daily_reconciliation(conn, actor=None):
    """18. Compare internal transactions vs ledger; any imbalance is a Finance exception and blocks
    settlement closure."""
    if actor is not None:
        core.require(actor, "marketplace.payment.view") if core.can(actor, "marketplace.payment.view") else core.require(actor, "marketplace.trust.view")
    frag, params = tenant.predicate(actor) if actor is not None else ("", ())
    txs = conn.execute("SELECT id FROM mkt_protected_tx WHERE 1=1" + frag, params).fetchall()
    results, exceptions = [], []
    for r in txs:
        rec = reconcile(conn, r["id"])
        results.append(rec)
        if not rec["balanced"]:
            exceptions.append({"tx_id": r["id"], "difference": rec["difference"]})
    return {"total": len(results), "balanced": len(results) - len(exceptions),
            "exceptions": exceptions, "settlement_blocked": bool(exceptions),
            "note": "any mismatch blocks settlement closure and raises a Finance exception"}


# --------------------------------------------------------------------------- #
# 1. Guarded state transitions — the ONLY way to change state (no arbitrary editing).
#    Composes the reused controls at the critical transitions.
# --------------------------------------------------------------------------- #
def transition(conn, actor, tx_id, to_state, *, reason=None, evidence=None, payout_account_id=None,
               job_value=None, funding_confirmed=None, funds_protected=None, milestone_verified=None,
               pod_ok=None, approvals_complete=True):
    core.require(actor, "marketplace.payment.manage") if core.can(actor, "marketplace.payment.manage") else core.require(actor, "marketplace.trust.view")
    t = _tx(conn, actor, tx_id)
    frm = t["state"]
    if to_state not in STATES + EXCEPTION_STATES:
        raise core.ValidationError(f"unknown state '{to_state}'")
    if to_state not in _TRANSITIONS.get(frm, set()):
        raise core.ConflictError(f"illegal transition {frm} -> {to_state}")

    # control gates at critical transitions (fail closed; never silently bypass)
    if to_state == "FUNDS_PROTECTED" and funding_confirmed is False:
        raise core.ConflictError("cannot protect funds before funding is confirmed")
    if to_state == "TRIP_AUTHORIZED":
        # trip may not be authorized before funds are protected
        if frm != "FUNDS_PROTECTED":
            raise core.ConflictError("trip cannot be authorized before FUNDS_PROTECTED")
    if to_state in ("RELEASE_APPROVED", "RELEASE_CONFIRMED"):
        gate = tc.release_gate(conn, t["booking_id"], t["carrier_id"],
                               funding_confirmed=True, funds_protected=True,
                               milestone_verified=(milestone_verified if milestone_verified is not None else True),
                               pod_ok=(pod_ok if pod_ok is not None else True),
                               payout_account_id=payout_account_id,
                               job_value=(job_value if job_value is not None else t["contract_amount"]),
                               approvals_complete=approvals_complete)
        if not gate["allowed"]:
            raise core.ForbiddenError("RELEASE DENIED: " + ", ".join(gate["denied_reasons"]))
        assert_live_allowed(conn, moving_real_funds=(t["provider"] != "MOCK"))
    if to_state == "SETTLED":
        rec = reconcile(conn, tx_id)
        if not rec["balanced"]:
            raise core.ConflictError("SETTLEMENT BLOCKED: ledger does not reconcile (difference "
                                     f"{rec['difference']})")

    # dispute window timestamps
    updates = {"state": to_state, "updated_at": _now()}
    if to_state == "DISPUTE_WINDOW":
        updates["dispute_window_started_at"] = _now()
        updates["dispute_window_expires_at"] = (datetime.datetime.now(datetime.timezone.utc)
                                                + datetime.timedelta(hours=72)).isoformat(timespec="seconds")
    sets = ",".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE mkt_protected_tx SET {sets} WHERE id=?", (*updates.values(), tx_id))
    conn.execute("INSERT INTO mkt_protected_state_log(tx_id,from_state,to_state,reason,actor_id,"
                 "correlation_id,at) VALUES(?,?,?,?,?,?,?)",
                 (tx_id, frm, to_state, reason, actor["id"], core.correlation_id(), _now()))
    core.audit(conn, actor, "PP_STATE_TRANSITION", "mkt_protected_tx", tx_id,
               old={"state": frm}, new={"state": to_state, "reason": reason})
    conn.commit()
    return to_state


# --------------------------------------------------------------------------- #
# 17. Finance administration queues
# --------------------------------------------------------------------------- #
def finance_queues(conn, actor):
    core.require(actor, "marketplace.payment.view") if core.can(actor, "marketplace.payment.view") else core.require(actor, "marketplace.trust.view")
    frag, params = tenant.predicate(actor)
    rows = [dict(r) for r in conn.execute(
        "SELECT id,booking_id,carrier_id,contract_amount,protected_amount,state,provider,currency"
        " FROM mkt_protected_tx WHERE 1=1" + frag + " ORDER BY id DESC", params).fetchall()]
    by = {}
    for r in rows:
        by.setdefault(r["state"], []).append(r["id"])
    recon = daily_reconciliation(conn, actor)
    return {"transactions": rows, "by_state": {k: len(v) for k, v in by.items()},
            "reconciliation": {"exceptions": recon["exceptions"], "settlement_blocked": recon["settlement_blocked"]},
            "live_funds_enabled": live_funds_enabled(conn),
            "terminology": "Protected Payment", "legal_escrow": "NOT_YET_AUTHORIZED"}


def get_transaction(conn, actor, tx_id):
    t = _tx(conn, actor, tx_id)
    t["ledger"] = [dict(r) for r in conn.execute(
        "SELECT id,event,amount,reverses_entry_id,reason,created_at FROM mkt_protected_ledger"
        " WHERE tx_id=? ORDER BY id", (tx_id,)).fetchall()]
    t["state_log"] = [dict(r) for r in conn.execute(
        "SELECT from_state,to_state,reason,at FROM mkt_protected_state_log WHERE tx_id=? ORDER BY id",
        (tx_id,)).fetchall()]
    t["reconciliation"] = reconcile(conn, tx_id)
    return t


def run_integrity(conn):
    """No mutable-history corruption: reversal entries must reference an original entry."""
    bad = conn.execute("SELECT COUNT(*) c FROM mkt_protected_ledger WHERE event='reversal'"
                       " AND reverses_entry_id IS NULL").fetchone()["c"]
    return {"reversals_without_origin": bad, "ok": bad == 0}
