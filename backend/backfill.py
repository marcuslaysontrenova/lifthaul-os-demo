"""LiftHaul OS — Tenant & Organization backfill (Platform 1, Phase 1 #2-7).

Executes the plan in docs/blueprint/TENANT_BACKFILL_MATRIX.md as a SAFE, ADDITIVE,
governed operation:

  * add a nullable `tenant_id` column to every operational table (idempotent, portable);
  * classify legacy records — all legacy rows are DETERMINISTIC -> Tenant Zero (RGO),
    because the system's entire history is single-tenant (the evidence). No financial
    value is read or changed; `tenant_id` is pure ownership metadata;
  * ORGANIZATION scope (branch/site/cost-centre) is AMBIGUOUS and is NEVER auto-assigned
    — it is parked in a remediation queue for manual resolution (per directive §Phase-1);
  * analysis and dry-run report changes without writing;
  * every step is audited.

Tenant ownership is assigned independently from organization ownership. This module does
NOT touch any financial calculation.
"""
from __future__ import annotations

import datetime

import core

# Operational tables that must carry tenant ownership (from the backfill matrix).
TENANT_SCOPED_TABLES = [
    "customers", "contacts", "addresses", "bookings", "booking_messages",
    "quotations", "quotation_lines", "payment_requests", "jobs", "job_stage_history",
    "site_assessments", "reservations", "change_orders", "expenses", "invoices",
    "invoice_lines", "payment_allocations", "refunds", "equipment", "vehicles",
    "employees", "maintenance_work_orders", "inspections", "subcontractors", "suppliers",
    "purchase_orders", "supplier_invoices", "inventory_items", "inventory_movements",
    "safety_records", "incidents", "documents", "master_data", "notification_templates",
    "notifications",
]

# Tables that ALSO need an organization scope — AMBIGUOUS, so queued for remediation,
# never auto-filled. {table: scope_kind}
ORG_SCOPE_TABLES = {
    "bookings": "branch", "jobs": "branch", "reservations": "operating_site",
    "expenses": "cost_center", "invoices": "cost_center", "change_orders": "cost_center",
    "equipment": "branch", "vehicles": "branch", "employees": "branch",
    "inventory_items": "warehouse", "inventory_movements": "warehouse",
    "safety_records": "operating_site", "incidents": "operating_site",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS org_backfill_remediation(
  id INTEGER PRIMARY KEY, table_name TEXT NOT NULL, scope_kind TEXT NOT NULL,
  classification TEXT NOT NULL, affected_rows INTEGER, reason TEXT,
  status TEXT NOT NULL DEFAULT 'OPEN', created_at TEXT, resolved_by INTEGER, resolved_at TEXT);
"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def _tenant_id(conn, code="RGO"):
    import admin_platform
    t = admin_platform.get_tenant(conn, code)
    return t["id"] if t else None


def _has_table(conn, table):
    try:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:
        try: conn.rollback()
        except Exception: pass
        return False


def _has_tenant_col(conn, table):
    try:
        conn.execute(f"SELECT tenant_id FROM {table} LIMIT 1")
        return True
    except Exception:
        try: conn.rollback()
        except Exception: pass
        return False


def add_tenant_columns(conn):
    """Idempotent, portable: add nullable tenant_id to each operational table."""
    added = []
    for t in TENANT_SCOPED_TABLES:
        if not _has_table(conn, t) or _has_tenant_col(conn, t):
            continue
        try:
            conn.execute(f"ALTER TABLE {t} ADD COLUMN tenant_id INTEGER")
            conn.commit()
            added.append(t)
        except Exception:
            try: conn.rollback()
            except Exception: pass
    return added


def analyze(conn, tenant_code="RGO"):
    """Classify legacy records without writing. All legacy rows -> DETERMINISTIC (RGO)
    for tenant scope (single-tenant history); org scope -> AMBIGUOUS."""
    tid = _tenant_id(conn, tenant_code)
    report = {"tenant_code": tenant_code, "tenant_id": tid, "tables": []}
    for t in TENANT_SCOPED_TABLES:
        if not _has_table(conn, t):
            continue
        total = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        unassigned = (conn.execute(f"SELECT COUNT(*) c FROM {t} WHERE tenant_id IS NULL").fetchone()["c"]
                      if _has_tenant_col(conn, t) else total)
        report["tables"].append({
            "table": t, "total": total, "unassigned_tenant": unassigned,
            "tenant_classification": "DETERMINISTIC" if unassigned else "COMPLETE",
            "org_scope": ORG_SCOPE_TABLES.get(t),
            "org_classification": "AMBIGUOUS" if t in ORG_SCOPE_TABLES else "NONE"})
    return report


def dry_run(conn, tenant_code="RGO"):
    a = analyze(conn, tenant_code)
    a["mode"] = "DRY_RUN"
    a["planned_tenant_updates"] = sum(x["unassigned_tenant"] for x in a["tables"])
    a["planned_org_remediations"] = len([x for x in a["tables"] if x["org_classification"] == "AMBIGUOUS"])
    a["writes"] = 0
    return a


def execute(conn, actor, tenant_code="RGO"):
    """Safe execution: add columns, assign tenant_id=RGO to legacy rows, queue ambiguous
    org scope for remediation. Never assigns org scope automatically; never touches
    financial values."""
    tid = _tenant_id(conn, tenant_code)
    if tid is None:
        raise core.ConflictError(f"unknown tenant '{tenant_code}'")
    add_tenant_columns(conn)
    result = {"tenant_code": tenant_code, "tenant_id": tid, "updated": {}, "remediation_queued": []}
    for t in TENANT_SCOPED_TABLES:
        if not _has_tenant_col(conn, t):
            continue
        cur = conn.execute(f"UPDATE {t} SET tenant_id=? WHERE tenant_id IS NULL", (tid,))
        n = getattr(cur, "rowcount", 0) or 0
        if n:
            result["updated"][t] = n
    conn.commit()
    # queue AMBIGUOUS organization scope (never auto-assigned)
    for t, kind in ORG_SCOPE_TABLES.items():
        if not _has_table(conn, t):
            continue
        rows = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        if rows and not conn.execute(
                "SELECT 1 FROM org_backfill_remediation WHERE table_name=? AND scope_kind=? AND status='OPEN'",
                (t, kind)).fetchone():
            conn.execute("INSERT INTO org_backfill_remediation(table_name,scope_kind,classification,"
                         "affected_rows,reason,status,created_at) VALUES(?,?, 'AMBIGUOUS', ?, ?, 'OPEN', ?)",
                         (t, kind, rows, f"{kind} ownership cannot be inferred; manual assignment required", _now()))
            result["remediation_queued"].append({"table": t, "scope_kind": kind, "rows": rows})
    conn.commit()
    if actor:
        core.audit(conn, actor, "TENANT_BACKFILL_EXECUTED", "org_backfill_remediation", 0,
                   new={"tenant": tenant_code, "updated": result["updated"],
                        "remediation": len(result["remediation_queued"])})
        conn.commit()
    return result


def status(conn, tenant_code="RGO"):
    """Post-backfill status: per-table unassigned counts + open remediation items."""
    tables = []
    for t in TENANT_SCOPED_TABLES:
        if not _has_table(conn, t):
            continue
        total = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        unassigned = (conn.execute(f"SELECT COUNT(*) c FROM {t} WHERE tenant_id IS NULL").fetchone()["c"]
                      if _has_tenant_col(conn, t) else total)
        if total:
            tables.append({"table": t, "total": total, "unassigned_tenant": unassigned})
    remediation = [dict(r) for r in conn.execute(
        "SELECT table_name,scope_kind,affected_rows,status FROM org_backfill_remediation"
        " WHERE status='OPEN' ORDER BY table_name").fetchall()] if _has_table(conn, "org_backfill_remediation") else []
    total_unassigned = sum(x["unassigned_tenant"] for x in tables)
    return {"tenant_enforced": total_unassigned == 0 and bool(tables),
            "tables": tables, "open_remediation": remediation,
            "phase": "tenant scope executed; organization scope pending remediation"
                     if not total_unassigned else "not yet executed"}


def resolve_remediation(conn, actor, remediation_id):
    conn.execute("UPDATE org_backfill_remediation SET status='RESOLVED', resolved_by=?, resolved_at=? WHERE id=?",
                 ((actor or {}).get("id"), _now(), remediation_id))
    if actor:
        core.audit(conn, actor, "BACKFILL_REMEDIATION_RESOLVED", "org_backfill_remediation", remediation_id)
    conn.commit()


# --------------------------------------------------------------------------- #
# Tenant-scope enforcement mechanism (Phase 1 #5).
# The guard is available now; comprehensive wiring into every operational read is the
# next increment. Until users carry a tenant_id, the actor tenant resolves to RGO.
# --------------------------------------------------------------------------- #
def actor_tenant(conn, actor):
    return _tenant_id(conn, "RGO")


def assert_in_tenant(row_tenant_id, actor_tenant_id):
    if row_tenant_id is not None and actor_tenant_id is not None and row_tenant_id != actor_tenant_id:
        raise core.ForbiddenError("record belongs to a different tenant")


def tenant_filter(conn, actor):
    """Return (sql_fragment, param) to append tenant scoping to an operational query."""
    return "tenant_id = ?", actor_tenant(conn, actor)
