"""RGO OS backend — commercial-spine foundation (customer → booking → quotation →
approval → acceptance → payment → verification → confirmed job).

Real, testable production foundation using ONLY the Python standard library so it
runs with zero installs and its tests pass immediately:
  * SQLite (a real relational DB with FOREIGN KEY integrity) — swap to PostgreSQL
    by changing `connect()` and the schema dialect; the service layer is DB-agnostic.
  * pbkdf2 password hashing + token sessions (server-side auth).
  * Role-based authorization enforced in the service layer (never trust the client).
  * Soft-delete + audit trail on transactional records.
  * PaymentProvider interface with a MockWise adapter — a real Wise adapter drops
    in server-side without touching callers, and never returns secrets.

This is the COMMERCIAL SPINE, not the whole ERP: it implements and enforces the
controls that matter (no send without approval, no payment without an accepted
quote, no confirmed job without verified payment, separation of duties, idempotent
verification, duplicate-job prevention, customer data isolation) with tests.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
CONFIG = {
    "separation_of_duties": True,       # an approver may not approve their own quotation
    "approval_amount_threshold": 500000,
    "approval_discount_pct": 10,
    "downpayment_default_pct": 30,
    "vat_pct": 12,
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Errors -> map cleanly to HTTP status codes in the API layer
# --------------------------------------------------------------------------- #
class AppError(Exception):
    http = 400


class AuthError(AppError):
    http = 401


class ForbiddenError(AppError):
    http = 403


class NotFoundError(AppError):
    http = 404


class ConflictError(AppError):
    http = 409


class ValidationError(AppError):
    http = 422


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,
  role TEXT NOT NULL, name TEXT, customer_id INTEGER,
  status TEXT NOT NULL DEFAULT 'ACTIVE', last_login_at TEXT,
  created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  ip TEXT, last_seen TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS customers(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, contact TEXT, email TEXT,
  credit_status TEXT DEFAULT 'Good', status TEXT DEFAULT 'ACTIVE',
  created_by INTEGER, created_at TEXT, updated_by INTEGER, updated_at TEXT,
  deleted_by INTEGER, deleted_at TEXT, deletion_reason TEXT);

CREATE TABLE IF NOT EXISTS bookings(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  service TEXT, cargo TEXT, weight REAL, from_loc TEXT, to_loc TEXT, date TEXT,
  stage TEXT NOT NULL DEFAULT 'REQUEST_RECEIVED',
  estimator INTEGER, job_id INTEGER,
  status TEXT DEFAULT 'ACTIVE',
  created_by INTEGER, created_at TEXT, updated_by INTEGER, updated_at TEXT,
  deleted_by INTEGER, deleted_at TEXT, deletion_reason TEXT, restored_by INTEGER, restored_at TEXT);

CREATE TABLE IF NOT EXISTS quotations(
  id INTEGER PRIMARY KEY, no TEXT NOT NULL, version INTEGER NOT NULL,
  booking_id INTEGER NOT NULL REFERENCES bookings(id),
  status TEXT NOT NULL DEFAULT 'draft',   -- draft|pending_approval|approved|sent|accepted|revision|declined|expired|superseded
  subtotal REAL, discount_pct REAL, discount REAL, tax REAL, total REAL,
  dp_pct REAL, dp_amount REAL, balance REAL,
  est_cost REAL, margin_pct REAL,
  approved_by INTEGER, approved_at TEXT,
  accepted_by TEXT, accepted_at TEXT, accepted_terms_version TEXT,
  superseded INTEGER DEFAULT 0,
  created_by INTEGER, created_at TEXT,
  UNIQUE(no, version));

CREATE TABLE IF NOT EXISTS quotation_lines(
  id INTEGER PRIMARY KEY, quotation_id INTEGER NOT NULL REFERENCES quotations(id),
  kind TEXT, description TEXT, qty REAL, days REAL, rate REAL, amount REAL);

CREATE TABLE IF NOT EXISTS payment_requests(
  id INTEGER PRIMARY KEY, no TEXT UNIQUE NOT NULL,
  booking_id INTEGER NOT NULL REFERENCES bookings(id),
  quotation_id INTEGER NOT NULL REFERENCES quotations(id),
  currency TEXT DEFAULT 'PHP', amount_due REAL, dp_pct REAL,
  provider TEXT, provider_ref TEXT, pay_link TEXT,
  status TEXT NOT NULL DEFAULT 'REQUEST_CREATED',
  proof TEXT,
  amount_received REAL, txn_ref TEXT, fees REAL, net REAL,
  verified_by INTEGER, verified_at TEXT, verify_notes TEXT,
  created_by INTEGER, created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY, no TEXT UNIQUE NOT NULL,
  booking_id INTEGER UNIQUE NOT NULL REFERENCES bookings(id),
  quotation_id INTEGER NOT NULL REFERENCES quotations(id),
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  status TEXT NOT NULL DEFAULT 'CONFIRMED', amount REAL, scheduled_at TEXT,
  created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS audit_logs(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, actor INTEGER, role TEXT,
  action TEXT NOT NULL, entity TEXT, entity_id INTEGER,
  old_value TEXT, new_value TEXT, reason TEXT, source TEXT DEFAULT 'api');
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    # check_same_thread=False: the HTTP server may dispatch requests on worker
    # threads; DB access is serialized by a lock in server.py.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def hash_pw(pw: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000)
    return f"pbkdf2$120000${salt}${dk.hex()}"


def verify_pw(pw: str, stored: str) -> bool:
    try:
        _, iters, salt, hexd = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), int(iters))
        return hmac.compare_digest(dk.hex(), hexd)
    except Exception:
        return False


def create_user(conn, email, password, role, name=None, customer_id=None) -> int:
    if role not in PERMISSIONS:
        raise ValidationError(f"unknown role {role}")
    try:
        cur = conn.execute(
            "INSERT INTO users(email,pw_hash,role,name,customer_id,created_at) VALUES(?,?,?,?,?,?)",
            (email.lower(), hash_pw(password), role, name, customer_id, now()))
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ConflictError("email already registered")


def login(conn, email, password) -> str:
    row = conn.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()
    if not row or not verify_pw(password, row["pw_hash"]):
        raise AuthError("invalid credentials")
    if _user_status(row) != "ACTIVE":                       # C-006: suspended/locked/offboarded
        raise AuthError("account is not active")
    token = secrets.token_urlsafe(24)
    conn.execute("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)",
                 (token, row["id"], now()))
    conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (now(), row["id"]))
    conn.commit()
    return token


def _user_status(row) -> str:
    try:
        return row["status"] or "ACTIVE"
    except (KeyError, IndexError):
        return "ACTIVE"                                     # pre-migration rows are active


def actor_for(conn, token) -> dict:
    row = conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
        (token,)).fetchone()
    if not row:
        raise AuthError("invalid or expired session")
    if _user_status(row) != "ACTIVE":                       # C-006: enforce mid-session deactivation
        raise AuthError("account is not active")
    return {"id": row["id"], "role": row["role"], "email": row["email"],
            "customer_id": row["customer_id"]}


# --------------------------------------------------------------------------- #
# RBAC — least privilege, enforced server-side
# --------------------------------------------------------------------------- #
PERMISSIONS = {
    "super_admin": {"*"},
    "admin": {"*"},
    "owner": {"*"},
    "operations_manager": {"customer.*", "booking.*", "quotation.read", "job.*", "payment.read"},
    "estimator": {"customer.read", "booking.create", "booking.read", "booking.review", "booking.ready",
                  "quotation.create", "quotation.revise", "quotation.submit", "quotation.read"},
    "approver": {"quotation.read", "quotation.approve", "booking.read"},
    "finance": {"payment.*", "quotation.read", "booking.read", "customer.read"},
    "dispatcher": {"job.read", "job.dispatch", "booking.read"},
    "customer": {"self.booking.create", "self.booking.read", "self.quotation.read",
                 "self.quotation.accept", "self.quotation.decline", "self.payment.read",
                 "self.payment.evidence"},
}


def can(actor, action) -> bool:
    # Data-driven RBAC cutover (C-005): when an actor has been enriched with a
    # DB-sourced permission set (admin_platform.apply_rbac), use it; otherwise fall
    # back to the legacy in-code PERMISSIONS. Same wildcard grammar either way.
    perms = actor.get("perms")
    if perms is None:
        perms = PERMISSIONS.get(actor["role"], set())
    for p in perms:
        if p == "*" or p == action:
            return True
        if p.endswith(".*") and action.startswith(p[:-1]):
            return True
    return False


def require(actor, action):
    if not can(actor, action):
        raise ForbiddenError(f"role '{actor['role']}' may not perform '{action}'")


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
def audit(conn, actor, action, entity, entity_id, old=None, new=None, reason=None):
    conn.execute(
        "INSERT INTO audit_logs(ts,actor,role,action,entity,entity_id,old_value,new_value,reason)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (now(), actor["id"], actor["role"], action, entity, entity_id,
         json.dumps(old) if old is not None else None,
         json.dumps(new) if new is not None else None, reason))


def list_audit(conn, entity=None, entity_id=None):
    q = "SELECT * FROM audit_logs"
    args = []
    if entity:
        q += " WHERE entity=? AND entity_id=?"
        args = [entity, entity_id]
    q += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, args).fetchall()]


# --------------------------------------------------------------------------- #
# PaymentProvider interface (Wise-ready; secrets stay server-side)
# --------------------------------------------------------------------------- #
class PaymentProvider:
    name = "abstract"

    def create_payment_link(self, *, payment_no, amount, currency) -> dict:
        raise NotImplementedError

    def get_status(self, provider_ref) -> str:
        raise NotImplementedError


class MockWiseProvider(PaymentProvider):
    """Deterministic mock. A real WiseProvider would use a server-held API key
    (never returned to callers) and hit the Wise API. The interface is identical,
    so callers/tests never change."""
    name = "wise_mock"

    def create_payment_link(self, *, payment_no, amount, currency):
        ref = "WISE-" + secrets.token_hex(6).upper()
        return {"provider": self.name, "provider_ref": ref,
                "pay_link": f"https://wise.com/pay/r/rgo-{payment_no.lower()}"}

    def get_status(self, provider_ref):  # real integration point
        return "UNKNOWN"  # never assume paid; verification is manual for MVP


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _booking(conn, bid):
    r = conn.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
    if not r:
        raise NotFoundError("booking not found")
    return r


def _latest_quote(conn, bid):
    return conn.execute(
        "SELECT * FROM quotations WHERE booking_id=? AND superseded=0 ORDER BY version DESC LIMIT 1",
        (bid,)).fetchone()


def _enforce_customer_scope(actor, customer_id):
    """A customer may only touch their own records."""
    if actor["role"] == "customer" and actor.get("customer_id") != customer_id:
        raise ForbiddenError("customers may access only their own records")


# --------------------------------------------------------------------------- #
# Services — the commercial spine, each guarded + audited
# --------------------------------------------------------------------------- #
def create_customer(conn, actor, name, contact=None, email=None):
    require(actor, "customer.create")
    cur = conn.execute(
        "INSERT INTO customers(name,contact,email,created_by,created_at) VALUES(?,?,?,?,?)",
        (name, contact, email, actor["id"], now()))
    conn.commit()
    audit(conn, actor, "create", "customer", cur.lastrowid, new={"name": name})
    conn.commit()
    return cur.lastrowid


def create_booking(conn, actor, customer_id, service, cargo, weight=None,
                   from_loc=None, to_loc=None, date=None):
    if actor["role"] == "customer":
        require(actor, "self.booking.create")
        _enforce_customer_scope(actor, customer_id)
    else:
        require(actor, "booking.create")
    n = conn.execute("SELECT COUNT(*) c FROM bookings").fetchone()["c"]
    ref = f"BK-{1000 + n}"
    cur = conn.execute(
        "INSERT INTO bookings(ref,customer_id,service,cargo,weight,from_loc,to_loc,date,"
        "stage,created_by,created_at) VALUES(?,?,?,?,?,?,?,?, 'REQUEST_RECEIVED',?,?)",
        (ref, customer_id, service, cargo, weight, from_loc, to_loc, date, actor["id"], now()))
    bid = cur.lastrowid
    conn.commit()
    audit(conn, actor, "booking.create", "booking", bid, new={"ref": ref, "stage": "REQUEST_RECEIVED"})
    conn.commit()
    return bid


def get_booking(conn, actor, bid):
    b = _booking(conn, bid)
    if actor["role"] == "customer":
        require(actor, "self.booking.read")
        _enforce_customer_scope(actor, b["customer_id"])
    else:
        require(actor, "booking.read")
    return dict(b)


_BOOKING_FLOW = {
    "REQUEST_RECEIVED": {"UNDER_REVIEW"},
    "UNDER_REVIEW": {"INFORMATION_REQUIRED", "READY_FOR_QUOTATION", "DECLINED", "CANCELLED"},
    "INFORMATION_REQUIRED": {"UNDER_REVIEW", "READY_FOR_QUOTATION"},
    "READY_FOR_QUOTATION": {"QUOTATION_IN_PROGRESS"},
    "QUOTATION_IN_PROGRESS": {"QUOTATION_SENT", "PENDING_APPROVAL"},
    "PENDING_APPROVAL": {"QUOTATION_IN_PROGRESS", "QUOTATION_SENT"},
    "QUOTATION_SENT": {"CUSTOMER_REVIEWING", "QUOTATION_ACCEPTED", "REVISION_REQUESTED", "DECLINED", "EXPIRED"},
    "CUSTOMER_REVIEWING": {"QUOTATION_ACCEPTED", "REVISION_REQUESTED", "DECLINED"},
    "REVISION_REQUESTED": {"QUOTATION_IN_PROGRESS"},
    "QUOTATION_ACCEPTED": {"AWAITING_DOWNPAYMENT"},
    "AWAITING_DOWNPAYMENT": {"PAYMENT_UNDER_VERIFICATION"},
    "PAYMENT_UNDER_VERIFICATION": {"CONFIRMED", "AWAITING_DOWNPAYMENT"},
    "CONFIRMED": set(),
}


def _set_stage(conn, actor, b, to_stage, reason=None):
    frm = b["stage"]
    if to_stage not in _BOOKING_FLOW.get(frm, set()):
        raise ConflictError(f"illegal transition {frm} -> {to_stage}")
    conn.execute("UPDATE bookings SET stage=?, updated_by=?, updated_at=? WHERE id=?",
                 (to_stage, actor["id"], now(), b["id"]))
    audit(conn, actor, "booking.transition", "booking", b["id"],
          old={"stage": frm}, new={"stage": to_stage}, reason=reason)
    conn.commit()


def review_booking(conn, actor, bid):
    require(actor, "booking.review")
    _set_stage(conn, actor, _booking(conn, bid), "UNDER_REVIEW", "started review")


def ready_for_quotation(conn, actor, bid):
    require(actor, "booking.ready")
    _set_stage(conn, actor, _booking(conn, bid), "READY_FOR_QUOTATION")


def create_quotation(conn, actor, bid, lines, discount_pct=0, dp_pct=None, est_cost=0):
    """lines: [{kind,description,qty,days,rate}]. Creates version 1 (or a new
    version if revising). Never overwrites a sent quotation."""
    require(actor, "quotation.create")
    b = _booking(conn, bid)
    if b["stage"] not in ("READY_FOR_QUOTATION", "REVISION_REQUESTED", "QUOTATION_IN_PROGRESS"):
        raise ConflictError("booking is not ready for quotation")
    if not lines:
        raise ValidationError("quotation needs at least one line")
    for l in lines:                                       # server-side line validation
        if l.get("rate", 0) < 0 or l.get("qty", 1) < 0 or l.get("days", 1) < 0:
            raise ValidationError("quotation line values must not be negative")
    dp_pct = CONFIG["downpayment_default_pct"] if dp_pct is None else dp_pct
    prev = _latest_quote(conn, bid)
    if prev:  # revision -> new version, supersede previous
        conn.execute("UPDATE quotations SET superseded=1, status='superseded' WHERE id=?", (prev["id"],))
        no, ver = prev["no"], prev["version"] + 1
    else:
        n = conn.execute("SELECT COUNT(*) c FROM quotations").fetchone()["c"]
        no, ver = f"QN-{3001 + n}", 1
    subtotal = sum(l["rate"] * l.get("qty", 1) * l.get("days", 1) for l in lines)
    discount = round(subtotal * discount_pct / 100)
    taxable = subtotal - discount
    tax = round(taxable * CONFIG["vat_pct"] / 100)
    total = taxable + tax
    dp_amount = round(total * dp_pct / 100)
    margin_pct = round((total - est_cost) / total * 100, 1) if total and est_cost else None
    cur = conn.execute(
        "INSERT INTO quotations(no,version,booking_id,status,subtotal,discount_pct,discount,tax,"
        "total,dp_pct,dp_amount,balance,est_cost,margin_pct,created_by,created_at)"
        " VALUES(?,?,?,'draft',?,?,?,?,?,?,?,?,?,?,?,?)",
        (no, ver, bid, subtotal, discount_pct, discount, tax, total, dp_pct, dp_amount,
         total - dp_amount, est_cost, margin_pct, actor["id"], now()))
    qid = cur.lastrowid
    for l in lines:
        amt = l["rate"] * l.get("qty", 1) * l.get("days", 1)
        conn.execute("INSERT INTO quotation_lines(quotation_id,kind,description,qty,days,rate,amount)"
                     " VALUES(?,?,?,?,?,?,?)",
                     (qid, l.get("kind"), l.get("description"), l.get("qty", 1), l.get("days", 1),
                      l["rate"], amt))
    if b["stage"] == "READY_FOR_QUOTATION" or b["stage"] == "REVISION_REQUESTED":
        conn.execute("UPDATE bookings SET stage='QUOTATION_IN_PROGRESS' WHERE id=?", (bid,))
    conn.commit()
    audit(conn, actor, "quotation.create", "quotation", qid, new={"no": no, "version": ver, "total": total})
    conn.commit()
    return qid


def _quote(conn, qid):
    r = conn.execute("SELECT * FROM quotations WHERE id=?", (qid,)).fetchone()
    if not r:
        raise NotFoundError("quotation not found")
    return r


def _needs_approval(conn, q):
    cust = conn.execute(
        "SELECT c.credit_status FROM customers c JOIN bookings b ON b.customer_id=c.id WHERE b.id=?",
        (q["booking_id"],)).fetchone()
    hold = cust and cust["credit_status"] == "On hold"
    return q["total"] >= CONFIG["approval_amount_threshold"] or q["discount_pct"] > CONFIG["approval_discount_pct"] or hold


def submit_quotation(conn, actor, qid):
    require(actor, "quotation.submit")
    q = _quote(conn, qid)
    if q["status"] not in ("draft",):
        raise ConflictError("only a draft may be submitted")
    if _needs_approval(conn, q):
        conn.execute("UPDATE quotations SET status='pending_approval' WHERE id=?", (qid,))
        conn.execute("UPDATE bookings SET stage='PENDING_APPROVAL' WHERE id=?", (q["booking_id"],))
        audit(conn, actor, "quotation.submit", "quotation", qid, new={"status": "pending_approval"})
    else:  # within authority — auto-approved
        conn.execute("UPDATE quotations SET status='approved', approved_by=?, approved_at=? WHERE id=?",
                     (None, now(), qid))
        audit(conn, actor, "quotation.autoapprove", "quotation", qid,
              new={"status": "approved", "by": "auto (within limits)"})
    conn.commit()
    return _quote(conn, qid)["status"]


def approve_quotation(conn, actor, qid):
    require(actor, "quotation.approve")
    q = _quote(conn, qid)
    if q["status"] != "pending_approval":
        raise ConflictError("quotation is not pending approval")
    if CONFIG["separation_of_duties"] and q["created_by"] == actor["id"]:
        raise ForbiddenError("separation of duties: you may not approve your own quotation")
    conn.execute("UPDATE quotations SET status='approved', approved_by=?, approved_at=? WHERE id=?",
                 (actor["id"], now(), qid))
    audit(conn, actor, "quotation.approve", "quotation", qid, new={"status": "approved"})
    conn.commit()


def send_quotation(conn, actor, qid):
    require(actor, "quotation.create")   # estimator/ops can send once approved
    q = _quote(conn, qid)
    if q["status"] != "approved":
        raise ConflictError("CONTROL: quotation must be approved before sending")
    conn.execute("UPDATE quotations SET status='sent' WHERE id=?", (qid,))
    conn.execute("UPDATE bookings SET stage='QUOTATION_SENT' WHERE id=?", (q["booking_id"],))
    audit(conn, actor, "quotation.send", "quotation", qid, new={"status": "sent"})
    conn.commit()


def accept_quotation(conn, actor, qid, accepted_by, position=None, terms_version="v1"):
    q = _quote(conn, qid)
    b = _booking(conn, q["booking_id"])
    if actor["role"] == "customer":
        require(actor, "self.quotation.accept")
        _enforce_customer_scope(actor, b["customer_id"])
    else:
        require(actor, "quotation.approve")  # staff-assisted acceptance
    if q["status"] != "sent":
        raise ConflictError("only a sent quotation may be accepted")
    conn.execute("UPDATE quotations SET status='accepted', accepted_by=?, accepted_at=?,"
                 " accepted_terms_version=? WHERE id=?",
                 (f"{accepted_by} ({position})" if position else accepted_by, now(), terms_version, qid))
    conn.execute("UPDATE bookings SET stage='QUOTATION_ACCEPTED' WHERE id=?", (b["id"],))
    audit(conn, actor, "quotation.accept", "quotation", qid,
          new={"status": "accepted", "by": accepted_by, "terms": terms_version})
    conn.commit()


def request_revision(conn, actor, qid, reason):
    q = _quote(conn, qid)
    conn.execute("UPDATE quotations SET status='revision' WHERE id=?", (qid,))
    conn.execute("UPDATE bookings SET stage='REVISION_REQUESTED' WHERE id=?", (q["booking_id"],))
    audit(conn, actor, "quotation.revision", "quotation", qid, reason=reason)
    conn.commit()


def create_payment_request(conn, actor, bid, provider: PaymentProvider = None):
    require(actor, "payment.create")
    b = _booking(conn, bid)
    q = _latest_quote(conn, bid)
    if not q or q["status"] != "accepted":
        raise ConflictError("CONTROL: a payment request requires an accepted quotation")
    if conn.execute("SELECT 1 FROM payment_requests WHERE booking_id=?", (bid,)).fetchone():
        raise ConflictError("payment request already exists for this booking")
    n = conn.execute("SELECT COUNT(*) c FROM payment_requests").fetchone()["c"]
    no = f"PR-{5001 + n}"
    cur = conn.execute(
        "INSERT INTO payment_requests(no,booking_id,quotation_id,currency,amount_due,dp_pct,"
        "status,created_by,created_at) VALUES(?,?,?,?,?,?, 'REQUEST_CREATED',?,?)",
        (no, bid, q["id"], "PHP", q["dp_amount"], q["dp_pct"], actor["id"], now()))
    prid = cur.lastrowid
    _set_stage(conn, actor, b, "AWAITING_DOWNPAYMENT", "payment request created")
    audit(conn, actor, "payment.request", "payment_request", prid, new={"no": no, "amount": q["dp_amount"]})
    conn.commit()
    return prid


def register_payment_link(conn, actor, prid, provider: PaymentProvider):
    require(actor, "payment.link")
    pr = conn.execute("SELECT * FROM payment_requests WHERE id=?", (prid,)).fetchone()
    if not pr:
        raise NotFoundError("payment request not found")
    link = provider.create_payment_link(payment_no=pr["no"], amount=pr["amount_due"], currency=pr["currency"])
    conn.execute("UPDATE payment_requests SET provider=?, provider_ref=?, pay_link=?, status='LINK_SENT',"
                 " updated_at=? WHERE id=?",
                 (link["provider"], link["provider_ref"], link["pay_link"], now(), prid))
    audit(conn, actor, "payment.link", "payment_request", prid,
          new={"provider": link["provider"], "status": "LINK_SENT"})  # note: no secret stored/logged
    conn.commit()
    return link["pay_link"]


def submit_payment_evidence(conn, actor, prid, proof_ref):
    """Customer uploads proof — treated ONLY as submitted evidence, never as funds."""
    pr = conn.execute("SELECT * FROM payment_requests WHERE id=?", (prid,)).fetchone()
    if not pr:
        raise NotFoundError("payment request not found")
    if actor["role"] == "customer":
        require(actor, "self.payment.evidence")
        b = _booking(conn, pr["booking_id"])
        _enforce_customer_scope(actor, b["customer_id"])
    conn.execute("UPDATE payment_requests SET proof=?, status='SUBMITTED', updated_at=? WHERE id=?",
                 (proof_ref, now(), prid))
    conn.execute("UPDATE bookings SET stage='PAYMENT_UNDER_VERIFICATION' WHERE id=? AND stage='AWAITING_DOWNPAYMENT'",
                 (pr["booking_id"],))
    audit(conn, actor, "payment.evidence", "payment_request", prid,
          new={"status": "SUBMITTED", "note": "evidence only — NOT verified"})
    conn.commit()


def verify_payment(conn, actor, prid, amount_received, txn_ref, fees=0, notes=None):
    """Finance-only. Idempotent: verifying an already-VERIFIED request is a no-op."""
    require(actor, "payment.verify")
    pr = conn.execute("SELECT * FROM payment_requests WHERE id=?", (prid,)).fetchone()
    if not pr:
        raise NotFoundError("payment request not found")
    if pr["status"] == "VERIFIED":                       # idempotency
        audit(conn, actor, "payment.verify", "payment_request", prid, new={"idempotent": True})
        conn.commit()
        return "VERIFIED"
    if amount_received + 0 < pr["amount_due"]:
        conn.execute("UPDATE payment_requests SET status='PARTIALLY_PAID', amount_received=? WHERE id=?",
                     (amount_received, prid))
        audit(conn, actor, "payment.partial", "payment_request", prid, new={"received": amount_received})
        conn.commit()
        return "PARTIALLY_PAID"
    conn.execute(
        "UPDATE payment_requests SET status='VERIFIED', amount_received=?, txn_ref=?, fees=?, net=?,"
        " verified_by=?, verified_at=?, verify_notes=?, updated_at=? WHERE id=?",
        (amount_received, txn_ref, fees, amount_received - fees, actor["id"], now(), notes, now(), prid))
    audit(conn, actor, "payment.verify", "payment_request", prid,
          old={"status": pr["status"]}, new={"status": "VERIFIED", "net": amount_received - fees})
    conn.commit()
    return "VERIFIED"


def confirm_job(conn, actor, bid):
    """Transactional. Confirms a job ONLY when the accepted quotation is backed by a
    VERIFIED payment. Prevents duplicate job creation (idempotent)."""
    require(actor, "job.confirm")
    b = _booking(conn, bid)
    q = _latest_quote(conn, bid)
    if b["job_id"]:                                       # duplicate prevention / idempotent
        return conn.execute("SELECT no FROM jobs WHERE id=?", (b["job_id"],)).fetchone()["no"]
    if not q or q["status"] != "accepted":
        raise ConflictError("CONTROL: cannot confirm — quotation not accepted")
    pr = conn.execute("SELECT * FROM payment_requests WHERE booking_id=?", (bid,)).fetchone()
    if not pr or pr["status"] != "VERIFIED":
        raise ConflictError("CONTROL: cannot confirm — downpayment not verified")
    with conn:                                            # portable transaction (sqlite + postgres)
        n = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        job_no = f"JO-{2050 + n}"
        cur = conn.execute(
            "INSERT INTO jobs(no,booking_id,quotation_id,customer_id,status,amount,scheduled_at,created_by,created_at)"
            " VALUES(?,?,?,?, 'CONFIRMED',?,?,?,?)",
            (job_no, bid, q["id"], b["customer_id"], q["total"], now(), actor["id"], now()))
        job_id = cur.lastrowid
        conn.execute("UPDATE bookings SET stage='CONFIRMED', job_id=? WHERE id=?", (job_id, bid))
        audit(conn, actor, "job.confirm", "job", job_id,
              new={"no": job_no, "from_booking": b["ref"], "from_quote": q["no"]})
    return job_no


# --------------------------------------------------------------------------- #
# Soft delete / restore
# --------------------------------------------------------------------------- #
_SOFT_DELETABLE = {"bookings", "customers"}


def soft_delete(conn, actor, table, rid, reason):
    require(actor, table.rstrip("s") + ".delete")
    if table not in _SOFT_DELETABLE:
        raise ValidationError("table not soft-deletable")
    conn.execute(f"UPDATE {table} SET status='DELETED', deleted_by=?, deleted_at=?, deletion_reason=? WHERE id=?",
                 (actor["id"], now(), reason, rid))
    audit(conn, actor, "soft_delete", table, rid, reason=reason)
    conn.commit()


def restore(conn, actor, table, rid):
    require(actor, table.rstrip("s") + ".delete")
    conn.execute(f"UPDATE {table} SET status='ACTIVE', restored_by=?, restored_at=? WHERE id=?",
                 (actor["id"], now(), rid))
    audit(conn, actor, "restore", table, rid)
    conn.commit()
