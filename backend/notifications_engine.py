"""LiftHaul Enterprise — Automated Customer & Operational Notifications.

Extends the EXISTING notification domain (notification_templates / notifications / notification_events)
into an event-driven lifecycle communications layer. It is NOT a new messaging system and does NOT
re-emit booking/tracking/payment/claims events — those canonical events are INPUTS here (fed via
api_platform.emit_event -> on_event).

Honesty first: provider delivery FAILS honestly when no real messaging provider is connected — never a
fabricated "sent". Mandatory transactional notices cannot be suppressed by opt-out. Sensitive values
(OTP codes, financial secrets) are never placed in notification bodies, history, or audit — the OTP is
delivered only via delivery_verification's authorized provider path, never through this layer's payload.
"""
from __future__ import annotations

import datetime
import hashlib
import json

import core
import tenant

CHANNELS = ("email", "sms", "push", "whatsapp", "in_app")
STATES = ("CREATED", "QUEUED", "PROVIDER_ACCEPTED", "DELIVERED", "FAILED", "RETRYING",
          "DEAD_LETTER", "SUPPRESSED")

# Canonical lifecycle events this layer can communicate (inputs from the rest of the platform).
EVENTS = (
    "booking_received", "booking_under_review", "information_required", "estimate_required",
    "quotation_ready", "quotation_accepted", "payment_required", "payment_confirmed", "funds_protected",
    "carrier_matching", "carrier_assigned", "driver_en_route", "pickup_complete", "in_transit",
    "at_origin_port", "sea_transit", "destination_port", "out_for_delivery", "delivery_otp_issued",
    "recipient_verified", "delivered", "pod_available", "dispute_opened", "claim_status",
    "refund_status", "settlement_complete",
)
# Transactional notices that MUST reach the customer and can never be opt-out-suppressed.
MANDATORY = {"quotation_ready", "payment_required", "carrier_assigned", "delivery_otp_issued",
             "recipient_verified", "delivered", "claim_status", "settlement_complete"}

# Map the webhook/event bus names (api_platform emit_event) -> notification events.
EVENT_MAP = {
    "booking.created": "booking_received", "booking.reviewed": "booking_under_review",
    "quotation.ready": "quotation_ready", "quotation.accepted": "quotation_accepted",
    "payment.required": "payment_required", "payment.confirmed": "payment_confirmed",
    "marketplace.matching": "carrier_matching", "carrier.assigned": "carrier_assigned",
    "trip.started": "driver_en_route", "trip.at_port": "at_origin_port", "trip.in_transit": "in_transit",
    "trip.delivered": "delivered", "pod.available": "pod_available", "dispute.opened": "dispute_opened",
    "settlement.completed": "settlement_complete", "claim.created": "claim_status",
    "claim.submitted": "claim_status", "claim.approved": "claim_status", "claim.denied": "claim_status",
    "claim.settled": "claim_status", "delivery.verification_required": "delivery_otp_issued",
    "delivery.recipient_verified": "recipient_verified", "delivery.completed": "delivered",
    "insurance.quote_ready": "quotation_ready",
}
_MANAGE, _VIEW = "integration.profile.manage", "integration.catalog.view"

SCHEMA = """
CREATE TABLE IF NOT EXISTS notify_policy(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, event_type TEXT NOT NULL, channel TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'OPTIONAL', UNIQUE(tenant_id,event_type,channel));
CREATE TABLE IF NOT EXISTS notify_templates(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, event_type TEXT NOT NULL, channel TEXT NOT NULL,
  locale TEXT DEFAULT 'en', subject TEXT, body TEXT, version INTEGER DEFAULT 1, active INTEGER DEFAULT 1,
  created_by INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS notify_prefs(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, recipient TEXT NOT NULL, channel TEXT NOT NULL,
  opted_out INTEGER DEFAULT 0, locale TEXT DEFAULT 'en', UNIQUE(tenant_id,recipient,channel));
"""
NOTIF_COLUMNS = [
    ("tenant_id", "INTEGER"), ("event_type", "TEXT"), ("locale", "TEXT"), ("correlation_id", "TEXT"),
    ("attempts", "INTEGER"), ("max_attempts", "INTEGER"), ("next_attempt_at", "TEXT"),
    ("provider", "TEXT"), ("dedup_key", "TEXT"), ("mandatory", "INTEGER"), ("last_error", "TEXT"),
    ("updated_at", "TEXT"),
]
# Default policy matrix (REQUIRED / OPTIONAL / OFF).  email, sms, push
_DEFAULT_POLICY = {
    "booking_received": {"email": "REQUIRED", "sms": "OPTIONAL", "push": "OPTIONAL"},
    "quotation_ready": {"email": "REQUIRED", "sms": "REQUIRED", "push": "REQUIRED"},
    "payment_required": {"email": "REQUIRED", "sms": "REQUIRED", "push": "REQUIRED"},
    "carrier_assigned": {"email": "REQUIRED", "sms": "REQUIRED", "push": "REQUIRED"},
    "driver_en_route": {"email": "OFF", "sms": "REQUIRED", "push": "REQUIRED"},
    "delivery_otp_issued": {"email": "OFF", "sms": "REQUIRED", "push": "OPTIONAL"},
    "pod_available": {"email": "REQUIRED", "sms": "OFF", "push": "REQUIRED"},
    "claim_status": {"email": "REQUIRED", "sms": "REQUIRED", "push": "REQUIRED"},
    "settlement_complete": {"email": "REQUIRED", "sms": "OPTIONAL", "push": "OPTIONAL"},
}
_DEFAULT_TEMPLATES = {
    "booking_received": ("LiftHaul booking {ref} received", "We've received your booking {ref}. Our team is reviewing it."),
    "quotation_ready": ("Your LiftHaul quotation {ref} is ready", "Quotation for {ref} is ready. Please review to proceed."),
    "payment_required": ("Payment required for {ref}", "Your booking {ref} is awaiting payment. Follow the secure instructions in your account."),
    "carrier_assigned": ("Carrier assigned for {ref}", "A verified carrier has been assigned to {ref}."),
    "driver_en_route": ("Driver en route — {ref}", "Your driver is on the way for {ref}."),
    "delivery_otp_issued": ("Delivery verification for {ref}", "A one-time verification code has been sent to the recipient for {ref}. Do not share it with anyone who is not receiving the delivery."),
    "recipient_verified": ("Recipient verified — {ref}", "The recipient for {ref} has been verified."),
    "delivered": ("Delivered — {ref}", "Your shipment {ref} has been delivered."),
    "pod_available": ("Proof of delivery for {ref}", "Proof of delivery for {ref} is now available."),
    "claim_status": ("Claim update for {ref}", "There is an update on the claim linked to {ref}."),
    "settlement_complete": ("Settlement complete — {ref}", "The settlement for {ref} is complete. Thank you."),
}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def init(conn):
    for col, typ in NOTIF_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE notifications ADD COLUMN {col} {typ}")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def seed(conn):
    for ev, chans in _DEFAULT_POLICY.items():
        for ch, mode in chans.items():
            try:
                conn.execute("INSERT INTO notify_policy(tenant_id,event_type,channel,mode) VALUES(NULL,?,?,?)"
                             " ON CONFLICT(tenant_id,event_type,channel) DO NOTHING", (ev, ch, mode))
            except Exception:
                pass
    for ev, (subj, body) in _DEFAULT_TEMPLATES.items():
        for ch in ("email", "sms", "push"):
            row = conn.execute("SELECT 1 FROM notify_templates WHERE tenant_id IS NULL AND event_type=? AND "
                               "channel=? AND locale='en'", (ev, ch)).fetchone()
            if not row:
                conn.execute("INSERT INTO notify_templates(tenant_id,event_type,channel,locale,subject,body,"
                             "version,active,created_at) VALUES(NULL,?,?, 'en', ?,?,1,1,?)",
                             (ev, ch, subj if ch != "sms" else "", body, _now()))
    conn.commit()
    return 0


def _cfg(conn, key, default=None):
    try:
        import admin_platform as ap
        v, _ = ap.resolve_config(conn, key)
        return v if v is not None else default
    except Exception:
        return default


# --------------------------------------------------------------------------- #
# Policy + templates + preferences
# --------------------------------------------------------------------------- #
def policy_for(conn, tenant_id, event_type):
    out = {}
    for ch in ("email", "sms", "push", "whatsapp"):
        r = conn.execute("SELECT mode FROM notify_policy WHERE event_type=? AND channel=? AND "
                         "(tenant_id=? OR tenant_id IS NULL) ORDER BY tenant_id DESC LIMIT 1",
                         (event_type, ch, tenant_id)).fetchone()
        if r:
            out[ch] = r["mode"]
    out["mandatory"] = event_type in MANDATORY   # transactional notices that cannot be suppressed
    return out


def _template(conn, tenant_id, event_type, channel, locale="en"):
    r = conn.execute("SELECT subject,body FROM notify_templates WHERE event_type=? AND channel=? AND "
                     "locale=? AND active=1 AND (tenant_id=? OR tenant_id IS NULL) "
                     "ORDER BY tenant_id DESC, version DESC LIMIT 1", (event_type, channel, locale, tenant_id)).fetchone()
    if not r:
        r = conn.execute("SELECT subject,body FROM notify_templates WHERE event_type=? AND channel=? AND "
                         "locale='en' AND active=1 ORDER BY tenant_id DESC, version DESC LIMIT 1",
                         (event_type, channel)).fetchone()
    return (r["subject"], r["body"]) if r else (event_type, event_type)


def upsert_template(conn, actor, event_type, channel, subject, body, locale="en", tenant_id=None):
    core.require(actor, _MANAGE)
    prev = conn.execute("SELECT MAX(version) v FROM notify_templates WHERE event_type=? AND channel=? AND "
                        "locale=? AND (tenant_id IS ? OR tenant_id=?)", (event_type, channel, locale, tenant_id, tenant_id)).fetchone()
    ver = ((prev["v"] or 0) + 1) if prev else 1
    conn.execute("UPDATE notify_templates SET active=0 WHERE event_type=? AND channel=? AND locale=? AND "
                 "(tenant_id IS ? OR tenant_id=?)", (event_type, channel, locale, tenant_id, tenant_id))
    conn.execute("INSERT INTO notify_templates(tenant_id,event_type,channel,locale,subject,body,version,"
                 "active,created_by,created_at) VALUES(?,?,?,?,?,?,?,1,?,?)",
                 (tenant_id, event_type, channel, locale, subject, body, ver, actor.get("id"), _now()))
    core.audit(conn, actor, "NOTIFY_TEMPLATE_VERSIONED", "notify_templates", 0, None,
               {"event": event_type, "channel": channel, "version": ver})
    conn.commit()
    return {"event_type": event_type, "channel": channel, "version": ver}


def set_pref(conn, actor, recipient, channel, opted_out, locale="en", tenant_id=None):
    core.require(actor, _MANAGE) if core.can(actor, _MANAGE) else core.require(actor, "customer.read")
    conn.execute("INSERT INTO notify_prefs(tenant_id,recipient,channel,opted_out,locale) VALUES(?,?,?,?,?)"
                 " ON CONFLICT(tenant_id,recipient,channel) DO UPDATE SET opted_out=excluded.opted_out,"
                 " locale=excluded.locale", (tenant_id, recipient, channel, 1 if opted_out else 0, locale))
    core.audit(conn, actor, "NOTIFY_PREF_SET", "notify_prefs", 0, None,
               {"recipient": _mask(recipient), "channel": channel, "opted_out": bool(opted_out)})
    conn.commit()
    return {"recipient": _mask(recipient), "channel": channel, "opted_out": bool(opted_out)}


def _opted_out(conn, tenant_id, recipient, channel):
    r = conn.execute("SELECT opted_out FROM notify_prefs WHERE recipient=? AND channel=? AND "
                     "(tenant_id=? OR tenant_id IS NULL) ORDER BY tenant_id DESC LIMIT 1",
                     (recipient, channel, tenant_id)).fetchone()
    return bool(r and r["opted_out"])


def _mask(r):
    r = str(r or "")
    if "@" in r:
        a, _, b = r.partition("@")
        return (a[:2] + "***@" + b) if a else ("***@" + b)
    return ("******" + r[-4:]) if len(r) >= 4 else "—"


# --------------------------------------------------------------------------- #
# Emission (from lifecycle events) — creates queued notifications per policy
# --------------------------------------------------------------------------- #
def notify(conn, tenant_id, event_type, recipient, data=None, correlation_id=None, locale="en"):
    if event_type not in EVENTS:
        return {"queued": 0, "reason": "unknown_event"}
    if not recipient:
        return {"queued": 0, "reason": "no_recipient"}
    data = _sanitize(data or {})
    pol = policy_for(conn, tenant_id, event_type)
    queued = 0
    for ch, mode in pol.items():
        if mode == "OFF":
            continue
        mandatory = (mode == "REQUIRED" and event_type in MANDATORY)
        if not mandatory and _opted_out(conn, tenant_id, recipient, ch):
            _record(conn, tenant_id, event_type, recipient, ch, "SUPPRESSED", None, None, correlation_id, mandatory)
            continue
        if mode == "OPTIONAL" and not _pref_wants(conn, tenant_id, recipient, ch):
            continue
        dedup = hashlib.sha256(f"{tenant_id}:{event_type}:{recipient}:{ch}:{correlation_id or ''}".encode()).hexdigest()[:32]
        if conn.execute("SELECT 1 FROM notifications WHERE dedup_key=?", (dedup,)).fetchone():
            continue   # duplicate prevention
        subj, body = _template(conn, tenant_id, event_type, ch, locale)
        try:
            subj = subj.format(**_safe(data)); body = body.format(**_safe(data))
        except Exception:
            pass
        _record(conn, tenant_id, event_type, recipient, ch, "QUEUED", subj, body, correlation_id, mandatory, dedup)
        queued += 1
    conn.commit()
    return {"queued": queued, "event_type": event_type}


def _pref_wants(conn, tenant_id, recipient, channel):
    # OPTIONAL channels require an explicit opt-in row (opted_out=0 present) OR default to email-only.
    r = conn.execute("SELECT opted_out FROM notify_prefs WHERE recipient=? AND channel=? AND "
                     "(tenant_id=? OR tenant_id IS NULL) LIMIT 1", (recipient, channel, tenant_id)).fetchone()
    if r is not None:
        return not r["opted_out"]
    return channel == "email"   # sensible default: optional email on, optional sms/push off until opted in


def _record(conn, tenant_id, event_type, recipient, channel, status, subject, body, correlation_id, mandatory, dedup=None):
    conn.execute("INSERT INTO notifications(tenant_id,template,event_type,recipient,subject,body,channel,"
                 "status,locale,correlation_id,attempts,max_attempts,mandatory,dedup_key,created_at,updated_at)"
                 " VALUES(?,?,?,?,?,?,?,?, 'en', ?, 0, ?, ?, ?, ?, ?)",
                 (tenant_id, event_type, event_type, recipient, subject, body, channel, status,
                  correlation_id, int(_cfg(conn, "notify.max_attempts", "5") or 5),
                  1 if mandatory else 0, dedup, _now(), _now()))


_SENSITIVE = ("otp", "code", "otp_hash", "secret", "password", "card", "cvv", "bank", "account_no", "pin")


def _sanitize(data):
    return {k: v for k, v in dict(data).items() if not any(s in str(k).lower() for s in _SENSITIVE)}


def _safe(data):
    d = dict(data)
    d.setdefault("ref", d.get("ref", "your booking"))
    return _DefaultDict(d)


class _DefaultDict(dict):
    def __missing__(self, k):
        return ""


def on_event(conn, tenant_id, bus_event_type, data):
    """Bridge from the api_platform event bus. Resolves the notification event + recipient and queues."""
    ev = EVENT_MAP.get(bus_event_type)
    if not ev:
        return {"queued": 0}
    recipient = None
    try:
        ref = (data or {}).get("ref")
        bid = (data or {}).get("booking")
        if bid:
            r = conn.execute("SELECT contact_email,contact_phone,tenant_id FROM mkt_bookings WHERE id=?", (bid,)).fetchone()
        elif ref:
            suffix = str(ref).split("-")[-1].lower()
            r = None
            for row in conn.execute("SELECT contact_email,contact_phone,tracking_token,tenant_id FROM mkt_bookings WHERE tracking_token IS NOT NULL").fetchall():
                if str(row["tracking_token"])[-6:].lower() == suffix:
                    r = row; break
        else:
            r = None
        if r:
            recipient = r["contact_email"] or r["contact_phone"]
            tenant_id = r["tenant_id"] if tenant_id is None else tenant_id
    except Exception:
        recipient = None
    if not recipient:
        return {"queued": 0, "reason": "no_recipient"}
    return notify(conn, tenant_id, ev, recipient, data, correlation_id=(data or {}).get("ref"))


# --------------------------------------------------------------------------- #
# Delivery — honest: no fabricated success when a provider is absent
# --------------------------------------------------------------------------- #
def provider_active(conn, channel):
    return str(_cfg(conn, f"notify.{channel}.provider_active", "false")).lower() == "true"


def deliver_pending(conn, sender_map=None, now=None, max_attempts=None):
    now_iso = now or _now()
    rows = conn.execute("SELECT * FROM notifications WHERE status IN ('QUEUED','RETRYING') AND "
                        "(next_attempt_at IS NULL OR next_attempt_at<=?)", (now_iso,)).fetchall()
    sent = failed = dead = noprov = 0
    for r in rows:
        ch = r["channel"]
        cap = max_attempts or (r["max_attempts"] or 5)
        sender = (sender_map or {}).get(ch)
        if sender is None and not provider_active(conn, ch):
            # honest: no provider -> not delivered. Mandatory notices retry; optional ones fail.
            _fail(conn, r, "provider_unavailable", cap, retry=bool(r["mandatory"]))
            noprov += 1
            continue
        ok = False
        try:
            ok = bool(sender(r["recipient"], r["subject"], r["body"])) if sender else False
        except Exception:
            ok = False
        if ok:
            conn.execute("UPDATE notifications SET status='DELIVERED', provider=?, sent_at=?, updated_at=? WHERE id=?",
                         (ch + ":sender", _now(), _now(), r["id"]))
            sent += 1
        else:
            res = _fail(conn, r, "delivery_failed", cap, retry=True)
            dead += 1 if res == "DEAD_LETTER" else 0
            failed += 1 if res == "RETRYING" else 0
    conn.commit()
    return {"delivered": sent, "retrying": failed, "dead_letter": dead, "no_provider": noprov}


def _fail(conn, r, err, cap, retry):
    attempts = (r["attempts"] or 0) + 1
    if not retry:
        conn.execute("UPDATE notifications SET status='FAILED', attempts=?, last_error=?, updated_at=? WHERE id=?",
                     (attempts, err, _now(), r["id"]))
        return "FAILED"
    if attempts >= cap:
        conn.execute("UPDATE notifications SET status='DEAD_LETTER', attempts=?, last_error=?, updated_at=? WHERE id=?",
                     (attempts, err, _now(), r["id"]))
        return "DEAD_LETTER"
    nxt = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=2 ** attempts)).isoformat(timespec="seconds")
    conn.execute("UPDATE notifications SET status='RETRYING', attempts=?, next_attempt_at=?, last_error=?, updated_at=? WHERE id=?",
                 (attempts, nxt, err, _now(), r["id"]))
    return "RETRYING"


# --------------------------------------------------------------------------- #
# History (admin + customer-safe)
# --------------------------------------------------------------------------- #
def history(conn, actor, limit=200):
    core.require(actor, _VIEW) if core.can(actor, _VIEW) else core.require(actor, _MANAGE)
    frag, params = tenant.predicate(actor)
    rows = conn.execute("SELECT id,event_type,channel,status,attempts,max_attempts,mandatory,created_at,sent_at,last_error "
                        "FROM notifications WHERE event_type IS NOT NULL" + frag +
                        " ORDER BY id DESC LIMIT ?", list(params) + [limit]).fetchall()
    return {"notifications": [dict(r) for r in rows]}   # recipient omitted from list


def customer_history(conn, recipient, limit=50):
    rows = conn.execute("SELECT event_type,channel,status,created_at FROM notifications WHERE recipient=? "
                        "AND event_type IS NOT NULL ORDER BY id DESC LIMIT ?", (recipient, limit)).fetchall()
    return {"recipient": _mask(recipient), "messages": [dict(r) for r in rows]}


def provider_health(conn):
    provs = []
    for ch in ("email", "sms", "push", "whatsapp"):
        active = provider_active(conn, ch)
        provs.append({"channel": ch, "status": "ACTIVE" if active else "NOT_CONFIGURED",
                      "detail": "provider adapter live" if active else "no live adapter — sends fail honestly (never fabricated)"})
    return {"providers": provs}
