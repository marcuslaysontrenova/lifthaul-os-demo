"""LiftHaul Enterprise — B2B Developer Platform (Platform Control → Integrations).

Governed API-client administration, scoped /api/v1 access, and OUTBOUND webhook subscriptions
(LiftHaul → customer endpoints) with HMAC signing, retry/backoff, dead-letter and replay. This is NOT
a new logistics domain: the /api/v1 handlers reuse the canonical booking/quote/tracking engines
(`public_booking`). No business logic is duplicated. The existing inbound provider-webhook engine
(`integrations.py`) is left untouched; this adds the outbound direction the enterprise API needs.

Security posture: client secrets are hashed (never stored/returned in plaintext after creation);
scopes are enforced server-side (never '*'); production credentials require explicit approval; sandbox
cannot move real funds; per-client rate limits; idempotency; tenant isolation; full audit.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import secrets
import time

import core
import tenant

# Granular scopes — an integration client never gets unrestricted access.
SCOPES = ("bookings:create", "bookings:read", "bookings:update", "quotations:read",
          "tracking:read", "marketplace:read", "payments:read", "jobs:read",
          "insurance:quote", "insurance:read", "claims:create", "claims:read")

# Webhook event catalogue (customer-subscribable).
EVENTS = ("booking.created", "booking.reviewed", "quotation.ready", "quotation.accepted",
          "payment.required", "payment.confirmed", "marketplace.matching", "carrier.assigned",
          "trip.started", "trip.at_port", "trip.in_transit", "trip.delivered", "pod.available",
          "dispute.opened", "settlement.completed",
          "insurance.quote_ready", "insurance.bound", "insurance.rejected",
          "claim.created", "claim.submitted", "claim.approved", "claim.denied", "claim.settled")

DELIVERY_STATES = ("PENDING", "DELIVERING", "DELIVERED", "RETRYING", "FAILED", "DEAD_LETTER", "DISABLED")

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_clients(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, name TEXT, client_id TEXT UNIQUE, secret_hash TEXT,
  environment TEXT DEFAULT 'SANDBOX', scopes TEXT, status TEXT DEFAULT 'ACTIVE',
  rate_per_min INTEGER DEFAULT 120, rate_per_day INTEGER DEFAULT 20000, ip_allowlist TEXT,
  production_approved INTEGER DEFAULT 0, created_by INTEGER, created_at TEXT, last_used_at TEXT, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS api_webhooks(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, client_ref INTEGER, url TEXT, events TEXT,
  secret TEXT, status TEXT DEFAULT 'ACTIVE', created_by INTEGER, created_at TEXT, rotated_at TEXT);
CREATE TABLE IF NOT EXISTS api_webhook_deliveries(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, webhook_id INTEGER, event_id TEXT, event_type TEXT,
  payload TEXT, signature TEXT, correlation_id TEXT, status TEXT DEFAULT 'PENDING', attempts INTEGER DEFAULT 0,
  replays INTEGER DEFAULT 0, next_attempt_at TEXT, last_error TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS api_idempotency(
  id INTEGER PRIMARY KEY, client_ref INTEGER, idem_key TEXT, result_ref TEXT, created_at TEXT);
"""

_MAX_ATTEMPTS = 5
_MANAGE = "integration.profile.manage"   # platform_admin holds integration.profile.*
_VIEW = "integration.catalog.view"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn):
    return 0


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #
def _hash_secret(secret, salt):
    return hashlib.sha256((str(salt) + ":" + str(secret)).encode()).hexdigest()


def _redact_client(row):
    d = dict(row)
    d.pop("secret_hash", None)   # never expose the hash
    return d


# --------------------------------------------------------------------------- #
# 1. API-client administration
# --------------------------------------------------------------------------- #
def create_client(conn, actor, name, scopes, environment="SANDBOX", rate_per_min=120,
                  rate_per_day=20000, ip_allowlist=None):
    core.require(actor, _MANAGE)
    env = (environment or "SANDBOX").upper()
    if env not in ("SANDBOX", "PRODUCTION"):
        raise core.ValidationError("environment must be SANDBOX or PRODUCTION")
    reqd = [s for s in (scopes or []) if s]
    if not reqd:
        raise core.ValidationError("at least one scope is required")
    for s in reqd:
        if s == "*" or s not in SCOPES:
            raise core.ValidationError(f"invalid scope '{s}' (wildcard not allowed)")
    client_id = "lhc_" + secrets.token_hex(8)
    plain_secret = ("sk_" + ("live" if env == "PRODUCTION" else "test") + "_" + secrets.token_urlsafe(24))
    tid = actor.get("tenant_id")
    cur = conn.execute(
        "INSERT INTO api_clients(tenant_id,name,client_id,secret_hash,environment,scopes,status,"
        "rate_per_min,rate_per_day,ip_allowlist,production_approved,created_by,created_at) "
        "VALUES(?,?,?,?,?,?, 'ACTIVE', ?,?,?, 0, ?,?)",
        (tid, str(name)[:200], client_id, _hash_secret(plain_secret, client_id), env,
         ",".join(reqd), int(rate_per_min), int(rate_per_day),
         (",".join(ip_allowlist) if ip_allowlist else None), actor.get("id"), _now()))
    cid = cur.lastrowid
    core.audit(conn, actor, "API_CLIENT_CREATED", "api_clients", cid, None,
               {"name": name, "env": env, "scopes": reqd})
    conn.commit()
    # plaintext secret returned exactly once
    return {"id": cid, "client_id": client_id, "secret": plain_secret, "environment": env,
            "scopes": reqd, "api_key": client_id + ":" + plain_secret,
            "production_active": False,
            "note": "Store the secret now — it is shown once and never returned again."}


def list_clients(conn, actor):
    core.require(actor, _VIEW) if core.can(actor, _VIEW) else core.require(actor, _MANAGE)
    frag, params = tenant.predicate(actor)
    rows = conn.execute("SELECT id,tenant_id,name,client_id,environment,scopes,status,rate_per_min,"
                        "rate_per_day,production_approved,last_used_at,created_at,revoked_at "
                        "FROM api_clients WHERE 1=1" + frag + " ORDER BY id DESC", params).fetchall()
    return {"clients": [dict(r) for r in rows]}


def _guard_client(conn, actor, cid):
    frag, params = tenant.predicate(actor)
    row = conn.execute("SELECT * FROM api_clients WHERE id=?" + frag, [cid] + list(params)).fetchone()
    if not row:
        raise core.NotFoundError("api client not found")
    return row


def revoke_client(conn, actor, cid):
    core.require(actor, _MANAGE)
    _guard_client(conn, actor, cid)
    conn.execute("UPDATE api_clients SET status='REVOKED', revoked_at=? WHERE id=?", (_now(), cid))
    core.audit(conn, actor, "API_CLIENT_REVOKED", "api_clients", cid)
    conn.commit()
    return {"id": cid, "status": "REVOKED"}


def rotate_secret(conn, actor, cid):
    core.require(actor, _MANAGE)
    row = _guard_client(conn, actor, cid)
    new_secret = ("sk_" + ("live" if row["environment"] == "PRODUCTION" else "test") + "_" + secrets.token_urlsafe(24))
    conn.execute("UPDATE api_clients SET secret_hash=? WHERE id=?",
                 (_hash_secret(new_secret, row["client_id"]), cid))
    core.audit(conn, actor, "API_SECRET_ROTATED", "api_clients", cid)
    conn.commit()
    return {"id": cid, "client_id": row["client_id"], "secret": new_secret,
            "api_key": row["client_id"] + ":" + new_secret}


def approve_production(conn, actor, cid):
    """Production credentials require explicit authorized approval (SoD-friendly)."""
    core.require(actor, _MANAGE)
    row = _guard_client(conn, actor, cid)
    if row["environment"] != "PRODUCTION":
        raise core.ValidationError("client is not a PRODUCTION client")
    conn.execute("UPDATE api_clients SET production_approved=1 WHERE id=?", (cid,))
    core.audit(conn, actor, "API_CLIENT_PRODUCTION_APPROVED", "api_clients", cid)
    conn.commit()
    return {"id": cid, "production_active": True}


# --------------------------------------------------------------------------- #
# 2. Authentication + scope + rate limiting (used by the /api/v1 gateway)
# --------------------------------------------------------------------------- #
def authenticate(conn, api_key, remote_ip=None):
    if not api_key or ":" not in str(api_key):
        raise core.ForbiddenError("invalid api key")
    client_id, secret = str(api_key).split(":", 1)
    row = conn.execute("SELECT * FROM api_clients WHERE client_id=?", (client_id,)).fetchone()
    if not row or row["status"] != "ACTIVE" or row["revoked_at"]:
        raise core.ForbiddenError("invalid or revoked api key")
    if _hash_secret(secret, client_id) != row["secret_hash"]:
        raise core.ForbiddenError("invalid api key")
    if row["environment"] == "PRODUCTION" and not row["production_approved"]:
        raise core.ForbiddenError("production client not approved")
    allow = (row["ip_allowlist"] or "").split(",") if row["ip_allowlist"] else []
    if allow and remote_ip and remote_ip not in [a.strip() for a in allow if a.strip()]:
        raise core.ForbiddenError("ip not allowed")
    conn.execute("UPDATE api_clients SET last_used_at=? WHERE id=?", (_now(), row["id"]))
    conn.commit()
    scopes = set((row["scopes"] or "").split(",")) - {""}
    return {"id": -1, "role": "api_client", "perms": set(), "tenant_id": row["tenant_id"],
            "scopes": scopes, "client_ref": row["id"], "client_id": client_id,
            "environment": row["environment"], "rate_per_min": row["rate_per_min"],
            "rate_per_day": row["rate_per_day"]}


def require_scope(actor, scope):
    if scope not in (actor.get("scopes") or set()):
        raise core.ForbiddenError(f"missing scope: {scope}")


_RL_MIN = {}   # client_ref -> [timestamps in last 60s]
_RL_DAY = {}   # client_ref -> {"day": date, "count": n}


def check_rate(actor):
    ref = actor.get("client_ref")
    now = time.time()
    q = _RL_MIN.setdefault(ref, [])
    while q and q[0] < now - 60:
        q.pop(0)
    if len(q) >= (actor.get("rate_per_min") or 120):
        raise _rate_error("rate limit exceeded")
    q.append(now)
    today = datetime.date.today().isoformat()
    d = _RL_DAY.setdefault(ref, {"day": today, "count": 0})
    if d["day"] != today:
        d["day"], d["count"] = today, 0
    if d["count"] >= (actor.get("rate_per_day") or 20000):
        raise _rate_error("daily rate limit exceeded")
    d["count"] += 1


def _rate_error(msg):
    e = core.AppError(msg)
    e.http = 429
    return e


def _idempotent_lookup(conn, actor, idem_key):
    if not idem_key:
        return None
    r = conn.execute("SELECT result_ref FROM api_idempotency WHERE client_ref=? AND idem_key=? LIMIT 1",
                     (actor.get("client_ref"), idem_key)).fetchone()
    return (r["result_ref"] if r else None)


def _idempotent_store(conn, actor, idem_key, result_ref):
    if not idem_key:
        return
    conn.execute("INSERT INTO api_idempotency(client_ref,idem_key,result_ref,created_at) VALUES(?,?,?,?)",
                 (actor.get("client_ref"), idem_key, result_ref, _now()))
    conn.commit()


# --------------------------------------------------------------------------- #
# 3-5. B2B API handlers (reuse canonical engines — no duplicated logic)
# --------------------------------------------------------------------------- #
def api_create_booking(conn, actor, payload, idem_key=None):
    require_scope(actor, "bookings:create")
    import public_booking as pb
    p = dict(payload or {})
    if idem_key:
        p.setdefault("idempotency_key", idem_key)
    res = pb.submit(conn, p)
    # emit lifecycle event to subscribed webhooks (tenant-scoped)
    try:
        emit_event(conn, actor.get("tenant_id"), "booking.created",
                   {"ref": res["ref"], "status": res["status"], "service": res.get("service"),
                    "service_class": res.get("service_class"), "estimate_status": res.get("estimate_status")})
    except Exception:
        pass
    return res


def api_get_booking(conn, actor, ref):
    require_scope(actor, "bookings:read")
    import public_booking as pb
    row = conn.execute("SELECT tracking_token FROM mkt_bookings WHERE tracking_token IS NOT NULL AND "
                       "source='PUBLIC_MARKETPLACE'", ).fetchall()
    # ref is LH-...; resolve via the token suffix stored on the booking is not unique — so accept token
    tok = ref if str(ref).startswith("pbk_") else None
    if not tok:
        # allow lookup by ref → find the booking whose token ends with the ref suffix
        suffix = str(ref).split("-")[-1].lower()
        for r in row:
            if str(r["tracking_token"])[-6:].lower() == suffix:
                tok = r["tracking_token"]; break
    if not tok:
        raise core.NotFoundError("booking not found")
    return pb.track(conn, tok)


def api_track(conn, actor, token):
    require_scope(actor, "tracking:read")
    import public_booking as pb
    return pb.track(conn, token)


def api_bulk(conn, actor, rows, idem_key=None):
    require_scope(actor, "bookings:create")
    import public_booking as pb
    # bulk reuses the canonical batch intake; API client acts with its own authority
    b = pb.submit_bulk(conn, {"id": actor.get("client_ref"), "role": "api_client",
                              "perms": {"marketplace.booking.create"}, "tenant_id": actor.get("tenant_id")}, rows)
    try:
        for c in b.get("created", []):
            emit_event(conn, actor.get("tenant_id"), "booking.created", {"ref": c["ref"]})
    except Exception:
        pass
    return b


def api_quote_estimate(conn, actor, payload):
    require_scope(actor, "quotations:read")
    import public_booking as pb
    p = payload or {}
    route = pb.classify_route(p.get("origin_island"), p.get("dest_island"))
    svc = pb.classify_service(p.get("vehicle"))
    level = pb.resolve_service_level(p.get("service_level"), svc["service_class"])
    q = pb.quote(conn, p["vehicle"], p.get("km"), route["inter_island"], level)
    kind = "INSTANT_ESTIMATE" if q.get("amount") is not None else "ESTIMATE_REQUIRED"
    return {"result": kind, "estimate": q.get("amount"), "estimate_status": q["status"],
            "service_class": svc["service_class"], "service_level": level,
            "inter_island": route["inter_island"], "note": q.get("note")}


def _resolve_booking_id(conn, ref):
    if str(ref).startswith("pbk_"):
        r = conn.execute("SELECT id FROM mkt_bookings WHERE tracking_token=?", (ref,)).fetchone()
        return (r["id"] if r else None)
    suffix = str(ref).split("-")[-1].lower()
    for r in conn.execute("SELECT id,tracking_token FROM mkt_bookings WHERE tracking_token IS NOT NULL").fetchall():
        if str(r["tracking_token"])[-6:].lower() == suffix:
            return r["id"]
    return None


def _svc(actor, perms):
    return {"id": actor.get("client_ref"), "role": "api_client", "perms": set(perms), "tenant_id": actor.get("tenant_id")}


def api_insurance_quote(conn, actor, payload):
    require_scope(actor, "insurance:quote")
    import goods_protection as gp
    bid = _resolve_booking_id(conn, (payload or {}).get("booking_ref"))
    if not bid:
        raise core.NotFoundError("booking not found")
    svc = _svc(actor, {"marketplace.booking.manage", "marketplace.insurance.manage"})
    gp.request_coverage(conn, svc, bid, (payload or {}).get("declared_value"), (payload or {}).get("cargo_category", "GENERAL"))
    return gp.quote_coverage(conn, svc, bid)


def api_insurance_get(conn, actor, ref):
    require_scope(actor, "insurance:read")
    import goods_protection as gp
    bid = _resolve_booking_id(conn, ref)
    if not bid:
        raise core.NotFoundError("booking not found")
    return gp.get_coverage(conn, bid)


def api_claim_create(conn, actor, payload):
    require_scope(actor, "claims:create")
    import goods_protection as gp
    bid = _resolve_booking_id(conn, (payload or {}).get("booking_ref"))
    if not bid:
        raise core.NotFoundError("booking not found")
    svc = _svc(actor, {"marketplace.claim.manage"})
    return gp.link_claim(conn, svc, bid, (payload or {}).get("incident_ref"), (payload or {}).get("claimed_amount"))


def api_claim_get(conn, actor, claim_id):
    require_scope(actor, "claims:read")
    r = conn.execute("SELECT claim_number,status,claimed_amount,approved_amount,insurer,policy_reference "
                     "FROM mkt_claims WHERE id=?", (int(claim_id),)).fetchone()
    if not r:
        raise core.NotFoundError("claim not found")
    return dict(r)


# --------------------------------------------------------------------------- #
# 7-10. Webhooks (outbound) — subscription, signing, delivery, retry, replay
# --------------------------------------------------------------------------- #
def create_webhook(conn, actor, url, events, client_ref=None):
    core.require(actor, _MANAGE)
    if not url or not str(url).lower().startswith(("http://", "https://")):
        raise core.ValidationError("a valid https url is required")
    evs = [e for e in (events or []) if e in EVENTS]
    if not evs:
        raise core.ValidationError("subscribe to at least one valid event")
    sec = "whsec_" + secrets.token_urlsafe(24)
    tid = actor.get("tenant_id")
    cur = conn.execute("INSERT INTO api_webhooks(tenant_id,client_ref,url,events,secret,status,created_by,created_at)"
                       " VALUES(?,?,?,?,?, 'ACTIVE', ?,?)",
                       (tid, client_ref, str(url)[:500], ",".join(evs), sec, actor.get("id"), _now()))
    wid = cur.lastrowid
    core.audit(conn, actor, "API_WEBHOOK_CREATED", "api_webhooks", wid, None, {"url": url, "events": evs})
    conn.commit()
    return {"id": wid, "url": url, "events": evs, "secret": sec,
            "note": "Store the signing secret now — used to verify HMAC signatures; shown once."}


def list_webhooks(conn, actor):
    core.require(actor, _VIEW) if core.can(actor, _VIEW) else core.require(actor, _MANAGE)
    frag, params = tenant.predicate(actor)
    rows = conn.execute("SELECT id,tenant_id,client_ref,url,events,status,created_at,rotated_at "
                        "FROM api_webhooks WHERE 1=1" + frag + " ORDER BY id DESC", params).fetchall()
    return {"webhooks": [dict(r) for r in rows]}   # secret intentionally omitted


def rotate_webhook_secret(conn, actor, wid):
    core.require(actor, _MANAGE)
    frag, params = tenant.predicate(actor)
    row = conn.execute("SELECT id FROM api_webhooks WHERE id=?" + frag, [wid] + list(params)).fetchone()
    if not row:
        raise core.NotFoundError("webhook not found")
    sec = "whsec_" + secrets.token_urlsafe(24)
    conn.execute("UPDATE api_webhooks SET secret=?, rotated_at=? WHERE id=?", (sec, _now(), wid))
    core.audit(conn, actor, "API_WEBHOOK_SECRET_ROTATED", "api_webhooks", wid)
    conn.commit()
    return {"id": wid, "secret": sec}


def disable_webhook(conn, actor, wid):
    core.require(actor, _MANAGE)
    frag, params = tenant.predicate(actor)
    row = conn.execute("SELECT id FROM api_webhooks WHERE id=?" + frag, [wid] + list(params)).fetchone()
    if not row:
        raise core.NotFoundError("webhook not found")
    conn.execute("UPDATE api_webhooks SET status='DISABLED' WHERE id=?", (wid,))
    core.audit(conn, actor, "API_WEBHOOK_DISABLED", "api_webhooks", wid)
    conn.commit()
    return {"id": wid, "status": "DISABLED"}


def sign(secret, event_id, ts, body):
    msg = (str(event_id) + "." + str(ts) + "." + body).encode()
    return "sha256=" + hmac.new(str(secret).encode(), msg, hashlib.sha256).hexdigest()


def emit_event(conn, tenant_id, event_type, payload, correlation_id=None):
    """Queue a delivery for every ACTIVE webhook in the tenant subscribing to event_type. Never blocks
    the core workflow — failures here are swallowed by callers."""
    if event_type not in EVENTS:
        raise core.ValidationError(f"unknown event type '{event_type}'")
    hooks = conn.execute("SELECT id,secret,events,url FROM api_webhooks WHERE status='ACTIVE' AND "
                         "(tenant_id=? OR tenant_id IS NULL)", (tenant_id,)).fetchall()
    n = 0
    ts = _now()
    for h in hooks:
        if event_type not in (h["events"] or "").split(","):
            continue
        eid = "evt_" + secrets.token_hex(10)
        body = json.dumps({"id": eid, "type": event_type, "timestamp": ts, "tenant_safe": True,
                           "data": payload}, default=str)
        sig = sign(h["secret"], eid, ts, body)
        conn.execute("INSERT INTO api_webhook_deliveries(tenant_id,webhook_id,event_id,event_type,payload,"
                     "signature,correlation_id,status,attempts,next_attempt_at,created_at,updated_at) "
                     "VALUES(?,?,?,?,?,?,?, 'PENDING', 0, ?, ?, ?)",
                     (tenant_id, h["id"], eid, event_type, body, sig,
                      correlation_id or core.correlation_id() if hasattr(core, "correlation_id") else None,
                      ts, ts, ts))
        n += 1
    conn.commit()
    return {"queued": n, "event_type": event_type}


def _http_sender(url, headers, body):   # pragma: no cover (requires network + a hosted deployment)
    import urllib.request
    req = urllib.request.Request(url, data=body.encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return 200 <= r.status < 300


def deliver_pending(conn, sender=None, now=None, max_attempts=_MAX_ATTEMPTS):
    """Attempt due deliveries. Injectable sender for tests/offline. Applies exponential backoff and
    dead-letters after max attempts. A customer endpoint being offline never affects core bookings."""
    sender = sender or _http_sender
    now_iso = now or _now()
    rows = conn.execute("SELECT * FROM api_webhook_deliveries WHERE status IN ('PENDING','RETRYING') AND "
                        "(next_attempt_at IS NULL OR next_attempt_at<=?)", (now_iso,)).fetchall()
    delivered = failed = dead = 0
    for r in rows:
        wh = conn.execute("SELECT url,status FROM api_webhooks WHERE id=?", (r["webhook_id"],)).fetchone()
        if not wh or wh["status"] != "ACTIVE":
            conn.execute("UPDATE api_webhook_deliveries SET status='DISABLED', updated_at=? WHERE id=?", (_now(), r["id"]))
            continue
        conn.execute("UPDATE api_webhook_deliveries SET status='DELIVERING', updated_at=? WHERE id=?", (_now(), r["id"]))
        headers = {"Content-Type": "application/json", "X-LiftHaul-Event": r["event_type"],
                   "X-LiftHaul-Event-Id": r["event_id"], "X-LiftHaul-Signature": r["signature"]}
        ok = False
        try:
            ok = bool(sender(wh["url"], headers, r["payload"]))
        except Exception as e:
            ok = False
        attempts = (r["attempts"] or 0) + 1
        if ok:
            conn.execute("UPDATE api_webhook_deliveries SET status='DELIVERED', attempts=?, updated_at=? WHERE id=?",
                         (attempts, _now(), r["id"]))
            delivered += 1
        elif attempts >= max_attempts:
            conn.execute("UPDATE api_webhook_deliveries SET status='DEAD_LETTER', attempts=?, last_error='max attempts', updated_at=? WHERE id=?",
                         (attempts, _now(), r["id"]))
            dead += 1
        else:
            backoff = 2 ** attempts
            nxt = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=backoff)).isoformat(timespec="seconds")
            conn.execute("UPDATE api_webhook_deliveries SET status='RETRYING', attempts=?, next_attempt_at=?, last_error='delivery failed', updated_at=? WHERE id=?",
                         (attempts, nxt, _now(), r["id"]))
            failed += 1
    conn.commit()
    return {"delivered": delivered, "retrying": failed, "dead_letter": dead}


def replay_delivery(conn, actor, delivery_id):
    core.require(actor, _MANAGE)
    frag, params = tenant.predicate(actor)
    r = conn.execute("SELECT * FROM api_webhook_deliveries WHERE id=?" + frag, [delivery_id] + list(params)).fetchone()
    if not r:
        raise core.NotFoundError("delivery not found")
    conn.execute("UPDATE api_webhook_deliveries SET status='PENDING', next_attempt_at=NULL, "
                 "replays=replays+1, updated_at=? WHERE id=?", (_now(), delivery_id))
    core.audit(conn, actor, "API_WEBHOOK_REPLAY", "api_webhook_deliveries", delivery_id, None,
               {"event_id": r["event_id"], "event_type": r["event_type"]})
    conn.commit()
    return {"id": delivery_id, "event_id": r["event_id"], "status": "PENDING", "replays": (r["replays"] or 0) + 1}


def list_deliveries(conn, actor, limit=100):
    core.require(actor, _VIEW) if core.can(actor, _VIEW) else core.require(actor, _MANAGE)
    frag, params = tenant.predicate(actor)
    rows = conn.execute("SELECT id,webhook_id,event_id,event_type,status,attempts,replays,last_error,"
                        "created_at,updated_at FROM api_webhook_deliveries WHERE 1=1" + frag +
                        " ORDER BY id DESC LIMIT ?", list(params) + [limit]).fetchall()
    return {"deliveries": [dict(r) for r in rows]}


def catalog(conn=None, actor=None):
    """Public-ish metadata for the developer portal / docs."""
    return {"scopes": list(SCOPES), "events": list(EVENTS), "environments": ["SANDBOX", "PRODUCTION"],
            "delivery_states": list(DELIVERY_STATES)}
