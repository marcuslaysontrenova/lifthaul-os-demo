"""Provider-backed customer payment gateway for LiftHaul.

The module implements the authoritative transaction edge between a customer booking and a licensed
payment provider.  It intentionally does *not* replace the protected-payment/release ledger in
``marketplace_payments`` or ``protected_payment``.  A checkout redirect, screenshot, HTTP 200, or
unverified callback is never payment evidence.

Production invariants:
  * payment channels are fail-closed and hidden until configured and independently certified;
  * every create/refund operation is idempotent;
  * a successful webhook is followed by a server-to-server provider status query;
  * booking reference, amount, currency, provider IDs and status must all match before PAID;
  * duplicate/invalid callbacks cannot mutate money or booking state;
  * manual verification remains UNDER_REVIEW until a second authorized operator validates an
    official bank/provider record; a screenshot alone is not accepted;
  * reconciliation records anomalies without silently correcting them.

The first adapter targets Xendit Payment Sessions / Payments API v3.  Live credentials are read only
from environment variables and are never persisted or returned by public APIs.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import os
import secrets
import urllib.error
import urllib.request

import core
import tenant


STATUSES = (
    "PENDING", "PROCESSING", "PAID", "FAILED", "EXPIRED", "CANCELLED",
    "REFUNDED", "PARTIALLY_REFUNDED", "UNDER_REVIEW",
)
TERMINAL_STATUSES = {"PAID", "FAILED", "EXPIRED", "CANCELLED", "REFUNDED"}
API_VERSION = "2024-11-11"
PROVIDER = "XENDIT"

CHANNELS = {
    "gcash": {
        "label": "GCash", "provider_codes": ["GCASH"], "kind": "E-wallet",
        "refund": True, "partial_refund": True,
    },
    "maya": {
        "label": "Maya", "provider_codes": ["PAYMAYA"], "kind": "E-wallet",
        "refund": True, "partial_refund": True,
    },
    "bank_transfer": {
        "label": "Online bank transfer", "provider_codes": ["BANK_TRANSFER"],
        "kind": "Bank transfer", "refund": False, "partial_refund": False,
    },
    "qrph": {
        "label": "QR Ph", "provider_codes": ["QRPH"], "kind": "QR payment",
        "refund": False, "partial_refund": False,
    },
    "card": {
        "label": "Debit or credit card", "provider_codes": ["CARDS"],
        "kind": "Hosted card checkout", "refund": True, "partial_refund": True,
    },
    "otc": {
        "label": "Over-the-counter", "provider_codes": ["7ELEVEN_CLIQQ"],
        "kind": "Retail payment code", "refund": False, "partial_refund": False,
    },
}

REQUIRED_CERTIFICATION_TESTS = (
    "successful_payment", "failed_payment", "cancelled_payment", "expired_payment",
    "duplicate_webhook", "invalid_webhook_signature", "incorrect_amount",
    "delayed_confirmation", "refund", "partial_refund", "reconciliation",
    "end_to_end_channel",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS gateway_channel_certifications(
  id INTEGER PRIMARY KEY, provider TEXT NOT NULL, environment TEXT NOT NULL,
  channel_key TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', tests_json TEXT,
  certified_by INTEGER, certified_at TEXT, notes TEXT,
  UNIQUE(provider,environment,channel_key));

CREATE TABLE IF NOT EXISTS gateway_payment_transactions(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, booking_id INTEGER NOT NULL,
  provider TEXT NOT NULL, environment TEXT NOT NULL, channel_key TEXT NOT NULL,
  amount REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'PHP', reference_id TEXT NOT NULL,
  provider_session_id TEXT, provider_payment_request_id TEXT, provider_payment_id TEXT,
  provider_channel_reference TEXT, checkout_url TEXT, provider_status TEXT,
  status TEXT NOT NULL DEFAULT 'PENDING', verification_method TEXT,
  idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
  webhook_verified_at TEXT, api_verified_at TEXT, paid_at TEXT, expires_at TEXT,
  refunded_amount REAL DEFAULT 0, failure_code TEXT, review_reason TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(provider,reference_id), UNIQUE(booking_id,idempotency_key));

CREATE TABLE IF NOT EXISTS gateway_webhook_events(
  id INTEGER PRIMARY KEY, provider TEXT NOT NULL, event_key TEXT NOT NULL,
  event_type TEXT, payload_hash TEXT NOT NULL, signature_verified INTEGER DEFAULT 0,
  transaction_id INTEGER, processing_status TEXT NOT NULL DEFAULT 'RECEIVED',
  safe_error TEXT, received_at TEXT NOT NULL, processed_at TEXT,
  UNIQUE(provider,event_key));

CREATE TABLE IF NOT EXISTS gateway_refunds(
  id INTEGER PRIMARY KEY, transaction_id INTEGER NOT NULL, provider TEXT NOT NULL,
  provider_refund_id TEXT, reference_id TEXT NOT NULL, amount REAL NOT NULL,
  currency TEXT NOT NULL DEFAULT 'PHP', reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING', idempotency_key TEXT NOT NULL,
  requested_by INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(transaction_id,idempotency_key));

CREATE TABLE IF NOT EXISTS gateway_manual_reviews(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, booking_id INTEGER NOT NULL, transaction_id INTEGER,
  amount REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'PHP', status TEXT NOT NULL DEFAULT 'UNDER_REVIEW',
  bank_reference TEXT, supporting_document TEXT, reason TEXT NOT NULL,
  opened_by INTEGER NOT NULL, approved_by INTEGER, opened_at TEXT NOT NULL, approved_at TEXT,
  audit_note TEXT);

CREATE TABLE IF NOT EXISTS gateway_reconciliation_runs(
  id INTEGER PRIMARY KEY, provider TEXT NOT NULL, environment TEXT NOT NULL,
  run_key TEXT UNIQUE,
  started_at TEXT NOT NULL, completed_at TEXT, checked_count INTEGER DEFAULT 0,
  issue_count INTEGER DEFAULT 0, status TEXT NOT NULL DEFAULT 'RUNNING', summary_json TEXT);

CREATE TABLE IF NOT EXISTS gateway_reconciliation_issues(
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, transaction_id INTEGER,
  issue_type TEXT NOT NULL, expected_json TEXT, actual_json TEXT,
  status TEXT NOT NULL DEFAULT 'OPEN', created_at TEXT NOT NULL);
"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _future(minutes=30):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _truthy(name):
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _csv(name):
    return {v.strip().lower() for v in os.getenv(name, "").split(",") if v.strip()}


def init(conn):
    conn.executescript(SCHEMA)
    # Preserve upgrade compatibility for development databases created while the
    # gateway schema was being introduced.  PostgreSQL's adapter translates the
    # SQLite-compatible PRAGMA/ALTER statements used throughout this codebase.
    migrations = {
        "gateway_manual_reviews": {"tenant_id": "INTEGER"},
        "gateway_reconciliation_runs": {"run_key": "TEXT"},
    }
    for table, columns in migrations.items():
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for column, declaration in columns.items():
            if column not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_gateway_reconciliation_run_key "
        "ON gateway_reconciliation_runs(run_key)"
    )
    conn.commit()


def seed(conn):
    return 0  # no channel is certified by default


def gateway_config():
    mode = os.getenv("PAYMENT_GATEWAY_MODE", "disabled").strip().lower()
    if mode not in {"disabled", "sandbox", "production"}:
        mode = "disabled"
    return {
        "mode": mode,
        "environment": "PRODUCTION" if mode == "production" else "SANDBOX",
        "secret_key": os.getenv("XENDIT_SECRET_KEY", "").strip(),
        "webhook_token": os.getenv("XENDIT_WEBHOOK_TOKEN", "").strip(),
        "enabled_channels": _csv("PAYMENT_ENABLED_CHANNELS"),
        "return_base_url": os.getenv("PAYMENT_RETURN_BASE_URL", "").strip().rstrip("/"),
        "provider_certified": _truthy("PAYMENT_PROVIDER_CERTIFIED"),
        "production_pilot_approved": _truthy("PAYMENT_PRODUCTION_PILOT_APPROVED"),
        "api_base_url": os.getenv("XENDIT_API_BASE_URL", "https://api.xendit.co").strip().rstrip("/"),
    }


def _certified(conn, channel_key, environment):
    row = conn.execute(
        "SELECT status,tests_json FROM gateway_channel_certifications WHERE provider=? AND environment=? AND channel_key=?",
        (PROVIDER, environment, channel_key),
    ).fetchone()
    if not row or row["status"] != "CERTIFIED":
        return False
    tests = json.loads(row["tests_json"] or "{}")
    return all(tests.get(name) is True for name in REQUIRED_CERTIFICATION_TESTS)


def available_channels(conn):
    """Return only channels that are safe to display to a customer."""
    cfg = gateway_config()
    configured = bool(cfg["secret_key"] and cfg["webhook_token"] and cfg["return_base_url"].startswith("https://"))
    production_gate = cfg["mode"] != "production" or cfg["production_pilot_approved"]
    ready = cfg["mode"] != "disabled" and configured and cfg["provider_certified"] and production_gate
    channels = []
    if ready:
        for key, meta in CHANNELS.items():
            if key in cfg["enabled_channels"] and _certified(conn, key, cfg["environment"]):
                channels.append({
                    "key": key, "label": meta["label"], "kind": meta["kind"],
                    "refund_supported": meta["refund"],
                    "partial_refund_supported": meta["partial_refund"],
                    "provider": PROVIDER, "environment": cfg["environment"],
                })
    reason = None
    if not channels:
        if cfg["mode"] == "disabled":
            reason = "Online payment is not activated. No charge will be collected."
        elif not configured:
            reason = "Payment-provider credentials or the secure return URL are incomplete."
        elif not cfg["provider_certified"]:
            reason = "The payment provider has not passed the activation gate."
        elif not production_gate:
            reason = "The controlled production pilot has not been approved."
        else:
            reason = "No payment channel has completed channel certification."
    return {
        "provider": PROVIDER, "mode": cfg["mode"], "environment": cfg["environment"],
        "available": bool(channels), "channels": channels, "reason": reason,
        "authoritative_confirmation": "Verified provider webhook plus server-to-server status check",
    }


def certify_channel(conn, actor, channel_key, environment, tests, notes=None):
    core.require(actor, "marketplace.payment.override")
    key = str(channel_key or "").lower()
    env = str(environment or "").upper()
    if key not in CHANNELS or env not in {"SANDBOX", "PRODUCTION"}:
        raise core.ValidationError("unknown channel or environment")
    tests = tests if isinstance(tests, dict) else {}
    missing = [name for name in REQUIRED_CERTIFICATION_TESTS if tests.get(name) is not True]
    if missing:
        raise core.ValidationError("payment channel certification is incomplete: " + ", ".join(missing))
    if env == "PRODUCTION":
        if not _certified(conn, key, "SANDBOX"):
            raise core.ConflictError("sandbox certification is required before production certification")
        if not gateway_config()["production_pilot_approved"]:
            raise core.ConflictError("controlled production pilot approval is required")
    existing = conn.execute(
        "SELECT id FROM gateway_channel_certifications WHERE provider=? AND environment=? AND channel_key=?",
        (PROVIDER, env, key),
    ).fetchone()
    payload = json.dumps(tests, sort_keys=True)
    if existing:
        conn.execute(
            "UPDATE gateway_channel_certifications SET status='CERTIFIED',tests_json=?,certified_by=?,certified_at=?,notes=? WHERE id=?",
            (payload, actor["id"], _now(), str(notes or "")[:1000], existing["id"]),
        )
        cid = existing["id"]
    else:
        cur = conn.execute(
            "INSERT INTO gateway_channel_certifications(provider,environment,channel_key,status,tests_json,certified_by,certified_at,notes) VALUES(?,?,?,'CERTIFIED',?,?,?,?)",
            (PROVIDER, env, key, payload, actor["id"], _now(), str(notes or "")[:1000]),
        )
        cid = cur.lastrowid
    core.audit(conn, actor, "PAYMENT_CHANNEL_CERTIFIED", "gateway_channel_certifications", cid, None,
               {"provider": PROVIDER, "environment": env, "channel": key})
    conn.commit()
    return {"id": cid, "provider": PROVIDER, "environment": env, "channel": key, "status": "CERTIFIED"}


class XenditClient:
    def __init__(self, secret_key, base_url="https://api.xendit.co", timeout=15):
        if not secret_key:
            raise core.ForbiddenError("payment provider credential is not configured")
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @classmethod
    def from_env(cls):
        cfg = gateway_config()
        return cls(cfg["secret_key"], cfg["api_base_url"])

    def _request(self, method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        auth = base64.b64encode((self.secret_key + ":").encode()).decode()
        req = urllib.request.Request(self.base_url + path, data=body, method=method, headers={
            "Authorization": "Basic " + auth,
            "Content-Type": "application/json",
            "api-version": API_VERSION,
            "User-Agent": "LiftHaulOS/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read() or b"{}")
                safe = detail.get("error_code") or detail.get("message") or f"HTTP {exc.code}"
            except Exception:
                safe = f"HTTP {exc.code}"
            raise core.ConflictError("payment provider rejected the request: " + str(safe)[:160])
        except urllib.error.URLError:
            raise core.ConflictError("payment provider is temporarily unreachable")

    def create_session(self, payload):
        return self._request("POST", "/sessions", payload)

    def get_session(self, session_id):
        return self._request("GET", "/sessions/" + session_id)

    def get_payment(self, payment_id):
        return self._request("GET", "/v3/payments/" + payment_id)

    def create_refund(self, payload):
        return self._request("POST", "/refunds", payload)


def _booking_by_token(conn, token):
    if not token or not str(token).startswith("pbk_") or len(str(token)) > 128:
        raise core.NotFoundError("booking not found")
    row = conn.execute(
        "SELECT id,tenant_id,status,quote_amount,quote_status,quotation_status,payment_status,tracking_token FROM mkt_bookings WHERE tracking_token=?",
        (token,),
    ).fetchone()
    if not row:
        raise core.NotFoundError("booking not found")
    return dict(row)


def _row(conn, transaction_id):
    row = conn.execute("SELECT * FROM gateway_payment_transactions WHERE id=?", (transaction_id,)).fetchone()
    if not row:
        raise core.NotFoundError("payment transaction not found")
    return dict(row)


def _public_transaction(row):
    return {
        "transaction_id": row["id"], "provider": row["provider"],
        "channel": row["channel_key"], "amount": row["amount"], "currency": row["currency"],
        "status": row["status"], "reference_number": row["reference_id"],
        "checkout_url": row.get("checkout_url") if row["status"] in {"PENDING", "PROCESSING"} else None,
        "expires_at": row.get("expires_at"), "paid_at": row.get("paid_at"),
        "refunded_amount": row.get("refunded_amount") or 0,
        "verification_method": row.get("verification_method"),
        "verification_reminder": "Payment is confirmed only from the provider or an audited official-record review.",
    }


def _safe_checkout_url(url):
    url = str(url or "")
    if not url.startswith("https://"):
        raise core.ConflictError("provider returned an unsafe checkout URL")
    host = url.split("/", 3)[2].lower().split(":", 1)[0]
    if not (host == "xen.to" or host == "dev.xen.to" or host.endswith(".xendit.co")):
        raise core.ConflictError("provider returned an unrecognized checkout host")
    return url


def create_payment_session(conn, token, channel_key, idempotency_key, client=None):
    booking = _booking_by_token(conn, token)
    if booking["status"] != "PAYMENT_REQUIRED":
        raise core.ConflictError("payment can start only after the final quotation is accepted")
    amount = float(booking.get("quote_amount") or 0)
    if amount <= 0:
        raise core.ConflictError("a final positive quotation amount is required")
    key = str(channel_key or "").lower()
    availability = available_channels(conn)
    if key not in {item["key"] for item in availability["channels"]}:
        raise core.ForbiddenError("payment channel is not enabled and certified")
    idem = str(idempotency_key or "").strip()
    if not idem or len(idem) > 160:
        raise core.ValidationError("a valid idempotency key is required")
    request_hash = _hash({"booking": booking["id"], "channel": key, "amount": amount, "currency": "PHP"})
    existing = conn.execute(
        "SELECT * FROM gateway_payment_transactions WHERE booking_id=? AND idempotency_key=?",
        (booking["id"], idem),
    ).fetchone()
    if existing:
        existing = dict(existing)
        if existing["request_hash"] != request_hash:
            raise core.ConflictError("idempotency key was reused with different payment details")
        result = _public_transaction(existing)
        result["idempotent"] = True
        return result

    cfg = gateway_config()
    reference = ("LH-%s-%s" % (booking["id"], secrets.token_hex(6)))[:64]
    created = _now()
    cur = conn.execute(
        "INSERT INTO gateway_payment_transactions(tenant_id,booking_id,provider,environment,channel_key,amount,currency,reference_id,status,idempotency_key,request_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,'PHP',?,'PROCESSING',?,?,?,?)",
        (booking.get("tenant_id"), booking["id"], PROVIDER, cfg["environment"], key, amount,
         reference, idem, request_hash, created, created),
    )
    tid = cur.lastrowid
    conn.commit()  # durable INITIATING/PROCESSING record before the external request

    payload = {
        "reference_id": reference, "session_type": "PAY", "mode": "PAYMENT_LINK",
        "amount": amount, "currency": "PHP", "country": "PH", "locale": "en",
        "capture_method": "AUTOMATIC", "allow_save_payment_method": "DISABLED",
        "allowed_payment_channels": CHANNELS[key]["provider_codes"],
        "expires_at": _future(30), "description": "LiftHaul booking " + reference,
        "success_return_url": cfg["return_base_url"] + "/track.html?payment=return",
        "cancel_return_url": cfg["return_base_url"] + "/track.html?payment=cancelled",
        "metadata": {"booking_id": str(booking["id"]), "lifthaul_reference": reference},
    }
    try:
        response = (client or XenditClient.from_env()).create_session(payload)
        session_id = str(response.get("payment_session_id") or "")
        if not session_id.startswith("ps-") or response.get("reference_id") != reference:
            raise core.ConflictError("provider session response did not match the request")
        checkout = _safe_checkout_url(response.get("payment_link_url"))
        expires = response.get("expires_at") or payload["expires_at"]
        conn.execute(
            "UPDATE gateway_payment_transactions SET provider_session_id=?,checkout_url=?,provider_status=?,status='PENDING',expires_at=?,updated_at=? WHERE id=?",
            (session_id, checkout, response.get("status") or "ACTIVE", expires, _now(), tid),
        )
        conn.execute("UPDATE mkt_bookings SET payment_status='PENDING',updated_at=? WHERE id=?", (_now(), booking["id"]))
        conn.commit()
        return _public_transaction(_row(conn, tid))
    except Exception as exc:
        conn.execute(
            "UPDATE gateway_payment_transactions SET status='FAILED',failure_code='PROVIDER_CREATE_FAILED',review_reason=?,updated_at=? WHERE id=?",
            (str(exc)[:300], _now(), tid),
        )
        conn.commit()
        raise


def accept_final_quote(conn, token, idempotency_key=None):
    """Customer acceptance using the opaque booking token.

    Acceptance changes only quotation/payment eligibility.  It never creates a transaction and never
    marks anything paid.  Replays are harmless.
    """
    booking = _booking_by_token(conn, token)
    if booking["status"] == "PAYMENT_REQUIRED":
        return {"booking_status": "PAYMENT_REQUIRED", "payment_status": "PENDING", "idempotent": True}
    if booking["status"] != "QUOTED" or float(booking.get("quote_amount") or 0) <= 0:
        raise core.ConflictError("only a final priced quotation can be accepted")
    conn.execute(
        "UPDATE mkt_bookings SET status='PAYMENT_REQUIRED',quotation_status='ACCEPTED',payment_status='PENDING',updated_at=? WHERE id=?",
        (_now(), booking["id"]),
    )
    try:
        import public_booking
        core.audit(conn, public_booking._service_actor(), "PUBLIC_QUOTATION_ACCEPTED", "mkt_bookings",
                   booking["id"], {"status": "QUOTED"},
                   {"status": "PAYMENT_REQUIRED", "idempotency_key_hash": _hash(idempotency_key or "")})
    except Exception:
        pass
    conn.commit()
    return {"booking_status": "PAYMENT_REQUIRED", "payment_status": "PENDING", "idempotent": False}


def latest_status(conn, token):
    booking = _booking_by_token(conn, token)
    row = conn.execute(
        "SELECT * FROM gateway_payment_transactions WHERE booking_id=? ORDER BY id DESC LIMIT 1",
        (booking["id"],),
    ).fetchone()
    return _public_transaction(dict(row)) if row else {
        "status": "PENDING", "transaction_id": None,
        "message": "No payment transaction has been created.",
    }


def _provider_state(conn, row, client):
    session = client.get_session(row["provider_session_id"])
    if session.get("reference_id") != row["reference_id"]:
        raise core.ConflictError("provider session reference mismatch")
    session_amount = float(session.get("amount") or 0)
    if abs(session_amount - float(row["amount"])) > 0.001 or session.get("currency") != row["currency"]:
        raise core.ConflictError("provider session amount or currency mismatch")
    payment_id = session.get("payment_id")
    payment = client.get_payment(payment_id) if payment_id else None
    return session, payment


def _apply_verified_state(conn, row, session, payment):
    session_status = str(session.get("status") or "").upper()
    payment_status = str((payment or {}).get("status") or "").upper()
    update = {
        "provider_status": payment_status or session_status,
        "provider_payment_request_id": session.get("payment_request_id"),
        "provider_payment_id": session.get("payment_id"),
        "api_verified_at": _now(),
    }
    if payment:
        expected = (row["reference_id"], float(row["amount"]), row["currency"])
        actual = (payment.get("reference_id"), float(payment.get("request_amount") or 0), payment.get("currency"))
        if expected != actual:
            update.update(status="UNDER_REVIEW", review_reason="provider payment did not match reference, amount, or currency")
        elif payment_status == "SUCCEEDED":
            update.update(status="PAID", verification_method="PROVIDER_API", paid_at=_now())
        elif payment_status in {"FAILED"}:
            update.update(status="FAILED", failure_code=payment.get("failure_code"))
        elif payment_status in {"CANCELED", "CANCELLED"}:
            update.update(status="CANCELLED")
        elif payment_status == "EXPIRED":
            update.update(status="EXPIRED")
        else:
            update.update(status="PROCESSING")
    elif session_status == "EXPIRED":
        update.update(status="EXPIRED")
    elif session_status in {"CANCELED", "CANCELLED"}:
        update.update(status="CANCELLED")
    elif session_status == "COMPLETED":
        update.update(status="UNDER_REVIEW", review_reason="completed session has no verifiable payment record")
    else:
        update.update(status="PENDING")
    sets = ",".join(k + "=?" for k in update)
    conn.execute("UPDATE gateway_payment_transactions SET " + sets + ",updated_at=? WHERE id=?",
                 (*update.values(), _now(), row["id"]))
    final = _row(conn, row["id"])
    if final["status"] == "PAID":
        conn.execute(
            "UPDATE mkt_bookings SET payment_status='PAID',status=CASE WHEN status='PAYMENT_REQUIRED' THEN 'PAYMENT_CONFIRMED' ELSE status END,updated_at=? WHERE id=?",
            (_now(), row["booking_id"]),
        )
    else:
        conn.execute("UPDATE mkt_bookings SET payment_status=?,updated_at=? WHERE id=?",
                     (final["status"], _now(), row["booking_id"]))
    conn.commit()
    return final


def refresh_transaction(conn, transaction_id, client=None):
    row = _row(conn, transaction_id)
    if not row.get("provider_session_id"):
        return _public_transaction(row)
    try:
        session, payment = _provider_state(conn, row, client or XenditClient.from_env())
        return _public_transaction(_apply_verified_state(conn, row, session, payment))
    except core.ConflictError as exc:
        conn.execute(
            "UPDATE gateway_payment_transactions SET status='UNDER_REVIEW',review_reason=?,api_verified_at=?,updated_at=? WHERE id=?",
            (str(exc)[:300], _now(), _now(), row["id"]),
        )
        conn.execute("UPDATE mkt_bookings SET payment_status='UNDER_REVIEW',updated_at=? WHERE id=?",
                     (_now(), row["booking_id"]))
        conn.commit()
        return _public_transaction(_row(conn, row["id"]))


def _webhook_event_key(payload):
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    identity = data.get("payment_id") or data.get("payment_session_id") or data.get("id") or data.get("payment_request_id")
    return _hash({"event": payload.get("event"), "created": payload.get("created"), "identity": identity})


def process_webhook(conn, callback_token, payload, client=None):
    expected = gateway_config()["webhook_token"]
    if not expected or not hmac.compare_digest(str(callback_token or ""), expected):
        raise core.ForbiddenError("invalid payment webhook signature")
    if not isinstance(payload, dict):
        raise core.ValidationError("invalid webhook payload")
    event_key = _webhook_event_key(payload)
    existing = conn.execute(
        "SELECT id,transaction_id,processing_status FROM gateway_webhook_events WHERE provider=? AND event_key=?",
        (PROVIDER, event_key),
    ).fetchone()
    if existing:
        return {"accepted": True, "idempotent": True, "event_id": existing["id"],
                "transaction_id": existing["transaction_id"], "status": existing["processing_status"]}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    event_type = str(payload.get("event") or "")[:120]
    cur = conn.execute(
        "INSERT INTO gateway_webhook_events(provider,event_key,event_type,payload_hash,signature_verified,processing_status,received_at) VALUES(?,?,?,?,1,'VERIFIED',?)",
        (PROVIDER, event_key, event_type, _hash(payload), _now()),
    )
    event_id = cur.lastrowid
    if event_type in {"refund.succeeded", "refund.failed"}:
        refund_ref = data.get("id") or data.get("refund_id")
        request_id = data.get("payment_request_id")
        refund = conn.execute(
            "SELECT r.*,t.booking_id,t.amount AS transaction_amount,t.refunded_amount AS prior_refunded "
            "FROM gateway_refunds r JOIN gateway_payment_transactions t ON t.id=r.transaction_id "
            "WHERE r.provider_refund_id=? OR (r.reference_id=? AND ? IS NOT NULL) ORDER BY r.id DESC LIMIT 1",
            (refund_ref, data.get("reference_id"), data.get("reference_id")),
        ).fetchone()
        if not refund:
            conn.execute(
                "UPDATE gateway_webhook_events SET processing_status='UNMATCHED',safe_error='refund not found',processed_at=? WHERE id=?",
                (_now(), event_id),
            )
            conn.commit()
            return {"accepted": True, "event_id": event_id, "status": "UNMATCHED"}
        refund = dict(refund)
        if request_id:
            tx = _row(conn, refund["transaction_id"])
            if tx.get("provider_payment_request_id") != request_id:
                conn.execute(
                    "UPDATE gateway_webhook_events SET transaction_id=?,processing_status='REVIEW_REQUIRED',safe_error='refund payment request mismatch',processed_at=? WHERE id=?",
                    (refund["transaction_id"], _now(), event_id),
                )
                conn.commit()
                return {"accepted": True, "event_id": event_id, "status": "REVIEW_REQUIRED"}
        if event_type == "refund.failed" or str(data.get("status") or "").upper() == "FAILED":
            conn.execute("UPDATE gateway_refunds SET status='FAILED',updated_at=? WHERE id=?", (_now(), refund["id"]))
        else:
            new_refunded = round(float(refund.get("prior_refunded") or 0) + float(refund["amount"]), 2)
            tx_status = "REFUNDED" if new_refunded >= float(refund["transaction_amount"]) else "PARTIALLY_REFUNDED"
            conn.execute("UPDATE gateway_refunds SET status='SUCCEEDED',updated_at=? WHERE id=?", (_now(), refund["id"]))
            conn.execute(
                "UPDATE gateway_payment_transactions SET refunded_amount=?,status=?,updated_at=? WHERE id=?",
                (new_refunded, tx_status, _now(), refund["transaction_id"]),
            )
            conn.execute("UPDATE mkt_bookings SET payment_status=?,updated_at=? WHERE id=?",
                         (tx_status, _now(), refund["booking_id"]))
        conn.execute(
            "UPDATE gateway_webhook_events SET transaction_id=?,processing_status='PROCESSED',processed_at=? WHERE id=?",
            (refund["transaction_id"], _now(), event_id),
        )
        conn.commit()
        return {"accepted": True, "event_id": event_id, "transaction_id": refund["transaction_id"],
                "refund_status": "FAILED" if event_type == "refund.failed" else "SUCCEEDED"}
    session_id = data.get("payment_session_id")
    payment_id = data.get("payment_id")
    request_id = data.get("payment_request_id")
    reference = data.get("reference_id")
    clauses, params = [], []
    for column, value in (("provider_session_id", session_id), ("provider_payment_id", payment_id),
                          ("provider_payment_request_id", request_id), ("reference_id", reference)):
        if value:
            clauses.append(column + "=?"); params.append(value)
    row = conn.execute(
        "SELECT * FROM gateway_payment_transactions WHERE " + " OR ".join(clauses) + " ORDER BY id DESC LIMIT 1",
        params,
    ).fetchone() if clauses else None
    if not row:
        conn.execute(
            "UPDATE gateway_webhook_events SET processing_status='UNMATCHED',safe_error='transaction not found',processed_at=? WHERE id=?",
            (_now(), event_id),
        )
        conn.commit()
        return {"accepted": True, "event_id": event_id, "status": "UNMATCHED"}
    row = dict(row)
    conn.execute(
        "UPDATE gateway_payment_transactions SET webhook_verified_at=?,provider_payment_id=COALESCE(provider_payment_id,?),provider_payment_request_id=COALESCE(provider_payment_request_id,?),updated_at=? WHERE id=?",
        (_now(), payment_id, request_id, _now(), row["id"]),
    )
    conn.commit()
    final = refresh_transaction(conn, row["id"], client=client)
    conn.execute(
        "UPDATE gateway_webhook_events SET transaction_id=?,processing_status='PROCESSED',processed_at=? WHERE id=?",
        (row["id"], _now(), event_id),
    )
    conn.commit()
    return {"accepted": True, "event_id": event_id, "transaction_id": row["id"], "payment": final}


def request_refund(conn, actor, transaction_id, amount, reason, idempotency_key, client=None):
    core.require(actor, "marketplace.refund.request")
    row = _row(conn, transaction_id)
    tenant.guard(actor, row)
    if row["status"] not in {"PAID", "PARTIALLY_REFUNDED"}:
        raise core.ConflictError("only a confirmed payment can be refunded")
    meta = CHANNELS[row["channel_key"]]
    amount = round(float(amount), 2)
    remaining = round(float(row["amount"]) - float(row.get("refunded_amount") or 0), 2)
    if amount <= 0 or amount > remaining:
        raise core.ValidationError("refund amount exceeds the refundable balance")
    if amount < remaining and not meta["partial_refund"]:
        raise core.ConflictError("this payment channel does not support partial refunds")
    if not meta["refund"]:
        raise core.ConflictError("this payment channel requires an exceptional manual refund workflow")
    idem = str(idempotency_key or "").strip()
    if not idem:
        raise core.ValidationError("refund idempotency key is required")
    existing = conn.execute(
        "SELECT * FROM gateway_refunds WHERE transaction_id=? AND idempotency_key=?",
        (transaction_id, idem),
    ).fetchone()
    if existing:
        return {"refund_id": existing["id"], "status": existing["status"], "idempotent": True}
    reference = ("LHRF-%s-%s" % (transaction_id, secrets.token_hex(5)))[:64]
    cur = conn.execute(
        "INSERT INTO gateway_refunds(transaction_id,provider,reference_id,amount,currency,reason,status,idempotency_key,requested_by,created_at,updated_at) VALUES(?,?,?,?,'PHP',?,'PENDING',?,?,?,?)",
        (transaction_id, PROVIDER, reference, amount, str(reason or "OTHERS")[:80], idem, actor["id"], _now(), _now()),
    )
    refund_id = cur.lastrowid
    conn.commit()
    try:
        response = (client or XenditClient.from_env()).create_refund({
            "reference_id": reference, "payment_request_id": row["provider_payment_request_id"],
            "currency": "PHP", "amount": amount, "reason": str(reason or "OTHERS").upper(),
            "metadata": {"booking_id": str(row["booking_id"]), "transaction_id": str(transaction_id)},
        })
    except Exception as exc:
        conn.execute("UPDATE gateway_refunds SET status='UNDER_REVIEW',updated_at=? WHERE id=?",
                     (_now(), refund_id))
        core.audit(conn, actor, "GATEWAY_REFUND_PROVIDER_UNCERTAIN", "gateway_refunds", refund_id,
                   None, {"transaction_id": transaction_id, "safe_error": str(exc)[:160]})
        conn.commit()
        raise
    provider_ref = response.get("id")
    conn.execute("UPDATE gateway_refunds SET provider_refund_id=?,status=?,updated_at=? WHERE id=?",
                 (provider_ref, str(response.get("status") or "PENDING").upper(), _now(), refund_id))
    core.audit(conn, actor, "GATEWAY_REFUND_REQUESTED", "gateway_refunds", refund_id, None,
               {"transaction_id": transaction_id, "amount": amount, "provider": PROVIDER})
    conn.commit()
    return {"refund_id": refund_id, "provider_refund_id": provider_ref, "status": "PENDING"}


def open_manual_review(conn, actor, booking_id, amount, reason, bank_reference=None, supporting_document=None):
    core.require(actor, "marketplace.payment.reconcile")
    booking = conn.execute("SELECT id,tenant_id,status,quote_amount FROM mkt_bookings WHERE id=?", (booking_id,)).fetchone()
    if not booking:
        raise core.NotFoundError("booking not found")
    tenant.guard(actor, booking)
    if booking["status"] != "PAYMENT_REQUIRED":
        raise core.ConflictError("manual verification is available only after quotation acceptance")
    amount = round(float(amount), 2)
    if amount <= 0 or not str(reason or "").strip():
        raise core.ValidationError("manual review requires a positive amount and reason")
    if booking["quote_amount"] is not None and abs(amount - float(booking["quote_amount"])) > 0.001:
        raise core.ValidationError("manual review amount must match the final booking amount")
    cur = conn.execute(
        "INSERT INTO gateway_manual_reviews(tenant_id,booking_id,amount,currency,status,bank_reference,supporting_document,reason,opened_by,opened_at) VALUES(?,?,?,'PHP','UNDER_REVIEW',?,?,?,?,?)",
        (booking["tenant_id"], booking_id, amount, str(bank_reference or "")[:200], str(supporting_document or "")[:500],
         str(reason)[:1000], actor["id"], _now()),
    )
    review_id = cur.lastrowid
    conn.execute("UPDATE mkt_bookings SET payment_status='UNDER_REVIEW',updated_at=? WHERE id=?", (_now(), booking_id))
    core.audit(conn, actor, "MANUAL_PAYMENT_REVIEW_OPENED", "gateway_manual_reviews", review_id, None,
               {"booking_id": booking_id, "amount": amount, "screenshot_is_proof": False})
    conn.commit()
    return {"review_id": review_id, "status": "UNDER_REVIEW"}


def approve_manual_review(conn, actor, review_id, official_record_reference, reason):
    core.require(actor, "marketplace.payment.verify")
    row = conn.execute("SELECT * FROM gateway_manual_reviews WHERE id=?", (review_id,)).fetchone()
    if not row:
        raise core.NotFoundError("manual payment review not found")
    row = dict(row)
    tenant.guard(actor, row)
    if row["status"] != "UNDER_REVIEW":
        raise core.ConflictError("manual review is not awaiting approval")
    if row["opened_by"] == actor["id"]:
        raise core.ForbiddenError("separation of duties: the reviewer cannot approve their own case")
    if not str(official_record_reference or "").strip() or not str(reason or "").strip():
        raise core.ValidationError("official bank/provider record and approval reason are required")
    conn.execute(
        "UPDATE gateway_manual_reviews SET status='PAID',bank_reference=?,approved_by=?,approved_at=?,audit_note=? WHERE id=?",
        (str(official_record_reference)[:200], actor["id"], _now(), str(reason)[:1000], review_id),
    )
    conn.execute(
        "UPDATE mkt_bookings SET payment_status='PAID',status=CASE WHEN status='PAYMENT_REQUIRED' THEN 'PAYMENT_CONFIRMED' ELSE status END,updated_at=? WHERE id=?",
        (_now(), row["booking_id"]),
    )
    core.audit(conn, actor, "MANUAL_PAYMENT_VERIFIED", "gateway_manual_reviews", review_id,
               {"status": "UNDER_REVIEW"}, {"status": "PAID", "verification": "OFFICIAL_RECORD"})
    conn.commit()
    return {"review_id": review_id, "status": "PAID", "verification_method": "MANUAL_OFFICIAL_RECORD"}


def reconcile_daily(conn, actor, client=None, run_key=None):
    core.require(actor, "marketplace.payment.reconcile")
    cfg = gateway_config()
    if run_key:
        prior = conn.execute(
            "SELECT id,checked_count,issue_count,status,summary_json FROM gateway_reconciliation_runs WHERE run_key=?",
            (str(run_key),),
        ).fetchone()
        if prior:
            summary = json.loads(prior["summary_json"] or "{}")
            return {"run_id": prior["id"], **summary, "status": prior["status"], "idempotent": True}
    cur = conn.execute(
        "INSERT INTO gateway_reconciliation_runs(provider,environment,run_key,started_at,status) VALUES(?,?,?,?,'RUNNING')",
        (PROVIDER, cfg["environment"], str(run_key) if run_key else None, _now()),
    )
    run_id = cur.lastrowid
    scope, scope_params = tenant.predicate(actor)
    rows = conn.execute(
        "SELECT * FROM gateway_payment_transactions WHERE provider=? AND status NOT IN('REFUNDED','FAILED','EXPIRED','CANCELLED')" + scope + " ORDER BY id",
        (PROVIDER, *scope_params),
    ).fetchall()
    issues = []
    for raw in rows:
        before = dict(raw)
        try:
            refresh_transaction(conn, before["id"], client=client)
            after = _row(conn, before["id"])
            if after["status"] == "UNDER_REVIEW":
                issues.append((before["id"], "PAYMENT_MISMATCH", before, after))
        except Exception as exc:
            issues.append((before["id"], "PROVIDER_QUERY_FAILED", {"status": before["status"]}, {"error": str(exc)[:200]}))
    for tid, issue_type, expected, actual in issues:
        conn.execute(
            "INSERT INTO gateway_reconciliation_issues(run_id,transaction_id,issue_type,expected_json,actual_json,status,created_at) VALUES(?,?,?,?,?,'OPEN',?)",
            (run_id, tid, issue_type, json.dumps(expected, default=str), json.dumps(actual, default=str), _now()),
        )
    summary = {"checked": len(rows), "issues": len(issues), "provider": PROVIDER,
               "environment": cfg["environment"]}
    conn.execute(
        "UPDATE gateway_reconciliation_runs SET completed_at=?,checked_count=?,issue_count=?,status=?,summary_json=? WHERE id=?",
        (_now(), len(rows), len(issues), "REVIEW_REQUIRED" if issues else "COMPLETED", json.dumps(summary), run_id),
    )
    core.audit(conn, actor, "PAYMENT_RECONCILIATION_COMPLETED", "gateway_reconciliation_runs", run_id,
               None, summary)
    conn.commit()
    return {"run_id": run_id, **summary, "status": "REVIEW_REQUIRED" if issues else "COMPLETED"}


def reconcile_automatic(conn, day=None, client=None):
    """Run the provider reconciliation once per UTC day across the payment ledger."""
    target_day = day or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    actor = {
        "id": 0, "role": "system", "name": "Payment Reconciliation Worker",
        "tenant_id": None, "perms": {"marketplace.payment.reconcile"},
    }
    key = "PAYMENT-RECONCILIATION:%s:%s:%s" % (PROVIDER, gateway_config()["environment"], target_day)
    return reconcile_daily(conn, actor, client=client, run_key=key)
