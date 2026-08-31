"""LiftHaul administration-fee settlement to a governed Wise business destination.

The public booking quote persists a 10% administration fee.  This module turns that
accounting snapshot into an independently auditable settlement instruction only after
the customer payment has been confirmed by BOTH the payment-provider webhook and a
server-to-server status query.

Creating a Wise quote or transfer is not the same as moving money.  A settlement is
reported COMPLETED only after Wise reports ``outgoing_payment_sent``/``completed``.
Production transfer submission is fail-closed until the Wise business profile,
recipient, balance-funding capability and early-release commercial treatment have all
been approved explicitly.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import urllib.error
import urllib.request
import uuid

import core
import tenant


PROVIDER = "WISE"
FEE_RATE = 0.10
SCHEMA = """
CREATE TABLE IF NOT EXISTS platform_fee_settlements(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, booking_id INTEGER NOT NULL,
  gateway_transaction_id INTEGER NOT NULL, provider TEXT NOT NULL DEFAULT 'WISE',
  transfer_timing TEXT NOT NULL, gross_amount REAL NOT NULL,
  transport_subtotal REAL NOT NULL, fee_rate REAL NOT NULL,
  fee_amount REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'PHP',
  status TEXT NOT NULL, idempotency_key TEXT NOT NULL,
  wise_profile_id TEXT, wise_recipient_id TEXT, wise_balance_id TEXT,
  wise_quote_id TEXT, wise_transfer_id TEXT, wise_status TEXT,
  provider_response_hash TEXT, blocker_json TEXT, safe_error TEXT,
  refunded_amount REAL NOT NULL DEFAULT 0, recovery_amount REAL NOT NULL DEFAULT 0,
  recovery_status TEXT,
  created_at TEXT NOT NULL, submitted_at TEXT, completed_at TEXT, updated_at TEXT NOT NULL,
  UNIQUE(gateway_transaction_id), UNIQUE(idempotency_key));

CREATE INDEX IF NOT EXISTS ix_platform_fee_settlement_status
ON platform_fee_settlements(status,created_at);
"""


class WiseFeeError(Exception):
    pass


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _truthy(name):
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn):
    return 0


def settlement_config():
    timing = os.getenv("ADMIN_FEE_WISE_TRANSFER_TIMING", "payment_confirmed").strip().lower()
    if timing not in {"payment_confirmed", "service_release"}:
        timing = "payment_confirmed"
    return {
        "enabled": _truthy("ADMIN_FEE_WISE_ENABLED"),
        "timing": timing,
        "business_account_approved": _truthy("WISE_BUSINESS_ACCOUNT_APPROVED"),
        "api_funding_approved": _truthy("WISE_API_FUNDING_APPROVED"),
        "early_release_approved": _truthy("ADMIN_FEE_EARLY_RELEASE_APPROVED"),
        "profile_id": os.getenv("WISE_PROFILE_ID", "").strip(),
        "recipient_id": os.getenv("WISE_ADMIN_FEE_RECIPIENT_ID", "").strip(),
        "balance_id": os.getenv("WISE_BALANCE_ID", "").strip(),
        "api_token": os.getenv("WISE_API_KEY", "").strip(),
        "api_base": os.getenv("WISE_API_BASE_URL", "https://api.wise.com").strip().rstrip("/"),
        "api_release": os.getenv("WISE_API_RELEASE", "2026Q3").strip().strip("/"),
    }


def _blockers(cfg):
    checks = (
        (cfg["enabled"], "ADMIN_FEE_WISE_ENABLED is false"),
        (cfg["timing"] == "payment_confirmed", "immediate transfer timing is not activated"),
        (cfg["business_account_approved"], "verified Wise Business destination is not approved"),
        (cfg["api_funding_approved"], "Wise API funding capability is not approved for this account/region"),
        (cfg["early_release_approved"], "early administration-fee release treatment is not approved"),
        (bool(cfg["profile_id"]), "WISE_PROFILE_ID is missing"),
        (bool(cfg["recipient_id"]), "WISE_ADMIN_FEE_RECIPIENT_ID is missing"),
        (bool(cfg["balance_id"]), "WISE_BALANCE_ID is missing"),
        (bool(cfg["api_token"]), "WISE_API_KEY is missing"),
        (cfg["api_base"].startswith("https://"), "Wise API base URL must use HTTPS"),
    )
    return [message for passed, message in checks if not passed]


class WiseAdminFeeClient:
    """Minimal Wise quote -> transfer -> balance-funding adapter.

    Tokens are read from the server environment and never persisted.  Wise's
    ``customerTransactionId`` is deterministic, providing provider-side idempotency
    if a network failure happens after transfer creation.
    """
    def __init__(self, cfg=None, timeout=20):
        self.cfg = cfg or settlement_config()
        self.timeout = timeout

    def _url(self, path):
        return f"{self.cfg['api_base']}/{self.cfg['api_release']}/{path.lstrip('/')}"

    def _request(self, method, path, payload=None, correlation_id=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": "Bearer " + self.cfg["api_token"],
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if correlation_id:
            headers["X-External-Correlation-Id"] = str(correlation_id)[:36]
        request = urllib.request.Request(self._url(path), data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            # Provider bodies can contain customer/recipient detail.  Return only a
            # small status-class error and never persist/log the raw body.
            raise WiseFeeError(f"Wise HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise WiseFeeError("Wise network request failed") from exc

    def create_quote(self, *, amount, currency, correlation_id):
        return self._request("POST", f"profiles/{self.cfg['profile_id']}/quotes", {
            "sourceCurrency": currency,
            "targetCurrency": currency,
            "sourceAmount": amount,
            "targetAmount": None,
            "targetAccount": int(self.cfg["recipient_id"]),
        }, correlation_id)

    def create_transfer(self, *, quote_id, customer_transaction_id, reference, correlation_id):
        return self._request("POST", "transfers", {
            "targetAccount": int(self.cfg["recipient_id"]),
            "quoteUuid": str(quote_id),
            "customerTransactionId": customer_transaction_id,
            "details": {"reference": reference[:100]},
        }, correlation_id)

    def fund_transfer(self, *, transfer_id, correlation_id):
        return self._request(
            "POST",
            f"profiles/{self.cfg['profile_id']}/transfers/{transfer_id}/payments",
            {"type": "BALANCE", "balanceId": int(self.cfg["balance_id"])},
            correlation_id,
        )

    def get_transfer(self, transfer_id, correlation_id=None):
        return self._request("GET", f"transfers/{transfer_id}", correlation_id=correlation_id)


def _system_actor():
    return {"id": 0, "role": "system", "tenant_id": None,
            "perms": {"marketplace.payment.reconcile"}}


def _row(conn, settlement_id):
    row = conn.execute("SELECT * FROM platform_fee_settlements WHERE id=?", (settlement_id,)).fetchone()
    if not row:
        raise core.NotFoundError("administration-fee settlement not found")
    return dict(row)


def _public(row):
    return {key: row.get(key) for key in (
        "id", "booking_id", "gateway_transaction_id", "provider", "transfer_timing",
        "gross_amount", "transport_subtotal", "fee_rate", "fee_amount", "currency",
        "status", "wise_transfer_id", "wise_status", "refunded_amount", "recovery_amount",
        "recovery_status", "created_at", "submitted_at", "completed_at", "updated_at",
    )}


def record_verified_payment(conn, gateway_transaction_id, client=None):
    """Create/submit exactly one Wise settlement for a provider-verified payment.

    This function is deliberately non-throwing for provider/configuration failures:
    customer payment confirmation remains authoritative even when revenue settlement
    requires finance intervention.  Data-integrity violations are stored as review
    blockers rather than converted into a false payout.
    """
    existing = conn.execute(
        "SELECT * FROM platform_fee_settlements WHERE gateway_transaction_id=?",
        (gateway_transaction_id,),
    ).fetchone()
    if existing:
        return {**_public(dict(existing)), "idempotent": True}
    source = conn.execute(
        "SELECT t.id AS transaction_id,t.tenant_id,t.booking_id,t.amount,t.currency,t.status AS payment_status,"
        "t.verification_method,b.quote_amount,b.transport_amount,b.administration_fee_rate,b.administration_fee "
        "FROM gateway_payment_transactions t JOIN mkt_bookings b ON b.id=t.booking_id WHERE t.id=?",
        (gateway_transaction_id,),
    ).fetchone()
    if not source:
        return {"status": "NOT_APPLICABLE", "reason": "payment transaction not found"}
    source = dict(source)
    if source["payment_status"] != "PAID" or source["verification_method"] != "PROVIDER_WEBHOOK_PLUS_API":
        return {"status": "NOT_ELIGIBLE", "reason": "payment is not provider-webhook-plus-API verified"}
    fee = round(float(source.get("administration_fee") or 0), 2)
    rate = float(source.get("administration_fee_rate") or 0)
    transport = round(float(source.get("transport_amount") or 0), 2)
    gross = round(float(source.get("amount") or 0), 2)
    integrity = []
    if fee <= 0:
        return {"status": "NOT_APPLICABLE", "reason": "booking has no administration fee"}
    if abs(rate - FEE_RATE) > 0.000001:
        integrity.append("administration fee rate is not the approved 10%")
    if abs(fee - round(transport * FEE_RATE, 2)) > 0.01:
        integrity.append("administration fee does not equal 10% of transport subtotal")
    if abs(gross - float(source.get("quote_amount") or 0)) > 0.01:
        integrity.append("paid amount does not match final quotation")
    cfg = settlement_config()
    blockers = integrity + _blockers(cfg)
    status = "REVIEW_REQUIRED" if integrity else ("BLOCKED_CONFIGURATION" if blockers else "READY")
    idem = f"WISE-ADMIN-FEE:{gateway_transaction_id}:v1"
    cur = conn.execute(
        "INSERT INTO platform_fee_settlements(tenant_id,booking_id,gateway_transaction_id,provider,"
        "transfer_timing,gross_amount,transport_subtotal,fee_rate,fee_amount,currency,status,idempotency_key,"
        "wise_profile_id,wise_recipient_id,wise_balance_id,blocker_json,created_at,updated_at) "
        "VALUES(?,?,?,'WISE',?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (source.get("tenant_id"), source["booking_id"], gateway_transaction_id, cfg["timing"], gross,
         transport, rate, fee, source["currency"], status, idem, cfg["profile_id"] or None,
         cfg["recipient_id"] or None, cfg["balance_id"] or None,
         json.dumps(blockers, sort_keys=True), _now(), _now()),
    )
    settlement_id = cur.lastrowid
    core.audit(conn, _system_actor(), "ADMIN_FEE_SETTLEMENT_RECORDED", "platform_fee_settlements",
               settlement_id, new={"booking_id": source["booking_id"], "fee_amount": fee,
                                   "currency": source["currency"], "status": status})
    conn.commit()
    if status != "READY":
        return _public(_row(conn, settlement_id))
    return attempt_transfer(conn, settlement_id, client=client)


def attempt_transfer(conn, settlement_id, client=None, actor=None):
    row = _row(conn, settlement_id)
    if actor is not None:
        core.require(actor, "marketplace.payment.reconcile")
        tenant.guard(actor, row)
    if row["status"] in {"SUBMITTED", "PROCESSING", "COMPLETED"}:
        return {**_public(row), "idempotent": True}
    cfg = settlement_config()
    blockers = _blockers(cfg)
    if blockers:
        conn.execute(
            "UPDATE platform_fee_settlements SET status='BLOCKED_CONFIGURATION',blocker_json=?,updated_at=? WHERE id=?",
            (json.dumps(blockers, sort_keys=True), _now(), settlement_id),
        )
        conn.commit()
        return _public(_row(conn, settlement_id))
    wise = client or WiseAdminFeeClient(cfg)
    correlation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, row["idempotency_key"]))
    customer_tx_id = correlation_id
    try:
        quote = wise.create_quote(amount=row["fee_amount"], currency=row["currency"], correlation_id=correlation_id)
        quote_id = quote.get("id") or quote.get("quoteUuid") or quote.get("quote_id")
        if not quote_id:
            raise WiseFeeError("Wise quote response has no identifier")
        transfer = wise.create_transfer(
            quote_id=quote_id, customer_transaction_id=customer_tx_id,
            reference=f"LiftHaul admin fee booking {row['booking_id']}", correlation_id=correlation_id,
        )
        transfer_id = transfer.get("id") or transfer.get("transferId")
        if not transfer_id:
            raise WiseFeeError("Wise transfer response has no identifier")
        conn.execute(
            "UPDATE platform_fee_settlements SET wise_quote_id=?,wise_transfer_id=?,wise_status=?,"
            "status='SUBMITTED',submitted_at=?,provider_response_hash=?,updated_at=? WHERE id=?",
            (str(quote_id), str(transfer_id), str(transfer.get("status") or "created"), _now(),
             _hash({"quote": quote_id, "transfer": transfer_id, "status": transfer.get("status")}),
             _now(), settlement_id),
        )
        conn.commit()  # durable provider id before attempting to fund
        funded = wise.fund_transfer(transfer_id=transfer_id, correlation_id=correlation_id)
        provider_status = str(funded.get("status") or transfer.get("status") or "processing").lower()
        completed = provider_status in {"outgoing_payment_sent", "completed"}
        conn.execute(
            "UPDATE platform_fee_settlements SET status=?,wise_status=?,provider_response_hash=?,"
            "completed_at=?,updated_at=? WHERE id=?",
            ("COMPLETED" if completed else "PROCESSING", provider_status,
             _hash({"transfer": transfer_id, "funding_status": provider_status}),
             _now() if completed else None, _now(), settlement_id),
        )
        core.audit(conn, _system_actor(), "ADMIN_FEE_WISE_TRANSFER_SUBMITTED",
                   "platform_fee_settlements", settlement_id,
                   new={"booking_id": row["booking_id"], "fee_amount": row["fee_amount"],
                        "currency": row["currency"], "wise_status": provider_status})
        conn.commit()
    except Exception as exc:
        safe = str(exc)[:160] if isinstance(exc, WiseFeeError) else "Wise transfer submission failed"
        conn.execute(
            "UPDATE platform_fee_settlements SET status='ACTION_REQUIRED',safe_error=?,updated_at=? WHERE id=?",
            (safe, _now(), settlement_id),
        )
        core.audit(conn, _system_actor(), "ADMIN_FEE_WISE_TRANSFER_FAILED",
                   "platform_fee_settlements", settlement_id,
                   new={"safe_error": safe, "money_reported_transferred": False})
        conn.commit()
    return _public(_row(conn, settlement_id))


def sync_transfer(conn, actor, settlement_id, client=None):
    core.require(actor, "marketplace.payment.reconcile")
    row = _row(conn, settlement_id)
    tenant.guard(actor, row)
    if not row.get("wise_transfer_id"):
        raise core.ConflictError("Wise transfer has not been created")
    wise = client or WiseAdminFeeClient()
    response = wise.get_transfer(row["wise_transfer_id"], correlation_id=core.correlation_id())
    provider_status = str(response.get("status") or "unknown").lower()
    if provider_status in {"outgoing_payment_sent", "completed"}:
        status, completed = "COMPLETED", _now()
    elif provider_status in {"bounced_back", "funds_refunded", "charged_back", "cancelled"}:
        status, completed = "FAILED", None
    else:
        status, completed = "PROCESSING", None
    conn.execute(
        "UPDATE platform_fee_settlements SET status=?,wise_status=?,completed_at=?,"
        "provider_response_hash=?,updated_at=? WHERE id=?",
        (status, provider_status, completed, _hash({"transfer": row["wise_transfer_id"],
                                                   "status": provider_status}), _now(), settlement_id),
    )
    core.audit(conn, actor, "ADMIN_FEE_WISE_STATUS_SYNCED", "platform_fee_settlements",
               settlement_id, new={"status": status, "wise_status": provider_status})
    conn.commit()
    return _public(_row(conn, settlement_id))


def handle_refund(conn, gateway_transaction_id, refunded_amount):
    row = conn.execute(
        "SELECT * FROM platform_fee_settlements WHERE gateway_transaction_id=?",
        (gateway_transaction_id,),
    ).fetchone()
    if not row:
        return {"status": "NOT_APPLICABLE"}
    row = dict(row)
    refunded = round(float(refunded_amount or 0), 2)
    recovery = round(min(row["fee_amount"], refunded * (row["fee_amount"] / row["gross_amount"])), 2)
    money_submitted = row["status"] in {"SUBMITTED", "PROCESSING", "COMPLETED", "ACTION_REQUIRED"} and bool(row.get("wise_transfer_id"))
    status = "REFUND_REVIEW_REQUIRED" if money_submitted else "CANCELLED_REFUND"
    conn.execute(
        "UPDATE platform_fee_settlements SET status=?,refunded_amount=?,recovery_amount=?,"
        "recovery_status=?,updated_at=? WHERE id=?",
        (status, refunded, recovery, "OPEN" if money_submitted else "NOT_REQUIRED", _now(), row["id"]),
    )
    core.audit(conn, _system_actor(), "ADMIN_FEE_REFUND_IMPACT_RECORDED",
               "platform_fee_settlements", row["id"],
               new={"refunded_amount": refunded, "recovery_amount": recovery, "status": status})
    conn.commit()
    return _public(_row(conn, row["id"]))


def list_settlements(conn, actor):
    core.require(actor, "marketplace.payment.reconcile")
    if actor.get("tenant_id") is None:
        rows = conn.execute("SELECT * FROM platform_fee_settlements ORDER BY id DESC LIMIT 500").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM platform_fee_settlements WHERE tenant_id=? ORDER BY id DESC LIMIT 500",
            (actor["tenant_id"],),
        ).fetchall()
    return [_public(dict(row)) for row in rows]
