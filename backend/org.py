"""LiftHaul OS — Organization Hierarchy (Platform 1, C-004).

Tenant-scoped organization graph that governs user assignments, approval routing,
dispatch ownership, financial allocation, reporting, and configuration inheritance.

Design (CTO-surfaced decision): the pure hierarchy nodes — company, business_unit,
branch, department, team, operating_site, warehouse, service_area — are one normalized
adjacency-list table `org_units` with a `kind` discriminator + `parent_id`. This
delivers the "levels may be omitted / not rigid" mandate directly (fixed per-kind parent
tables would fight it). Cost centers, calendars, managers, user assignments, and company
profile have their own tables. Typed constructor functions (create_branch, ...) give the
requested per-entity API surface.

ADDITIVE: does not touch the operational spine. All mutations are tenant-scoped, audited
via core.audit, and validated against the graph rules in §3 of the C-004 directive.
"""
from __future__ import annotations

import datetime
import sqlite3

import core
import admin_platform as ap

# --------------------------------------------------------------------------- #
UNIT_KINDS = ("company", "business_unit", "branch", "department", "team",
              "operating_site", "warehouse", "service_area")
ACTIVE, INACTIVE, ARCHIVED = "ACTIVE", "INACTIVE", "ARCHIVED"
ASSIGN_TYPES = ("PRIMARY", "SECONDARY", "TEMPORARY")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.date.today().isoformat()


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS org_units(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, kind TEXT NOT NULL,
  code TEXT NOT NULL, name TEXT NOT NULL, description TEXT, parent_id INTEGER,
  status TEXT NOT NULL DEFAULT 'ACTIVE', effective_from TEXT, effective_to TEXT,
  created_by INTEGER, created_at TEXT, updated_by INTEGER, updated_at TEXT,
  archived_by INTEGER, archived_at TEXT,
  UNIQUE(tenant_id, kind, code));

CREATE TABLE IF NOT EXISTS cost_centers(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
  description TEXT, parent_id INTEGER, branch_id INTEGER, department_id INTEGER,
  manager_user_id INTEGER, status TEXT NOT NULL DEFAULT 'ACTIVE',
  effective_from TEXT, effective_to TEXT, budget_ref TEXT, external_code TEXT,
  default_expense_category TEXT, created_by INTEGER, created_at TEXT,
  updated_by INTEGER, updated_at TEXT, archived_by INTEGER, archived_at TEXT,
  UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS holiday_calendars(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
  scope TEXT DEFAULT 'company', parent_id INTEGER, status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_by INTEGER, created_at TEXT, updated_by INTEGER, updated_at TEXT,
  UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS holidays(
  id INTEGER PRIMARY KEY, calendar_id INTEGER NOT NULL REFERENCES holiday_calendars(id),
  name TEXT NOT NULL, day TEXT NOT NULL, recurring INTEGER NOT NULL DEFAULT 0,
  created_at TEXT);

CREATE TABLE IF NOT EXISTS working_calendars(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
  workdays TEXT NOT NULL DEFAULT 'Mon,Tue,Wed,Thu,Fri', shift_start TEXT DEFAULT '08:00',
  shift_end TEXT DEFAULT '17:00', break_minutes INTEGER DEFAULT 60, overtime_after TEXT,
  parent_id INTEGER, status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_by INTEGER, created_at TEXT, updated_by INTEGER, updated_at TEXT,
  UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS org_managers(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, scope_kind TEXT NOT NULL,
  scope_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT DEFAULT 'MANAGER',
  effective_from TEXT, effective_to TEXT, status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS user_organization_assignments(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  scope_kind TEXT NOT NULL, scope_id INTEGER NOT NULL,
  assignment_type TEXT NOT NULL DEFAULT 'PRIMARY', status TEXT NOT NULL DEFAULT 'ACTIVE',
  reason TEXT, effective_from TEXT, effective_to TEXT,
  assigned_by INTEGER, approved_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS company_profile(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, legal_name TEXT, trade_name TEXT,
  registration_number TEXT, tax_number TEXT, address TEXT, contact TEXT, logo TEXT,
  country TEXT, timezone TEXT, default_currency TEXT, locale TEXT, fiscal_year_start TEXT,
  default_branch_id INTEGER, default_cost_center_id INTEGER,
  default_holiday_calendar_id INTEGER, default_working_calendar_id INTEGER,
  updated_by INTEGER, updated_at TEXT, UNIQUE(tenant_id));
"""


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def _audit(conn, actor, action, entity, entity_id, old=None, new=None, reason=None):
    if actor:
        core.audit(conn, actor, action, entity, entity_id, old=old, new=new, reason=reason)


# --------------------------------------------------------------------------- #
# Org units (§2, §3)
# --------------------------------------------------------------------------- #
def _unit(conn, unit_id):
    return conn.execute("SELECT * FROM org_units WHERE id=?", (unit_id,)).fetchone()


def _valid_dates(effective_from, effective_to):
    if effective_from and effective_to and effective_from > effective_to:
        raise core.ValidationError("effective_from must be on or before effective_to")


def _assert_parent(conn, tenant_id, parent_id):
    if parent_id is None:
        return
    p = _unit(conn, parent_id)
    if not p:
        raise core.ValidationError("parent does not exist")
    if p["tenant_id"] != tenant_id:
        raise core.ForbiddenError("cross-tenant parent assignment is not allowed")
    if p["status"] != ACTIVE:
        raise core.ValidationError("cannot assign an inactive/archived parent")


def _ancestors(conn, unit_id):
    seen, cur = [], _unit(conn, unit_id)
    while cur and cur["parent_id"]:
        if cur["parent_id"] in seen:
            break
        seen.append(cur["parent_id"])
        cur = _unit(conn, cur["parent_id"])
    return seen


def subtree_ids(conn, unit_id):
    """The unit plus all its descendants (BFS over the adjacency list)."""
    out, frontier = {unit_id}, [unit_id]
    while frontier:
        nxt = []
        for pid in frontier:
            for r in conn.execute("SELECT id FROM org_units WHERE parent_id=?", (pid,)).fetchall():
                if r["id"] not in out:
                    out.add(r["id"]); nxt.append(r["id"])
        frontier = nxt
    return out


def create_unit(conn, actor, tenant_id, kind, code, name, parent_id=None, description=None,
                effective_from=None, effective_to=None) -> int:
    if kind not in UNIT_KINDS:
        raise core.ValidationError(f"invalid org unit kind '{kind}'")
    if not code or not name:
        raise core.ValidationError("code and name are required")
    _valid_dates(effective_from, effective_to)
    _assert_parent(conn, tenant_id, parent_id)
    try:
        cur = conn.execute(
            "INSERT INTO org_units(tenant_id,kind,code,name,description,parent_id,status,"
            "effective_from,effective_to,created_by,created_at) VALUES(?,?,?,?,?,?, 'ACTIVE', ?,?,?,?)",
            (tenant_id, kind, code, name, description, parent_id, effective_from, effective_to,
             (actor or {}).get("id"), _now()))
    except sqlite3.IntegrityError:
        raise core.ConflictError(f"duplicate {kind} code '{code}' in this tenant")
    uid = cur.lastrowid
    _audit(conn, actor, "ORG_UNIT_CREATED", "org_units", uid,
           new={"tenant_id": tenant_id, "kind": kind, "code": code, "parent_id": parent_id})
    conn.commit()
    return uid


# Typed constructors — the requested per-entity API surface (all delegate to create_unit)
def create_company(conn, actor, tenant_id, code, name, **kw):        return create_unit(conn, actor, tenant_id, "company", code, name, **kw)
def create_business_unit(conn, actor, tenant_id, code, name, **kw):  return create_unit(conn, actor, tenant_id, "business_unit", code, name, **kw)
def create_branch(conn, actor, tenant_id, code, name, **kw):         return create_unit(conn, actor, tenant_id, "branch", code, name, **kw)
def create_department(conn, actor, tenant_id, code, name, **kw):     return create_unit(conn, actor, tenant_id, "department", code, name, **kw)
def create_team(conn, actor, tenant_id, code, name, **kw):           return create_unit(conn, actor, tenant_id, "team", code, name, **kw)
def create_operating_site(conn, actor, tenant_id, code, name, **kw): return create_unit(conn, actor, tenant_id, "operating_site", code, name, **kw)
def create_warehouse(conn, actor, tenant_id, code, name, **kw):      return create_unit(conn, actor, tenant_id, "warehouse", code, name, **kw)
def create_service_area(conn, actor, tenant_id, code, name, **kw):   return create_unit(conn, actor, tenant_id, "service_area", code, name, **kw)


def reparent_preview(conn, unit_id, new_parent_id):
    """Impact preview for a re-parent (Item 6 org administration): descendant count,
    active assigned-user count, validity + warnings — no mutation."""
    sub = list(subtree_ids(conn, unit_id))
    ph = ",".join("?" for _ in sub)
    assigned = conn.execute(
        f"SELECT COUNT(*) c FROM user_organization_assignments WHERE status='ACTIVE' AND scope_id IN ({ph})",
        tuple(sub)).fetchone()["c"] if sub else 0
    u = _unit(conn, unit_id)
    valid, warnings = True, []
    if not u:
        return {"valid": False, "warnings": ["unknown unit"]}
    if new_parent_id == unit_id:
        valid = False; warnings.append("a unit cannot be its own parent")
    if new_parent_id in set(sub):
        valid = False; warnings.append("re-parenting would create a circular hierarchy")
    try:
        _assert_parent(conn, u["tenant_id"], new_parent_id)
    except core.AppError as e:
        valid = False; warnings.append(str(e))
    refs = [str(x) for x in sub]
    ph2 = ",".join("?" for _ in refs)
    cfg_keys = [r["key"] for r in conn.execute(
        f"SELECT DISTINCT key FROM platform_config WHERE scope IN ('branch','department','team')"
        f" AND scope_ref IN ({ph2})", tuple(refs)).fetchall()] if refs else []
    managers = conn.execute(
        f"SELECT COUNT(*) c FROM org_managers WHERE status='ACTIVE' AND scope_id IN ({ph2})",
        tuple(refs)).fetchone()["c"] if refs else 0
    def _parent_overrides(pid):
        if not pid:
            return {}
        return {r["key"]: r["value"] for r in conn.execute(
            "SELECT key,value FROM platform_config WHERE scope_ref=?", (str(pid),)).fetchall()}
    cur_ov, new_ov = _parent_overrides(u["parent_id"]), _parent_overrides(new_parent_id)
    config_inheritance = [{"key": k, "current_source_value": cur_ov.get(k),
                           "proposed_source_value": new_ov.get(k),
                           "changed": cur_ov.get(k) != new_ov.get(k)}
                          for k in sorted(set(cur_ov) | set(new_ov))]
    return {"unit_id": unit_id, "current_parent_id": u["parent_id"], "new_parent_id": new_parent_id,
            "descendants": len(sub) - 1, "active_assigned_users": assigned, "active_managers": managers,
            "config_keys_in_subtree": cfg_keys, "config_impact_count": len(cfg_keys),
            "config_inheritance": config_inheritance, "valid": valid, "warnings": warnings}


def _resolve_with_override(conn, key, ctx, ov_scope, ov_ref, ov_value):
    order = [("user", ctx.get("user")), ("team", ctx.get("team")), ("department", ctx.get("department")),
             ("branch", ctx.get("branch")), ("business_unit", ctx.get("business_unit")),
             ("tenant", ctx.get("tenant")), ("platform", "")]
    for scope, ref in order:
        if ref is None:
            continue
        if scope == ov_scope and str(ref) == str(ov_ref or ""):
            return {"value": str(ov_value), "scope": scope, "scope_ref": str(ref)}
        row = conn.execute("SELECT value FROM platform_config WHERE scope=? AND scope_ref=? AND key=?",
                          (scope, str(ref), key)).fetchone()
        if row:
            return {"value": row["value"], "scope": scope, "scope_ref": str(ref)}
    return {"value": None, "scope": None, "scope_ref": None}


def effective_config_preview(conn, key, scope, scope_ref, proposed_value, **ctx):
    """Non-mutating preview of a proposed config override (Item 5 config viewer)."""
    current = resolve_org_config(conn, key, **ctx)
    proposed = _resolve_with_override(conn, key, ctx, scope, scope_ref, proposed_value)
    valid, error = True, None
    if any(t in key for t in ("_pct", "threshold", "_length", "_minutes", "_days")):
        try:
            int(str(proposed_value))
        except Exception:
            valid, error = False, "value must be numeric for this configuration key"
    overrides = [dict(r) for r in conn.execute(
        "SELECT scope,scope_ref,value,effective_to FROM platform_config WHERE key=? ORDER BY scope",
        (key,)).fetchall()]
    # per-scope value breakdown for the given context (platform..user)
    scope_values = {}
    for sc, ref in (("platform", ""), ("tenant", ctx.get("tenant")), ("business_unit", ctx.get("business_unit")),
                    ("branch", ctx.get("branch")), ("department", ctx.get("department")),
                    ("team", ctx.get("team")), ("user", ctx.get("user"))):
        if ref is None:
            scope_values[sc] = None; continue
        row = conn.execute("SELECT value FROM platform_config WHERE scope=? AND scope_ref=? AND key=?",
                          (sc, str(ref), key)).fetchone()
        scope_values[sc] = row["value"] if row else None
    return {"key": key, "current": current, "proposed_value": str(proposed_value),
            "proposed_scope": scope, "proposed_ref": scope_ref, "proposed_effective": proposed,
            "changed": current.get("value") != proposed.get("value"), "scope_values": scope_values,
            "overrides": overrides, "valid": valid, "error": error}


def working_calendar_conflicts(conn, calendar_id):
    """Inherited-calendar conflict detection (Item 6 calendar administration)."""
    resolved, source = effective_working_calendar(conn, calendar_id)
    conflicts = []
    ss, se = resolved.get("shift_start"), resolved.get("shift_end")
    if ss and se and ss >= se:
        conflicts.append({"type": "shift_inverted", "detail": f"{ss} >= {se}"})
    bm = resolved.get("break_minutes")
    if bm is not None and int(bm) < 0:
        conflicts.append({"type": "invalid_break", "detail": str(bm)})
    row = conn.execute("SELECT parent_id FROM working_calendars WHERE id=?", (calendar_id,)).fetchone()
    if row and row["parent_id"]:
        p = conn.execute("SELECT status FROM working_calendars WHERE id=?", (row["parent_id"],)).fetchone()
        if p and p["status"] != "ACTIVE":
            conflicts.append({"type": "inactive_parent_calendar", "detail": p["status"]})
    return {"calendar_id": calendar_id, "resolved": resolved, "source": source,
            "conflicts": conflicts, "valid": not conflicts}


def reparent(conn, actor, unit_id, new_parent_id):
    u = _unit(conn, unit_id)
    if not u:
        raise core.ConflictError("unknown org unit")
    if new_parent_id == unit_id:
        raise core.ValidationError("a unit cannot be its own parent")
    _assert_parent(conn, u["tenant_id"], new_parent_id)
    if new_parent_id in subtree_ids(conn, unit_id):        # would create a cycle
        raise core.ValidationError("re-parenting would create a circular hierarchy")
    conn.execute("UPDATE org_units SET parent_id=?, updated_by=?, updated_at=? WHERE id=?",
                 (new_parent_id, (actor or {}).get("id"), _now(), unit_id))
    _audit(conn, actor, "ORG_UNIT_REPARENTED", "org_units", unit_id,
           old={"parent_id": u["parent_id"]}, new={"parent_id": new_parent_id})
    conn.commit()


def _active_children(conn, unit_id):
    return conn.execute("SELECT COUNT(*) c FROM org_units WHERE parent_id=? AND status='ACTIVE'",
                        (unit_id,)).fetchone()["c"]


def _referenced_by_users(conn, unit_id):
    return conn.execute("SELECT COUNT(*) c FROM user_organization_assignments"
                        " WHERE scope_id=? AND status='ACTIVE'", (unit_id,)).fetchone()["c"]


def set_status(conn, actor, unit_id, status):
    if status not in (ACTIVE, INACTIVE, ARCHIVED):
        raise core.ValidationError(f"invalid status '{status}'")
    u = _unit(conn, unit_id)
    if not u:
        raise core.ConflictError("unknown org unit")
    if status in (INACTIVE, ARCHIVED) and _active_children(conn, unit_id):
        raise core.ConflictError("cannot deactivate/archive a unit with active children")
    if status == ARCHIVED:
        conn.execute("UPDATE org_units SET status='ARCHIVED', archived_by=?, archived_at=? WHERE id=?",
                     ((actor or {}).get("id"), _now(), unit_id))
    else:
        conn.execute("UPDATE org_units SET status=?, updated_by=?, updated_at=?,"
                     " archived_by=NULL, archived_at=NULL WHERE id=?",
                     (status, (actor or {}).get("id"), _now(), unit_id))
    _audit(conn, actor, "ORG_UNIT_STATUS_CHANGED", "org_units", unit_id,
           old={"status": u["status"]}, new={"status": status})
    conn.commit()


def archive_unit(conn, actor, unit_id):  set_status(conn, actor, unit_id, ARCHIVED)
def restore_unit(conn, actor, unit_id):  set_status(conn, actor, unit_id, ACTIVE)
def deactivate_unit(conn, actor, unit_id):  set_status(conn, actor, unit_id, INACTIVE)


def delete_unit(conn, actor, unit_id):
    """Hard delete, guarded. Prefer archive_unit. Blocked if it has children or user refs."""
    if _active_children(conn, unit_id) or conn.execute(
            "SELECT COUNT(*) c FROM org_units WHERE parent_id=?", (unit_id,)).fetchone()["c"]:
        raise core.ConflictError("cannot delete a unit that has children")
    if _referenced_by_users(conn, unit_id):
        raise core.ConflictError("cannot delete a unit referenced by user assignments")
    conn.execute("DELETE FROM org_units WHERE id=?", (unit_id,))
    _audit(conn, actor, "ORG_UNIT_DELETED", "org_units", unit_id)
    conn.commit()


def get_unit(conn, unit_id):
    return _unit(conn, unit_id)


def list_units(conn, tenant_id, kind=None, status=None, parent_id=None, q=None,
               limit=100, offset=0):
    sql = "SELECT * FROM org_units WHERE tenant_id=?"
    args = [tenant_id]
    if kind:      sql += " AND kind=?";     args.append(kind)
    if status:    sql += " AND status=?";   args.append(status)
    if parent_id is not None: sql += " AND parent_id=?"; args.append(parent_id)
    if q:         sql += " AND (code LIKE ? OR name LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY kind, code LIMIT ? OFFSET ?"; args += [limit, offset]
    return conn.execute(sql, tuple(args)).fetchall()


def children(conn, unit_id):
    return conn.execute("SELECT * FROM org_units WHERE parent_id=? ORDER BY kind, code",
                        (unit_id,)).fetchall()


def tree(conn, tenant_id):
    """Nested dict tree of the tenant's org units (roots = units with no parent in tenant)."""
    rows = conn.execute("SELECT * FROM org_units WHERE tenant_id=? ORDER BY kind, code",
                        (tenant_id,)).fetchall()
    by_id = {r["id"]: {"id": r["id"], "kind": r["kind"], "code": r["code"], "name": r["name"],
                       "status": r["status"], "children": []} for r in rows}
    roots = []
    for r in rows:
        node = by_id[r["id"]]
        if r["parent_id"] and r["parent_id"] in by_id:
            by_id[r["parent_id"]]["children"].append(node)
        else:
            roots.append(node)
    return roots


# --------------------------------------------------------------------------- #
# Cost centers (§9)
# --------------------------------------------------------------------------- #
def create_cost_center(conn, actor, tenant_id, code, name, parent_id=None, branch_id=None,
                       department_id=None, manager_user_id=None, budget_ref=None,
                       external_code=None, default_expense_category=None,
                       effective_from=None, effective_to=None) -> int:
    _valid_dates(effective_from, effective_to)
    try:
        cur = conn.execute(
            "INSERT INTO cost_centers(tenant_id,code,name,parent_id,branch_id,department_id,"
            "manager_user_id,status,effective_from,effective_to,budget_ref,external_code,"
            "default_expense_category,created_by,created_at) VALUES(?,?,?,?,?,?,?, 'ACTIVE', ?,?,?,?,?,?,?)",
            (tenant_id, code, name, parent_id, branch_id, department_id, manager_user_id,
             effective_from, effective_to, budget_ref, external_code, default_expense_category,
             (actor or {}).get("id"), _now()))
    except sqlite3.IntegrityError:
        raise core.ConflictError(f"duplicate cost centre code '{code}' in this tenant")
    ccid = cur.lastrowid
    _audit(conn, actor, "COST_CENTER_CREATED", "cost_centers", ccid, new={"code": code})
    conn.commit()
    return ccid


def list_cost_centers(conn, tenant_id, status=None):
    sql = "SELECT * FROM cost_centers WHERE tenant_id=?"
    args = [tenant_id]
    if status: sql += " AND status=?"; args.append(status)
    return conn.execute(sql + " ORDER BY code", tuple(args)).fetchall()


def archive_cost_center(conn, actor, cc_id):
    conn.execute("UPDATE cost_centers SET status='ARCHIVED', archived_by=?, archived_at=? WHERE id=?",
                 ((actor or {}).get("id"), _now(), cc_id))
    _audit(conn, actor, "COST_CENTER_ARCHIVED", "cost_centers", cc_id)
    conn.commit()


# --------------------------------------------------------------------------- #
# Holiday & working calendars (§8)
# --------------------------------------------------------------------------- #
def create_holiday_calendar(conn, actor, tenant_id, code, name, scope="company", parent_id=None) -> int:
    try:
        cur = conn.execute("INSERT INTO holiday_calendars(tenant_id,code,name,scope,parent_id,"
                           "status,created_by,created_at) VALUES(?,?,?,?,?, 'ACTIVE', ?,?)",
                           (tenant_id, code, name, scope, parent_id, (actor or {}).get("id"), _now()))
    except sqlite3.IntegrityError:
        raise core.ConflictError(f"duplicate holiday calendar '{code}'")
    cid = cur.lastrowid
    _audit(conn, actor, "HOLIDAY_CALENDAR_CREATED", "holiday_calendars", cid, new={"code": code})
    conn.commit()
    return cid


def add_holiday(conn, actor, calendar_id, name, day, recurring=False) -> int:
    if conn.execute("SELECT 1 FROM holidays WHERE calendar_id=? AND day=?", (calendar_id, day)).fetchone():
        raise core.ConflictError(f"duplicate holiday on {day} in this calendar")   # Item 6 calendar validation
    cur = conn.execute("INSERT INTO holidays(calendar_id,name,day,recurring,created_at) VALUES(?,?,?,?,?)",
                       (calendar_id, name, day, 1 if recurring else 0, _now()))
    _audit(conn, actor, "HOLIDAY_ADDED", "holiday_calendars", calendar_id, new={"day": day, "name": name})
    conn.commit()
    return cur.lastrowid


def effective_holidays(conn, calendar_id):
    """Holidays for a calendar, including those inherited from parent calendars."""
    out, seen = [], set()
    cid = calendar_id
    while cid and cid not in seen:
        seen.add(cid)
        out += conn.execute("SELECT name,day,recurring FROM holidays WHERE calendar_id=?", (cid,)).fetchall()
        row = conn.execute("SELECT parent_id FROM holiday_calendars WHERE id=?", (cid,)).fetchone()
        cid = row["parent_id"] if row else None
    return out


def create_working_calendar(conn, actor, tenant_id, code, name, workdays="Mon,Tue,Wed,Thu,Fri",
                            shift_start="08:00", shift_end="17:00", break_minutes=60,
                            overtime_after=None, parent_id=None) -> int:
    if shift_start and shift_end and shift_start >= shift_end:      # Item 6 calendar validation
        raise core.ValidationError("shift_start must be before shift_end")
    if break_minutes is not None and int(break_minutes) < 0:
        raise core.ValidationError("break_minutes must not be negative")
    try:
        cur = conn.execute(
            "INSERT INTO working_calendars(tenant_id,code,name,workdays,shift_start,shift_end,"
            "break_minutes,overtime_after,parent_id,status,created_by,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?, 'ACTIVE', ?,?)",
            (tenant_id, code, name, workdays, shift_start, shift_end, break_minutes,
             overtime_after, parent_id, (actor or {}).get("id"), _now()))
    except sqlite3.IntegrityError:
        raise core.ConflictError(f"duplicate working calendar '{code}'")
    wid = cur.lastrowid
    _audit(conn, actor, "WORKING_CALENDAR_CREATED", "working_calendars", wid, new={"code": code})
    conn.commit()
    return wid


def effective_working_calendar(conn, calendar_id):
    """Resolve a working calendar with inheritance: a child's non-null fields override the
    parent's; missing fields fall back up the chain (branch-specific override, §8)."""
    fields = ("workdays", "shift_start", "shift_end", "break_minutes", "overtime_after")
    resolved, source = {}, {}
    cid = calendar_id
    seen = set()
    while cid and cid not in seen:
        seen.add(cid)
        row = conn.execute("SELECT * FROM working_calendars WHERE id=?", (cid,)).fetchone()
        if not row:
            break
        for f in fields:
            if f not in resolved and row[f] is not None:
                resolved[f] = row[f]; source[f] = cid
        cid = row["parent_id"]
    return resolved, source


# --------------------------------------------------------------------------- #
# Managers (§3 effective-dated) & user assignments (§4)
# --------------------------------------------------------------------------- #
def assign_manager(conn, actor, tenant_id, scope_kind, scope_id, user_id,
                   effective_from=None, effective_to=None) -> int:
    _valid_dates(effective_from, effective_to)
    cur = conn.execute("INSERT INTO org_managers(tenant_id,scope_kind,scope_id,user_id,role,"
                       "effective_from,effective_to,status,created_by,created_at)"
                       " VALUES(?,?,?,?, 'MANAGER', ?,?, 'ACTIVE', ?,?)",
                       (tenant_id, scope_kind, scope_id, user_id, effective_from, effective_to,
                        (actor or {}).get("id"), _now()))
    _audit(conn, actor, "MANAGER_ASSIGNED", scope_kind, scope_id, new={"user_id": user_id})
    conn.commit()
    return cur.lastrowid


def current_manager(conn, scope_kind, scope_id, on=None):
    on = on or _today()
    row = conn.execute(
        "SELECT user_id FROM org_managers WHERE scope_kind=? AND scope_id=? AND status='ACTIVE'"
        " AND (effective_from IS NULL OR effective_from<=?) AND (effective_to IS NULL OR effective_to>=?)"
        " ORDER BY id DESC LIMIT 1", (scope_kind, scope_id, on, on)).fetchone()
    return row["user_id"] if row else None


def assign_user(conn, actor, tenant_id, user_id, scope_kind, scope_id, assignment_type="PRIMARY",
                reason=None, effective_from=None, effective_to=None, approved_by=None,
                allow_multiple_primary=False) -> int:
    if assignment_type not in ASSIGN_TYPES:
        raise core.ValidationError(f"invalid assignment_type '{assignment_type}'")
    _valid_dates(effective_from, effective_to)
    # cannot assign users to an inactive/archived org unit
    if scope_kind in UNIT_KINDS:
        u = _unit(conn, scope_id)
        if not u or u["status"] != ACTIVE:
            raise core.ConflictError("cannot assign a user to an inactive/unknown org unit")
    # one active PRIMARY per (user, scope_kind) unless explicitly configured
    if assignment_type == "PRIMARY" and not allow_multiple_primary:
        dup = conn.execute(
            "SELECT COUNT(*) c FROM user_organization_assignments WHERE user_id=? AND scope_kind=?"
            " AND assignment_type='PRIMARY' AND status='ACTIVE'", (user_id, scope_kind)).fetchone()["c"]
        if dup:
            raise core.ConflictError(
                f"user already has an active PRIMARY {scope_kind} assignment")
    cur = conn.execute(
        "INSERT INTO user_organization_assignments(tenant_id,user_id,scope_kind,scope_id,"
        "assignment_type,status,reason,effective_from,effective_to,assigned_by,approved_by,created_at)"
        " VALUES(?,?,?,?,?, 'ACTIVE', ?,?,?,?,?,?)",
        (tenant_id, user_id, scope_kind, scope_id, assignment_type, reason, effective_from,
         effective_to, (actor or {}).get("id"), approved_by, _now()))
    aid = cur.lastrowid
    _audit(conn, actor, "USER_ORG_ASSIGNED", "user_organization_assignments", aid,
           new={"user_id": user_id, "scope_kind": scope_kind, "scope_id": scope_id,
                "type": assignment_type})
    conn.commit()
    return aid


def remove_assignment(conn, actor, assignment_id):
    conn.execute("UPDATE user_organization_assignments SET status='INACTIVE' WHERE id=?", (assignment_id,))
    _audit(conn, actor, "USER_ORG_UNASSIGNED", "user_organization_assignments", assignment_id)
    conn.commit()


def _active_assignments(conn, user_id, on=None):
    on = on or _today()
    return conn.execute(
        "SELECT * FROM user_organization_assignments WHERE user_id=? AND status='ACTIVE'"
        " AND (effective_from IS NULL OR effective_from<=?) AND (effective_to IS NULL OR effective_to>=?)",
        (user_id, on, on)).fetchall()


def user_assignments(conn, user_id):
    return conn.execute("SELECT * FROM user_organization_assignments WHERE user_id=? ORDER BY id",
                        (user_id,)).fetchall()


def users_in_scope(conn, scope_kind, scope_id):
    return conn.execute(
        "SELECT DISTINCT user_id FROM user_organization_assignments"
        " WHERE scope_kind=? AND scope_id=? AND status='ACTIVE'", (scope_kind, scope_id)).fetchall()


# --------------------------------------------------------------------------- #
# Organization-scoped authorization (§5)
# --------------------------------------------------------------------------- #
def governed_unit_ids(conn, user_id):
    """Org-unit ids a user governs = each active org-unit assignment expanded to its subtree.
    A 'tenant'/'company' assignment governs the whole tenant."""
    ids = set()
    for a in _active_assignments(conn, user_id):
        if a["scope_kind"] == "tenant":
            ids |= {r["id"] for r in conn.execute(
                "SELECT id FROM org_units WHERE tenant_id=?", (a["tenant_id"],)).fetchall()}
        elif a["scope_kind"] in UNIT_KINDS:
            ids |= subtree_ids(conn, a["scope_id"])
    return ids


def governed_cost_center_ids(conn, user_id):
    gov_units = governed_unit_ids(conn, user_id)
    direct = {a["scope_id"] for a in _active_assignments(conn, user_id)
              if a["scope_kind"] == "cost_center"}
    out = set(direct)
    for cc in conn.execute("SELECT id,branch_id,department_id FROM cost_centers").fetchall():
        if cc["branch_id"] in gov_units or cc["department_id"] in gov_units:
            out.add(cc["id"])
    return out


def in_scope(conn, user_id, scope_kind, scope_id) -> bool:
    if scope_kind == "cost_center":
        return scope_id in governed_cost_center_ids(conn, user_id)
    return scope_id in governed_unit_ids(conn, user_id)


def authorize(conn, user_id, permission, scope_kind=None, scope_id=None) -> bool:
    """Authority = permission AND organizational scope (§5). Platform admins holding a
    cross-tenant permission ('*' or 'tenant.*') bypass scope. Missing/out-of-scope context
    with a scope requirement is denied."""
    if not ap.has_permission(conn, user_id, permission):
        return False
    perms = ap.effective_permissions(conn, user_id)
    if "*" in perms or "tenant.*" in perms:
        return True
    if scope_kind is None:
        return True                                        # non-org-scoped action
    return in_scope(conn, user_id, scope_kind, scope_id)


# --------------------------------------------------------------------------- #
# Company profile (§7) — identity only; cascade-governed values stay in config
# --------------------------------------------------------------------------- #
def upsert_company_profile(conn, actor, tenant_id, **fields):
    cols = ("legal_name", "trade_name", "registration_number", "tax_number", "address",
            "contact", "logo", "country", "timezone", "default_currency", "locale",
            "fiscal_year_start", "default_branch_id", "default_cost_center_id",
            "default_holiday_calendar_id", "default_working_calendar_id")
    existing = conn.execute("SELECT id FROM company_profile WHERE tenant_id=?", (tenant_id,)).fetchone()
    data = {c: fields.get(c) for c in cols}
    if existing:
        sets = ", ".join(f"{c}=?" for c in cols)
        conn.execute(f"UPDATE company_profile SET {sets}, updated_by=?, updated_at=? WHERE tenant_id=?",
                     tuple(data[c] for c in cols) + ((actor or {}).get("id"), _now(), tenant_id))
    else:
        placeholders = ",".join("?" for _ in cols)
        conn.execute(f"INSERT INTO company_profile(tenant_id,{','.join(cols)},updated_by,updated_at)"
                     f" VALUES(?,{placeholders},?,?)",
                     (tenant_id,) + tuple(data[c] for c in cols) + ((actor or {}).get("id"), _now()))
    _audit(conn, actor, "COMPANY_PROFILE_UPSERTED", "company_profile", tenant_id, new=data)
    conn.commit()


def company_profile(conn, tenant_id):
    return conn.execute("SELECT * FROM company_profile WHERE tenant_id=?", (tenant_id,)).fetchone()


# --------------------------------------------------------------------------- #
# Configuration cascade extension (§11): platform -> tenant -> BU -> branch ->
# department -> team -> user. Delegates storage/resolution to admin_platform.
# --------------------------------------------------------------------------- #
def resolve_org_config(conn, key, tenant=None, business_unit=None, branch=None,
                       department=None, team=None, user=None):
    """Most-specific valid config wins across platform->tenant->BU->branch->department->
    team->user. Config on an INACTIVE/ARCHIVED org unit is skipped (inactive-scope
    fallback, §11). Returns value + source scope/ref + fallback path."""
    def active_ref(uid):
        if uid is None:
            return None
        u = _unit(conn, uid)
        return str(uid) if (u and u["status"] == ACTIVE) else None
    chain = [("user", str(user) if user is not None else None),
             ("team", active_ref(team)), ("department", active_ref(department)),
             ("branch", active_ref(branch)), ("business_unit", active_ref(business_unit)),
             ("tenant", str(tenant) if tenant is not None else None)]
    return ap.resolve_config_chain(conn, key, chain)
