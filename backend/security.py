"""RGO OS backend — security hardening (§24).

Additive to core (does not weaken existing auth): password policy, login
rate-limiting, session TTL + logout, an env/secret-manager abstraction (no secrets
in code), and a DB backup helper. All server-side, all tested.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import datetime, timezone

import core
from core import AppError, AuthError, ValidationError, ForbiddenError, require, audit, now


class RateLimitError(AppError):
    http = 429


# --------------------------------------------------------------------------- #
# Password policy
# --------------------------------------------------------------------------- #
PW_MIN = 10


def validate_password(pw: str):
    if not pw or len(pw) < PW_MIN:
        raise ValidationError(f"password must be at least {PW_MIN} characters")
    if not re.search(r"[A-Z]", pw) or not re.search(r"[a-z]", pw) or not re.search(r"\d", pw):
        raise ValidationError("password must include upper, lower and a digit")


def register_user(conn, actor, email, password, role, name=None, customer_id=None):
    """Admin-only user creation that enforces the password policy."""
    require(actor, "user.create")
    validate_password(password)
    uid = core.create_user(conn, email, password, role, name, customer_id)
    audit(conn, actor, "user.create", "user", uid, new={"email": email, "role": role})
    conn.commit()
    return uid


# --------------------------------------------------------------------------- #
# Login rate limiting (per email + generic) — in-memory; back with Redis in prod
# --------------------------------------------------------------------------- #
class LoginLimiter:
    def __init__(self, max_fails=5, window_seconds=300):
        self.max_fails = max_fails
        self.window = window_seconds
        self._fails = {}

    def _recent(self, key):
        cutoff = time.time() - self.window
        self._fails[key] = [t for t in self._fails.get(key, []) if t > cutoff]
        return self._fails[key]

    def check(self, email):
        if len(self._recent(email)) >= self.max_fails:
            raise RateLimitError("too many failed logins — try again later")

    def record_fail(self, email):
        self._fails.setdefault(email, []).append(time.time())

    def reset(self, email):
        self._fails.pop(email, None)


def login_guarded(conn, limiter: LoginLimiter, email, password):
    limiter.check(email)
    try:
        token = core.login(conn, email, password)
    except AuthError:
        limiter.record_fail(email)
        raise
    limiter.reset(email)
    return token


# --------------------------------------------------------------------------- #
# Session control — TTL + logout
# --------------------------------------------------------------------------- #
SESSION_TTL_SECONDS = 8 * 3600


def actor_checked(conn, token, ttl=SESSION_TTL_SECONDS):
    row = conn.execute(
        "SELECT u.*, s.created_at sc FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
        (token,)).fetchone()
    if not row:
        raise AuthError("invalid session")
    started = datetime.fromisoformat(row["sc"])
    if (datetime.now(timezone.utc) - started).total_seconds() > ttl:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()
        raise AuthError("session expired")
    return {"id": row["id"], "role": row["role"], "email": row["email"], "customer_id": row["customer_id"]}


def logout(conn, token):
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()


# --------------------------------------------------------------------------- #
# Secret manager — reads from environment / secret store, never from source
# --------------------------------------------------------------------------- #
class SecretManager:
    """Abstraction over a secret store. The env-backed impl is the MVP; a real
    deployment injects AWS/GCP/Vault here. Secrets are NEVER hardcoded or sent
    to the frontend — server code fetches them at call time."""

    def get(self, name: str) -> str:
        val = os.environ.get(name)
        if not val:
            raise ValidationError(f"secret {name} not configured")
        return val


def wise_api_key(secrets: SecretManager) -> str:
    return secrets.get("WISE_API_KEY")   # only ever used server-side


# --------------------------------------------------------------------------- #
# Backup / recovery
# --------------------------------------------------------------------------- #
def backup_db(conn: sqlite3.Connection, dest_path: str):
    dest = sqlite3.connect(dest_path)
    with dest:
        conn.backup(dest)
    dest.close()
    return dest_path
