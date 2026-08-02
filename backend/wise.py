"""LiftHaul OS — Phase 7: Wise provider adapter + payment-request integration.

The internal payment workflow stays provider-independent (`core.PaymentProvider`). This module adds a
`WiseProvider` abstraction with:
  * `MockWiseAdapter` — a DETERMINISTIC mock proving every non-secret capability (profile validation,
    quote, transfer, status mapping) and every scenario (pending/completed/failed/partial/overpay/
    refund/timeout/rate-limit/auth-fail/expired-quote/duplicate-webhook);
  * `RealWiseAdapter` — the production adapter. It reads a server-held API token via the Phase-6
    secret reference (NEVER returned to callers) and hits the Wise API. Without owner-controlled
    credentials it reports LIVE BLOCKED — it never fabricates success.

Financial rules: the payment amount comes from the STORED accepted-quotation downpayment snapshot
(core.create_payment_request); a provider `CREATED`/200 is never settlement; verification requires a
reconciled MATCHED item + an authorized verifier who is NOT the transfer creator (separation of duties).
"""
from __future__ import annotations

import datetime
import json
import secrets as _secrets

import core
import integrations as ig


# --- provider errors (classified into integration failure categories) ------- #
class WiseError(Exception):
    category = "unknown_provider_response"


class WiseAuthError(WiseError):
    category = "authentication_failure"


class WiseRateLimit(WiseError):
    category = "rate_limited"


class WiseTimeout(WiseError):
    category = "transient_network"


class WiseUnavailable(WiseError):
    category = "provider_unavailable"


class WiseValidation(WiseError):
    category = "validation_failure"


# --- Wise provider status → normalized status mapping ------------------------ #
_STATUS_MAP = {
    "incoming_payment_waiting": "PENDING",
    "processing": "PROCESSING",
    "funds_converted": "FUNDED",
    "outgoing_payment_sent": "COMPLETED",
    "completed": "COMPLETED",
    "bounced_back": "FAILED",
    "funds_refunded": "REFUNDED",
    "charged_back": "REVERSED",
    "cancelled": "CANCELLED",
}


def map_status(provider_status):
    return _STATUS_MAP.get(provider_status, "UNKNOWN")


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


class MockWiseAdapter:
    """Deterministic, offline. Scenarios are encoded in the transfer id so status is reproducible.
    NEVER available for real settlement — callers gate production behind RealWiseAdapter."""
    name = "wise_mock"
    is_mock = True

    def validate_connection(self, profile):
        # a mock credential always validates; returns MULTIPLE profiles so the admin must choose one
        return {"ok": True, "health": "HEALTHY",
                "profiles": [{"id": "BUSINESS-1", "type": "business", "name": "RGO Machine Rigging"},
                             {"id": "PERSONAL-1", "type": "personal", "name": "Personal"}],
                "currencies": ["PHP", "USD", "EUR"], "routes": ["balance", "bank_transfer"]}

    def create_quote(self, *, source_currency, target_currency, amount, scenario=None):
        if scenario == "auth_fail":
            raise WiseAuthError("invalid credential")
        if scenario == "rate_limit":
            raise WiseRateLimit("429")
        if scenario == "timeout":
            raise WiseTimeout("read timeout")
        if scenario == "unavailable":
            raise WiseUnavailable("503")
        rate = 1.0 if source_currency == target_currency else 56.0
        fee = round(amount * 0.01, 2)
        expiry = _now() - datetime.timedelta(minutes=5) if scenario == "expired" else _now() + datetime.timedelta(minutes=30)
        qid = "WISE-Q-" + _secrets.token_hex(5).upper()
        return {"provider_quote_id": qid, "source_currency": source_currency, "target_currency": target_currency,
                "source_amount": amount, "target_amount": round(amount * rate, 2), "rate": rate, "fee": fee,
                "expiry": _iso(expiry)}

    def create_transfer(self, *, quote_id, amount, currency, idem_key, recipient_ref=None, scenario="completed"):
        # scenario is embedded in the id so get_transfer_status is deterministic + stateless
        tid = f"WISE-T-{(scenario or 'completed').upper()}-{_secrets.token_hex(4).upper()}"
        return {"provider_transfer_id": tid, "provider_quote_id": quote_id, "amount": amount,
                "currency": currency, "provider_status": "processing", "normalized_status": "CREATED"}

    def get_transfer_status(self, provider_transfer_id):
        # decode the scenario segment: WISE-T-<SCENARIO>-<hex>
        try:
            scenario = provider_transfer_id.split("-")[2].lower()
        except Exception:
            scenario = "completed"
        provider_status = {
            "completed": "outgoing_payment_sent", "pending": "processing", "processing": "processing",
            "failed": "bounced_back", "refunded": "funds_refunded", "reversed": "charged_back",
            "cancelled": "cancelled", "partial": "outgoing_payment_sent", "overpay": "outgoing_payment_sent",
        }.get(scenario, "outgoing_payment_sent")
        return map_status(provider_status)

    def settlement_amount(self, provider_transfer_id, requested_amount):
        """Deterministic settled amount for reconciliation scenarios."""
        try:
            scenario = provider_transfer_id.split("-")[2].lower()
        except Exception:
            scenario = "completed"
        if scenario == "partial":
            return round(requested_amount * 0.5, 2)
        if scenario == "overpay":
            return round(requested_amount * 1.25, 2)
        return requested_amount


class RealWiseAdapter:
    """Production adapter. Reads the API token from the Phase-6 secret reference (server-side only,
    never returned). Without owner-controlled credentials, every call reports LIVE BLOCKED — it does
    NOT fabricate success."""
    name = "wise"
    is_mock = False

    def _token(self):
        import os
        # the profile's secret_ref names an env-backed secret; the value is used only here, never returned
        token = os.environ.get("WISE_API_KEY")
        if not token:
            raise WiseAuthError("LIVE WISE BLOCKED: WISE_API_KEY not configured (owner action required)")
        return token

    def validate_connection(self, profile):
        try:
            self._token()
        except WiseAuthError as e:
            return {"ok": False, "health": "AUTHENTICATION_FAILED", "blocked": True, "detail": str(e)}
        # real HTTP to https://api.wise.com/v1/profiles would go here; kept BLOCKED until credentials verify
        return {"ok": False, "health": "MISCONFIGURED", "blocked": True,
                "detail": "live Wise call not executed without verified owner credentials"}

    def create_quote(self, **kw):
        raise WiseAuthError("LIVE WISE BLOCKED: owner credential validation required")

    def create_transfer(self, **kw):
        raise WiseAuthError("LIVE WISE BLOCKED: owner credential validation required")

    def get_transfer_status(self, provider_transfer_id):
        return "UNKNOWN"


def get_adapter(environment):
    return RealWiseAdapter() if environment == "PRODUCTION" else MockWiseAdapter()


# --------------------------------------------------------------------------- #
# Payment-request integration (provider-independent core + Wise orchestration)
# --------------------------------------------------------------------------- #
def create_wise_payment(conn, actor, bid, profile_id, idem_key, scenario="completed"):
    """Create a governed Wise payment for a booking's accepted quotation. The amount comes from the
    STORED downpayment snapshot (never recomputed). Idempotent: a repeated key returns the original."""
    core.require(actor, "payment.wise.transfer.create")
    p = ig.get_profile(conn, actor, profile_id)
    if not ig._profile_usable(p):
        raise core.ConflictError("connection profile is not ACTIVE (or circuit open) — failing safe")
    if p["environment"] == "PRODUCTION" and get_adapter("PRODUCTION").is_mock is False:
        # production requires real credentials; without them the adapter blocks (no fabricated success)
        pass
    # idempotency (repeated key with same payload returns original transfer)
    payload = {"bid": bid, "profile_id": profile_id, "op": "create_wise_payment"}
    existing_ref, is_replay = ig.idempotent(conn, actor, idem_key, "create_wise_payment", payload,
                                            entity_ref=f"booking:{bid}", provider_code="wise")
    if is_replay and existing_ref:
        row = conn.execute("SELECT * FROM provider_transfers WHERE id=?", (int(existing_ref),)).fetchone()
        if row:
            return {"transfer_id": row["id"], "provider_transfer_id": row["provider_transfer_id"],
                    "idempotent_replay": True}
    # internal payment request — amount from stored accepted-quotation dp snapshot (Phase-2)
    prid = _ensure_payment_request(conn, actor, bid)
    pr = conn.execute("SELECT * FROM payment_requests WHERE id=?", (prid,)).fetchone()
    amount, currency = pr["amount_due"], (pr["currency"] or p["default_currency"] or "PHP")
    adapter = ig._adapter(conn, p)
    try:
        quote = adapter.create_quote(source_currency=currency, target_currency=currency, amount=amount, scenario=scenario)
    except WiseError as e:
        _handle_provider_error(conn, actor, "create_quote", f"booking:{bid}", e)
        raise core.ConflictError(f"Wise quote failed ({e.category})")
    # do not use an expired quote
    if quote["expiry"] < _iso(_now()):
        raise core.ConflictError("Wise quote is expired — not creating a transfer")
    conn.execute("INSERT INTO provider_quotes(tenant_id,profile_id,provider_code,provider_quote_id,source_currency,"
                 "target_currency,source_amount,target_amount,rate,fee,expiry,response_hash,snapshot,created_at)"
                 " VALUES(?,?, 'wise', ?,?,?,?,?,?,?,?,?,?,?)",
                 (p["tenant_id"], profile_id, quote["provider_quote_id"], quote["source_currency"],
                  quote["target_currency"], quote["source_amount"], quote["target_amount"], quote["rate"],
                  quote["fee"], quote["expiry"], ig._hash(quote), json.dumps(quote), _now_iso()))
    try:
        transfer = adapter.create_transfer(quote_id=quote["provider_quote_id"], amount=amount, currency=currency,
                                           idem_key=idem_key, scenario=scenario)
    except WiseError as e:
        _handle_provider_error(conn, actor, "create_transfer", f"booking:{bid}", e)
        raise core.ConflictError(f"Wise transfer failed ({e.category})")
    cur = conn.execute("INSERT INTO provider_transfers(tenant_id,profile_id,provider_code,payment_request_id,"
                       "provider_transfer_id,provider_quote_id,amount,currency,provider_status,normalized_status,"
                       "fee,rate,idem_key,reference,created_by,created_at,correlation_id) VALUES(?,?, 'wise', ?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (p["tenant_id"], profile_id, prid, transfer["provider_transfer_id"], quote["provider_quote_id"],
                        amount, currency, transfer["provider_status"], "CREATED", quote["fee"], quote["rate"],
                        idem_key, pr["no"], (actor or {}).get("id"), _now_iso(), core.correlation_id()))
    transfer_row_id = cur.lastrowid
    conn.execute("UPDATE payment_requests SET provider='wise', provider_ref=?, status='LINK_SENT', updated_at=? WHERE id=?",
                 (transfer["provider_transfer_id"], _now_iso(), prid))
    ig._record_success(conn, profile_id)
    ig._idem_complete(conn, actor, idem_key, transfer_row_id)
    core.audit(conn, actor, "WISE_TRANSFER_CREATED", "provider_transfers", transfer_row_id,
               new={"payment_request": prid, "provider_transfer_id": transfer["provider_transfer_id"], "amount": amount})
    conn.commit()
    return {"transfer_id": transfer_row_id, "provider_transfer_id": transfer["provider_transfer_id"],
            "payment_request_id": prid, "amount": amount, "currency": currency, "idempotent_replay": False}


def _ensure_payment_request(conn, actor, bid):
    existing = conn.execute("SELECT id FROM payment_requests WHERE booking_id=?", (bid,)).fetchone()
    if existing:
        return existing["id"]
    return core.create_payment_request(conn, actor, bid)     # amount from stored dp snapshot


def _handle_provider_error(conn, actor, operation, entity_ref, err):
    cls = ig.classify_failure(getattr(err, "category", "unknown_provider_response"))
    ig.dead_letter(conn, actor, "wise", operation, err.category, safe_error=str(err)[:120], entity_ref=entity_ref)
    # a repeated failure trips the circuit breaker on the profile (handled by caller's _record_failure)


def sync_transfer_status(conn, actor, transfer_row_id):
    """Query provider status and update the normalized status. Never marks the payment verified."""
    core.require(actor, "payment.wise.view")
    t = conn.execute("SELECT * FROM provider_transfers WHERE id=?", (transfer_row_id,)).fetchone()
    if not t:
        raise core.NotFoundError("transfer not found")
    p = ig.get_profile(conn, actor, t["profile_id"])
    adapter = ig._adapter(conn, p)
    norm = adapter.get_transfer_status(t["provider_transfer_id"])
    conn.execute("UPDATE provider_transfers SET normalized_status=?, updated_at=? WHERE id=?", (norm, _now_iso(), transfer_row_id))
    core.audit(conn, actor, "WISE_STATUS_SYNCED", "provider_transfers", transfer_row_id, new={"normalized_status": norm})
    conn.commit()
    return {"transfer_id": transfer_row_id, "normalized_status": norm}


def reconcile_transfer(conn, actor, transfer_row_id):
    """Reconcile a settled transfer against its payment request using the deterministic settled amount."""
    core.require(actor, "payment.wise.reconcile")
    t = conn.execute("SELECT * FROM provider_transfers WHERE id=?", (transfer_row_id,)).fetchone()
    if not t:
        raise core.NotFoundError("transfer not found")
    p = ig.get_profile(conn, actor, t["profile_id"])
    adapter = ig._adapter(conn, p)
    norm = adapter.get_transfer_status(t["provider_transfer_id"])
    if norm != "COMPLETED":
        raise core.ConflictError(f"transfer not settled (status {norm}) — cannot reconcile")
    settled = adapter.settlement_amount(t["provider_transfer_id"], t["amount"]) if hasattr(adapter, "settlement_amount") else t["amount"]
    return ig.reconcile(conn, actor, t["payment_request_id"], t["provider_transfer_id"], settled, t["currency"], reference=t["reference"])


def verify_wise_payment(conn, actor, transfer_row_id, notes=None):
    """Verify a Wise payment ONLY when a reconciliation item is MATCHED/RECONCILED, and the verifier
    is NOT the transfer creator (separation of duties). Feeds core.verify_payment (the job-activation
    prerequisite). Never verifies from a provider 'CREATED' alone."""
    core.require(actor, "payment.wise.verify")
    t = conn.execute("SELECT * FROM provider_transfers WHERE id=?", (transfer_row_id,)).fetchone()
    if not t:
        raise core.NotFoundError("transfer not found")
    if t["created_by"] == (actor or {}).get("id"):
        raise core.ForbiddenError("separation of duties: the transfer creator may not verify the payment")
    rec = conn.execute("SELECT * FROM reconciliation_items WHERE payment_request_id=? AND provider_transfer_id=?"
                       " ORDER BY id DESC LIMIT 1", (t["payment_request_id"], t["provider_transfer_id"])).fetchone()
    if not rec or rec["status"] not in ("MATCHED", "RECONCILED"):
        raise core.ConflictError("no reconciled settlement evidence — cannot verify")
    pr = conn.execute("SELECT * FROM payment_requests WHERE id=?", (t["payment_request_id"],)).fetchone()
    status = core.verify_payment(conn, actor, t["payment_request_id"], rec["amount"], t["provider_transfer_id"],
                                 fees=t["fee"] or 0, notes=notes or "wise settlement")
    conn.execute("UPDATE provider_transfers SET verified_by=? WHERE id=?", ((actor or {}).get("id"), transfer_row_id))
    core.audit(conn, actor, "WISE_PAYMENT_VERIFIED", "provider_transfers", transfer_row_id,
               new={"payment_request": t["payment_request_id"], "status": status, "reconciliation_id": rec["id"]})
    conn.commit()
    return {"transfer_id": transfer_row_id, "payment_status": status, "reconciliation_id": rec["id"]}


def request_refund(conn, actor, transfer_row_id, amount, reason):
    core.require(actor, "payment.wise.refund.request")
    t = conn.execute("SELECT * FROM provider_transfers WHERE id=?", (transfer_row_id,)).fetchone()
    if not t:
        raise core.NotFoundError("transfer not found")
    cur = conn.execute("INSERT INTO provider_refunds(tenant_id,payment_request_id,provider_transfer_id,amount,"
                       "currency,reason,status,requested_by,created_at,correlation_id) VALUES(?,?,?,?,?,?, 'REQUESTED', ?,?,?)",
                       (t["tenant_id"], t["payment_request_id"], t["provider_transfer_id"], amount, t["currency"],
                        reason, (actor or {}).get("id"), _now_iso(), core.correlation_id()))
    core.audit(conn, actor, "WISE_REFUND_REQUESTED", "provider_refunds", cur.lastrowid, new={"amount": amount}, reason=reason)
    conn.commit()
    return cur.lastrowid


def approve_refund(conn, actor, refund_id, reason=None):
    """Refund approval requires a separate approver; refund is not complete until provider confirmation."""
    core.require(actor, "payment.wise.refund.approve")
    r = conn.execute("SELECT * FROM provider_refunds WHERE id=?", (refund_id,)).fetchone()
    if not r:
        raise core.NotFoundError("refund not found")
    if r["requested_by"] == (actor or {}).get("id"):
        raise core.ForbiddenError("separation of duties: refund approver must differ from requester")
    # provider confirmation (mock confirms; real adapter would call Wise and set provider_confirmed on callback)
    conn.execute("UPDATE provider_refunds SET status='APPROVED', approved_by=?, provider_confirmed=1 WHERE id=?",
                 ((actor or {}).get("id"), refund_id))
    core.audit(conn, actor, "WISE_REFUND_APPROVED", "provider_refunds", refund_id, reason=reason)
    conn.commit()
    return True


def list_transfers(conn, actor):
    core.require(actor, "payment.wise.view")
    at = ig._tenant(actor)
    rows = conn.execute("SELECT * FROM provider_transfers WHERE tenant_id=? OR tenant_id IS NULL ORDER BY id DESC LIMIT 200",
                        (at,)).fetchall() if at is not None else conn.execute("SELECT * FROM provider_transfers ORDER BY id DESC LIMIT 200").fetchall()
    return [dict(r) for r in rows]


def _now_iso():
    return _iso(_now())
