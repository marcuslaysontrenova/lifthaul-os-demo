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
  status TEXT NOT NULL DEFAULT 'ACTIVE', last_login_at TEXT, tenant_id INTEGER,
  created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  ip TEXT, last_seen TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS customers(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, contact TEXT, email TEXT,
  credit_status TEXT DEFAULT 'Good', status TEXT DEFAULT 'ACTIVE',
  customer_number TEXT, merged_into INTEGER,
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
  tax_snapshot TEXT, dp_snapshot TEXT, approval_snapshot TEXT,
  valid_until TEXT, validity_snapshot TEXT,
  created_by INTEGER, created_at TEXT,
  UNIQUE(no, version));

CREATE TABLE IF NOT EXISTS quotation_lines(
  id INTEGER PRIMARY KEY, quotation_id INTEGER NOT NULL REFERENCES quotations(id),
  kind TEXT, description TEXT, qty REAL, days REAL, rate REAL, amount REAL,
  -- Pricing subsystem (LiftHaul quotation rate/override enhancement):
  equipment_code TEXT, billing_unit TEXT DEFAULT 'day',
  standard_rate REAL, quoted_rate REAL, internal_cost REAL,
  discount_pct REAL DEFAULT 0, line_tax REAL, subtotal REAL,
  gross_profit REAL, margin_percent REAL,
  rate_source TEXT DEFAULT 'catalog', rate_version INTEGER,
  override_reason TEXT, created_by INTEGER, updated_by INTEGER,
  created_at TEXT, updated_at TEXT);

-- Governed, effective-dated master rate catalog. Never overwrite a historical rate:
-- edits create a new version and supersede the prior row. quoted_rate on a quotation
-- line is independent and never mutates this master.
CREATE TABLE IF NOT EXISTS rate_cards(
  id INTEGER PRIMARY KEY, tenant_id INTEGER,
  equipment_code TEXT NOT NULL, equipment_name TEXT, service_type TEXT,
  billing_unit TEXT NOT NULL DEFAULT 'day',
  standard_rate REAL NOT NULL, min_rate REAL, internal_cost REAL,
  currency TEXT NOT NULL DEFAULT 'PHP', branch TEXT, region TEXT,
  customer_id INTEGER,
  version INTEGER NOT NULL DEFAULT 1,
  effective_from TEXT, effective_to TEXT,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  superseded INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS payment_requests(
  id INTEGER PRIMARY KEY, no TEXT UNIQUE NOT NULL,
  booking_id INTEGER NOT NULL REFERENCES bookings(id),
  quotation_id INTEGER NOT NULL REFERENCES quotations(id),
  currency TEXT DEFAULT 'PHP', amount_due REAL, dp_pct REAL,
  provider TEXT, provider_ref TEXT, pay_link TEXT,
  status TEXT NOT NULL DEFAULT 'REQUEST_CREATED',
  proof TEXT,
  amount_received REAL, txn_ref TEXT, fees REAL, net REAL,
  verified_by INTEGER, verified_at TEXT, verify_notes TEXT, dp_snapshot TEXT,
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
  old_value TEXT, new_value TEXT, reason TEXT, source TEXT DEFAULT 'api',
  correlation_id TEXT);
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    # check_same_thread=False: the HTTP server may dispatch requests on worker
    # threads; DB access is serialized by a lock in server.py.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate_pricing(conn)
    try:
        import rates
        rates.seed_default_rate_cards(conn)          # governed baseline catalog (idempotent)
    except Exception:
        pass
    return conn


# Idempotent column adds for DBs created before the pricing subsystem. CREATE TABLE
# IF NOT EXISTS never adds columns to an existing table, so persistent/file DBs need this
# on every reconnect — that is what makes rate/override/margin data survive a restart.
_PRICING_COLS = [
    ("equipment_code", "TEXT"), ("billing_unit", "TEXT DEFAULT 'day'"),
    ("standard_rate", "REAL"), ("quoted_rate", "REAL"), ("internal_cost", "REAL"),
    ("discount_pct", "REAL DEFAULT 0"), ("line_tax", "REAL"), ("subtotal", "REAL"),
    ("gross_profit", "REAL"), ("margin_percent", "REAL"),
    ("rate_source", "TEXT DEFAULT 'catalog'"), ("rate_version", "INTEGER"),
    ("override_reason", "TEXT"), ("created_by", "INTEGER"), ("updated_by", "INTEGER"),
    ("created_at", "TEXT"), ("updated_at", "TEXT"),
]


def _migrate_pricing(conn):
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(quotation_lines)").fetchall()}
    for name, decl in _PRICING_COLS:
        if name not in existing:
            conn.execute(f"ALTER TABLE quotation_lines ADD COLUMN {name} {decl}")
    qcols = {r["name"] for r in conn.execute("PRAGMA table_info(quotations)").fetchall()}
    for name in ("valid_until", "validity_snapshot"):      # governed validity (config consumer)
        if name not in qcols:
            conn.execute(f"ALTER TABLE quotations ADD COLUMN {name} TEXT")
    conn.commit()


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
    actor = {"id": row["id"], "role": row["role"], "email": row["email"],
             "customer_id": row["customer_id"],
             "tenant_id": row["tenant_id"] if "tenant_id" in row.keys() else None}
    import tenant; tenant.enrich_cross_access(conn, actor)   # active, unexpired grant only
    return actor


# --------------------------------------------------------------------------- #
# RBAC — least privilege, enforced server-side
# --------------------------------------------------------------------------- #
PERMISSIONS = {
    "super_admin": {"*"},
    "admin": {"*"},
    "owner": {"*"},
    "operations_manager": {"customer.*", "booking.*", "quotation.read", "job.*", "payment.read"},
    "estimator": {"customer.read", "booking.create", "booking.read", "booking.view",
                  "booking.review", "booking.ready", "booking.edit_draft",
                  "booking.revise_returned", "booking.submit", "booking.attachment.manage",
                  "booking.print", "booking.audit.view", "quotation.create", "quotation.edit_draft",
                  "quotation.revise", "quotation.revise_returned", "quotation.submit", "quotation.read",
                  "quotation.view", "quotation.customer_price.view", "quotation.print",
                  "quotation.audit.view", "quotation.customer_price.edit",
                  "quotation.rate.override", "quotation.discount.override"},
    "booking_quotation_administrator": {
        "customer.read", "booking.create", "booking.read", "booking.view", "booking.review",
        "booking.ready", "booking.edit_draft", "booking.revise_returned", "booking.submit",
        "booking.cancel", "booking.delete_draft", "booking.attachment.manage", "booking.export",
        "booking.print", "booking.audit.view", "quotation.create", "quotation.read", "quotation.view",
        "quotation.edit_draft", "quotation.revise", "quotation.revise_returned", "quotation.submit",
        "quotation.cancel", "quotation.delete_draft", "quotation.export", "quotation.print",
        "quotation.audit.view", "quotation.customer_price.view",
        "quotation.customer_price.edit", "quotation.rate.override", "quotation.discount.override"},
    "approver": {"quotation.read", "quotation.view", "quotation.approve", "quotation.reject",
                 "quotation.return", "quotation.customer_price.view", "quotation.carrier_cost.view",
                 "quotation.platform_fee.view", "quotation.margin.view", "quotation.audit.view",
                 "quotation.print", "booking.read", "booking.view", "booking.audit.view"},
    "finance": {"payment.*", "quotation.read", "booking.read", "customer.read"},
    "finance_admin": {"payment.*", "finance.*", "quotation.read", "quotation.view", "booking.read",
                      "booking.view", "customer.read", "quotation.customer_price.view",
                      "quotation.carrier_cost.view", "quotation.carrier_cost.edit",
                      "quotation.platform_fee.view", "quotation.margin.view", "quotation.audit.view",
                      "crm.admin.pricing.view", "crm.admin.pricing.manage"},
    "executive": {"booking.read", "booking.view", "booking.audit.view", "quotation.read",
                  "quotation.view", "quotation.customer_price.view", "quotation.carrier_cost.view",
                  "quotation.platform_fee.view", "quotation.margin.view", "quotation.audit.view",
                  "quotation.approve.exceptional", "payment.read", "finance.view", "reporting.view",
                  "audit.view", "job.read", "customer.read", "fleet.view", "safety.view"},
    "user_administrator": {"user_admin.view", "user_admin.manage", "user_admin.assign_roles",
                           "role_admin.view", "security.view", "org.view"},
    "operational_user": {"booking.read", "booking.view", "job.read", "customer.read"},
    "dispatcher": {"job.read", "job.dispatch", "booking.read"},
    "customer": {"self.booking.create", "self.booking.read", "self.quotation.read",
                 "self.quotation.accept", "self.quotation.decline", "self.payment.read",
                 "self.payment.evidence"},
    # Carrier / Fleet Owner Portal principal — self-service over the carrier's OWN data only.
    # Holds NONE of the operational marketplace.* perms (so /admin/* is 403) and NO verify/
    # approve/activate. See carrier_portal.PORTAL_PERMISSIONS.
    "carrier_principal": {"carrier.portal.view", "carrier.portal.fleet.manage",
                          "carrier.portal.compliance.submit", "carrier.portal.offers.manage",
                          "carrier.portal.trips.execute", "carrier.portal.settings.manage"},
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
# Request-scoped correlation id. server._handle sets this per request (under _DB_LOCK,
# which serializes DB access) so every audit event within one request shares an id and
# the whole chain of governed writes is traceable end-to-end.
_correlation_id = None


def set_correlation_id(cid):
    global _correlation_id
    _correlation_id = cid


def correlation_id():
    return _correlation_id


def audit(conn, actor, action, entity, entity_id, old=None, new=None, reason=None,
          correlation_id=None):
    conn.execute(
        "INSERT INTO audit_logs(ts,actor,role,action,entity,entity_id,old_value,new_value,"
        "reason,correlation_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (now(), actor["id"], actor["role"], action, entity, entity_id,
         json.dumps(old) if old is not None else None,
         json.dumps(new) if new is not None else None, reason,
         correlation_id if correlation_id is not None else _correlation_id))


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
def _booking(conn, bid, actor=None):
    r = conn.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
    if not r:
        raise NotFoundError("booking not found")
    if actor is not None:
        import tenant; tenant.guard(actor, r)             # cross-tenant -> 404 (no leak)
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
    import tenant
    tenant.stamp(conn, actor, "customers", cur.lastrowid)   # server-derived tenant ownership
    try:                                                    # governed customer numbering (Phase 3, additive)
        import crm_admin
        crm_admin.assign_customer_number(conn, actor, cur.lastrowid)
    except Exception:
        pass                                                # tolerant: numbering unavailable => number stays NULL
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
    import tenant
    tenant.assert_related(conn, actor, "customers", customer_id)   # no cross-tenant linkage
    import policy
    _bk_prefix = policy.number_prefix(conn, policy.policy_context(conn, actor), "booking", "BK")
    n = conn.execute("SELECT COUNT(*) c FROM bookings").fetchone()["c"]
    ref = f"{_bk_prefix}-{1000 + n}"
    cur = conn.execute(
        "INSERT INTO bookings(ref,customer_id,service,cargo,weight,from_loc,to_loc,date,"
        "stage,created_by,created_at) VALUES(?,?,?,?,?,?,?,?, 'REQUEST_RECEIVED',?,?)",
        (ref, customer_id, service, cargo, weight, from_loc, to_loc, date, actor["id"], now()))
    bid = cur.lastrowid
    conn.commit()
    tenant.stamp(conn, actor, "bookings", bid)
    audit(conn, actor, "booking.create", "booking", bid, new={"ref": ref, "stage": "REQUEST_RECEIVED"})
    conn.commit()
    return bid


def get_booking(conn, actor, bid):
    b = _booking(conn, bid, actor)                        # tenant guard in the loader
    if actor["role"] == "customer":
        require(actor, "self.booking.read")
        _enforce_customer_scope(actor, b["customer_id"])
    else:
        require(actor, "booking.read")
    return dict(b)


def update_booking(conn, actor, bid, changes):
    """Update operational booking fields while the record is editable.

    Approval-pending and later records are deliberately locked. An approver must
    return the transaction before an encoder can revise it.
    """
    b = _booking(conn, bid, actor)
    returned = b["stage"] == "REVISION_REQUESTED"
    require(actor, "booking.revise_returned" if returned else "booking.edit_draft")
    editable = {
        "REQUEST_RECEIVED", "UNDER_REVIEW", "INFORMATION_REQUIRED", "READY_FOR_QUOTATION",
        "QUOTATION_IN_PROGRESS", "REVISION_REQUESTED",
    }
    if b["stage"] not in editable:
        raise ConflictError("booking is locked; return it for revision before editing")
    OPERATIONAL_FIELDS = {"service", "cargo", "weight", "from_loc", "to_loc", "date", "estimator"}
    COMMERCIAL_FIELDS = {"customer_id"}   # customer/contact reference is a commercial linkage
    allowed = OPERATIONAL_FIELDS | COMMERCIAL_FIELDS
    patch = {k: v for k, v in (changes or {}).items() if k in allowed}
    if not patch:
        raise ValidationError("no editable booking fields supplied")
    # field-level authorization — operational vs commercial fields require distinct grants
    if patch.keys() & OPERATIONAL_FIELDS:
        require(actor, "booking.edit_operational")
    if patch.keys() & COMMERCIAL_FIELDS:
        require(actor, "booking.edit_commercial")
    old = {k: b[k] for k in patch}
    sets = ",".join(f"{k}=?" for k in patch)
    conn.execute(
        f"UPDATE bookings SET {sets}, updated_by=?, updated_at=? WHERE id=?",
        tuple(patch.values()) + (actor["id"], now(), bid),
    )
    audit(conn, actor, "booking.material_revision", "booking", bid, old=old, new=patch)
    conn.commit()
    return get_booking(conn, actor, bid)


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
    _set_stage(conn, actor, _booking(conn, bid, actor), "UNDER_REVIEW", "started review")


def ready_for_quotation(conn, actor, bid):
    require(actor, "booking.ready")
    _set_stage(conn, actor, _booking(conn, bid, actor), "READY_FOR_QUOTATION")


def _price_lines(conn, actor, rows, ctx, customer_id=None):
    """Authoritative per-line pricing + governed override enforcement. Returns processed line
    rows (with every computed money field) plus aggregates the quotation layer consumes.

    Legacy-shaped lines ({kind,description,qty,days,rate}) pass through unchanged: standard==quoted
    so there is no override and no internal cost, exactly matching pre-subsystem behaviour.
    """
    import rates, policy
    reason_thr, _ = policy._num(conn, "quotation.rate_override.reason_threshold_pct", ctx,
                                rates.DEFAULT_REASON_THRESHOLD_PCT)
    disc_thr, _ = policy._num(conn, "quotation.approval.discount_threshold_pct", ctx, 10)
    processed, est_cost_sum, any_internal, max_var, overrides = [], 0.0, False, 0.0, []
    actor_tenant = (actor or {}).get("tenant_id")
    for l in rows:
        code = l.get("equipment_code")
        card = rates.resolve_rate(conn, code, customer_id=customer_id, tenant_id=actor_tenant) if code else None
        standard_rate = l.get("standard_rate")
        if standard_rate is None:
            standard_rate = card["standard_rate"] if card else l.get("rate", 0)
        quoted_rate = l.get("quoted_rate")
        if quoted_rate is None:
            quoted_rate = l.get("rate") if l.get("rate") is not None else standard_rate
        internal_cost = l.get("internal_cost")
        client_set_cost = internal_cost is not None
        if internal_cost is None:
            internal_cost = card["internal_cost"] if (card and card["internal_cost"] is not None) else 0
        if client_set_cost or (card and card["internal_cost"] is not None):
            any_internal = True
        qty = l.get("qty", 1) or 1
        days = l.get("days", 1) or 1
        disc = l.get("discount_pct", 0) or 0
        var = rates.variance(standard_rate, quoted_rate)
        if var["amount"] != 0:                                   # rate override → governed control
            require(actor, rates.PERM_RATE_OVERRIDE)
            if card and card["min_rate"] is not None and quoted_rate < card["min_rate"]:
                raise ValidationError("quoted_rate is below the governed minimum rate")
            if var["abs_pct"] >= reason_thr and not (l.get("override_reason") or "").strip():
                raise ValidationError("override reason required: quoted rate varies materially from standard")
        if disc and disc > disc_thr:                             # discount beyond policy → override perm
            require(actor, rates.PERM_DISCOUNT_OVERRIDE)
        if client_set_cost and card and card["internal_cost"] is not None \
                and round(internal_cost, 2) != round(card["internal_cost"], 2):
            require(actor, rates.PERM_COST_EDIT)                 # editing internal cost is finance-gated
        priced = rates.price_line(quoted_rate, qty, days, disc, internal_cost)
        est_cost_sum += priced["internal_total"]
        max_var = max(max_var, var["abs_pct"])
        source = "override" if var["amount"] != 0 else ("catalog" if card else "manual")
        processed.append({
            "kind": l.get("kind"),
            "description": l.get("description") or (card["equipment_name"] if card else None),
            "equipment_code": code,
            "billing_unit": l.get("billing_unit") or (card["billing_unit"] if card else "day"),
            "qty": qty, "days": days, "standard_rate": standard_rate, "quoted_rate": quoted_rate,
            "internal_cost": internal_cost, "discount_pct": disc, "subtotal": priced["subtotal"],
            "gross_profit": priced["gross_profit"], "margin_percent": priced["margin_percent"],
            "rate": quoted_rate, "amount": priced["subtotal"], "rate_source": source,
            "rate_version": (card["version"] if card else None), "override_reason": l.get("override_reason")})
        if var["amount"] != 0:
            overrides.append({"equipment_code": code, "standard_rate": standard_rate,
                              "quoted_rate": quoted_rate, "variance_amount": var["amount"],
                              "variance_pct": var["pct"], "reason": l.get("override_reason"),
                              "changed_by": actor["id"], "changed_at": now()})
    return {"lines": processed, "subtotal": round(sum(r["subtotal"] for r in processed), 2),
            "est_cost": round(est_cost_sum, 2), "any_internal": any_internal,
            "max_variance_pct": max_var, "overrides": overrides}


def _insert_priced_line(conn, actor, qid, r):
    conn.execute(
        "INSERT INTO quotation_lines(quotation_id,kind,description,qty,days,rate,amount,"
        "equipment_code,billing_unit,standard_rate,quoted_rate,internal_cost,discount_pct,subtotal,"
        "gross_profit,margin_percent,rate_source,rate_version,override_reason,created_by,updated_by,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (qid, r["kind"], r["description"], r["qty"], r["days"], r["rate"], r["amount"],
         r["equipment_code"], r["billing_unit"], r["standard_rate"], r["quoted_rate"], r["internal_cost"],
         r["discount_pct"], r["subtotal"], r["gross_profit"], r["margin_percent"], r["rate_source"],
         r["rate_version"], r["override_reason"], actor["id"], actor["id"], now(), now()))


def create_quotation(conn, actor, bid, lines, discount_pct=0, dp_pct=None, est_cost=0):
    """lines: [{kind,description,qty,days,rate}]. Creates version 1 (or a new
    version if revising). Never overwrites a sent quotation."""
    b = _booking(conn, bid, actor)                        # booking must be in the actor's tenant
    if b["stage"] == "REVISION_REQUESTED":
        require(actor, "quotation.revise_returned")
    else:
        require(actor, "quotation.create")
    if b["stage"] not in ("READY_FOR_QUOTATION", "REVISION_REQUESTED", "QUOTATION_IN_PROGRESS"):
        raise ConflictError("booking is not ready for quotation")
    if not lines:
        raise ValidationError("quotation needs at least one line")
    for l in lines:                                       # server-side line validation
        if l.get("rate", 0) < 0 or l.get("qty", 1) < 0 or l.get("days", 1) < 0 \
                or (l.get("quoted_rate") or 0) < 0 or (l.get("standard_rate") or 0) < 0 \
                or (l.get("internal_cost") or 0) < 0:
            raise ValidationError("quotation line values must not be negative")
    requested_dp = dp_pct                                 # explicit override or None (=policy default)
    import policy
    ctx = policy.policy_context(conn, actor, b)            # tenant/org context from the authenticated actor
    prev = _latest_quote(conn, bid)
    if prev:  # revision -> new version, supersede previous (recalculated with CURRENT policy)
        conn.execute("UPDATE quotations SET superseded=1, status='superseded' WHERE id=?", (prev["id"],))
        no, ver = prev["no"], prev["version"] + 1
    else:
        n = conn.execute("SELECT COUNT(*) c FROM quotations").fetchone()["c"]
        no, ver = f"{policy.number_prefix(conn, ctx, 'quotation', 'QN')}-{3001 + n}", 1
    validity = policy.quotation_validity(conn, ctx)         # governed validity window + snapshot
    priced = _price_lines(conn, actor, lines, ctx, customer_id=b["customer_id"])  # authoritative line math + override governance
    subtotal = priced["subtotal"]                          # server-side truth; any client total is ignored
    if priced["any_internal"]:
        est_cost = priced["est_cost"]                      # internal cost sourced from lines/catalog
    discount = round(subtotal * discount_pct / 100)
    taxable = subtotal - discount
    tp = policy.evaluate_tax(conn, taxable, ctx)           # governed tax policy (default == 12% exclusive)
    tax = tp["tax"]
    total = taxable if tp["inclusive"] else taxable + tax  # inclusive => tax already embedded in the price
    dpe = policy.evaluate_downpayment(conn, total, ctx, requested_rate=requested_dp)  # default == 30%
    dp_pct, dp_amount = dpe["rate"], dpe["amount"]
    margin_pct = round((total - est_cost) / total * 100, 1) if total and est_cost else None
    ape = policy.evaluate_approval(conn, total, discount_pct, ctx,
                                   rate_variance_pct=priced["max_variance_pct"], margin_pct=margin_pct)
    cur = conn.execute(
        "INSERT INTO quotations(no,version,booking_id,status,subtotal,discount_pct,discount,tax,"
        "total,dp_pct,dp_amount,balance,est_cost,margin_pct,tax_snapshot,dp_snapshot,approval_snapshot,"
        "valid_until,validity_snapshot,created_by,created_at) VALUES(?,?,?,'draft',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (no, ver, bid, subtotal, discount_pct, discount, tax, total, dp_pct, dp_amount,
         total - dp_amount, est_cost, margin_pct, json.dumps(tp["snapshot"]),
         json.dumps(dpe["snapshot"]), json.dumps(ape["snapshot"]),
         validity["valid_until"], json.dumps(validity["snapshot"]), actor["id"], now()))
    qid = cur.lastrowid
    import tenant; tenant.stamp(conn, actor, "quotations", qid)   # inherit tenant from context
    for r in priced["lines"]:
        _insert_priced_line(conn, actor, qid, r)
    if b["stage"] == "READY_FOR_QUOTATION" or b["stage"] == "REVISION_REQUESTED":
        conn.execute("UPDATE bookings SET stage='QUOTATION_IN_PROGRESS' WHERE id=?", (bid,))
    for ov in priced["overrides"]:                         # record every rate override with full lineage
        audit(conn, actor, "quotation.rate_override", "quotation", qid,
              new={**ov, "approval_required": ape["required"], "approval_status": "pending" if ape["required"] else "not_required"})
    conn.commit()
    audit(conn, actor, "quotation.material_revision" if prev else "quotation.create", "quotation", qid,
          new={"no": no, "version": ver, "total": total})
    conn.commit()
    return qid


def _quote(conn, qid, actor=None):
    r = conn.execute("SELECT * FROM quotations WHERE id=?", (qid,)).fetchone()
    if not r:
        raise NotFoundError("quotation not found")
    if actor is not None:
        import tenant; tenant.guard(actor, r)             # cross-tenant -> 404 (no leak)
    return r


def _require_either(actor, *actions):
    if not any(can(actor, action) for action in actions):
        require(actor, actions[0])


def get_quotation(conn, actor, qid):
    """Return a tenant-scoped quotation with field-level financial redaction."""
    _require_either(actor, "quotation.read", "quotation.view")
    q = dict(_quote(conn, qid, actor))
    lines = [dict(row) for row in conn.execute(
        "SELECT id,kind,description,qty,days,rate,amount,equipment_code,billing_unit,standard_rate,"
        "quoted_rate,internal_cost,discount_pct,subtotal,gross_profit,margin_percent,rate_source,"
        "rate_version,override_reason FROM quotation_lines WHERE quotation_id=? ORDER BY id",
        (qid,),
    ).fetchall()]
    redacted = []
    if not can(actor, "quotation.customer_price.view"):
        for key in ("subtotal", "discount_pct", "discount", "tax", "total", "dp_pct", "dp_amount", "balance"):
            q[key] = None
        for line in lines:
            for key in ("rate", "amount", "standard_rate", "quoted_rate", "subtotal"):
                line[key] = None
        redacted.append("customer_price")
    if not can(actor, "quotation.carrier_cost.view"):
        q["est_cost"] = None
        for line in lines:                                   # internal/vendor cost never leaks
            line["internal_cost"] = None
        redacted.append("carrier_cost")
    if not can(actor, "quotation.margin.view"):
        q["margin_pct"] = None
        for line in lines:
            line["gross_profit"] = None
            line["margin_percent"] = None
        redacted.append("margin")
    q["platform_fee"] = None
    if not can(actor, "quotation.platform_fee.view"):
        redacted.append("platform_fee")
    q["lines"] = lines
    q["redacted_fields"] = redacted
    return q


def update_quotation_draft(conn, actor, qid, lines=None, discount_pct=None,
                           dp_pct=None, est_cost=None):
    """Materially revise a draft/returned quotation with full recalculation and audit."""
    q = _quote(conn, qid, actor)
    returned = q["status"] in ("revision", "returned", "rejected")
    require(actor, "quotation.revise_returned" if returned else "quotation.edit_draft")
    if q["status"] not in ("draft", "revision", "returned", "rejected"):
        raise ConflictError("only a draft or returned quotation may be revised")
    rows = lines if lines is not None else [dict(row) for row in conn.execute(
        "SELECT kind,description,qty,days,rate,equipment_code,billing_unit,standard_rate,quoted_rate,"
        "internal_cost,discount_pct,override_reason FROM quotation_lines WHERE quotation_id=? ORDER BY id",
        (qid,),
    ).fetchall()]
    if not rows:
        raise ValidationError("quotation needs at least one line")
    for line in rows:
        if line.get("rate", 0) < 0 or line.get("qty", 1) < 0 or line.get("days", 1) < 0 \
                or (line.get("quoted_rate") or 0) < 0 or (line.get("internal_cost") or 0) < 0:
            raise ValidationError("quotation line values must not be negative")
    disc = q["discount_pct"] if discount_pct is None else discount_pct
    requested_dp = q["dp_pct"] if dp_pct is None else dp_pct
    cost = q["est_cost"] if est_cost is None else est_cost
    b = _booking(conn, q["booking_id"], actor)
    import policy
    ctx = policy.policy_context(conn, actor, b)
    priced = _price_lines(conn, actor, rows, ctx, customer_id=b["customer_id"])  # authoritative recompute
    subtotal = priced["subtotal"]
    if priced["any_internal"]:
        cost = priced["est_cost"]
    discount = round(subtotal * disc / 100)
    taxable = subtotal - discount
    tp = policy.evaluate_tax(conn, taxable, ctx)
    tax = tp["tax"]
    total = taxable if tp["inclusive"] else taxable + tax
    dpe = policy.evaluate_downpayment(conn, total, ctx, requested_rate=requested_dp)
    margin_pct = round((total - cost) / total * 100, 1) if total and cost else None
    ape = policy.evaluate_approval(conn, total, disc, ctx,
                                   rate_variance_pct=priced["max_variance_pct"], margin_pct=margin_pct)
    old = {"subtotal": q["subtotal"], "discount_pct": q["discount_pct"], "total": q["total"],
           "dp_pct": q["dp_pct"], "est_cost": q["est_cost"]}
    conn.execute(
        "UPDATE quotations SET status='draft',subtotal=?,discount_pct=?,discount=?,tax=?,total=?,"
        "dp_pct=?,dp_amount=?,balance=?,est_cost=?,margin_pct=?,tax_snapshot=?,dp_snapshot=?,"
        "approval_snapshot=? WHERE id=?",
        (subtotal, disc, discount, tax, total, dpe["rate"], dpe["amount"], total - dpe["amount"],
         cost, margin_pct, json.dumps(tp["snapshot"]), json.dumps(dpe["snapshot"]),
         json.dumps(ape["snapshot"]), qid),
    )
    conn.execute("DELETE FROM quotation_lines WHERE quotation_id=?", (qid,))
    for r in priced["lines"]:
        _insert_priced_line(conn, actor, qid, r)
    for ov in priced["overrides"]:
        audit(conn, actor, "quotation.rate_override", "quotation", qid,
              new={**ov, "approval_required": ape["required"], "approval_status": "pending" if ape["required"] else "not_required"})
    conn.execute("UPDATE bookings SET stage='QUOTATION_IN_PROGRESS',updated_by=?,updated_at=? WHERE id=?",
                 (actor["id"], now(), q["booking_id"]))
    audit(conn, actor, "quotation.material_revision", "quotation", qid, old=old,
          new={"subtotal": subtotal, "discount_pct": disc, "total": total,
               "dp_pct": dpe["rate"], "est_cost": cost})
    conn.commit()
    return get_quotation(conn, actor, qid)


def _needs_approval(conn, q):
    cust = conn.execute(
        "SELECT c.credit_status FROM customers c JOIN bookings b ON b.customer_id=c.id WHERE b.id=?",
        (q["booking_id"],)).fetchone()
    hold = bool(cust and cust["credit_status"] == "On hold")
    # use the quotation version's PERSISTED approval snapshot (historical reproducibility);
    # a credit hold always forces approval regardless of the amount policy
    snap = q["approval_snapshot"] if "approval_snapshot" in q.keys() else None
    if snap:
        return bool(json.loads(snap).get("required")) or hold
    import policy
    return policy.evaluate_approval(conn, q["total"], q["discount_pct"] or 0, {})["required"] or hold


def submit_quotation(conn, actor, qid):
    require(actor, "quotation.submit")
    q = _quote(conn, qid, actor)
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


def approve_quotation(conn, actor, qid, comment=None, override_reason=None):
    q = _quote(conn, qid, actor)
    if can(actor, "quotation.approve"):
        pass
    elif can(actor, "quotation.approve.exceptional") and q["total"] >= CONFIG["approval_amount_threshold"]:
        pass
    else:
        require(actor, "quotation.approve")
    if q["status"] != "pending_approval":
        raise ConflictError("quotation is not pending approval")
    if CONFIG["separation_of_duties"]:
        _governed_review_override(conn, actor, q, "approve", override_reason)
    conn.execute("UPDATE quotations SET status='approved', approved_by=?, approved_at=? WHERE id=?",
                 (actor["id"], now(), qid))
    audit(conn, actor, "quotation.approve", "quotation", qid,
          new={"status": "approved"}, reason=str(comment).strip() if comment else None)
    conn.commit()


def _transaction_maker_ids(conn, q):
    """All users who created or materially revised the booking/quotation."""
    makers = {q["created_by"]} if q["created_by"] is not None else set()
    b = _booking(conn, q["booking_id"])
    if b["created_by"] is not None:
        makers.add(b["created_by"])
    rows = conn.execute(
        """SELECT actor FROM audit_logs
           WHERE actor IS NOT NULL AND (
             (entity='quotation' AND entity_id=? AND action IN ('quotation.create','quotation.material_revision'))
             OR (entity='booking' AND entity_id=? AND action IN ('booking.create','booking.material_revision'))
           )""",
        (q["id"], q["booking_id"]),
    ).fetchall()
    makers.update(row["actor"] for row in rows)
    return makers


def _governed_review_override(conn, actor, q, action, override_reason=None):
    privileged = actor.get("role") in ("super_admin", "super_platform_admin")
    is_maker = actor["id"] in _transaction_maker_ids(conn, q)
    if is_maker and not privileged:
        raise ForbiddenError("separation of duties: a creator or material reviser may not review this transaction")
    if privileged:
        if not override_reason or not str(override_reason).strip():
            raise ValidationError("governed Super Administrator override reason is required")
        audit(conn, actor, "quotation.governed_override", "quotation", q["id"],
              new={"action": action}, reason=str(override_reason).strip())


def return_quotation(conn, actor, qid, reason, override_reason=None):
    require(actor, "quotation.return")
    q = _quote(conn, qid, actor)
    if q["status"] != "pending_approval":
        raise ConflictError("quotation is not pending approval")
    if not reason or not str(reason).strip():
        raise ValidationError("return reason is required")
    if CONFIG["separation_of_duties"]:
        _governed_review_override(conn, actor, q, "return", override_reason)
    conn.execute("UPDATE quotations SET status='returned' WHERE id=?", (qid,))
    conn.execute("UPDATE bookings SET stage='REVISION_REQUESTED' WHERE id=?", (q["booking_id"],))
    audit(conn, actor, "quotation.return", "quotation", qid,
          new={"status": "returned"}, reason=str(reason).strip())
    conn.commit()


def reject_quotation(conn, actor, qid, reason, override_reason=None):
    require(actor, "quotation.reject")
    q = _quote(conn, qid, actor)
    if q["status"] != "pending_approval":
        raise ConflictError("quotation is not pending approval")
    if not reason or not str(reason).strip():
        raise ValidationError("rejection reason is required")
    if CONFIG["separation_of_duties"]:
        _governed_review_override(conn, actor, q, "reject", override_reason)
    conn.execute("UPDATE quotations SET status='rejected' WHERE id=?", (qid,))
    conn.execute("UPDATE bookings SET stage='REVISION_REQUESTED' WHERE id=?", (q["booking_id"],))
    audit(conn, actor, "quotation.reject", "quotation", qid,
          new={"status": "rejected"}, reason=str(reason).strip())
    conn.commit()


def send_quotation(conn, actor, qid):
    require(actor, "quotation.create")   # estimator/ops can send once approved
    q = _quote(conn, qid, actor)
    if q["status"] != "approved":
        raise ConflictError("CONTROL: quotation must be approved before sending")
    conn.execute("UPDATE quotations SET status='sent' WHERE id=?", (qid,))
    conn.execute("UPDATE bookings SET stage='QUOTATION_SENT' WHERE id=?", (q["booking_id"],))
    audit(conn, actor, "quotation.send", "quotation", qid, new={"status": "sent"})
    conn.commit()


def accept_quotation(conn, actor, qid, accepted_by, position=None, terms_version="v1"):
    q = _quote(conn, qid, actor)
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
    q = _quote(conn, qid, actor)
    conn.execute("UPDATE quotations SET status='revision' WHERE id=?", (qid,))
    conn.execute("UPDATE bookings SET stage='REVISION_REQUESTED' WHERE id=?", (q["booking_id"],))
    audit(conn, actor, "quotation.revision", "quotation", qid, reason=reason)
    conn.commit()


def create_payment_request(conn, actor, bid, provider: PaymentProvider = None):
    require(actor, "payment.create")
    b = _booking(conn, bid, actor)
    q = _latest_quote(conn, bid)
    if not q or q["status"] != "accepted":
        raise ConflictError("CONTROL: a payment request requires an accepted quotation")
    if conn.execute("SELECT 1 FROM payment_requests WHERE booking_id=?", (bid,)).fetchone():
        raise ConflictError("payment request already exists for this booking")
    n = conn.execute("SELECT COUNT(*) c FROM payment_requests").fetchone()["c"]
    no = f"PR-{5001 + n}"
    # derive strictly from the ACCEPTED quotation's stored values + snapshot (never current config)
    q_dp_snap = q["dp_snapshot"] if "dp_snapshot" in q.keys() else None
    cur = conn.execute(
        "INSERT INTO payment_requests(no,booking_id,quotation_id,currency,amount_due,dp_pct,"
        "dp_snapshot,status,created_by,created_at) VALUES(?,?,?,?,?,?,?, 'REQUEST_CREATED',?,?)",
        (no, bid, q["id"], "PHP", q["dp_amount"], q["dp_pct"], q_dp_snap, actor["id"], now()))
    prid = cur.lastrowid
    import tenant; tenant.stamp(conn, actor, "payment_requests", prid)
    _set_stage(conn, actor, b, "AWAITING_DOWNPAYMENT", "payment request created")
    audit(conn, actor, "payment.request", "payment_request", prid, new={"no": no, "amount": q["dp_amount"]})
    conn.commit()
    return prid


def register_payment_link(conn, actor, prid, provider: PaymentProvider):
    require(actor, "payment.link")
    pr = conn.execute("SELECT * FROM payment_requests WHERE id=?", (prid,)).fetchone()
    if not pr:
        raise NotFoundError("payment request not found")
    import tenant; tenant.guard(actor, pr)                # cross-tenant -> 404
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
    import tenant; tenant.guard(actor, pr)                # cross-tenant -> 404
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
    import tenant; tenant.guard(actor, pr)                # cross-tenant -> 404
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
    b = _booking(conn, bid, actor)
    q = _latest_quote(conn, bid)
    if b["job_id"]:                                       # duplicate prevention / idempotent
        return conn.execute("SELECT no FROM jobs WHERE id=?", (b["job_id"],)).fetchone()["no"]
    if not q or q["status"] != "accepted":
        raise ConflictError("CONTROL: cannot confirm — quotation not accepted")
    pr = conn.execute("SELECT * FROM payment_requests WHERE booking_id=?", (bid,)).fetchone()
    if not pr or pr["status"] != "VERIFIED":
        raise ConflictError("CONTROL: cannot confirm — downpayment not verified")
    import policy
    _job_prefix = policy.number_prefix(conn, policy.policy_context(conn, actor), "job", "JO")
    with conn:                                            # portable transaction (sqlite + postgres)
        n = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        job_no = f"{_job_prefix}-{2050 + n}"
        cur = conn.execute(
            "INSERT INTO jobs(no,booking_id,quotation_id,customer_id,status,amount,scheduled_at,created_by,created_at)"
            " VALUES(?,?,?,?, 'CONFIRMED',?,?,?,?)",
            (job_no, bid, q["id"], b["customer_id"], q["total"], now(), actor["id"], now()))
        job_id = cur.lastrowid
        conn.execute("UPDATE bookings SET stage='CONFIRMED', job_id=? WHERE id=?", (job_id, bid))
        audit(conn, actor, "job.confirm", "job", job_id,
              new={"no": job_no, "from_booking": b["ref"], "from_quote": q["no"]})
    import tenant; tenant.stamp(conn, actor, "jobs", job_id)   # job inherits booking's tenant
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
