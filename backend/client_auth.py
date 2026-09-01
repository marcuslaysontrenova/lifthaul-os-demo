"""Client authentication recovery and non-production QA workspace support.

Login/session enforcement stays in the platform authentication services.  This
module adds client-facing identifier resolution and one-time password recovery;
reset tokens are persisted only as SHA-256 digests and revoke every active
session after use.
"""
from __future__ import annotations

import datetime
import hashlib
import os
import secrets

import admin_platform
import core


SCHEMA = """
CREATE TABLE IF NOT EXISTS client_password_resets(
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  token_hash TEXT UNIQUE NOT NULL,
  requested_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  requested_ip TEXT);
"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(value):
    return value.isoformat(timespec="seconds")


def init(conn):
    conn.executescript(SCHEMA)
    admin_platform._ensure_columns(conn, "users", {
        "mobile": "TEXT",
        "verified_at": "TEXT",
        "password_reset_required": "INTEGER NOT NULL DEFAULT 0",
    })
    conn.commit()


def resolve_identifier(conn, identifier):
    """Resolve an email address or Philippine mobile number to a user email."""
    value = str(identifier or "").strip().lower()
    if not value:
        raise core.ValidationError("email address or mobile number is required")
    if "@" in value:
        return value
    digits = "".join(ch for ch in value if ch.isdigit())
    if digits.startswith("63") and len(digits) == 12:
        digits = "0" + digits[2:]
    row = conn.execute("SELECT email FROM users WHERE mobile=?", (digits,)).fetchone()
    # Deliberately return a non-matching identifier when unknown so guarded_login
    # keeps one generic invalid-credentials response and does not enumerate users.
    return row["email"].lower() if row else value


def request_password_reset(conn, identifier, requested_ip=None, reveal_token=False):
    """Create a single-use reset request without revealing account existence."""
    email = resolve_identifier(conn, identifier)
    row = conn.execute("SELECT id,status FROM users WHERE email=?", (email,)).fetchone()
    response = {"message": "If the account is eligible, password-reset instructions have been issued."}
    if not row or str(row["status"] or "").upper() not in ("ACTIVE", "PENDING", "UNVERIFIED"):
        return response
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    now = _now()
    expires = now + datetime.timedelta(minutes=30)
    conn.execute("UPDATE client_password_resets SET used_at=? WHERE user_id=? AND used_at IS NULL",
                 (_iso(now), row["id"]))
    conn.execute(
        "INSERT INTO client_password_resets(user_id,token_hash,requested_at,expires_at,requested_ip) "
        "VALUES(?,?,?,?,?)", (row["id"], digest, _iso(now), _iso(expires), requested_ip))
    conn.commit()
    # In development/test only, the UI may display the one-time value for QA.
    # Production integrations must deliver it out-of-band through an approved provider.
    if reveal_token:
        response["development_reset_token"] = raw
        response["expires_in_minutes"] = 30
    return response


def reset_password(conn, token, new_password):
    value = str(token or "").strip()
    if not value:
        raise core.ValidationError("reset token is required")
    admin_platform.validate_password(conn, new_password)
    digest = hashlib.sha256(value.encode()).hexdigest()
    row = conn.execute(
        "SELECT * FROM client_password_resets WHERE token_hash=? AND used_at IS NULL", (digest,)
    ).fetchone()
    now = _now()
    if not row or row["expires_at"] < _iso(now):
        raise core.AuthError("password reset link is invalid or expired")
    conn.execute("UPDATE users SET pw_hash=?,password_reset_required=0 WHERE id=?",
                 (core.hash_pw(new_password), row["user_id"]))
    conn.execute("UPDATE client_password_resets SET used_at=? WHERE id=?", (_iso(now), row["id"]))
    admin_platform.revoke_sessions(conn, row["user_id"])
    core.audit(conn, {"id": row["user_id"], "role": "customer"},
               "CLIENT_PASSWORD_RESET_COMPLETED", "users", row["user_id"])
    conn.commit()
    return {"message": "Password reset successful. Sign in with your new password."}


def development_token_allowed():
    return os.environ.get("APP_ENV", "development").strip().lower() in {
        "development", "dev", "local", "test", "testing"
    }


def seed_demo_workspace(conn):
    """Create authorized QA credentials only in explicit non-production modes."""
    if not development_token_allowed():
        return None
    email, password = "client.qa@lifthaul.demo", "DemoClient123!"
    user = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if user:
        uid = user["id"]
    else:
        uid = core.create_user(conn, email, password, "customer", "LiftHaul QA Client")
        conn.execute("UPDATE users SET verified_at=?,mobile=? WHERE id=?",
                     (_iso(_now()), "09170000058", uid))
    shipper = conn.execute(
        "SELECT id FROM mkt_shippers WHERE legal_name='LiftHaul QA Client (Synthetic)' ORDER BY id LIMIT 1"
    ).fetchone()
    if shipper:
        sid = shipper["id"]
    else:
        cur = conn.execute(
            "INSERT INTO mkt_shippers(applicant_type,legal_name,status,created_by,created_at) "
            "VALUES('CORPORATION','LiftHaul QA Client (Synthetic)','ACTIVE',?,?)",
            (uid, _iso(_now())))
        sid = cur.lastrowid
    if not conn.execute("SELECT 1 FROM client_principals WHERE user_id=? AND shipper_id=?", (uid, sid)).fetchone():
        conn.execute(
            "INSERT INTO client_principals(user_id,shipper_id,portal_role,status,created_by,created_at) "
            "VALUES(?,?,'CLIENT_BOOKER','ACTIVE',?,?)", (uid, sid, uid, _iso(_now())))
    conn.commit()
    return {"email": email, "password": password, "user_id": uid, "shipper_id": sid}
