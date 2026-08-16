"""Public Service-Provider intake — the front door to the Service Provider & Fleet Registration
Workspace. A provider self-registers its COMPANY here, sets a login credential, verifies its contact
via a one-time code, and lands in its own workspace to register vehicles / equipment / drivers.

Everything reuses canonical domains — no new carrier, vehicle, driver, equipment, KYB or compliance
domain, and no new identity system:

  * the provider record is the canonical `mkt_carriers` application (status APPLICATION), created via a
    platform service actor exactly like public booking;
  * the login is a canonical `users` row (role `carrier_principal`) created by `core.create_user`, held
    INACTIVE (status `PENDING_VERIFICATION`) until the contact one-time code is verified, then ACTIVE;
  * portal access is the canonical `carrier_principals` binding (`carrier_portal.bind_principal`) — the
    login resolves to its OWN carrier only, never client-supplied;
  * the one-time code is server-generated, hashed at rest (`core.hash_pw`), single-use and short-lived,
    the same discipline as the delivery-verification OTP.

Governance held here: a provider self-registers and manages its own DRAFT fleet, but it never
self-verifies regulated documents and is NOT marketplace-eligible — staff verify KYB/documents/LTFRB
independently. Contact-code delivery is honest: with no messaging provider connected the code is not
faked as "sent"; in non-production the code is surfaced so the flow is testable end to end.

This module also exposes the master-data vehicle taxonomy and the classification engine as READ-ONLY
public previews so the registration UI can show the canonical variant live.
"""
from __future__ import annotations

import os
import secrets

import core
import fleet_registration as fr


PROVIDER_TYPES = (
    "OWNER_OPERATOR", "TRUCK_OWNER", "FLEET_OPERATOR", "HAULING_COMPANY", "LOGISTICS_PROVIDER",
    "HEAVY_HAUL_COMPANY", "CRANE_COMPANY", "RIGGING_CONTRACTOR", "EQUIPMENT_RENTAL",
    "MOTORCYCLE_DELIVERY", "VAN_OPERATOR", "COURIER_LOGISTICS", "SUBCONTRACTOR", "SPECIALIZED_TRANSPORT",
)

_CODE_TTL_SECONDS = 15 * 60
_MAX_ATTEMPTS = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_signup(
  id INTEGER PRIMARY KEY, carrier_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  login TEXT NOT NULL, channel TEXT, destination TEXT,
  code_hash TEXT NOT NULL, issued_at TEXT, expires_at TEXT,
  attempt_count INTEGER DEFAULT 0, status TEXT NOT NULL DEFAULT 'PENDING',
  verified_at TEXT, created_at TEXT);
"""


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    return  # nothing to seed; signups are created by the public


# --------------------------------------------------------------------------- #
# service actor + helpers
# --------------------------------------------------------------------------- #
def _service_actor():
    """Platform-scoped system actor for public intake — never derived from public input."""
    import admin_platform as ap
    return {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": getattr(ap, "PLATFORM_TENANT", 0)}


def _carrier_type(provider_type):
    p = (provider_type or "").upper()
    if "OWNER" in p or "INDIVIDUAL" in p:
        return "OWNER_OPERATOR"
    if "SOLE" in p:
        return "SOLE_PROPRIETOR"
    if "PARTNER" in p:
        return "PARTNERSHIP"
    return "CORPORATION"


# --------------------------------------------------------------------------- #
# Environment posture — FAIL CLOSED. The one-time code may only ever be surfaced
# in an explicitly-declared development/test environment. An ambiguous, unknown,
# empty, staging or production APP_ENV is treated as production for every security
# decision, so a mis-set or missing environment can NEVER behave like development.
# --------------------------------------------------------------------------- #
_DEV_ENVS = frozenset({"development", "dev", "local", "test", "testing"})
_KNOWN_ENVS = _DEV_ENVS | frozenset({"production", "prod", "staging", "stage", "ci"})


def _env():
    return os.environ.get("APP_ENV", "").strip().lower()


def _dev_code_allowed():
    """True ONLY when APP_ENV is an explicitly recognised development/test value.
    Missing/unknown/staging/production -> False (never leak the code)."""
    return _env() in _DEV_ENVS


def _is_production():
    """Production posture for messaging/UX wording: anything that is NOT an explicit
    dev/test environment is treated as production (fail closed)."""
    return not _dev_code_allowed()


def _capture_enabled():
    """In-process test capture of the plaintext code for the acceptance-lifecycle harness.
    Opt-in ONLY via OTP_TEST_CAPTURE=1 — never on by default, never exposed over HTTP,
    and never read unless the harness explicitly asks. Real production leaves this unset."""
    return os.environ.get("OTP_TEST_CAPTURE", "") == "1"


# module-local capture sink: {challenge_id: code}. Populated only when _capture_enabled().
_CODE_CAPTURE = {}


def env_posture():
    """Startup/report helper: the resolved posture and whether the env value is recognised."""
    e = _env()
    return {"app_env": e or "(unset)", "recognised": e in _KNOWN_ENVS,
            "dev_code_allowed": _dev_code_allowed(), "treated_as_production": _is_production()}


def _seconds_from(iso, seconds):
    import datetime
    try:
        base = datetime.datetime.fromisoformat(iso)
    except Exception:
        base = datetime.datetime.now(datetime.timezone.utc)
    return (base + datetime.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _expired(iso):
    import datetime
    try:
        exp = datetime.datetime.fromisoformat(iso)
    except Exception:
        return True
    now = datetime.datetime.now(datetime.timezone.utc)
    if exp.tzinfo is None:
        now = now.replace(tzinfo=None)
    return now >= exp


def _mask(dest):
    d = str(dest or "")
    if "@" in d:
        name, _, dom = d.partition("@")
        return (name[:2] + "***@" + dom) if name else ("***@" + dom)
    return ("***" + d[-3:]) if len(d) >= 3 else "***"


# --------------------------------------------------------------------------- #
# 1-2. company + credential registration -> contact-code challenge (no token yet)
# --------------------------------------------------------------------------- #
def submit(conn, payload):
    """Create the provider (carrier) APPLICATION + a PENDING login, then issue a one-time contact code.
    Returns a challenge reference; NO session token is issued until the code is verified. Compliance
    verification + marketplace eligibility remain staff-gated (a provider never self-verifies)."""
    if not isinstance(payload, dict):
        raise core.ValidationError("invalid payload")
    legal_name = str(payload.get("legal_name", "")).strip()
    if not legal_name:
        raise core.ValidationError("legal business name is required")
    email = str(payload.get("email", "")).strip()
    mobile = str(payload.get("mobile", "")).strip()
    if not (email or mobile):
        raise core.ValidationError("a contact mobile or email is required")
    login = str(payload.get("username", "") or email).strip().lower()
    if not login:
        raise core.ValidationError("a username (or email) is required to create your login")
    password = str(payload.get("password", ""))
    if len(password) < 8:
        raise core.ValidationError("password must be at least 8 characters")

    provider_type = str(payload.get("provider_type", "") or "FLEET_OPERATOR").strip().upper()
    actor = _service_actor()

    # --- canonical carrier APPLICATION (reuses marketplace onboarding; no new domain) ---
    attrs = {
        "trade_name": str(payload.get("trade_name", ""))[:200] or None,
        "registration_type": str(payload.get("registration_type", "") or "DTI")[:20],
        "registration_number": str(payload.get("registration_number", ""))[:80] or None,
        "tax_id": str(payload.get("tin", ""))[:40] or None,
        "operating_address": str(payload.get("operating_address", payload.get("province", "")))[:400] or None,
        "contacts": {"mobile": mobile[:40], "email": email[:200],
                     "website": str(payload.get("website", ""))[:200],
                     "representative": str(payload.get("representative", ""))[:200]},
        "service_areas": payload.get("service_areas") or ([payload.get("island_group")] if payload.get("island_group") else []),
        "cargo_capabilities": payload.get("capabilities") or [],
        "risk_status": "UNVERIFIED",
    }
    cid = mo_create(conn, actor, _carrier_type(provider_type), legal_name, attrs)

    # --- canonical login (reuses core.users), held INACTIVE until contact is verified ---
    try:
        uid = core.create_user(conn, login, password, "carrier_principal",
                               name=(attrs["trade_name"] or legal_name))
    except core.ConflictError:
        raise core.ConflictError("that username/email is already registered — please sign in instead")
    conn.execute("UPDATE users SET status='PENDING_VERIFICATION' WHERE id=?", (uid,))

    # --- canonical portal binding (login resolves to its OWN carrier only) ---
    import carrier_portal as cp
    cp.bind_principal(conn, actor, uid, cid, portal_role="CARRIER_OWNER")

    core.audit(conn, actor, "PUBLIC_PROVIDER_APPLIED", "mkt_carriers", cid, None,
               {"provider_type": provider_type, "legal_name": legal_name, "user_id": uid,
                "source": "PUBLIC_SIGNUP"})

    # --- one-time contact code: server-generated, hashed at rest, single-use, short-lived ---
    challenge = _issue_code(conn, actor, cid, uid, login, email, mobile)
    conn.commit()

    resp = {"ref": f"SP-{cid}", "carrier_id": cid, "user_id": uid, "login": login,
            "provider_type": provider_type, "status": "VERIFY_CONTACT",
            "challenge_id": challenge["challenge_id"], "channel": challenge["channel"],
            "destination": _mask(challenge["destination"]),
            "delivered": challenge["delivered"], "delivery_note": challenge["delivery_note"],
            "next": ("Enter the one-time code we sent to your contact to activate your login and open your "
                     "Fleet Workspace. Your business documents and LTFRB authority are verified by our team "
                     "afterward — you are not marketplace-eligible until verification completes.")}
    if challenge.get("dev_code"):
        resp["dev_code"] = challenge["dev_code"]   # non-production only, for testable end-to-end flow
    return resp


def _issue_code(conn, actor, carrier_id, user_id, login, email, mobile):
    channel = "email" if email else "sms"
    destination = email or mobile
    code = f"{secrets.randbelow(1000000):06d}"
    now = core.now()
    conn.execute("UPDATE provider_signup SET status='SUPERSEDED' WHERE user_id=? AND status='PENDING'",
                 (user_id,))
    conn.execute("INSERT INTO provider_signup(carrier_id,user_id,login,channel,destination,code_hash,"
                 "issued_at,expires_at,attempt_count,status,created_at) "
                 "VALUES(?,?,?,?,?,?,?,?,0,'PENDING',?)",
                 (carrier_id, user_id, login, channel, destination, core.hash_pw(code),
                  now, _seconds_from(now, _CODE_TTL_SECONDS), now))
    cid_row = conn.execute("SELECT id FROM provider_signup WHERE user_id=? AND status='PENDING' "
                           "ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    challenge_id = cid_row["id"]
    delivered, note = _deliver_code(conn, channel, destination, code)
    # NOTE: the plaintext code is NEVER written to the audit ledger or any log.
    core.audit(conn, actor, "PROVIDER_SIGNUP_CODE_ISSUED", "provider_signup", challenge_id, None,
               {"channel": channel, "destination": _mask(destination), "delivered": delivered})
    out = {"challenge_id": challenge_id, "channel": channel, "destination": destination,
           "delivered": delivered, "delivery_note": note}
    # In-process test capture (opt-in only) so the acceptance harness can complete the flow
    # without a live provider. Never reachable over HTTP; unset in real production.
    if _capture_enabled():
        _CODE_CAPTURE[challenge_id] = code
    # dev_code is returned ONLY in an explicitly-declared dev/test environment.
    if _dev_code_allowed():
        out["dev_code"] = code
    return out


def _deliver_code(conn, channel, destination, code):
    """Honest delivery: only claims 'sent' if a messaging provider is actually connected. With none
    connected the code is NOT faked as delivered. In production with no provider the account stays
    PENDING_VERIFICATION and the caller surfaces VERIFICATION DELIVERY UNAVAILABLE — it fails closed."""
    if _provider_active(conn, channel):
        return True, f"A one-time code was sent to your {channel}."
    if _dev_code_allowed():
        return False, "Development environment: no messaging provider connected; use the code shown for testing."
    return False, ("VERIFICATION DELIVERY UNAVAILABLE — no messaging provider is connected, so the code "
                   "could not be delivered. Your account stays pending; contact support to verify your contact.")


def _provider_active(conn, channel):
    """True only if a real messaging provider is connected for this channel (default OFF).
    The seam a real email/SMS provider plugs into via the notification engine."""
    try:
        import notifications_engine as ne
        fn = getattr(ne, "provider_active", None)
        return bool(fn(conn, channel)) if callable(fn) else False
    except Exception:
        return False


def peek_code(conn, challenge_id):
    """TEST-ONLY, in-process: return the plaintext code for a challenge so the acceptance-lifecycle
    harness can complete verification without a live provider. Returns None unless OTP_TEST_CAPTURE=1.
    There is NO HTTP route to this; it is never callable by a public client."""
    if not _capture_enabled():
        return None
    return _CODE_CAPTURE.get(int(challenge_id))


# --------------------------------------------------------------------------- #
# 3. verify the contact code -> activate login + issue a session token (land in workspace)
# --------------------------------------------------------------------------- #
def verify(conn, payload):
    """Verify the one-time contact code. On success the login is activated and a session token is issued
    so the provider lands directly in its Fleet Workspace. Single-use, attempt-limited, expiry-checked."""
    if not isinstance(payload, dict):
        raise core.ValidationError("invalid payload")
    code = str(payload.get("code", "")).strip()
    if not code:
        raise core.ValidationError("the one-time code is required")
    challenge_id = payload.get("challenge_id")
    login = str(payload.get("username", "") or payload.get("login", "")).strip().lower()
    if challenge_id:
        row = conn.execute("SELECT * FROM provider_signup WHERE id=?", (challenge_id,)).fetchone()
    elif login:
        row = conn.execute("SELECT * FROM provider_signup WHERE login=? AND status='PENDING' "
                           "ORDER BY id DESC LIMIT 1", (login,)).fetchone()
    else:
        raise core.ValidationError("challenge_id or username is required")
    if not row or row["status"] != "PENDING":
        raise core.ValidationError("no pending verification found — please register again")
    if _expired(row["expires_at"]):
        conn.execute("UPDATE provider_signup SET status='EXPIRED' WHERE id=?", (row["id"],))
        conn.commit()
        raise core.ValidationError("the code has expired — please request a new one")
    if row["attempt_count"] >= _MAX_ATTEMPTS:
        conn.execute("UPDATE provider_signup SET status='LOCKED' WHERE id=?", (row["id"],))
        conn.commit()
        raise core.ValidationError("too many attempts — this verification is locked; please register again")
    if not core.verify_pw(code, row["code_hash"]):
        conn.execute("UPDATE provider_signup SET attempt_count=attempt_count+1 WHERE id=?", (row["id"],))
        conn.commit()
        remaining = _MAX_ATTEMPTS - (row["attempt_count"] + 1)
        raise core.ValidationError(f"incorrect code — {max(remaining, 0)} attempt(s) remaining")

    now = core.now()
    conn.execute("UPDATE provider_signup SET status='VERIFIED', verified_at=? WHERE id=?", (now, row["id"]))
    conn.execute("UPDATE users SET status='ACTIVE' WHERE id=?", (row["user_id"],))
    token = secrets.token_urlsafe(24)
    conn.execute("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)", (token, row["user_id"], now))
    conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (now, row["user_id"]))
    core.audit(conn, _service_actor(), "PROVIDER_SIGNUP_VERIFIED", "provider_signup", row["id"], None,
               {"user_id": row["user_id"], "carrier_id": row["carrier_id"]})
    conn.commit()
    return {"token": token, "role": "carrier_principal", "carrier_id": row["carrier_id"],
            "ref": f"SP-{row['carrier_id']}", "status": "ACTIVE", "redirect": "portal.html",
            "message": "Contact verified. Your login is active — welcome to your Fleet Workspace."}


def resend(conn, payload):
    """Re-issue a one-time code for a still-pending signup (same login, new code)."""
    if not isinstance(payload, dict):
        raise core.ValidationError("invalid payload")
    challenge_id = payload.get("challenge_id")
    login = str(payload.get("username", "") or payload.get("login", "")).strip().lower()
    if challenge_id:
        row = conn.execute("SELECT * FROM provider_signup WHERE id=?", (challenge_id,)).fetchone()
    elif login:
        row = conn.execute("SELECT * FROM provider_signup WHERE login=? AND status='PENDING' "
                           "ORDER BY id DESC LIMIT 1", (login,)).fetchone()
    else:
        raise core.ValidationError("challenge_id or username is required")
    if not row or row["status"] not in ("PENDING", "EXPIRED"):
        raise core.ValidationError("no pending verification found — please register again")
    u = conn.execute("SELECT email FROM users WHERE id=?", (row["user_id"],)).fetchone()
    ch = _issue_code(conn, _service_actor(), row["carrier_id"], row["user_id"], row["login"],
                     row["destination"] if row["channel"] == "email" else "",
                     row["destination"] if row["channel"] == "sms" else "")
    conn.commit()
    out = {"challenge_id": ch["challenge_id"], "channel": ch["channel"],
           "destination": _mask(ch["destination"]), "delivered": ch["delivered"],
           "delivery_note": ch["delivery_note"], "status": "VERIFY_CONTACT"}
    if ch.get("dev_code"):
        out["dev_code"] = ch["dev_code"]
    return out


def mo_create(conn, actor, carrier_type, legal_name, attrs):
    import marketplace_onboarding as mo
    return mo.create_carrier_application(conn, actor, carrier_type, legal_name, **attrs)


# --------------------------------------------------------------------------- #
# read-only public previews (taxonomy + classification)
# --------------------------------------------------------------------------- #
def variants(conn):
    """Public read of the master-data vehicle-variant taxonomy (category -> variant), for the registration
    UI's category/variant pickers. Read-only, no auth, no writes."""
    rows = conn.execute("SELECT category,variant_code,variant_name FROM vehicle_variants WHERE active=1 "
                        "AND tenant_id IS NULL ORDER BY category, priority DESC, variant_code").fetchall()
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append({"code": r["variant_code"], "name": r["variant_name"]})
    return {"provider_types": list(PROVIDER_TYPES), "categories": by_cat}


def classify_preview(conn, specs):
    """Public, READ-ONLY classification preview: provider enters physical specs, LiftHaul returns the
    canonical variant + tonnage class. Never writes; never registers anything."""
    if not isinstance(specs, dict):
        raise core.ValidationError("invalid specs")
    try:
        return fr.classify(conn, specs, tenant_id=None)
    except core.ValidationError as e:
        return {"error": str(e), "classified": False}
