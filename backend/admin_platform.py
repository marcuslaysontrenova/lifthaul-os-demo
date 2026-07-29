"""LiftHaul OS — Enterprise Administration Platform (Platform 1) foundation.

This is the control layer that governs the operational modules — it does NOT replace
them. It implements the three keystone capabilities from the Enterprise Product
Blueprint (docs/blueprint), on which the rest of Platform 1 depends:

  * C-003  Tenant / Organization dimension  (LiftHaul OS = product, RGO = tenant 0)
  * C-005  Data-driven RBAC                  (roles/permissions as DATA, not core.PERMISSIONS)
  * C-008  Configuration cascade + registry  (platform -> tenant -> unit -> user)

Design rules honored:
  - ADDITIVE: the 96-test commercial spine keeps working; this sits above it. The
    seed mirrors today's core.PERMISSIONS so behavior parity is provable before any cutover.
  - CONFIGURATION-FIRST (ED-004): roles, permissions, and limits become admin-owned data.
  - MULTI-TENANT: roles/config are tenant-scoped; system roles are global templates.
  - GOVERNED: mutations emit audit_logs events via core.audit.

Role model reflects the CTO 4-layer administration hierarchy:
  Layer 1 Platform Administration   (Super Platform Admin, Platform Admin)
  Layer 2 Company Administration    (Business Administrator)
  Layer 3 Functional Administration (CRM / Fleet / Finance / Dispatch / Safety Admin)
  Layer 4 Operational Users         (Ops Mgr, Estimator, Approver, Finance, Dispatcher,
                                     Fleet Mgr, Safety Officer, Mechanic, Driver, Operator, Customer)
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import re
import secrets
import struct
import time

import core   # for audit() + shared error types; core does not import this module


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, legal_name TEXT NOT NULL,
  trading_name TEXT, status TEXT DEFAULT 'ACTIVE', plan TEXT DEFAULT 'STANDARD',
  created_at TEXT);

CREATE TABLE IF NOT EXISTS admin_permissions(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, module TEXT NOT NULL,
  action TEXT NOT NULL, description TEXT);

CREATE TABLE IF NOT EXISTS admin_roles(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL DEFAULT 0, code TEXT NOT NULL,
  name TEXT NOT NULL, layer INTEGER NOT NULL, system_locked INTEGER DEFAULT 0,
  created_at TEXT, UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS admin_role_permissions(
  id INTEGER PRIMARY KEY, role_id INTEGER NOT NULL REFERENCES admin_roles(id),
  permission_code TEXT NOT NULL, UNIQUE(role_id, permission_code));

CREATE TABLE IF NOT EXISTS admin_user_roles(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, role_id INTEGER NOT NULL REFERENCES admin_roles(id),
  UNIQUE(user_id, role_id));

CREATE TABLE IF NOT EXISTS platform_config(
  id INTEGER PRIMARY KEY, scope TEXT NOT NULL, scope_ref TEXT NOT NULL DEFAULT '',
  key TEXT NOT NULL, value TEXT, effective_to TEXT, updated_by INTEGER, updated_at TEXT,
  UNIQUE(scope, scope_ref, key));

CREATE TABLE IF NOT EXISTS login_history(
  id INTEGER PRIMARY KEY, user_id INTEGER, email TEXT, success INTEGER NOT NULL,
  reason TEXT, ip TEXT, ts TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS mfa_enrollments(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, secret TEXT NOT NULL,
  confirmed INTEGER NOT NULL DEFAULT 0, enrolled_at TEXT, UNIQUE(user_id));
"""

# tenant_id 0 is reserved to mean "platform-global" (system role templates, platform config).
PLATFORM_TENANT = 0


def init(conn):
    conn.executescript(SCHEMA)
    _ensure_columns(conn, "users", {"status": "TEXT NOT NULL DEFAULT 'ACTIVE'", "last_login_at": "TEXT"})
    _ensure_columns(conn, "sessions", {"ip": "TEXT", "last_seen": "TEXT"})
    _ensure_columns(conn, "platform_config", {"effective_to": "TEXT"})
    _ensure_columns(conn, "audit_logs", {"correlation_id": "TEXT"})
    conn.commit()


def _ensure_columns(conn, table, cols_spec):
    """Idempotent additive migration for pre-existing SQLite DBs (C-006/C-007).
    Fresh DBs get columns from core.SCHEMA; Postgres from the translated DDL."""
    try:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return                                             # non-SQLite: columns come from DDL
    if not have:
        return
    for col, spec in cols_spec.items():
        if col not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {spec}")
    conn.commit()


# --------------------------------------------------------------------------- #
# Permission catalog  (module x action)  — seeded from today's require() sites,
# expanded with the CTO's full quotation action set and the admin modules.
# --------------------------------------------------------------------------- #
_ACTIONS_CRUD = ("view", "create", "edit", "delete", "export")
CATALOG = []

def _perm(module, action, desc=""):
    CATALOG.append((f"{module}.{action}", module, action, desc or f"{action} {module}"))

# Commercial / operational (mirror existing capabilities)
for _m in ("customer", "booking", "job"):
    for _a in _ACTIONS_CRUD:
        _perm(_m, _a)
for _a in ("view", "create", "edit", "delete", "approve", "archive", "restore",
           "export", "print", "email", "generate_pdf", "submit", "revise"):
    _perm("quotation", _a)                       # CTO: each independently assignable
for _a in ("view", "review", "ready", "create", "read"):
    _perm("booking", _a)
for _a in ("read", "create", "link", "verify", "refund", "view"):
    _perm("payment", _a)
for _a in ("read", "dispatch", "transition", "safety", "reserve", "view"):
    _perm("job", _a)
for _a in ("view", "manage", "reserve"):
    _perm("fleet", _a)
for _a in ("view", "record", "manage"):
    _perm("safety", _a)
for _a in ("view", "post", "approve", "export"):
    _perm("finance", _a)
# Administration modules (Platform 1)
for _mod in ("tenant", "license", "org", "user_admin", "role_admin", "permission_admin",
             "workflow_admin", "crm_admin", "fleet_admin", "finance_admin", "dispatch_admin",
             "master_data", "system_config", "branding", "integration", "ai_admin",
             "reporting", "audit", "security"):
    for _a in ("view", "manage"):
        _perm(_mod, _a)


# --------------------------------------------------------------------------- #
# System roles  (global templates, tenant_id = PLATFORM, system_locked = 1)
# grants use the same wildcard grammar as core.can: "*", "module.*", or exact code.
#
# IMPORTANT: the operational permission model is assembled across modules at import
# time — core.PERMISSIONS is the BASE and admin/ops/catalog/pdfgen augment it with
# `|=`. So we seed operational-role grants FROM the assembled core.PERMISSIONS (the
# single source of truth), guaranteeing parity by construction and auto-capturing any
# future permission a domain module contributes. Only the NEW admin-layer roles
# (Platform 1) carry explicit grants here.
# --------------------------------------------------------------------------- #

# New administration roles introduced by Platform 1 (not present in core.PERMISSIONS).
ADMIN_ROLES = [
    # code, display name, layer, grant patterns
    ("super_platform_admin", "Super Platform Administrator", 1, {"*"}),
    ("platform_admin",       "Platform Administrator",       1,
        {"tenant.*", "license.*", "integration.*", "system_config.*", "ai_admin.*",
         "workflow_admin.*", "branding.*", "security.*", "audit.*", "reporting.*",
         "master_data.*"}),
    ("business_admin",       "Business Administrator",       2,
        {"org.*", "user_admin.*", "role_admin.*", "permission_admin.*", "crm_admin.*",
         "master_data.*", "system_config.view", "reporting.*", "audit.view"}),
    ("crm_admin",            "CRM Administrator",            3, {"crm_admin.*", "customer.*", "contact.*", "address.*"}),
    ("fleet_admin",          "Fleet Administrator",          3, {"fleet_admin.*", "equipment.*", "vehicle.*", "maintenance.*", "inspection.*"}),
    ("finance_admin",        "Finance Administrator",        3, {"finance_admin.*", "invoice.*", "expense.*", "payment.*", "refund.*"}),
    ("dispatch_admin",       "Dispatch Administrator",       3, {"dispatch_admin.*", "job.read", "job.dispatch", "job.transition", "reservation.*"}),
    ("safety_admin",         "Safety Administrator",         3, {"safety.*", "incident.*"}),
]

# Operational roles: (code -> display name, layer). Grants are pulled from the assembled
# core.PERMISSIONS at seed time; DEFAULT_GRANT is used only if a code isn't defined there.
OPERATIONAL_ROLE_META = {
    "super_admin":        ("Super Administrator", 1),
    "admin":              ("Administrator",       1),
    "owner":              ("Owner",               1),
    "operations_manager": ("Operations Manager",  4),
    "estimator":          ("Estimator",           4),
    "approver":           ("Approver",            4),
    "finance":            ("Finance Clerk",       4),
    "dispatcher":         ("Dispatcher",          4),
    "fleet_manager":      ("Fleet Manager",       4),
    "safety_officer":     ("Safety Officer",      4),
    "mechanic":           ("Mechanic",            4),
    "driver":             ("Driver",              4),
    "operator":           ("Operator",            4),
    "customer":           ("Customer",            4),
}
_DEFAULT_GRANT = {"job.read"}

# Default configuration seed — the constants ED-004 says become admin-owned data.
DEFAULT_CONFIG = {
    "approval.quotation_threshold": "500000",   # was hard-coded ₱500k in the approval path
    "finance.downpayment_pct": "30",            # was hard-coded 30%
    "finance.vat_pct": "12",                    # was hard-coded 12%
    "quotation.validity_days": "30",
    "dispatch.double_book": "block",            # reservation conflict policy
    "iam.rbac_source": "hybrid",                # legacy | hybrid | db  (C-005 cutover switch)
    # Authentication policy (C-007) — all admin-owned, resolved via the config cascade
    "auth.pw_min_length": "10",
    "auth.pw_require_complexity": "true",       # upper + lower + digit
    "auth.lockout_threshold": "5",              # consecutive failures before lock (0 = off)
    "auth.lockout_window_min": "15",            # minutes the failure window spans
    "auth.mfa_policy": "optional",              # off | optional | required
}


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #
def seed(conn, actor=None):
    """Idempotently seed permission catalog, system roles, platform config, and tenant 0."""
    for code, module, action, desc in CATALOG:
        conn.execute("INSERT INTO admin_permissions(code,module,action,description) VALUES(?,?,?,?)"
                     " ON CONFLICT(code) DO UPDATE SET module=excluded.module,"
                     " action=excluded.action, description=excluded.description",
                     (code, module, action, desc))
    def _seed_role(code, name, layer, grants):
        rid = _upsert_role(conn, PLATFORM_TENANT, code, name, layer, system_locked=1)
        for g in grants:
            conn.execute("INSERT INTO admin_role_permissions(role_id,permission_code) VALUES(?,?)"
                         " ON CONFLICT(role_id,permission_code) DO NOTHING", (rid, g))
    for code, name, layer, grants in ADMIN_ROLES:
        _seed_role(code, name, layer, grants)
    for code, (name, layer) in OPERATIONAL_ROLE_META.items():
        _seed_role(code, name, layer, set(core.PERMISSIONS.get(code, _DEFAULT_GRANT)))
    for key, val in DEFAULT_CONFIG.items():
        set_config(conn, "platform", "", key, val, actor=actor)
    # Tenant zero: RGO Machine Rigging Services
    if not get_tenant(conn, "RGO"):
        create_tenant(conn, "RGO", "RGO Machine Rigging Services", trading_name="RGO Machine Rigging",
                      actor=actor)
    conn.commit()


def _upsert_role(conn, tenant_id, code, name, layer, system_locked=0) -> int:
    row = conn.execute("SELECT id FROM admin_roles WHERE tenant_id=? AND code=?",
                       (tenant_id, code)).fetchone()
    if row:
        conn.execute("UPDATE admin_roles SET name=?, layer=?, system_locked=? WHERE id=?",
                     (name, layer, system_locked, row["id"]))
        return row["id"]
    cur = conn.execute("INSERT INTO admin_roles(tenant_id,code,name,layer,system_locked,created_at)"
                       " VALUES(?,?,?,?,?,?)", (tenant_id, code, name, layer, system_locked, _now()))
    return cur.lastrowid


# --------------------------------------------------------------------------- #
# Tenants (C-003)
# --------------------------------------------------------------------------- #
def create_tenant(conn, code, legal_name, trading_name=None, plan="STANDARD", actor=None) -> int:
    if get_tenant(conn, code):
        raise core.ConflictError(f"tenant '{code}' already exists")
    cur = conn.execute("INSERT INTO tenants(code,legal_name,trading_name,status,plan,created_at)"
                       " VALUES(?,?,?, 'ACTIVE', ?, ?)", (code, legal_name, trading_name, plan, _now()))
    tid = cur.lastrowid
    if actor:
        core.audit(conn, actor, "TENANT_CREATED", "tenants", tid, new={"code": code})
    conn.commit()
    return tid


def get_tenant(conn, code):
    return conn.execute("SELECT * FROM tenants WHERE code=?", (code,)).fetchone()


def list_tenants(conn):
    return conn.execute("SELECT * FROM tenants ORDER BY id").fetchall()


# --------------------------------------------------------------------------- #
# Roles & permissions (C-005) — data-driven, replaces reliance on core.PERMISSIONS
# --------------------------------------------------------------------------- #
def create_role(conn, tenant_code, code, name, layer=4, grants=None, actor=None) -> int:
    """Create a tenant-scoped custom role. Enforcement follows immediately — no code change."""
    t = get_tenant(conn, tenant_code)
    if not t:
        raise core.ConflictError(f"unknown tenant '{tenant_code}'")
    existing = conn.execute("SELECT id FROM admin_roles WHERE tenant_id=? AND code=?",
                            (t["id"], code)).fetchone()
    if existing:
        raise core.ConflictError(f"role '{code}' already exists for tenant '{tenant_code}'")
    cur = conn.execute("INSERT INTO admin_roles(tenant_id,code,name,layer,system_locked,created_at)"
                       " VALUES(?,?,?,?,0,?)", (t["id"], code, name, layer, _now()))
    rid = cur.lastrowid
    for g in (grants or set()):
        grant_permission(conn, rid, g)
    if actor:
        core.audit(conn, actor, "ROLE_UPSERTED", "admin_roles", rid, new={"code": code, "grants": sorted(grants or [])})
    conn.commit()
    return rid


def grant_permission(conn, role_id, permission_code, actor=None):
    role = conn.execute("SELECT * FROM admin_roles WHERE id=?", (role_id,)).fetchone()
    if not role:
        raise core.ConflictError("unknown role")
    if role["system_locked"]:
        raise core.ForbiddenError("system role is locked; clone it to customize")
    conn.execute("INSERT INTO admin_role_permissions(role_id,permission_code) VALUES(?,?)"
                 " ON CONFLICT(role_id,permission_code) DO NOTHING", (role_id, permission_code))
    if actor:
        core.audit(conn, actor, "ROLE_PERMISSION_CHANGED", "admin_roles", role_id,
                   new={"granted": permission_code})
    conn.commit()


def revoke_permission(conn, role_id, permission_code, actor=None):
    conn.execute("DELETE FROM admin_role_permissions WHERE role_id=? AND permission_code=?",
                 (role_id, permission_code))
    if actor:
        core.audit(conn, actor, "ROLE_PERMISSION_CHANGED", "admin_roles", role_id,
                   new={"revoked": permission_code})
    conn.commit()


def list_roles(conn, tenant_code=None):
    """Global system roles (tenant 0) plus this tenant's custom roles."""
    if tenant_code is None:
        return conn.execute("SELECT * FROM admin_roles ORDER BY layer, code").fetchall()
    t = get_tenant(conn, tenant_code)
    tid = t["id"] if t else -1
    return conn.execute("SELECT * FROM admin_roles WHERE tenant_id IN (?, ?) ORDER BY layer, code",
                        (PLATFORM_TENANT, tid)).fetchall()


def role_by_code(conn, tenant_code, code):
    t = get_tenant(conn, tenant_code)
    tid = t["id"] if t else PLATFORM_TENANT
    return conn.execute("SELECT * FROM admin_roles WHERE code=? AND tenant_id IN (?, ?) ORDER BY tenant_id DESC",
                        (code, tid, PLATFORM_TENANT)).fetchone()


def assign_role(conn, user_id, role_id, actor=None):
    conn.execute("INSERT INTO admin_user_roles(user_id,role_id) VALUES(?,?)"
                 " ON CONFLICT(user_id,role_id) DO NOTHING", (user_id, role_id))
    if actor:
        core.audit(conn, actor, "USER_ROLE_GRANTED", "admin_user_roles", user_id, new={"role_id": role_id})
    conn.commit()


def unassign_role(conn, user_id, role_id, actor=None):
    conn.execute("DELETE FROM admin_user_roles WHERE user_id=? AND role_id=?", (user_id, role_id))
    conn.commit()


def effective_role_grants(conn, role_id) -> set:
    """Grant patterns held by a single role (used for parity checks / role editor)."""
    rows = conn.execute("SELECT permission_code p FROM admin_role_permissions WHERE role_id=?",
                        (role_id,)).fetchall()
    return {r["p"] for r in rows}


def effective_permissions(conn, user_id) -> set:
    """Union of all grant patterns from every role assigned to the user."""
    rows = conn.execute(
        "SELECT rp.permission_code p FROM admin_user_roles ur"
        " JOIN admin_role_permissions rp ON rp.role_id = ur.role_id WHERE ur.user_id=?",
        (user_id,)).fetchall()
    return {r["p"] for r in rows}


def has_permission(conn, user_id, action) -> bool:
    """Data-driven equivalent of core.can — same wildcard grammar, but grants live in the DB."""
    return _match(effective_permissions(conn, user_id), action)


def apply_rbac(conn, actor):
    """Enrich an actor with DB-sourced permissions per the `iam.rbac_source` flag (C-005).

    Reversible cutover switch (a platform_config value, admin-owned):
      * legacy  — leave the actor untouched; core.can uses in-code PERMISSIONS.
      * hybrid  — use DB permissions IF the user has assigned roles, else legacy.
      * db      — always use DB permissions (empty set = deny-all).
    Returns the (possibly enriched) actor. server._actor calls this on every request.
    """
    src, _ = resolve_config(conn, "iam.rbac_source")
    src = (src or "legacy").lower()
    if src in ("db", "hybrid") and actor and "id" in actor:
        perms = effective_permissions(conn, actor["id"])
        if perms or src == "db":
            actor["perms"] = perms
    return actor


def backfill_user_roles(conn, tenant_code="RGO", actor=None) -> int:
    """Assign each existing user the system role matching their legacy users.role.

    Completes the cutover at parity (system-role grants == assembled core.PERMISSIONS).
    Idempotent; returns the number of new assignments made."""
    made = 0
    for u in conn.execute("SELECT id, role FROM users").fetchall():
        role = role_by_code(conn, tenant_code, u["role"])
        if not role:
            continue
        exists = conn.execute("SELECT 1 FROM admin_user_roles WHERE user_id=? AND role_id=?",
                              (u["id"], role["id"])).fetchone()
        if not exists:
            assign_role(conn, u["id"], role["id"], actor=actor)
            made += 1
    return made


def _match(patterns, action) -> bool:
    for p in patterns:
        if p == "*" or p == action:
            return True
        if p.endswith(".*") and action.startswith(p[:-1]):
            return True
    return False


# --------------------------------------------------------------------------- #
# Configuration cascade (C-008): platform -> tenant -> unit -> user (most specific wins)
# --------------------------------------------------------------------------- #
# Config scopes: platform/tenant/user (base) + the org levels added by C-004.
_CONFIG_SCOPES = ("platform", "tenant", "business_unit", "branch", "department", "team", "unit", "user")


def set_config(conn, scope, scope_ref, key, value, actor=None, effective_to=None):
    if scope not in _CONFIG_SCOPES:
        raise core.ConflictError(f"invalid config scope '{scope}'")
    conn.execute("INSERT INTO platform_config(scope,scope_ref,key,value,effective_to,updated_by,updated_at)"
                 " VALUES(?,?,?,?,?,?,?)"
                 " ON CONFLICT(scope,scope_ref,key) DO UPDATE SET value=excluded.value,"
                 " effective_to=excluded.effective_to, updated_by=excluded.updated_by,"
                 " updated_at=excluded.updated_at",
                 (scope, scope_ref or "", key, str(value), effective_to, (actor or {}).get("id"), _now()))
    if actor:
        core.audit(conn, actor, "CONFIG_SET", "platform_config", 0,
                   new={"scope": scope, "ref": scope_ref, "key": key, "value": str(value)})
    conn.commit()


def resolve_config(conn, key, tenant=None, unit=None, user=None):
    """Return (value, source_scope) using most-specific-wins; (None, None) if unset."""
    r = resolve_config_chain(conn, key, [("user", user), ("unit", unit), ("tenant", tenant)])
    return r["value"], r["scope"]


def resolve_config_chain(conn, key, chain):
    """Resolve `key` down an ordered scope chain (most-specific first); platform is always
    the final fallback. Skips expired rows (effective_to < today). Returns a dict with the
    effective value, source scope + ref, updated_at, and the fallback path walked (C-004 §11)."""
    today = datetime.date.today().isoformat()
    path = []
    for scope, ref in list(chain) + [("platform", "")]:
        if ref is None:
            continue
        path.append(f"{scope}:{ref or '-'}")
        row = conn.execute(
            "SELECT value, updated_at, effective_to FROM platform_config"
            " WHERE scope=? AND scope_ref=? AND key=?", (scope, ref, key)).fetchone()
        if row and (row["effective_to"] is None or row["effective_to"] >= today):
            return {"value": row["value"], "scope": scope, "scope_ref": ref,
                    "updated_at": row["updated_at"], "fallback_path": path}
    return {"value": None, "scope": None, "scope_ref": None, "updated_at": None, "fallback_path": path}


# --------------------------------------------------------------------------- #
# User lifecycle administration (C-006)
# Full lifecycle above core.create_user: invite -> active <-> suspended/locked ->
# deactivated (offboard, soft). Non-active users cannot authenticate or act
# (enforced in core.login / core.actor_for); state changes revoke live sessions.
# --------------------------------------------------------------------------- #
STATUSES = {"ACTIVE", "SUSPENDED", "LOCKED", "DEACTIVATED"}


def create_user(conn, actor, email, password, role, name=None, tenant_code="RGO", customer_id=None) -> int:
    """Invite/create a user AND assign the matching system role so DB-RBAC governs them."""
    validate_password(conn, password, tenant=tenant_code)          # C-007 policy
    uid = core.create_user(conn, email, password, role, name, customer_id)
    r = role_by_code(conn, tenant_code, role)
    if r:
        assign_role(conn, uid, r["id"])
    if actor:
        core.audit(conn, actor, "USER_INVITED", "users", uid, new={"email": email, "role": role})
    conn.commit()
    return uid


def set_status(conn, actor, user_id, status):
    status = status.upper()
    if status not in STATUSES:
        raise core.ConflictError(f"invalid user status '{status}'")
    old = conn.execute("SELECT status FROM users WHERE id=?", (user_id,)).fetchone()
    if not old:
        raise core.ConflictError("unknown user")
    conn.execute("UPDATE users SET status=? WHERE id=?", (status, user_id))
    if status != "ACTIVE":
        revoke_sessions(conn, user_id)                     # kill live sessions immediately
    if actor:
        core.audit(conn, actor, "USER_STATUS_CHANGED", "users", user_id,
                   old={"status": old["status"]}, new={"status": status})
    conn.commit()


def suspend_user(conn, actor, uid):    set_status(conn, actor, uid, "SUSPENDED")
def activate_user(conn, actor, uid):   set_status(conn, actor, uid, "ACTIVE")
def lock_user(conn, actor, uid):       set_status(conn, actor, uid, "LOCKED")
def unlock_user(conn, actor, uid):     set_status(conn, actor, uid, "ACTIVE")
def deactivate_user(conn, actor, uid): set_status(conn, actor, uid, "DEACTIVATED")   # offboard (soft)


def reset_password(conn, actor, user_id, new_password):
    """Admin-initiated password reset: set a new hash and revoke all sessions."""
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
        raise core.ConflictError("unknown user")
    validate_password(conn, new_password)                          # C-007 policy
    conn.execute("UPDATE users SET pw_hash=? WHERE id=?", (core.hash_pw(new_password), user_id))
    revoke_sessions(conn, user_id)
    if actor:
        core.audit(conn, actor, "USER_PASSWORD_RESET", "users", user_id)
    conn.commit()


def revoke_sessions(conn, user_id) -> int:
    cur = conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    conn.commit()
    return getattr(cur, "rowcount", 0) or 0


def list_users(conn):
    return conn.execute("SELECT id,email,role,name,status,last_login_at,created_at"
                        " FROM users ORDER BY id").fetchall()


def get_user(conn, user_id):
    return conn.execute("SELECT id,email,role,name,status,last_login_at,created_at"
                        " FROM users WHERE id=?", (user_id,)).fetchone()


def user_roles(conn, user_id):
    return conn.execute(
        "SELECT r.code,r.name,r.layer FROM admin_user_roles ur"
        " JOIN admin_roles r ON r.id=ur.role_id WHERE ur.user_id=? ORDER BY r.layer, r.code",
        (user_id,)).fetchall()


def permission_review(conn, user_id) -> set:
    """The effective permission set an admin sees when reviewing a user (Vol2 §3.4)."""
    return effective_permissions(conn, user_id)


def user_audit(conn, user_id, limit=100):
    return conn.execute(
        "SELECT ts,actor,role,action,new_value FROM audit_logs"
        " WHERE entity='users' AND entity_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit)).fetchall()


# --------------------------------------------------------------------------- #
# Authentication policy, MFA & session governance (C-007)
# All policy is admin-owned config (ED-004), resolved through the cascade.
# --------------------------------------------------------------------------- #
def password_policy(conn, tenant=None) -> dict:
    def g(key, default):
        v, _ = resolve_config(conn, key, tenant=tenant)
        return v if v is not None else default
    return {"min_length": int(g("auth.pw_min_length", "10")),
            "complexity": g("auth.pw_require_complexity", "true").lower() == "true"}


def validate_password(conn, pw, tenant=None):
    p = password_policy(conn, tenant)
    if not pw or len(pw) < p["min_length"]:
        raise core.ValidationError(f"password must be at least {p['min_length']} characters")
    if p["complexity"] and (not re.search(r"[A-Z]", pw) or not re.search(r"[a-z]", pw)
                            or not re.search(r"\d", pw)):
        raise core.ValidationError("password must include an upper-case letter, a lower-case letter and a digit")


# ---- login history + lockout (persistent, tenant-aware) -------------------- #
def record_login(conn, email, success, reason, ip=None):
    try:
        u = conn.execute("SELECT id FROM users WHERE email=?", (email.lower(),)).fetchone()
        conn.execute("INSERT INTO login_history(user_id,email,success,reason,ip,ts) VALUES(?,?,?,?,?,?)",
                     (u["id"] if u else None, email.lower(), 1 if success else 0, reason, ip, _now()))
        conn.commit()
    except Exception:
        pass                                               # degrade gracefully on a non-seeded conn


def login_locked(conn, email, tenant=None) -> bool:
    """Locked when consecutive recent failures (since the last success, within the
    window) reach the threshold. Threshold 0 disables lockout."""
    try:
        thr, _ = resolve_config(conn, "auth.lockout_threshold", tenant=tenant)
        win, _ = resolve_config(conn, "auth.lockout_window_min", tenant=tenant)
        thr, win = int(thr or "5"), int(win or "15")
        if thr <= 0:
            return False
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(minutes=win)).isoformat(timespec="seconds")
        rows = conn.execute("SELECT success FROM login_history WHERE email=? AND ts>=? ORDER BY id DESC",
                            (email.lower(), cutoff)).fetchall()
        fails = 0
        for r in rows:
            if r["success"]:
                break
            fails += 1
        return fails >= thr
    except Exception:
        return False


def list_login_history(conn, user_id=None, email=None, limit=100):
    if user_id is not None:
        return conn.execute("SELECT * FROM login_history WHERE user_id=? ORDER BY id DESC LIMIT ?",
                            (user_id, limit)).fetchall()
    if email:
        return conn.execute("SELECT * FROM login_history WHERE email=? ORDER BY id DESC LIMIT ?",
                            (email.lower(), limit)).fetchall()
    return conn.execute("SELECT * FROM login_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# ---- MFA (TOTP, RFC 6238, stdlib only) ------------------------------------- #
def _b32secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret_b32, counter) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def _totp(secret_b32, t=None, step=30) -> str:
    t = time.time() if t is None else t
    return _hotp(secret_b32, int(t // step))


def verify_totp(secret_b32, code, t=None, step=30, window=1) -> bool:
    t = time.time() if t is None else t
    code = str(code).strip()
    base = int(t // step)
    return any(_hotp(secret_b32, base + w) == code
               for w in range(-window, window + 1) if base + w >= 0)


def enroll_mfa(conn, user_id) -> str:
    """Begin MFA enrollment: create/replace an unconfirmed secret; return it for the
    authenticator app (also expose as an otpauth:// URI via mfa_provisioning_uri)."""
    secret = _b32secret()
    conn.execute("INSERT INTO mfa_enrollments(user_id,secret,confirmed,enrolled_at) VALUES(?,?,0,?)"
                 " ON CONFLICT(user_id) DO UPDATE SET secret=excluded.secret, confirmed=0,"
                 " enrolled_at=excluded.enrolled_at", (user_id, secret, _now()))
    conn.commit()
    return secret


def mfa_provisioning_uri(secret, email, issuer="LiftHaul OS") -> str:
    return f"otpauth://totp/{issuer}:{email}?secret={secret}&issuer={issuer}"


def confirm_mfa(conn, user_id, code) -> bool:
    row = conn.execute("SELECT secret FROM mfa_enrollments WHERE user_id=?", (user_id,)).fetchone()
    if not row or not verify_totp(row["secret"], code):
        raise core.AuthError("invalid MFA code")
    conn.execute("UPDATE mfa_enrollments SET confirmed=1 WHERE user_id=?", (user_id,))
    conn.commit()
    return True


def verify_mfa(conn, user_id, code) -> bool:
    row = conn.execute("SELECT secret,confirmed FROM mfa_enrollments WHERE user_id=?", (user_id,)).fetchone()
    return bool(row and row["confirmed"] and verify_totp(row["secret"], code))


def mfa_enrolled(conn, user_id) -> bool:
    try:
        row = conn.execute("SELECT confirmed FROM mfa_enrollments WHERE user_id=?", (user_id,)).fetchone()
        return bool(row and row["confirmed"])
    except Exception:
        return False


def disable_mfa(conn, actor, user_id):
    conn.execute("DELETE FROM mfa_enrollments WHERE user_id=?", (user_id,))
    if actor:
        core.audit(conn, actor, "MFA_DISABLED", "users", user_id)
    conn.commit()


def mfa_policy(conn, tenant=None) -> str:
    v, _ = resolve_config(conn, "auth.mfa_policy", tenant=tenant)
    return (v or "optional").lower()


def mfa_required_for(conn, user_row, tenant=None) -> bool:
    pol = mfa_policy(conn, tenant)
    if pol == "required":
        return True
    if pol == "off":
        return False
    return mfa_enrolled(conn, user_row["id"])              # optional: enforced once enrolled


# ---- guarded login (lockout -> credentials -> status -> MFA -> session) ----- #
def guarded_login(conn, email, password, ip=None, tenant="RGO", mfa_code=None) -> str:
    if login_locked(conn, email, tenant):
        record_login(conn, email, False, "locked", ip)
        raise core.AuthError("account temporarily locked — too many failed attempts")
    row = conn.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()
    if not row or not core.verify_pw(password, row["pw_hash"]):
        record_login(conn, email, False, "invalid_credentials", ip)
        raise core.AuthError("invalid credentials")
    if core._user_status(row) != "ACTIVE":
        record_login(conn, email, False, "inactive", ip)
        raise core.AuthError("account is not active")
    if mfa_required_for(conn, row, tenant):
        if not mfa_code:
            record_login(conn, email, False, "mfa_required", ip)
            raise core.AuthError("MFA code required")
        if not verify_mfa(conn, row["id"], mfa_code):
            record_login(conn, email, False, "mfa_invalid", ip)
            raise core.AuthError("invalid MFA code")
    token = core.login(conn, email, password)              # verifies again + creates session
    if ip is not None:
        conn.execute("UPDATE sessions SET ip=?, last_seen=? WHERE token=?", (ip, _now(), token))
        conn.commit()
    record_login(conn, email, True, "ok", ip)
    return token


# ---- session administration ------------------------------------------------ #
def list_sessions(conn, user_id=None):
    if user_id is not None:
        return conn.execute("SELECT token,user_id,ip,created_at,last_seen FROM sessions"
                            " WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
    return conn.execute("SELECT token,user_id,ip,created_at,last_seen FROM sessions"
                        " ORDER BY created_at DESC").fetchall()


def revoke_session(conn, token, actor=None):
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    if actor:
        core.audit(conn, actor, "SESSION_REVOKED", "sessions", 0, new={"token": token[:6] + "…"})
    conn.commit()
