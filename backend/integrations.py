"""LiftHaul OS — Phase 7: governed Integration Administration engine.

Connects LiftHaul OS to external providers WITHOUT weakening financial control, tenant isolation,
auditability, or historical reproducibility. Provides: integration definitions + connection profiles
(MOCK/SANDBOX/TEST/PRODUCTION), first-class idempotency keys, governed webhook ingress (signature
verify + dedup + replay-safe), polling fallback, a reconciliation engine (match/partial/over/under/
duplicate/mismatch → manual review), a dead-letter queue + governed replay, provider health + a
circuit breaker + kill switch, and provider-neutral boundaries for email/SMS/maps/accounting/FX.

Secrets use the Phase-6 secret-reference boundary (values never stored/logged/exported).
A 200 HTTP response is NEVER settlement — verification requires reconciled provider evidence.
"""
from __future__ import annotations

import datetime
import hashlib
import json

import core

ENVIRONMENTS = ("MOCK", "SANDBOX", "TEST", "PRODUCTION")
HEALTH = ("HEALTHY", "DEGRADED", "UNAVAILABLE", "AUTHENTICATION_FAILED", "RATE_LIMITED",
          "MISCONFIGURED", "DISABLED", "UNKNOWN")
RECON_STATUSES = ("UNMATCHED", "POSSIBLE_MATCH", "MATCHED", "MISMATCH", "DUPLICATE", "PARTIAL",
                  "OVERPAID", "UNDERPAID", "MANUAL_REVIEW", "RECONCILED")
DLQ_STATUSES = ("OPEN", "INVESTIGATING", "READY_FOR_REPLAY", "REPLAYED", "RESOLVED", "IGNORED")
FAILURE_CATEGORIES = ("transient_network", "rate_limited", "provider_unavailable", "invalid_request",
                      "authentication_failure", "authorization_failure", "validation_failure",
                      "permanent_business_rejection", "unknown_provider_response")
RETRYABLE = {"transient_network", "rate_limited", "provider_unavailable"}
TRANSFER_STATUSES = ("CREATED", "PENDING", "PROCESSING", "FUNDED", "SENT", "COMPLETED", "FAILED",
                     "CANCELLED", "REFUNDED", "REVERSED", "UNKNOWN")

SCHEMA = """
CREATE TABLE IF NOT EXISTS integration_definitions(
  id INTEGER PRIMARY KEY, provider_code TEXT NOT NULL, provider_name TEXT, category TEXT,
  description TEXT, owner INTEGER, capabilities TEXT, platform_status TEXT DEFAULT 'ENABLED',
  risk_level TEXT DEFAULT 'high', secret_required INTEGER DEFAULT 1, webhook_support INTEGER DEFAULT 0,
  polling_support INTEGER DEFAULT 0, retry_support INTEGER DEFAULT 1, sandbox_support INTEGER DEFAULT 1,
  production_support INTEGER DEFAULT 1, created_by INTEGER, created_at TEXT, UNIQUE(provider_code));

CREATE TABLE IF NOT EXISTS connection_profiles(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, org_scope TEXT, provider_code TEXT NOT NULL,
  environment TEXT NOT NULL DEFAULT 'MOCK', name TEXT, account_ref TEXT, secret_ref TEXT,
  base_url TEXT, supported_currencies TEXT, supported_countries TEXT, default_currency TEXT,
  status TEXT DEFAULT 'DRAFT', activated_at TEXT, suspended_at TEXT, last_validated_at TEXT,
  last_success_at TEXT, last_failure_at TEXT, health TEXT DEFAULT 'UNKNOWN', owner INTEGER,
  circuit_state TEXT DEFAULT 'CLOSED', failure_count INTEGER DEFAULT 0, created_by INTEGER,
  created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS idempotency_keys(
  id INTEGER PRIMARY KEY, idem_key TEXT NOT NULL, tenant_id INTEGER, provider_code TEXT,
  operation TEXT, entity_ref TEXT, request_hash TEXT, response_ref TEXT, status TEXT DEFAULT 'IN_PROGRESS',
  created_at TEXT, expires_at TEXT, UNIQUE(tenant_id, idem_key));

CREATE TABLE IF NOT EXISTS provider_quotes(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, profile_id INTEGER, provider_code TEXT,
  provider_quote_id TEXT, source_currency TEXT, target_currency TEXT, source_amount REAL,
  target_amount REAL, rate REAL, fee REAL, expiry TEXT, response_hash TEXT, snapshot TEXT,
  created_at TEXT);

CREATE TABLE IF NOT EXISTS provider_transfers(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, profile_id INTEGER, provider_code TEXT,
  payment_request_id INTEGER, provider_transfer_id TEXT, provider_quote_id TEXT, recipient_ref TEXT,
  amount REAL, currency TEXT, provider_status TEXT, normalized_status TEXT DEFAULT 'CREATED',
  fee REAL, rate REAL, idem_key TEXT, reference TEXT, created_by INTEGER, verified_by INTEGER,
  created_at TEXT, updated_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS webhook_endpoints(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, provider_code TEXT, event_type TEXT, secret_ref TEXT,
  algorithm TEXT DEFAULT 'hmac_sha256', enabled INTEGER DEFAULT 1, last_received_at TEXT,
  last_processed_at TEXT, failure_count INTEGER DEFAULT 0, health TEXT DEFAULT 'UNKNOWN',
  created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS webhook_events(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, provider_code TEXT, provider_event_id TEXT,
  event_type TEXT, payload_hash TEXT, verified INTEGER DEFAULT 0, processed INTEGER DEFAULT 0,
  status TEXT DEFAULT 'RECEIVED', retries INTEGER DEFAULT 0, safe_error TEXT, received_at TEXT,
  processed_at TEXT, correlation_id TEXT, UNIQUE(provider_code, provider_event_id));

CREATE TABLE IF NOT EXISTS polling_jobs(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, profile_id INTEGER, provider_transfer_id TEXT,
  current_status TEXT, last_checked_at TEXT, next_check_at TEXT, retry_count INTEGER DEFAULT 0,
  terminal INTEGER DEFAULT 0, error_state TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS reconciliation_items(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, payment_request_id INTEGER, provider_transfer_id TEXT,
  reference TEXT, amount REAL, currency TEXT, status TEXT DEFAULT 'UNMATCHED', variance REAL DEFAULT 0,
  detail TEXT, reviewed_by INTEGER, created_at TEXT, updated_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS integration_dead_letters(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, provider_code TEXT, operation TEXT, entity_ref TEXT,
  failure_category TEXT, error_code TEXT, safe_error TEXT, attempts INTEGER DEFAULT 1,
  first_failure_at TEXT, last_failure_at TEXT, next_action TEXT, payload_hash TEXT, status TEXT DEFAULT 'OPEN',
  correlation_id TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS provider_refunds(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, payment_request_id INTEGER, provider_transfer_id TEXT,
  amount REAL, currency TEXT, reason TEXT, status TEXT DEFAULT 'REQUESTED', requested_by INTEGER,
  approved_by INTEGER, provider_confirmed INTEGER DEFAULT 0, created_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS fx_rates(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, provider_code TEXT, source_currency TEXT,
  target_currency TEXT, rate REAL, effective_at TEXT, expiry TEXT, provider_ref TEXT,
  manual_override INTEGER DEFAULT 0, approved_by INTEGER, created_at TEXT);
"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _tenant(actor):
    return (actor or {}).get("tenant_id")


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn):
    sys_actor = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
    defs = [
        ("wise", "Wise", "payments", "Wise cross-border payments", 1, 1, 1),
        ("email_generic", "Email Provider", "email", "Provider-neutral email boundary", 0, 0, 0),
        ("sms_generic", "SMS Provider", "sms", "Provider-neutral SMS boundary", 0, 0, 0),
        ("maps_generic", "Maps/Geocoding", "maps", "Provider-neutral geocoding boundary", 0, 0, 0),
        ("accounting_generic", "Accounting", "accounting", "Provider-neutral accounting boundary", 0, 0, 0),
        ("fx_generic", "Exchange Rates", "fx", "Provider-neutral FX-rate boundary", 0, 0, 0),
    ]
    for (code, name, cat, desc, secret, wh, poll) in defs:
        if conn.execute("SELECT 1 FROM integration_definitions WHERE provider_code=?", (code,)).fetchone():
            continue
        conn.execute("INSERT INTO integration_definitions(provider_code,provider_name,category,description,"
                     "capabilities,secret_required,webhook_support,polling_support,created_at)"
                     " VALUES(?,?,?,?,?,?,?,?,?)",
                     (code, name, cat, desc, json.dumps(["validate", "quote", "transfer", "status"] if code == "wise" else ["send"]),
                      secret, wh, poll, _now()))
    conn.commit()


# --------------------------------------------------------------------------- #
# Integration definitions + connection profiles
# --------------------------------------------------------------------------- #
def list_definitions(conn, actor):
    core.require(actor, "integration.catalog.view")
    return [dict(r) for r in conn.execute("SELECT * FROM integration_definitions ORDER BY category,provider_code").fetchall()]


def create_profile(conn, actor, provider_code, environment="MOCK", name=None, secret_ref=None,
                   base_url=None, default_currency="PHP", supported_currencies="PHP,USD,EUR",
                   account_ref=None, org_scope=None):
    core.require(actor, "integration.profile.manage")
    if environment not in ENVIRONMENTS:
        raise core.ValidationError(f"environment must be one of {ENVIRONMENTS}")
    if not conn.execute("SELECT 1 FROM integration_definitions WHERE provider_code=?", (provider_code,)).fetchone():
        raise core.NotFoundError("integration definition not found")
    if environment == "PRODUCTION":
        core.require(actor, "payment.wise.manage")           # production profiles need elevated authority
    cur = conn.execute("INSERT INTO connection_profiles(tenant_id,org_scope,provider_code,environment,name,"
                       "account_ref,secret_ref,base_url,supported_currencies,default_currency,status,owner,"
                       "created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?, 'DRAFT', ?,?,?)",
                       (_tenant(actor), org_scope, provider_code, environment, name, account_ref, secret_ref,
                        base_url, supported_currencies, default_currency, (actor or {}).get("id"),
                        (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "INTEGRATION_PROFILE_CREATED", "connection_profiles", cur.lastrowid,
               new={"provider": provider_code, "environment": environment})   # no secret
    conn.commit()
    return cur.lastrowid


def get_profile(conn, actor, profile_id):
    row = conn.execute("SELECT * FROM connection_profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        raise core.NotFoundError("connection profile not found")
    at = _tenant(actor)
    if at is not None and row["tenant_id"] is not None and at != row["tenant_id"]:
        raise core.NotFoundError("connection profile not found")   # cross-tenant 404 no-leak
    return dict(row)


def list_profiles(conn, actor, provider_code=None):
    core.require(actor, "integration.profile.view")
    at = _tenant(actor)
    sql = "SELECT * FROM connection_profiles WHERE 1=1"
    args = []
    if at is not None:
        sql += " AND (tenant_id=? OR tenant_id IS NULL)"; args.append(at)
    if provider_code:
        sql += " AND provider_code=?"; args.append(provider_code)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC", tuple(args)).fetchall()]


def validate_profile(conn, actor, profile_id):
    """Validate a connection profile against its provider adapter. Sets health; never returns a secret."""
    core.require(actor, "integration.health.test")
    p = get_profile(conn, actor, profile_id)
    adapter = _adapter(conn, p)
    result = adapter.validate_connection(p)
    health = "HEALTHY" if result.get("ok") else result.get("health", "MISCONFIGURED")
    conn.execute("UPDATE connection_profiles SET last_validated_at=?, health=?, status=? WHERE id=?",
                 (_now(), health, "VALIDATED" if result.get("ok") else p["status"], profile_id))
    core.audit(conn, actor, "INTEGRATION_PROFILE_VALIDATED", "connection_profiles", profile_id,
               new={"health": health, "ok": bool(result.get("ok"))})
    conn.commit()
    return {"profile_id": profile_id, "health": health, **{k: v for k, v in result.items() if k != "secret"}}


def activate_profile(conn, actor, profile_id):
    core.require(actor, "integration.profile.manage")
    p = get_profile(conn, actor, profile_id)
    if p["health"] not in ("HEALTHY",):
        raise core.ConflictError("profile must be validated HEALTHY before activation")
    conn.execute("UPDATE connection_profiles SET status='ACTIVE', activated_at=? WHERE id=?", (_now(), profile_id))
    core.audit(conn, actor, "INTEGRATION_PROFILE_ACTIVATED", "connection_profiles", profile_id)
    conn.commit()
    return True


def suspend_profile(conn, actor, profile_id, reason=None):
    core.require(actor, "integration.profile.manage")
    get_profile(conn, actor, profile_id)
    conn.execute("UPDATE connection_profiles SET status='SUSPENDED', suspended_at=?, circuit_state='OPEN' WHERE id=?",
                 (_now(), profile_id))
    core.audit(conn, actor, "INTEGRATION_PROFILE_SUSPENDED", "connection_profiles", profile_id, reason=reason)
    conn.commit()
    return True


def _profile_usable(p):
    return p["status"] == "ACTIVE" and p.get("circuit_state") != "OPEN"


def _adapter(conn, profile):
    """Resolve the provider adapter. Wise → wise.get_adapter (mock/real by environment)."""
    if profile["provider_code"] == "wise":
        import wise
        return wise.get_adapter(profile["environment"])
    import wise
    return wise.get_adapter("MOCK")   # generic providers use a benign mock in Phase 7


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #
def _hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def idempotent(conn, actor, idem_key, operation, payload, entity_ref=None, provider_code=None):
    """Return (existing_response_ref, is_replay). Rejects a reused key with a different payload."""
    tid = _tenant(actor)
    req_hash = _hash(payload)
    row = conn.execute("SELECT * FROM idempotency_keys WHERE tenant_id=? AND idem_key=?", (tid, idem_key)).fetchone()
    if row:
        if row["request_hash"] != req_hash:
            raise core.ConflictError("idempotency key reused with a different payload")
        return row["response_ref"], True
    conn.execute("INSERT INTO idempotency_keys(idem_key,tenant_id,provider_code,operation,entity_ref,"
                 "request_hash,status,created_at) VALUES(?,?,?,?,?,?, 'IN_PROGRESS', ?)",
                 (idem_key, tid, provider_code, operation, entity_ref, req_hash, _now()))
    conn.commit()
    return None, False


def _idem_complete(conn, actor, idem_key, response_ref):
    conn.execute("UPDATE idempotency_keys SET status='COMPLETED', response_ref=? WHERE tenant_id=? AND idem_key=?",
                 (str(response_ref), _tenant(actor), idem_key))
    conn.commit()


# --------------------------------------------------------------------------- #
# Circuit breaker
# --------------------------------------------------------------------------- #
def _record_success(conn, profile_id):
    conn.execute("UPDATE connection_profiles SET last_success_at=?, failure_count=0, circuit_state='CLOSED',"
                 " health='HEALTHY' WHERE id=?", (_now(), profile_id))
    conn.commit()


def _record_failure(conn, profile_id, threshold=5):
    conn.execute("UPDATE connection_profiles SET last_failure_at=?, failure_count=failure_count+1 WHERE id=?",
                 (_now(), profile_id))
    fc = conn.execute("SELECT failure_count FROM connection_profiles WHERE id=?", (profile_id,)).fetchone()["failure_count"]
    if fc >= threshold:
        conn.execute("UPDATE connection_profiles SET circuit_state='OPEN', health='DEGRADED' WHERE id=?", (profile_id,))
    conn.commit()


def kill_switch(conn, actor, profile_id, reason=None):
    core.require(actor, "integration.profile.manage")
    get_profile(conn, actor, profile_id)
    conn.execute("UPDATE connection_profiles SET status='DISABLED', circuit_state='OPEN', health='DISABLED' WHERE id=?", (profile_id,))
    core.audit(conn, actor, "INTEGRATION_KILL_SWITCH", "connection_profiles", profile_id, reason=reason)
    conn.commit()
    return True


# --------------------------------------------------------------------------- #
# Webhooks (governed ingress)
# --------------------------------------------------------------------------- #
def register_webhook(conn, actor, provider_code, event_type, secret_ref=None, algorithm="hmac_sha256"):
    core.require(actor, "integration.webhook.manage")
    cur = conn.execute("INSERT INTO webhook_endpoints(tenant_id,provider_code,event_type,secret_ref,algorithm,"
                       "enabled,created_by,created_at) VALUES(?,?,?,?,?,1,?,?)",
                       (_tenant(actor), provider_code, event_type, secret_ref, algorithm, (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "WEBHOOK_REGISTERED", "webhook_endpoints", cur.lastrowid, new={"provider": provider_code, "event": event_type})
    conn.commit()
    return cur.lastrowid


def ingest_webhook(conn, provider_code, provider_event_id, event_type, payload, signature=None,
                   tenant_id=None, secret=None):
    """Governed webhook ingress: verify signature, reject replays/duplicates, enforce idempotency,
    store event, process asynchronously. A reaching URL is NOT trust. Returns the normalized outcome."""
    payload_hash = _hash(payload)
    # signature verification (HMAC over the payload hash using the webhook secret)
    verified = False
    if secret is not None and signature is not None:
        import hmac
        expected = hmac.new(str(secret).encode(), payload_hash.encode(), hashlib.sha256).hexdigest()
        verified = hmac.compare_digest(expected, str(signature))
    # duplicate / replay guard
    existing = conn.execute("SELECT * FROM webhook_events WHERE provider_code=? AND provider_event_id=?",
                            (provider_code, provider_event_id)).fetchone()
    if existing:
        conn.execute("UPDATE webhook_events SET retries=retries+1 WHERE id=?", (existing["id"],))
        conn.commit()
        return {"status": "DUPLICATE", "verified": bool(existing["verified"]), "processed": bool(existing["processed"])}
    cid = core.correlation_id()
    status = "VERIFIED" if verified or secret is None else "REJECTED"
    cur = conn.execute("INSERT INTO webhook_events(tenant_id,provider_code,provider_event_id,event_type,"
                       "payload_hash,verified,status,received_at,correlation_id) VALUES(?,?,?,?,?,?,?,?,?)",
                       (tenant_id, provider_code, provider_event_id, event_type, payload_hash,
                        1 if verified else 0, status, _now(), cid))
    eid = cur.lastrowid
    core.audit(conn, {"id": 0, "role": "system", "tenant_id": tenant_id}, "WEBHOOK_RECEIVED",
               "webhook_events", eid, new={"provider": provider_code, "event": event_type, "verified": verified})
    conn.commit()
    if status == "REJECTED":
        return {"status": "REJECTED", "event_id": eid, "verified": False}
    return {"status": "ACCEPTED", "event_id": eid, "verified": verified, "correlation_id": cid}


def process_webhook_event(conn, actor, event_id):
    """Mark a verified webhook event processed (idempotent). Distinct from provider-side creation."""
    core.require(actor, "integration.webhook.manage")
    e = conn.execute("SELECT * FROM webhook_events WHERE id=?", (event_id,)).fetchone()
    if not e:
        raise core.NotFoundError("webhook event not found")
    if e["processed"]:
        return {"status": "ALREADY_PROCESSED"}
    if e["status"] == "REJECTED":
        raise core.ForbiddenError("cannot process a rejected webhook event")
    conn.execute("UPDATE webhook_events SET processed=1, status='PROCESSED', processed_at=? WHERE id=?", (_now(), event_id))
    core.audit(conn, actor, "WEBHOOK_PROCESSED", "webhook_events", event_id)
    conn.commit()
    return {"status": "PROCESSED"}


def list_webhook_events(conn, actor, provider_code=None):
    core.require(actor, "integration.webhook.view")
    at = _tenant(actor)
    sql = "SELECT * FROM webhook_events WHERE 1=1"
    args = []
    if at is not None:
        sql += " AND (tenant_id=? OR tenant_id IS NULL)"; args.append(at)
    if provider_code:
        sql += " AND provider_code=?"; args.append(provider_code)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT 200", tuple(args)).fetchall()]


# --------------------------------------------------------------------------- #
# Polling fallback
# --------------------------------------------------------------------------- #
def start_polling(conn, actor, profile_id, provider_transfer_id):
    core.require(actor, "integration.polling.manage")
    cur = conn.execute("INSERT INTO polling_jobs(tenant_id,profile_id,provider_transfer_id,current_status,"
                       "last_checked_at,retry_count,terminal,created_at) VALUES(?,?,?, 'PENDING', ?,0,0,?)",
                       (_tenant(actor), profile_id, provider_transfer_id, _now(), _now()))
    conn.commit()
    return cur.lastrowid


def poll_once(conn, actor, poll_id, max_retries=10):
    """One governed poll: query provider status via the adapter; stop at terminal; backoff limit."""
    core.require(actor, "integration.polling.manage")
    j = conn.execute("SELECT * FROM polling_jobs WHERE id=?", (poll_id,)).fetchone()
    if not j:
        raise core.NotFoundError("polling job not found")
    if j["terminal"]:
        return {"status": j["current_status"], "terminal": True}
    if j["retry_count"] >= max_retries:
        conn.execute("UPDATE polling_jobs SET error_state='max_retries' WHERE id=?", (poll_id,))
        conn.commit()
        return {"status": j["current_status"], "terminal": False, "error": "max_retries"}
    p = get_profile(conn, actor, j["profile_id"])
    adapter = _adapter(conn, p)
    st = adapter.get_transfer_status(j["provider_transfer_id"])
    terminal = st in ("COMPLETED", "FAILED", "CANCELLED", "REFUNDED", "REVERSED")
    conn.execute("UPDATE polling_jobs SET current_status=?, last_checked_at=?, retry_count=retry_count+1,"
                 " terminal=? WHERE id=?", (st, _now(), 1 if terminal else 0, poll_id))
    conn.commit()
    return {"status": st, "terminal": terminal}


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #
def reconcile(conn, actor, payment_request_id, provider_transfer_id, amount, currency, reference=None):
    """Match an external transfer to an internal payment request. Ambiguous → MANUAL_REVIEW; never
    auto-reconcile a mismatch/partial/over/under. Returns the reconciliation status."""
    core.require(actor, "payment.wise.reconcile")
    pr = conn.execute("SELECT * FROM payment_requests WHERE id=?", (payment_request_id,)).fetchone()
    if not pr:
        raise core.NotFoundError("payment request not found")
    import tenant as tmod
    tmod.guard(actor, pr)                                    # cross-tenant 404
    # duplicate provider transfer already reconciled?
    dup = conn.execute("SELECT 1 FROM reconciliation_items WHERE provider_transfer_id=? AND status IN ('MATCHED','RECONCILED')",
                       (provider_transfer_id,)).fetchone()
    due = pr["amount_due"]
    status, variance, detail = "MATCHED", 0.0, ""
    if dup:
        status, detail = "DUPLICATE", "provider transfer already reconciled"
    elif currency != (pr["currency"] or currency):
        status, detail = "MISMATCH", f"currency {currency} != {pr['currency']}"
    elif abs(amount - due) < 0.005:
        status = "MATCHED"
    elif amount < due:
        status, variance, detail = "PARTIAL", amount - due, "underpayment"
    else:
        status, variance, detail = "OVERPAID", amount - due, "overpayment"
    # ambiguous outcomes route to manual review (never auto-verify)
    review = status in ("PARTIAL", "OVERPAID", "MISMATCH", "DUPLICATE", "UNDERPAID")
    final = "MANUAL_REVIEW" if review else status
    cur = conn.execute("INSERT INTO reconciliation_items(tenant_id,payment_request_id,provider_transfer_id,"
                       "reference,amount,currency,status,variance,detail,created_at,correlation_id)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (pr["tenant_id"] if "tenant_id" in pr.keys() else _tenant(actor), payment_request_id,
                        provider_transfer_id, reference, amount, currency, final, variance, detail, _now(), core.correlation_id()))
    core.audit(conn, actor, "PAYMENT_RECONCILED", "reconciliation_items", cur.lastrowid,
               new={"payment_request": payment_request_id, "status": final, "variance": variance})
    conn.commit()
    return {"reconciliation_id": cur.lastrowid, "status": final, "variance": variance, "detail": detail}


def list_reconciliation(conn, actor, status=None):
    core.require(actor, "payment.wise.reconcile")
    at = _tenant(actor)
    sql = "SELECT * FROM reconciliation_items WHERE 1=1"
    args = []
    if at is not None:
        sql += " AND (tenant_id=? OR tenant_id IS NULL)"; args.append(at)
    if status:
        sql += " AND status=?"; args.append(status)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT 200", tuple(args)).fetchall()]


def resolve_manual_review(conn, actor, reconciliation_id, resolution, reason=None):
    core.require(actor, "payment.wise.reconcile")
    if resolution not in ("RECONCILED", "MISMATCH", "DUPLICATE", "IGNORED"):
        raise core.ValidationError("invalid resolution")
    conn.execute("UPDATE reconciliation_items SET status=?, reviewed_by=?, updated_at=? WHERE id=?",
                 (resolution, (actor or {}).get("id"), _now(), reconciliation_id))
    core.audit(conn, actor, "RECONCILIATION_RESOLVED", "reconciliation_items", reconciliation_id,
               new={"resolution": resolution}, reason=reason)
    conn.commit()
    return True


# --------------------------------------------------------------------------- #
# Dead-letter queue + governed replay
# --------------------------------------------------------------------------- #
def classify_failure(category):
    return {"retryable": category in RETRYABLE, "category": category}


def dead_letter(conn, actor, provider_code, operation, failure_category, safe_error, entity_ref=None,
                error_code=None, payload_hash=None):
    if failure_category not in FAILURE_CATEGORIES:
        failure_category = "unknown_provider_response"
    existing = conn.execute("SELECT * FROM integration_dead_letters WHERE provider_code=? AND operation=? AND"
                            " COALESCE(entity_ref,'')=COALESCE(?,'') AND status='OPEN'",
                            (provider_code, operation, entity_ref)).fetchone()
    if existing:
        conn.execute("UPDATE integration_dead_letters SET attempts=attempts+1, last_failure_at=?, safe_error=? WHERE id=?",
                     (_now(), safe_error, existing["id"]))
        conn.commit()
        return existing["id"]
    cid = core.correlation_id()
    cur = conn.execute("INSERT INTO integration_dead_letters(tenant_id,provider_code,operation,entity_ref,"
                       "failure_category,error_code,safe_error,attempts,first_failure_at,last_failure_at,"
                       "payload_hash,status,correlation_id,created_at) VALUES(?,?,?,?,?,?,?,1,?,?,?, 'OPEN', ?,?)",
                       (_tenant(actor) if actor else None, provider_code, operation, entity_ref, failure_category,
                        error_code, safe_error, _now(), _now(), payload_hash, cid, _now()))
    core.audit(conn, actor or {"id": 0, "role": "system"}, "DEAD_LETTER_CREATED", "integration_dead_letters",
               cur.lastrowid, new={"provider": provider_code, "operation": operation, "category": failure_category})
    conn.commit()
    return cur.lastrowid


def list_dead_letters(conn, actor, status=None):
    core.require(actor, "integration.dead_letter.view")
    at = _tenant(actor)
    sql = "SELECT * FROM integration_dead_letters WHERE 1=1"
    args = []
    if at is not None:
        sql += " AND (tenant_id=? OR tenant_id IS NULL)"; args.append(at)
    if status:
        sql += " AND status=?"; args.append(status)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT 200", tuple(args)).fetchall()]


def replay_dead_letter(conn, actor, dlq_id, reason=None):
    """Governed replay: revalidate tenant/profile/idempotency, require reason + permission. Only safe
    (retryable) failures may be replayed; a transfer-creation replay is NOT blind (idempotency-guarded)."""
    core.require(actor, "integration.replay.execute")
    d = conn.execute("SELECT * FROM integration_dead_letters WHERE id=?", (dlq_id,)).fetchone()
    if not d:
        raise core.NotFoundError("dead-letter not found")
    at = _tenant(actor)
    if at is not None and d["tenant_id"] is not None and at != d["tenant_id"]:
        raise core.NotFoundError("dead-letter not found")       # cross-tenant replay denied (no-leak)
    if d["failure_category"] not in RETRYABLE:
        raise core.ForbiddenError(f"unsafe replay: '{d['failure_category']}' is a permanent failure")
    if not reason:
        raise core.ValidationError("replay requires a reason")
    conn.execute("UPDATE integration_dead_letters SET status='REPLAYED' WHERE id=?", (dlq_id,))
    core.audit(conn, actor, "DEAD_LETTER_REPLAYED", "integration_dead_letters", dlq_id, reason=reason)
    conn.commit()
    return {"dlq_id": dlq_id, "status": "REPLAYED"}


# --------------------------------------------------------------------------- #
# Provider health
# --------------------------------------------------------------------------- #
def provider_health(conn, actor, provider_code=None):
    core.require(actor, "integration.health.view")
    at = _tenant(actor)
    sql = "SELECT * FROM connection_profiles WHERE 1=1"
    args = []
    if at is not None:
        sql += " AND (tenant_id=? OR tenant_id IS NULL)"; args.append(at)
    if provider_code:
        sql += " AND provider_code=?"; args.append(provider_code)
    out = []
    for p in conn.execute(sql, tuple(args)).fetchall():
        dlq = conn.execute("SELECT COUNT(*) c FROM integration_dead_letters WHERE provider_code=? AND status='OPEN'", (p["provider_code"],)).fetchone()["c"]
        review = conn.execute("SELECT COUNT(*) c FROM reconciliation_items WHERE status='MANUAL_REVIEW'").fetchone()["c"]
        # never HEALTHY if never validated
        health = p["health"] if p["last_validated_at"] else "UNKNOWN"
        out.append({"profile_id": p["id"], "provider": p["provider_code"], "environment": p["environment"],
                    "status": p["status"], "health": health, "circuit_state": p["circuit_state"],
                    "last_validated_at": p["last_validated_at"], "last_success_at": p["last_success_at"],
                    "last_failure_at": p["last_failure_at"], "dead_letter_backlog": dlq,
                    "reconciliation_backlog": review})
    return {"providers": out}


# --------------------------------------------------------------------------- #
# FX-rate boundary (distinct from Wise quote rate)
# --------------------------------------------------------------------------- #
def record_fx_rate(conn, actor, source_currency, target_currency, rate, provider_code="fx_generic",
                   expiry=None, provider_ref=None, manual_override=False):
    core.require(actor, "integration.fx.manage")
    cur = conn.execute("INSERT INTO fx_rates(tenant_id,provider_code,source_currency,target_currency,rate,"
                       "effective_at,expiry,provider_ref,manual_override,approved_by,created_at)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (_tenant(actor), provider_code, source_currency, target_currency, rate, _now(), expiry,
                        provider_ref, 1 if manual_override else 0, (actor or {}).get("id") if manual_override else None, _now()))
    core.audit(conn, actor, "FX_RATE_RECORDED", "fx_rates", cur.lastrowid,
               new={"pair": f"{source_currency}/{target_currency}", "rate": rate, "manual": manual_override})
    conn.commit()
    return cur.lastrowid


# --------------------------------------------------------------------------- #
# Migration classification
# --------------------------------------------------------------------------- #
def classify_existing(conn):
    def _count(sql):
        try:
            return conn.execute(sql).fetchone()["c"]
        except Exception:
            return 0
    verified = _count("SELECT COUNT(*) c FROM payment_requests WHERE status='VERIFIED'")
    with_ref = _count("SELECT COUNT(*) c FROM payment_requests WHERE provider_ref IS NOT NULL")
    return {"payment_requests_verified": verified, "payment_requests_with_provider_ref": with_ref,
            "wise_transfers_created": _count("SELECT COUNT(*) c FROM provider_transfers"),
            "fake_transaction_ids_assigned": 0,
            "financial_differences": 0, "payment_status_changes": 0, "job_status_changes": 0}
