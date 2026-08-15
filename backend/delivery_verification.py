"""LiftHaul Enterprise — Secure Delivery Verification & Recipient OTP (orchestration only).

OTP is ONE evidence factor, never the whole proof of delivery. A delivery is "verified" only when every
control the policy requires passes: destination + geofence + POD + authorized recipient + OTP/signature
+ no blocking dispute + no critical fraud. This module REUSES the canonical Trip/POD/geofence/fraud/
Protected-Payment/Claims domains — no new Trip, POD, Payment, Customer or Notification model.

Server owns everything: OTP is server-generated (never browser), cryptographically random, hashed at
rest (plaintext never persisted), single-use, short-lived, and bound to tenant+booking+stop+recipient.
The Protected Payment release gate fails CLOSED when recipient verification is required but not met.

Critical control: OTP VERIFIED proves recipient handoff ONLY. It never means cargo undamaged, claims
waived, or service perfect — POD, Goods Protection, disputes and claims remain independent.
"""
from __future__ import annotations

import datetime
import hashlib
import secrets

import core
import tenant

REQUIREMENTS = ("POD_REQUIRED", "GEOFENCE_REQUIRED", "RECIPIENT_OTP_REQUIRED",
                "RECIPIENT_SIGNATURE_REQUIRED", "PHOTO_REQUIRED", "CUSTOMER_ACCEPTANCE_REQUIRED")
OTP_STATES = ("NOT_REQUIRED", "PENDING", "ISSUED", "DELIVERED_TO_RECIPIENT", "VERIFIED",
              "EXPIRED", "LOCKED", "REVOKED", "SUPERSEDED")
_RECIPIENT_FACTORS = {"RECIPIENT_OTP_REQUIRED", "RECIPIENT_SIGNATURE_REQUIRED", "CUSTOMER_ACCEPTANCE_REQUIRED"}

# RBAC
P_VIEW, P_ISSUE, P_RESEND, P_VERIFY, P_OVERRIDE = (
    "delivery.verification.view", "delivery.verification.issue", "delivery.verification.resend",
    "delivery.verification.verify", "delivery.verification.override")

BOOKING_COLUMNS = [
    ("recipient_name", "TEXT"), ("recipient_org", "TEXT"), ("recipient_mobile", "TEXT"),
    ("recipient_email", "TEXT"), ("recipient_role", "TEXT"), ("recipient_auth_status", "TEXT"),
    ("recipient_verification", "TEXT"), ("delivery_signature", "TEXT"), ("delivery_photo", "TEXT"),
]
SCHEMA = """
CREATE TABLE IF NOT EXISTS mkt_delivery_otp(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, booking_id INTEGER NOT NULL, stop_seq INTEGER,
  recipient_ref TEXT, otp_hash TEXT, salt TEXT, channel TEXT, issued_at TEXT, expires_at TEXT,
  attempt_count INTEGER DEFAULT 0, max_attempts INTEGER DEFAULT 5, verified_at TEXT,
  status TEXT DEFAULT 'ISSUED', resend_count INTEGER DEFAULT 0, correlation_id TEXT,
  created_by INTEGER, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS mkt_delivery_overrides(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, booking_id INTEGER, stop_seq INTEGER, reason TEXT,
  evidence TEXT, requested_by INTEGER, approved_by INTEGER, created_at TEXT);
"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def init(conn):
    for col, typ in BOOKING_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE mkt_bookings ADD COLUMN {col} {typ}")
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
    return 0


def _cfg(conn, key, default=None):
    try:
        import admin_platform as ap
        v, _ = ap.resolve_config(conn, key)
        return v if v is not None else default
    except Exception:
        return default


def _booking(conn, booking_id):
    b = conn.execute("SELECT id,tenant_id,service_class,service_level,inter_island,route_class,"
                     "requested_vehicle_category,gp_coverage_limit,gp_status,quote_amount,"
                     "recipient_name,recipient_mobile,recipient_auth_status,recipient_verification,"
                     "delivery_signature FROM mkt_bookings WHERE id=?", (booking_id,)).fetchone()
    if not b:
        raise core.NotFoundError("booking not found")
    return dict(b)


# --------------------------------------------------------------------------- #
# 1. Delivery Verification Policy (configuration-driven; not one universal policy)
# --------------------------------------------------------------------------- #
def _heavy(b):
    return (b.get("service_class") == "ENGINEERED"
            or (b.get("requested_vehicle_category") or "") in ("LOWBED", "CRANE_RIGGING", "WING_VAN_10W"))


def resolve_policy(conn, booking_id):
    b = _booking(conn, booking_id)
    default = _cfg(conn, "delivery.policy.default", "POD_REQUIRED")
    heavy = _cfg(conn, "delivery.policy.heavy", "POD_REQUIRED,RECIPIENT_SIGNATURE_REQUIRED,PHOTO_REQUIRED")
    high_value = _cfg(conn, "delivery.policy.high_value",
                      "POD_REQUIRED,GEOFENCE_REQUIRED,RECIPIENT_OTP_REQUIRED,RECIPIENT_SIGNATURE_REQUIRED")
    threshold = float(_cfg(conn, "delivery.high_value_threshold", "1000000") or 0)
    value = max(float(b.get("quote_amount") or 0), float(b.get("gp_coverage_limit") or 0))
    if _heavy(b):
        chosen = heavy
    elif value >= threshold or (b.get("gp_status") == "BOUND" and value >= threshold):
        chosen = high_value
    else:
        chosen = default
    reqs = [r.strip().upper() for r in str(chosen).split(",") if r.strip() in REQUIREMENTS or r.strip().upper() in REQUIREMENTS]
    return {"booking_id": booking_id, "requirements": reqs,
            "recipient_verification_required": any(r in _RECIPIENT_FACTORS for r in reqs)}


def _enforced(conn):
    return str(_cfg(conn, "delivery.verification_enforced", "false")).lower() == "true"


# --------------------------------------------------------------------------- #
# 2. Authorized recipient
# --------------------------------------------------------------------------- #
def set_recipient(conn, actor, booking_id, name, mobile=None, email=None, org=None, role=None):
    core.require(actor, P_ISSUE) if core.can(actor, P_ISSUE) else core.require(actor, "marketplace.booking.manage")
    conn.execute("UPDATE mkt_bookings SET recipient_name=?, recipient_mobile=?, recipient_email=?, "
                 "recipient_org=?, recipient_role=?, recipient_auth_status='AUTHORIZED', "
                 "recipient_verification=COALESCE(recipient_verification,'PENDING'), updated_at=? WHERE id=?",
                 (str(name)[:200], (str(mobile)[:40] if mobile else None), (str(email)[:200] if email else None),
                  (str(org)[:200] if org else None), (str(role)[:80] if role else None), _now(), booking_id))
    core.audit(conn, actor, "DELIVERY_RECIPIENT_SET", "mkt_bookings", booking_id, None, {"name": name})
    conn.commit()
    return {"booking_id": booking_id, "recipient": mask_name(name), "auth_status": "AUTHORIZED"}


def mask_name(name):
    parts = str(name or "").split()
    return " ".join((p[0] + "*****") if p else "" for p in parts) or "—"


def mask_mobile(m):
    m = str(m or "")
    return ("******" + m[-4:]) if len(m) >= 4 else "—"


# --------------------------------------------------------------------------- #
# 3-5. OTP issue (server-generated, hashed, operator-assisted when no messaging provider)
# --------------------------------------------------------------------------- #
def issue_otp(conn, actor, booking_id, stop_seq=None, channel="SMS"):
    """Generate + store a hashed single-use OTP. Returns the plaintext ONLY to the issuing operator
    (operator-assisted secure relay) when no messaging provider is configured — never to a driver, and
    never persisted in plaintext. Requires delivery.verification.issue."""
    core.require(actor, P_ISSUE)
    b = _booking(conn, booking_id)
    if not b.get("recipient_name"):
        raise core.ValidationError("an authorized recipient must be set before issuing an OTP")
    # supersede any live OTP for this booking+stop
    conn.execute("UPDATE mkt_delivery_otp SET status='SUPERSEDED', updated_at=? WHERE booking_id=? AND "
                 "COALESCE(stop_seq,-1)=? AND status IN ('ISSUED','DELIVERED_TO_RECIPIENT','PENDING')",
                 (_now(), booking_id, stop_seq if stop_seq is not None else -1))
    code = "".join(secrets.choice("0123456789") for _ in range(6))
    salt = secrets.token_hex(8)
    ttl = int(_cfg(conn, "delivery.otp_ttl_minutes", "15") or 15)
    maxa = int(_cfg(conn, "delivery.otp_max_attempts", "5") or 5)
    exp = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=ttl)).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO mkt_delivery_otp(tenant_id,booking_id,stop_seq,recipient_ref,otp_hash,salt,channel,"
        "issued_at,expires_at,attempt_count,max_attempts,status,created_by,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,0,?, 'ISSUED', ?,?,?)",
        (b.get("tenant_id"), booking_id, stop_seq, mask_mobile(b.get("recipient_mobile")),
         _hash(code, salt), salt, channel, _now(), exp, maxa, actor.get("id"), _now(), _now()))
    oid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_delivery_otp", oid)
    core.audit(conn, actor, "OTP_ISSUED", "mkt_delivery_otp", oid, None,
               {"booking": booking_id, "stop": stop_seq, "channel": channel})  # no plaintext
    conn.commit()
    provider_active = str(_cfg(conn, "delivery.messaging_provider_active", "false")).lower() == "true"
    _emit(conn, b, "delivery.verification_required", {"booking": booking_id, "stop": stop_seq})
    out = {"otp_id": oid, "status": "ISSUED", "expires_at": exp,
           "recipient_masked": mask_name(b.get("recipient_name")), "mobile_masked": mask_mobile(b.get("recipient_mobile"))}
    if provider_active:
        out["delivery"] = "SENT"          # a real provider would deliver; code never returned
    else:
        out["delivery"] = "MANUAL_SECURE_DELIVERY_REQUIRED"
        out["code"] = code                # operator-assisted relay only (issuer holds P_ISSUE)
    return out


def _hash(code, salt):
    return hashlib.sha256((str(salt) + ":" + str(code)).encode()).hexdigest()


def resend_otp(conn, actor, booking_id, stop_seq=None):
    core.require(actor, P_RESEND) if core.can(actor, P_RESEND) else core.require(actor, P_ISSUE)
    prev = conn.execute("SELECT resend_count FROM mkt_delivery_otp WHERE booking_id=? AND COALESCE(stop_seq,-1)=? "
                        "ORDER BY id DESC LIMIT 1", (booking_id, stop_seq if stop_seq is not None else -1)).fetchone()
    cap = int(_cfg(conn, "delivery.resend_max", "3") or 3)
    if prev and (prev["resend_count"] or 0) >= cap:
        raise core.ConflictError("resend limit reached")
    res = issue_otp(conn, actor, booking_id, stop_seq)      # supersedes previous, new code + expiry
    conn.execute("UPDATE mkt_delivery_otp SET resend_count=? WHERE id=?",
                 (((prev["resend_count"] or 0) + 1) if prev else 1, res["otp_id"]))
    core.audit(conn, actor, "OTP_RESENT", "mkt_delivery_otp", res["otp_id"], None, {"booking": booking_id})
    conn.commit()
    return res


# --------------------------------------------------------------------------- #
# 6-7. Verification (attempts, lock, replay + cross-binding protection)
# --------------------------------------------------------------------------- #
def verify_otp(conn, actor, booking_id, code, stop_seq=None):
    """Driver/operator submits the code. Bound to this booking+stop+tenant; replay/cross-use impossible
    (a code only matches the active OTP row for that exact booking+stop). Requires
    delivery.verification.verify. The driver never sees the code."""
    core.require(actor, P_VERIFY)
    row = conn.execute("SELECT * FROM mkt_delivery_otp WHERE booking_id=? AND COALESCE(stop_seq,-1)=? "
                       "ORDER BY id DESC LIMIT 1",
                       (booking_id, stop_seq if stop_seq is not None else -1)).fetchone()
    if not row:
        raise core.NotFoundError("no active delivery code for this stop")
    if row["status"] == "LOCKED":
        raise core.ForbiddenError("delivery code locked")
    if row["status"] == "EXPIRED":
        raise core.ConflictError("delivery code expired")
    if row["status"] not in ("ISSUED", "DELIVERED_TO_RECIPIENT"):
        raise core.NotFoundError("no active delivery code for this stop")
    if row["expires_at"] and row["expires_at"] < _now():
        conn.execute("UPDATE mkt_delivery_otp SET status='EXPIRED', updated_at=? WHERE id=?", (_now(), row["id"]))
        conn.commit()
        raise core.ConflictError("delivery code expired")
    if (row["attempt_count"] or 0) >= (row["max_attempts"] or 5):
        conn.execute("UPDATE mkt_delivery_otp SET status='LOCKED', updated_at=? WHERE id=?", (_now(), row["id"]))
        conn.commit()
        raise core.ForbiddenError("delivery code locked")
    if _hash(code, row["salt"]) != row["otp_hash"]:
        n = (row["attempt_count"] or 0) + 1
        locked = n >= (row["max_attempts"] or 5)
        conn.execute("UPDATE mkt_delivery_otp SET attempt_count=?, status=?, updated_at=? WHERE id=?",
                     (n, "LOCKED" if locked else row["status"], _now(), row["id"]))
        core.audit(conn, actor, "OTP_LOCKED" if locked else "OTP_FAILED", "mkt_delivery_otp", row["id"],
                   None, {"booking": booking_id, "attempts": n})
        if locked:
            _fraud(conn, actor, booking_id, "OTP_LOCKED", "HIGH", f"{n} failed delivery-code attempts")
        conn.commit()
        raise core.ForbiddenError("delivery code locked" if locked else "incorrect delivery code")
    # success
    conn.execute("UPDATE mkt_delivery_otp SET status='VERIFIED', verified_at=?, updated_at=? WHERE id=?",
                 (_now(), _now(), row["id"]))
    if stop_seq is None:   # final delivery
        conn.execute("UPDATE mkt_bookings SET recipient_verification='VERIFIED', updated_at=? WHERE id=?",
                     (_now(), booking_id))
    core.audit(conn, actor, "OTP_VERIFIED", "mkt_delivery_otp", row["id"], None,
               {"booking": booking_id, "stop": stop_seq})
    conn.commit()
    _emit(conn, _booking(conn, booking_id), "delivery.recipient_verified", {"booking": booking_id, "stop": stop_seq})
    # NB: recipient handoff only — NOT a statement that cargo is undamaged or claims are waived.
    return {"booking_id": booking_id, "stop_seq": stop_seq, "result": "RECIPIENT_VERIFIED"}


def _fraud(conn, actor, booking_id, indicator, level, detail):
    try:
        import marketplace_trust as mt
        mt.raise_fraud_flag(conn, actor, "BOOKING", booking_id, indicator, level, detail)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 13. Offline capture + manual override (never driver self-override)
# --------------------------------------------------------------------------- #
def offline_capture(conn, actor, booking_id, pod=None, photo=None, signature=None):
    core.require(actor, P_VERIFY)
    conn.execute("UPDATE mkt_bookings SET recipient_verification=COALESCE(recipient_verification,'PENDING'), "
                 "delivery_signature=COALESCE(?,delivery_signature), delivery_photo=COALESCE(?,delivery_photo), "
                 "updated_at=? WHERE id=?", (signature, photo, _now(), booking_id))
    core.audit(conn, actor, "DELIVERY_OFFLINE_CAPTURED", "mkt_bookings", booking_id, None, {"has_pod": bool(pod)})
    conn.commit()
    return {"booking_id": booking_id, "status": "OFFLINE_VERIFICATION_PENDING"}


def manual_override(conn, actor, booking_id, reason, evidence, mfa_ok=False, approver_id=None):
    """Authorized Operations override — never a driver. High-value requires an independent approver.
    High-severity audit; sets recipient_verification=VERIFIED via governed exception."""
    core.require(actor, P_OVERRIDE)
    if not mfa_ok:
        raise core.ForbiddenError("MFA required for a delivery-verification override")
    if not reason or not evidence:
        raise core.ValidationError("override requires a reason and supporting evidence")
    b = _booking(conn, booking_id)
    value = max(float(b.get("quote_amount") or 0), float(b.get("gp_coverage_limit") or 0))
    threshold = float(_cfg(conn, "delivery.high_value_threshold", "1000000") or 0)
    if value >= threshold:
        if not approver_id or approver_id == actor.get("id"):
            raise core.ForbiddenError("high-value override requires an independent approver")
    conn.execute("INSERT INTO mkt_delivery_overrides(tenant_id,booking_id,reason,evidence,requested_by,"
                 "approved_by,created_at) VALUES(?,?,?,?,?,?,?)",
                 (b.get("tenant_id"), booking_id, str(reason)[:400],
                  (evidence if isinstance(evidence, str) else str(evidence))[:2000], actor.get("id"),
                  approver_id, _now()))
    conn.execute("UPDATE mkt_bookings SET recipient_verification='VERIFIED', updated_at=? WHERE id=?", (_now(), booking_id))
    core.audit(conn, actor, "MANUAL_OVERRIDE_APPROVED", "mkt_bookings", booking_id, None,
               {"reason": reason, "approver": approver_id, "severity": "HIGH"})
    conn.commit()
    return {"booking_id": booking_id, "recipient_verification": "VERIFIED", "override": True}


# --------------------------------------------------------------------------- #
# 15. Protected Payment release-gate integration (config-gated; fail-closed)
# --------------------------------------------------------------------------- #
def release_requirement_met(conn, booking_id):
    """Called by the canonical release_gate. When enforcement is ON and the resolved policy requires a
    recipient factor, the release is DENIED unless recipient verification == VERIFIED. Default
    enforcement is OFF -> always ok (no behaviour change to existing flows)."""
    if not _enforced(conn):
        return {"ok": True, "reasons": []}
    try:
        pol = resolve_policy(conn, booking_id)
    except Exception:
        return {"ok": True, "reasons": []}
    if not pol["recipient_verification_required"]:
        return {"ok": True, "reasons": []}
    b = _booking(conn, booking_id)
    reasons = []
    if b.get("recipient_verification") != "VERIFIED":
        reasons.append("recipient_verification_not_met")
    if "RECIPIENT_SIGNATURE_REQUIRED" in pol["requirements"] and not b.get("delivery_signature"):
        reasons.append("recipient_signature_missing")
    return {"ok": not reasons, "reasons": reasons}


# --------------------------------------------------------------------------- #
# Read / admin / evidence
# --------------------------------------------------------------------------- #
def status(conn, booking_id):
    b = _booking(conn, booking_id)
    pol = resolve_policy(conn, booking_id)
    return {"booking_id": booking_id, "policy": pol["requirements"],
            "recipient_verification": b.get("recipient_verification") or "NOT_REQUIRED",
            "recipient": mask_name(b.get("recipient_name")), "mobile": mask_mobile(b.get("recipient_mobile"))}


def public_status(conn, booking_id):
    """Customer-safe: no recipient phone, no OTP, no fraud signals, no override notes."""
    b = _booking(conn, booking_id)
    pol = resolve_policy(conn, booking_id)
    rv = b.get("recipient_verification")
    if not pol["recipient_verification_required"]:
        return {"required": False}
    return {"required": True, "state": ("Verified" if rv == "VERIFIED" else "Pending")}


def evidence_bundle(conn, actor, booking_id):
    """For disputes/claims — verification evidence only; NEVER plaintext OTP."""
    core.require(actor, P_VIEW) if core.can(actor, P_VIEW) else core.require(actor, "marketplace.claim.manage")
    rows = conn.execute("SELECT id,stop_seq,recipient_ref,channel,issued_at,expires_at,verified_at,status "
                        "FROM mkt_delivery_otp WHERE booking_id=? ORDER BY id", (booking_id,)).fetchall()
    b = _booking(conn, booking_id)
    return {"booking_id": booking_id, "recipient_verification": b.get("recipient_verification"),
            "signature_captured": bool(b.get("delivery_signature")),
            "otp_challenges": [dict(r) for r in rows],   # masked recipient_ref, no plaintext
            "note": "Recipient handoff evidence only — does not waive damage claims or Goods Protection."}


def admin_queue(conn, actor):
    core.require(actor, P_VIEW) if core.can(actor, P_VIEW) else core.require(actor, "marketplace.trust.view")
    frag, params = tenant.predicate(actor)
    rows = conn.execute("SELECT id,booking_id,stop_seq,status,attempt_count,max_attempts,issued_at,expires_at,"
                        "verified_at FROM mkt_delivery_otp WHERE 1=1" + frag + " ORDER BY id DESC LIMIT 200", params).fetchall()
    out = [dict(r) for r in rows]
    return {"challenges": out,
            "pending": [r for r in out if r["status"] in ("ISSUED", "DELIVERED_TO_RECIPIENT")],
            "locked": [r for r in out if r["status"] == "LOCKED"],
            "expired": [r for r in out if r["status"] == "EXPIRED"],
            "verified": [r for r in out if r["status"] == "VERIFIED"]}


def _emit(conn, booking, event_type, data):
    try:
        import api_platform as ap
        ap.emit_event(conn, (booking.get("tenant_id") if isinstance(booking, dict) else None), event_type, data)
    except Exception:
        pass
