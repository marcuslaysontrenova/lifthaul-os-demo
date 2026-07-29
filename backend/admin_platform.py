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

import datetime

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
  key TEXT NOT NULL, value TEXT, updated_by INTEGER, updated_at TEXT,
  UNIQUE(scope, scope_ref, key));
"""

# tenant_id 0 is reserved to mean "platform-global" (system role templates, platform config).
PLATFORM_TENANT = 0


def init(conn):
    conn.executescript(SCHEMA)
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
def set_config(conn, scope, scope_ref, key, value, actor=None):
    if scope not in ("platform", "tenant", "unit", "user"):
        raise core.ConflictError(f"invalid config scope '{scope}'")
    conn.execute("INSERT INTO platform_config(scope,scope_ref,key,value,updated_by,updated_at)"
                 " VALUES(?,?,?,?,?,?)"
                 " ON CONFLICT(scope,scope_ref,key) DO UPDATE SET value=excluded.value,"
                 " updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                 (scope, scope_ref or "", key, str(value), (actor or {}).get("id"), _now()))
    if actor:
        core.audit(conn, actor, "CONFIG_SET", "platform_config", 0,
                   new={"scope": scope, "ref": scope_ref, "key": key, "value": str(value)})
    conn.commit()


def resolve_config(conn, key, tenant=None, unit=None, user=None):
    """Return (value, source_scope) using most-specific-wins; (None, None) if unset."""
    for scope, ref in (("user", user), ("unit", unit), ("tenant", tenant), ("platform", "")):
        if ref is None:
            continue
        row = conn.execute("SELECT value FROM platform_config WHERE scope=? AND scope_ref=? AND key=?",
                           (scope, ref, key)).fetchone()
        if row:
            return row["value"], scope
    return None, None
