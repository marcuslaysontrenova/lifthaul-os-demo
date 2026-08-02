"""LiftHaul OS — Phase 3: canonical governed master-data model + shared Master Data Center.

A single canonical table (`md_entries`) governs every simple reference list (customer
classifications, lead/sales lookups, operations/finance/geo/document domains) with a uniform
lifecycle, effective-dating, dependency protection, replacement mapping, and audit. Dedicated
tables (credit policy, customer numbering, duplicate/merge, custom fields) live in `crm_admin`.

Design invariants (Phase 3 directive):
  * referenced values are NEVER hard-deleted; inactive values remain historically visible but
    cannot be selected for NEW transactions;
  * duplicate (tenant, domain, code) is blocked; cross-tenant parent relationships are blocked;
  * system-protected values require elevated permission (`master_data.system.manage`);
  * replacement mapping preserves historical meaning;
  * tax codes / currencies here are DESCRIPTIVE reference — the effective tax RATE stays in the
    Phase-2 config cascade, so nothing in this module changes a financial value.

Statuses: DRAFT | ACTIVE | INACTIVE | ARCHIVED | DEPRECATED.
"""
from __future__ import annotations

import datetime
import json

import core

STATUSES = ("DRAFT", "ACTIVE", "INACTIVE", "ARCHIVED", "DEPRECATED")
SELECTABLE_STATUSES = ("ACTIVE",)          # only ACTIVE + in-window values may be chosen for new txns

SCHEMA = """
CREATE TABLE IF NOT EXISTS md_entries(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  org_scope TEXT,
  domain TEXT NOT NULL,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  parent_id INTEGER,
  sort_order INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  effective_from TEXT,
  effective_to TEXT,
  system_protected INTEGER DEFAULT 0,
  replacement_id INTEGER,
  metadata TEXT,
  version INTEGER DEFAULT 1,
  created_by INTEGER, created_at TEXT,
  updated_by INTEGER, updated_at TEXT,
  archived_by INTEGER, archived_at TEXT,
  correlation_id TEXT,
  UNIQUE(tenant_id, domain, code));

CREATE TABLE IF NOT EXISTS master_data_import_log(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, domain TEXT, actor INTEGER,
  total INTEGER, valid INTEGER, invalid INTEGER, duplicates INTEGER, applied INTEGER,
  dry_run INTEGER, report TEXT, correlation_id TEXT, created_at TEXT);
"""

# --------------------------------------------------------------------------- #
# Governed domain registry — drives the Master Data Center menu + validation.
# (domain, category, label, hierarchical, dedicated)  dedicated=True means a
# dedicated table owns it (credit policy etc.) and it is listed for reference only.
# --------------------------------------------------------------------------- #
DOMAINS = [
    # CRM — customer classifications
    ("customer.category", "CRM", "Customer Category", False),
    ("customer.type", "CRM", "Customer Type", False),
    ("customer.industry", "CRM", "Industry", False),
    ("customer.group", "CRM", "Customer Group", False),
    ("customer.account_status", "CRM", "Account Status", False),
    ("customer.account_rating", "CRM", "Account Rating", False),
    ("customer.credit_rating", "CRM", "Credit Rating", False),
    ("customer.risk_class", "CRM", "Risk Classification", False),
    ("customer.lifecycle", "CRM", "Lifecycle Stage", False),
    ("customer.strategic_indicator", "CRM", "Strategic Account Indicator", False),
    ("customer.attachment_category", "CRM", "Attachment Category", False),
    ("customer.retention_rule", "CRM", "Retention Rule", False),
    ("contact.classification", "CRM", "Contact Classification", False),
    ("address.type", "CRM", "Address Type", False),
    ("portal.access_policy", "CRM", "Portal Access Policy", False),
    ("portal.eligibility", "CRM", "Portal Eligibility", False),
    # Lead & sales
    ("lead.source", "CRM", "Lead Source", False),
    ("lead.qualification", "CRM", "Lead Qualification", False),
    ("sales.territory", "CRM", "Sales Territory", True),
    ("opportunity.type", "CRM", "Opportunity Type", False),
    ("opportunity.source", "CRM", "Opportunity Source", False),
    ("opportunity.lost_reason", "CRM", "Opportunity Lost Reason", False),
    ("campaign.source", "CRM", "Campaign Source", False),
    ("commercial.payment_term", "CRM", "Payment Term", False),
    ("commercial.pricing_policy", "CRM", "Pricing Policy", False),
    # Operations
    ("ops.service_type", "OPERATIONS", "Service Type", False),
    ("ops.job_category", "OPERATIONS", "Job Category", False),
    ("ops.equipment_type", "OPERATIONS", "Equipment Type", False),
    ("ops.vehicle_type", "OPERATIONS", "Vehicle Type", False),
    ("ops.trailer_type", "OPERATIONS", "Trailer Type", False),
    ("ops.lifting_equipment_category", "OPERATIONS", "Lifting Equipment Category", False),
    ("ops.rigging_equipment_category", "OPERATIONS", "Rigging Equipment Category", False),
    ("ops.crew_role", "OPERATIONS", "Crew Role", False),
    ("ops.maintenance_category", "OPERATIONS", "Maintenance Category", False),
    ("ops.inspection_category", "OPERATIONS", "Inspection Category", False),
    ("ops.incident_category", "OPERATIONS", "Incident Category", False),
    ("ops.safety_category", "OPERATIONS", "Safety Category", False),
    ("ops.cancellation_reason", "OPERATIONS", "Cancellation Reason", False),
    ("ops.status_reason", "OPERATIONS", "Status Reason", False),
    # Finance (descriptive; effective rates stay in the Phase-2 config cascade)
    ("finance.expense_category", "FINANCE", "Expense Category", False),
    ("finance.payment_method", "FINANCE", "Payment Method", False),
    ("finance.payment_term", "FINANCE", "Finance Payment Term", False),
    ("finance.currency", "FINANCE", "Currency", False),
    ("finance.tax_code", "FINANCE", "Tax Code (reference)", False),
    ("finance.uom", "FINANCE", "Unit of Measure", False),
    ("finance.invoice_category", "FINANCE", "Invoice Category", False),
    ("finance.change_order_reason", "FINANCE", "Change-Order Reason", False),
    ("finance.refund_reason", "FINANCE", "Refund Reason", False),
    ("finance.adjustment_reason", "FINANCE", "Adjustment Reason", False),
    # Geography (hierarchical)
    ("geo.country", "GEOGRAPHY", "Country", True),
    ("geo.region", "GEOGRAPHY", "Region", True),
    ("geo.province", "GEOGRAPHY", "Province", True),
    ("geo.city", "GEOGRAPHY", "City", True),
    ("geo.municipality", "GEOGRAPHY", "Municipality", True),
    ("geo.barangay", "GEOGRAPHY", "Barangay", True),
    ("geo.service_area", "GEOGRAPHY", "Service Area", False),
    # Documents & communication
    ("doc.type", "DOCUMENTS", "Document Type", False),
    ("doc.attachment_type", "DOCUMENTS", "Attachment Type", False),
    ("doc.template", "DOCUMENTS", "Document Template", False),
    ("comms.email_template", "DOCUMENTS", "Email Template", False),
    ("comms.sms_template", "DOCUMENTS", "SMS Template", False),
    ("comms.notification_template", "DOCUMENTS", "Notification Template", False),
]
DOMAIN_KEYS = {d[0] for d in DOMAINS}
HIERARCHICAL = {d[0] for d in DOMAINS if d[3]}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today():
    return datetime.date.today().isoformat()


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


# --------------------------------------------------------------------------- #
# Core CRUD + lifecycle
# --------------------------------------------------------------------------- #
def _require_domain(domain):
    if domain not in DOMAIN_KEYS:
        raise core.ValidationError(f"unknown master-data domain '{domain}'")


def _actor_tenant(actor):
    return (actor or {}).get("tenant_id")


def create_value(conn, actor, domain, code, name, description=None, parent_id=None,
                 sort_order=0, status="ACTIVE", effective_from=None, effective_to=None,
                 system_protected=False, metadata=None, tenant_id=None, org_scope=None):
    """Create a governed master-data value. Uniqueness (tenant, domain, code) enforced;
    cross-tenant parent blocked; system-protected creation needs elevated permission."""
    core.require(actor, "master_data.manage")
    _require_domain(domain)
    if status not in STATUSES:
        raise core.ValidationError(f"status must be one of {STATUSES}")
    if system_protected:
        core.require(actor, "master_data.system.manage")
    tid = tenant_id if tenant_id is not None else _actor_tenant(actor)
    # duplicate code guard (per tenant + domain)
    if _by_code_row(conn, domain, code, tid) is not None:
        raise core.ConflictError(f"duplicate code '{code}' in domain '{domain}'")
    if parent_id is not None:
        _assert_parent(conn, domain, parent_id, tid)
    cid = core.correlation_id()
    cur = conn.execute(
        "INSERT INTO md_entries(tenant_id,org_scope,domain,code,name,description,parent_id,"
        "sort_order,status,effective_from,effective_to,system_protected,metadata,version,"
        "created_by,created_at,correlation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
        (tid, org_scope, domain, code, name, description, parent_id, sort_order, status,
         effective_from, effective_to, 1 if system_protected else 0,
         json.dumps(metadata) if metadata is not None else None, (actor or {}).get("id"),
         _now(), cid))
    vid = cur.lastrowid
    core.audit(conn, actor, "MASTER_DATA_CREATED", "md_entries", vid,
               new={"domain": domain, "code": code, "status": status})
    conn.commit()
    return vid


def _assert_parent(conn, domain, parent_id, tid):
    prow = conn.execute("SELECT tenant_id,domain FROM md_entries WHERE id=?", (parent_id,)).fetchone()
    if not prow:
        raise core.ValidationError("parent value not found")
    pt = prow["tenant_id"]
    if pt is not None and tid is not None and pt != tid:
        raise core.ForbiddenError("cross-tenant parent relationship is not allowed")


def _by_code_row(conn, domain, code, tid):
    if tid is None:
        return conn.execute(
            "SELECT * FROM md_entries WHERE domain=? AND code=? AND tenant_id IS NULL",
            (domain, code)).fetchone()
    return conn.execute(
        "SELECT * FROM md_entries WHERE domain=? AND code=? AND tenant_id=?",
        (domain, code, tid)).fetchone()


def get_value(conn, actor, value_id):
    row = conn.execute("SELECT * FROM md_entries WHERE id=?", (value_id,)).fetchone()
    if not row:
        raise core.NotFoundError("master-data value not found")
    _guard(actor, row)
    return dict(row)


def _guard(actor, row):
    """404 no-leak: never reveal another tenant's value. Platform (NULL) values are shared."""
    at = _actor_tenant(actor)
    rt = row["tenant_id"] if row is not None else None
    if at is not None and rt is not None and at != rt:
        raise core.NotFoundError("master-data value not found")


def list_values(conn, actor, domain, include_inactive=True, q=None):
    """Values visible to the actor's tenant: own-tenant rows + shared platform (NULL) rows."""
    _require_domain(domain)
    at = _actor_tenant(actor)
    sql = "SELECT * FROM md_entries WHERE domain=?"
    args = [domain]
    if at is not None:
        sql += " AND (tenant_id=? OR tenant_id IS NULL)"; args.append(at)
    if not include_inactive:
        sql += " AND status='ACTIVE'"
    if q:
        sql += " AND (code LIKE ? OR name LIKE ?)"; args += ["%" + q + "%", "%" + q + "%"]
    sql += " ORDER BY sort_order, code"
    return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def update_value(conn, actor, value_id, **fields):
    core.require(actor, "master_data.manage")
    row = conn.execute("SELECT * FROM md_entries WHERE id=?", (value_id,)).fetchone()
    if not row:
        raise core.NotFoundError("master-data value not found")
    _guard(actor, row)
    if row["system_protected"]:
        core.require(actor, "master_data.system.manage")
    allowed = {"name", "description", "sort_order", "effective_from", "effective_to", "metadata"}
    sets, args = [], []
    old = {}
    for k, v in fields.items():
        if k not in allowed:
            continue
        old[k] = row[k] if k in row.keys() else None
        sets.append(f"{k}=?")
        args.append(json.dumps(v) if k == "metadata" and v is not None else v)
    if not sets:
        return False
    sets += ["updated_by=?", "updated_at=?", "version=version+1"]
    args += [(actor or {}).get("id"), _now(), value_id]
    conn.execute(f"UPDATE md_entries SET {', '.join(sets)} WHERE id=?", tuple(args))
    core.audit(conn, actor, "MASTER_DATA_UPDATED", "md_entries", value_id,
               old=old, new={k: fields[k] for k in old})
    conn.commit()
    return True


def set_status(conn, actor, value_id, status, reason=None):
    """Governed lifecycle transition (activate/deactivate/archive/restore/deprecate).
    Deactivating/archiving a REFERENCED value requires a replacement or explicit reason."""
    core.require(actor, "master_data.manage")
    if status not in STATUSES:
        raise core.ValidationError(f"status must be one of {STATUSES}")
    row = conn.execute("SELECT * FROM md_entries WHERE id=?", (value_id,)).fetchone()
    if not row:
        raise core.NotFoundError("master-data value not found")
    _guard(actor, row)
    if row["system_protected"]:
        core.require(actor, "master_data.system.manage")
    if status == "ARCHIVED":
        core.require(actor, "master_data.archive")
    if status == "ACTIVE" and row["status"] in ("ARCHIVED", "INACTIVE", "DEPRECATED"):
        core.require(actor, "master_data.restore")
    arch_by = (actor or {}).get("id") if status == "ARCHIVED" else None
    arch_at = _now() if status == "ARCHIVED" else None
    action = {"ACTIVE": "MASTER_DATA_ACTIVATED", "INACTIVE": "MASTER_DATA_DEACTIVATED",
              "ARCHIVED": "MASTER_DATA_ARCHIVED", "DEPRECATED": "MASTER_DATA_DEPRECATED",
              "DRAFT": "MASTER_DATA_DRAFTED"}[status]
    conn.execute("UPDATE md_entries SET status=?, archived_by=?, archived_at=?, updated_by=?,"
                 " updated_at=?, version=version+1 WHERE id=?",
                 (status, arch_by, arch_at, (actor or {}).get("id"), _now(), value_id))
    core.audit(conn, actor, action, "md_entries", value_id,
               old={"status": row["status"]}, new={"status": status}, reason=reason)
    conn.commit()
    return True


def selectable(conn, value_id, on_date=None):
    """True iff the value is ACTIVE and within its effective window on `on_date` (default today).
    New transactions must consult this — inactive/expired values cannot be chosen."""
    row = conn.execute("SELECT * FROM md_entries WHERE id=?", (value_id,)).fetchone()
    if not row:
        return False
    return _row_selectable(row, on_date or _today())


def _row_selectable(row, on_date):
    if row["status"] not in SELECTABLE_STATUSES:
        return False
    if row["effective_from"] and row["effective_from"] > on_date:
        return False
    if row["effective_to"] and row["effective_to"] < on_date:
        return False
    return True


# --------------------------------------------------------------------------- #
# Dependency & impact management
# --------------------------------------------------------------------------- #
# (domain -> list of (table, column) references to count). Only real, existing columns.
DEPENDENCY_MAP = {
    "ops.equipment_type": [("equipment", "etype")],
    "ops.vehicle_type": [("vehicles", "vtype")],
    "ops.service_type": [("bookings", "service")],
    "ops.crew_role": [("employees", "role")],
    "ops.maintenance_category": [("maintenance_work_orders", "mtype")],
    "ops.inspection_category": [("inspections", "itype")],
    "finance.expense_category": [("expenses", "category")],
    "finance.change_order_reason": [("change_orders", "reason")],
    "contact.classification": [("contacts", "role")],
    "address.type": [("addresses", "kind")],
    "customer.credit_rating": [("customers", "credit_status")],
}


def dependencies(conn, actor, value_id):
    """Impact report: how many records reference this value (by its CODE, matching the
    legacy free-text columns), plus replacement options. Read-only."""
    row = conn.execute("SELECT * FROM md_entries WHERE id=?", (value_id,)).fetchone()
    if not row:
        raise core.NotFoundError("master-data value not found")
    _guard(actor, row)
    domain, code = row["domain"], row["code"]
    refs = []
    total = 0
    for table, column in DEPENDENCY_MAP.get(domain, []):
        try:
            n = conn.execute(f"SELECT COUNT(*) c FROM {table} WHERE {column}=?", (code,)).fetchone()["c"]
        except Exception:
            n = 0
        refs.append({"table": table, "column": column, "records": n})
        total += n
    # values that could replace this one (same domain, ACTIVE, different id)
    options = [{"id": r["id"], "code": r["code"], "name": r["name"]}
               for r in list_values(conn, actor, domain, include_inactive=False)
               if r["id"] != value_id]
    return {"value": {"id": value_id, "domain": domain, "code": code, "name": row["name"],
                      "status": row["status"]},
            "total_references": total, "references": refs,
            "safe_to_deactivate": True, "safe_to_hard_delete": total == 0,
            "replacement_options": options}


def replace(conn, actor, value_id, replacement_id, reason=None):
    """Map a value to a replacement, preserving historical meaning. The old value is
    DEPRECATED and points at its replacement; historical records keep their original code."""
    core.require(actor, "master_data.replace")
    row = conn.execute("SELECT * FROM md_entries WHERE id=?", (value_id,)).fetchone()
    rep = conn.execute("SELECT * FROM md_entries WHERE id=?", (replacement_id,)).fetchone()
    if not row or not rep:
        raise core.NotFoundError("value or replacement not found")
    _guard(actor, row)
    if row["domain"] != rep["domain"]:
        raise core.ValidationError("replacement must be in the same domain")
    rt, tt = row["tenant_id"], rep["tenant_id"]
    if rt is not None and tt is not None and rt != tt:
        raise core.ForbiddenError("cross-tenant replacement is not allowed")
    conn.execute("UPDATE md_entries SET replacement_id=?, status='DEPRECATED', updated_by=?,"
                 " updated_at=?, version=version+1 WHERE id=?",
                 (replacement_id, (actor or {}).get("id"), _now(), value_id))
    core.audit(conn, actor, "MASTER_DATA_REPLACED", "md_entries", value_id,
               new={"replacement_id": replacement_id, "replacement_code": rep["code"]}, reason=reason)
    conn.commit()
    return {"value_id": value_id, "replacement_id": replacement_id,
            "note": "historical records retain the original code; new selection resolves to the replacement"}


def resolve_effective(conn, value_id):
    """Follow the replacement chain to the currently-effective value (for NEW selection)."""
    seen = set()
    row = conn.execute("SELECT * FROM md_entries WHERE id=?", (value_id,)).fetchone()
    while row and row["replacement_id"] and row["id"] not in seen:
        seen.add(row["id"])
        row = conn.execute("SELECT * FROM md_entries WHERE id=?", (row["replacement_id"],)).fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------- #
# Controlled import / export
# --------------------------------------------------------------------------- #
def import_values(conn, actor, domain, rows, dry_run=True):
    """Validate + (optionally) apply master-data rows with a full report. Partial-success:
    valid rows apply, invalid rows are reported and skipped. Tenant-scoped + audited."""
    core.require(actor, "master_data.import")
    _require_domain(domain)
    tid = _actor_tenant(actor)
    seen_codes = set()
    valid, invalid, dups = [], [], []
    for i, r in enumerate(rows):
        code = (r.get("code") or "").strip()
        name = (r.get("name") or "").strip()
        if not code or not name:
            invalid.append({"row": i, "reason": "code and name are required", "data": r}); continue
        if code in seen_codes:
            dups.append({"row": i, "code": code, "reason": "duplicate code within import"}); continue
        if _by_code_row(conn, domain, code, tid) is not None:
            dups.append({"row": i, "code": code, "reason": "code already exists for tenant"}); continue
        seen_codes.add(code)
        valid.append({"row": i, "code": code, "name": name,
                      "description": r.get("description"), "sort_order": r.get("sort_order", 0)})
    applied = 0
    if not dry_run and valid:
        for v in valid:
            create_value(conn, actor, domain, v["code"], v["name"],
                         description=v["description"], sort_order=v["sort_order"])
            applied += 1
    report = {"domain": domain, "total": len(rows), "valid": len(valid), "invalid": len(invalid),
              "duplicates": len(dups), "applied": applied, "dry_run": bool(dry_run),
              "invalid_rows": invalid, "duplicate_rows": dups}
    conn.execute(
        "INSERT INTO master_data_import_log(tenant_id,domain,actor,total,valid,invalid,duplicates,"
        "applied,dry_run,report,correlation_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, domain, (actor or {}).get("id"), len(rows), len(valid), len(invalid), len(dups),
         applied, 1 if dry_run else 0, json.dumps(report), core.correlation_id(), _now()))
    core.audit(conn, actor, "MASTER_DATA_IMPORTED", "md_entries", 0,
               new={"domain": domain, "applied": applied, "dry_run": bool(dry_run)})
    conn.commit()
    return report


def export_values(conn, actor, domain):
    """Tenant-scoped export (own-tenant + shared platform values). Audited."""
    core.require(actor, "master_data.export")
    rows = list_values(conn, actor, domain, include_inactive=True)
    core.audit(conn, actor, "MASTER_DATA_EXPORTED", "md_entries", 0,
               new={"domain": domain, "count": len(rows)})
    conn.commit()
    return [{"code": r["code"], "name": r["name"], "description": r["description"],
             "status": r["status"], "sort_order": r["sort_order"]} for r in rows]


# --------------------------------------------------------------------------- #
# Seed — real reference values (additive; descriptive; no financial effect)
# --------------------------------------------------------------------------- #
SEED = {
    "customer.category": [("STRATEGIC", "Strategic Account"), ("KEY", "Key Account"),
                          ("STANDARD", "Standard Account"), ("PROSPECT", "Prospect")],
    "customer.type": [("CORPORATE", "Corporate"), ("SME", "Small/Medium Enterprise"),
                      ("GOVERNMENT", "Government"), ("INDIVIDUAL", "Individual")],
    "customer.industry": [("CONSTRUCTION", "Construction"), ("ENERGY", "Energy & Power"),
                          ("MANUFACTURING", "Manufacturing"), ("LOGISTICS", "Logistics"),
                          ("INFRA", "Infrastructure")],
    "customer.account_status": [("ACTIVE", "Active"), ("ON_HOLD", "On Hold"), ("DORMANT", "Dormant")],
    "customer.credit_rating": [("GOOD", "Good"), ("WATCH", "Watch"), ("HOLD", "Credit Hold")],
    "customer.risk_class": [("LOW", "Low Risk"), ("MEDIUM", "Medium Risk"), ("HIGH", "High Risk")],
    "lead.source": [("REFERRAL", "Referral"), ("WEBSITE", "Website"), ("OUTBOUND", "Outbound"),
                    ("TENDER", "Public Tender"), ("REPEAT", "Repeat Customer")],
    "sales.territory": [("LUZON", "Luzon"), ("VISAYAS", "Visayas"), ("MINDANAO", "Mindanao")],
    "opportunity.type": [("NEW", "New Business"), ("REPEAT", "Repeat"), ("EXPANSION", "Expansion")],
    "opportunity.lost_reason": [("PRICE", "Price"), ("TIMELINE", "Timeline"),
                                ("COMPETITOR", "Lost to Competitor"), ("NO_BUDGET", "No Budget")],
    "commercial.payment_term": [("COD", "Cash on Delivery"), ("NET15", "Net 15"),
                                ("NET30", "Net 30"), ("50_50", "50% Down / 50% on Completion")],
    "ops.service_type": [("CRANE_RENTAL", "Crane Rental"), ("RIGGING", "Rigging Services"),
                         ("HEAVY_HAUL", "Heavy Haulage"), ("HOUSE_MOVING", "Structure/House Moving"),
                         ("EQUIPMENT_TRANSPORT", "Equipment Transport")],
    "ops.equipment_type": [("MOBILE_CRANE", "Mobile Crane"), ("CRAWLER_CRANE", "Crawler Crane"),
                           ("BOOM_TRUCK", "Boom Truck"), ("FORKLIFT", "Forklift"),
                           ("GANTRY", "Gantry System")],
    "ops.vehicle_type": [("PRIME_MOVER", "Prime Mover"), ("LOWBED", "Lowbed Trailer"),
                         ("FLATBED", "Flatbed Truck"), ("SELF_LOADER", "Self Loader")],
    "ops.crew_role": [("RIGGER", "Rigger"), ("SIGNALMAN", "Signalman"), ("OPERATOR", "Crane Operator"),
                      ("SUPERVISOR", "Lift Supervisor"), ("DRIVER", "Driver")],
    "ops.maintenance_category": [("PREVENTIVE", "Preventive"), ("CORRECTIVE", "Corrective"),
                                 ("INSPECTION", "Inspection")],
    "ops.inspection_category": [("PRE_LIFT", "Pre-Lift"), ("PERIODIC", "Periodic"),
                                ("CERTIFICATION", "Certification")],
    "ops.incident_category": [("NEAR_MISS", "Near Miss"), ("PROPERTY", "Property Damage"),
                              ("INJURY", "Injury"), ("ENVIRONMENTAL", "Environmental")],
    "ops.cancellation_reason": [("CUSTOMER", "Customer Cancelled"), ("WEATHER", "Weather"),
                                ("PERMIT", "Permit Issue"), ("DUPLICATE", "Duplicate Booking")],
    "finance.expense_category": [("FUEL", "Fuel"), ("PERMITS", "Permits"), ("LABOR", "Labor"),
                                 ("SUBCON", "Subcontractor"), ("CONSUMABLES", "Consumables")],
    "finance.payment_method": [("CASH", "Cash"), ("CHECK", "Check"), ("BANK_TRANSFER", "Bank Transfer"),
                               ("WISE", "Wise")],
    "finance.currency": [("PHP", "Philippine Peso"), ("USD", "US Dollar")],
    "finance.tax_code": [("VAT", "VAT 12% (reference)"), ("ZERO", "Zero-Rated (reference)"),
                         ("EXEMPT", "VAT-Exempt (reference)")],
    "finance.uom": [("DAY", "Day"), ("HOUR", "Hour"), ("UNIT", "Unit"), ("KM", "Kilometer"),
                    ("LOT", "Lot")],
    "finance.change_order_reason": [("SCOPE", "Scope Change"), ("SITE", "Site Condition"),
                                    ("DELAY", "Customer Delay"), ("ADDITIONAL", "Additional Equipment")],
    "geo.country": [("PH", "Philippines")],
    "geo.region": [("NCR", "National Capital Region"), ("R3", "Central Luzon"), ("R4A", "CALABARZON")],
    "doc.type": [("QUOTATION", "Quotation"), ("INVOICE", "Invoice"), ("PERMIT", "Permit"),
                 ("LIFT_PLAN", "Lift Plan"), ("CERTIFICATE", "Certificate")],
    "comms.email_template": [("QUOTE_SENT", "Quotation Sent"), ("PAYMENT_REQUEST", "Payment Request"),
                             ("JOB_CONFIRMED", "Job Confirmed")],
}


def seed(conn, tenant_id=None, actor=None):
    """Idempotently seed reference values at platform scope (tenant_id NULL = shared).
    Uses a system actor; values are ACTIVE. Safe to re-run."""
    sys_actor = actor or {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
    for domain, values in SEED.items():
        for order, (code, name) in enumerate(values):
            if _by_code_row(conn, domain, code, tenant_id) is None:
                conn.execute(
                    "INSERT INTO md_entries(tenant_id,domain,code,name,status,sort_order,"
                    "system_protected,created_by,created_at) VALUES(?,?,?,?, 'ACTIVE', ?, 0, 0, ?)",
                    (tenant_id, domain, code, name, order, _now()))
    conn.commit()


def domain_catalog(conn, actor):
    """The governed domain registry + per-domain value counts (drives the MD Center menu)."""
    at = _actor_tenant(actor)
    out = []
    for domain, category, label, hier in DOMAINS:
        if at is not None:
            n = conn.execute("SELECT COUNT(*) c FROM md_entries WHERE domain=? AND (tenant_id=? OR tenant_id IS NULL)",
                             (domain, at)).fetchone()["c"]
        else:
            n = conn.execute("SELECT COUNT(*) c FROM md_entries WHERE domain=?", (domain,)).fetchone()["c"]
        out.append({"domain": domain, "category": category, "label": label,
                    "hierarchical": hier, "values": n})
    return out
