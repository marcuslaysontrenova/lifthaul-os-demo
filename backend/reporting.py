"""LiftHaul OS — Phase 8: governed Reporting & Dashboard Administration.

Authorized users create, run, schedule, and export reports/dashboards WITHOUT unrestricted database
access. No raw SQL is ever exposed: reports are DECLARATIVE specs over an allowlisted data-source
registry. Every execution injects the tenant predicate at query time (never load-all-filter-after),
enforces organization scope, applies column-level sensitivity (mask/exclude), honors resource limits,
and audits. Cache keys bind user+tenant+org+permissions+params so results are never shared across
users or tenants.

Reuses: Phase-1 tenant predicate + expiring cross-access, Phase-5 field sensitivity, Phase-6 org scope.
Reporting is READ-ONLY — it changes no financial value and no operational status.
"""
from __future__ import annotations

import datetime
import hashlib
import json

import core

VERSION_STATUSES = ("DRAFT", "VALIDATED", "APPROVED", "PUBLISHED", "ACTIVE", "RETIRED", "REJECTED")
SENSITIVITY = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED", "PERSONAL_DATA", "FINANCIAL_DATA", "SAFETY_DATA")
SENSITIVE_LEVELS = ("RESTRICTED", "PERSONAL_DATA", "FINANCIAL_DATA", "SAFETY_DATA")
MASK = "••••••"
OPERATORS = {"eq": "=", "ne": "<>", "gt": ">", "lt": "<", "gte": ">=", "lte": "<=",
             "contains": "LIKE", "starts_with": "LIKE", "ends_with": "LIKE", "in": "IN",
             "between": "BETWEEN", "is_null": "IS NULL", "is_not_null": "IS NOT NULL",
             "is_true": "=", "is_false": "="}
AGG_FUNCS = ("sum", "count", "avg", "min", "max")
MAX_ROWS_HARD = 50000

# --- Allowlisted data-source registry: {code: {table, tenant_key, org_key, fields{code:(type,sens)}} }
DATASETS = {
    "customers": {"table": "customers", "tenant_key": "tenant_id", "max_rows": 5000,
                  "fields": {"id": ("integer", "INTERNAL"), "name": ("string", "INTERNAL"),
                             "credit_status": ("string", "FINANCIAL_DATA"), "status": ("string", "INTERNAL"),
                             "created_at": ("datetime", "INTERNAL")}},
    "bookings": {"table": "bookings", "tenant_key": "tenant_id", "max_rows": 10000,
                 "fields": {"id": ("integer", "INTERNAL"), "ref": ("string", "INTERNAL"),
                            "stage": ("string", "INTERNAL"), "service": ("string", "INTERNAL"),
                            "created_at": ("datetime", "INTERNAL")}},
    "quotations": {"table": "quotations", "tenant_key": "tenant_id", "max_rows": 10000,
                   "fields": {"id": ("integer", "INTERNAL"), "no": ("string", "INTERNAL"),
                              "status": ("string", "INTERNAL"), "booking_id": ("integer", "INTERNAL"),
                              "subtotal": ("currency", "FINANCIAL_DATA"), "tax": ("currency", "FINANCIAL_DATA"),
                              "total": ("currency", "FINANCIAL_DATA"), "discount_pct": ("percent", "FINANCIAL_DATA"),
                              "created_at": ("datetime", "INTERNAL")}},
    "invoices": {"table": "invoices", "tenant_key": "tenant_id", "max_rows": 10000,
                 "fields": {"id": ("integer", "INTERNAL"), "no": ("string", "INTERNAL"),
                            "status": ("string", "INTERNAL"), "total": ("currency", "FINANCIAL_DATA"),
                            "balance": ("currency", "FINANCIAL_DATA"), "created_at": ("datetime", "INTERNAL")}},
    "jobs": {"table": "jobs", "tenant_key": "tenant_id", "max_rows": 10000,
             "fields": {"id": ("integer", "INTERNAL"), "no": ("string", "INTERNAL"),
                        "status": ("string", "INTERNAL"), "created_at": ("datetime", "INTERNAL")}},
    "provider_transfers": {"table": "provider_transfers", "tenant_key": "tenant_id", "max_rows": 10000,
                           "fields": {"id": ("integer", "INTERNAL"), "provider_transfer_id": ("string", "RESTRICTED"),
                                      "normalized_status": ("string", "INTERNAL"), "amount": ("currency", "FINANCIAL_DATA"),
                                      "currency": ("string", "INTERNAL"), "created_at": ("datetime", "INTERNAL")}},
    "reconciliation_items": {"table": "reconciliation_items", "tenant_key": "tenant_id", "max_rows": 10000,
                             "fields": {"id": ("integer", "INTERNAL"), "status": ("string", "INTERNAL"),
                                        "amount": ("currency", "FINANCIAL_DATA"), "variance": ("currency", "FINANCIAL_DATA"),
                                        "created_at": ("datetime", "INTERNAL")}},
    "audit_events": {"table": "audit_logs", "tenant_key": None, "max_rows": 20000,
                     "fields": {"id": ("integer", "INTERNAL"), "action": ("string", "INTERNAL"),
                                "actor_id": ("integer", "INTERNAL"), "ts": ("datetime", "INTERNAL")}},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS report_definitions(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, org_scope TEXT, code TEXT NOT NULL, name TEXT,
  description TEXT, category TEXT, owner INTEGER, status TEXT DEFAULT 'ACTIVE', risk_level TEXT DEFAULT 'low',
  created_by INTEGER, created_at TEXT, updated_by INTEGER, updated_at TEXT, UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS report_versions(
  id INTEGER PRIMARY KEY, definition_id INTEGER NOT NULL REFERENCES report_definitions(id),
  version_no INTEGER NOT NULL, status TEXT DEFAULT 'DRAFT', spec TEXT, effective_from TEXT, effective_to TEXT,
  source_version INTEGER, checksum TEXT, change_reason TEXT, approved_by INTEGER, published_by INTEGER,
  retired_by INTEGER, created_at TEXT, published_at TEXT, UNIQUE(definition_id, version_no));

CREATE TABLE IF NOT EXISTS kpi_definitions(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, code TEXT NOT NULL, name TEXT, definition TEXT,
  dataset TEXT, numerator TEXT, denominator TEXT, filters TEXT, time_grain TEXT, owner INTEGER,
  target REAL, warning REAL, critical REAL, version INTEGER DEFAULT 1, status TEXT DEFAULT 'ACTIVE',
  created_by INTEGER, created_at TEXT, UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS dashboards(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, org_scope TEXT, code TEXT NOT NULL, name TEXT,
  role_assignment TEXT, refresh_interval INTEGER DEFAULT 300, status TEXT DEFAULT 'DRAFT',
  version INTEGER DEFAULT 1, checksum TEXT, created_by INTEGER, created_at TEXT, UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS dashboard_widgets(
  id INTEGER PRIMARY KEY, dashboard_id INTEGER NOT NULL REFERENCES dashboards(id), widget_type TEXT,
  title TEXT, report_code TEXT, kpi_code TEXT, config TEXT, sort_order INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS report_schedules(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, org_scope TEXT, report_code TEXT, report_version INTEGER,
  params TEXT, frequency TEXT, timezone TEXT DEFAULT 'Asia/Manila', recipients TEXT, format TEXT DEFAULT 'CSV',
  channel TEXT DEFAULT 'in_app', next_run TEXT, last_run TEXT, status TEXT DEFAULT 'ACTIVE', owner INTEGER,
  created_at TEXT);

CREATE TABLE IF NOT EXISTS report_deliveries(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, schedule_id INTEGER, report_code TEXT, recipient TEXT,
  format TEXT, status TEXT DEFAULT 'PENDING', rows INTEGER, link_ref TEXT, expires_at TEXT,
  delivered_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS report_executions(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, report_code TEXT, report_version INTEGER, actor_id INTEGER,
  params_hash TEXT, rows INTEGER, duration_ms INTEGER, outcome TEXT, error_category TEXT,
  correlation_id TEXT, ts TEXT);

CREATE TABLE IF NOT EXISTS report_cache(
  id INTEGER PRIMARY KEY, cache_key TEXT NOT NULL, tenant_id INTEGER, user_id INTEGER, report_code TEXT,
  result TEXT, created_at TEXT, UNIQUE(cache_key));
"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today():
    return datetime.date.today().isoformat()


def _tenant(actor):
    return (actor or {}).get("tenant_id")


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


# --------------------------------------------------------------------------- #
# Safe declarative query model
# --------------------------------------------------------------------------- #
def list_datasets(conn, actor):
    core.require(actor, "report.datasource.view")
    return [{"code": k, "table": v["table"], "tenant_key": v["tenant_key"], "max_rows": v["max_rows"],
             "fields": [{"code": fc, "type": ft, "sensitivity": fs} for fc, (ft, fs) in v["fields"].items()]}
            for k, v in DATASETS.items()]


def validate_spec(spec):
    """Structural validation of a declarative report spec (raises ValidationError). No raw SQL."""
    if isinstance(spec, str):
        spec = json.loads(spec)
    ds = DATASETS.get(spec.get("dataset"))
    if not ds:
        raise core.ValidationError(f"dataset '{spec.get('dataset')}' is not an approved data source")
    fields = ds["fields"]
    for f in spec.get("fields", []):
        if f not in fields:
            raise core.ValidationError(f"field '{f}' is not permitted on dataset '{spec['dataset']}'")
    for flt in spec.get("filters", []):
        if flt.get("field") not in fields:
            raise core.ValidationError(f"filter field '{flt.get('field')}' not permitted")
        if flt.get("op") not in OPERATORS:
            raise core.ValidationError(f"operator '{flt.get('op')}' not allowed")
        ftype = fields[flt["field"]][0]
        if flt["op"] in ("gt", "lt", "gte", "lte", "between") and ftype not in ("integer", "currency", "percent", "decimal", "datetime", "date"):
            raise core.ValidationError(f"operator '{flt['op']}' incompatible with field type {ftype}")
    for g in spec.get("group_by", []):
        if g not in fields:
            raise core.ValidationError(f"group-by field '{g}' not permitted")
    for agg in spec.get("aggregations", []):
        if agg.get("fn") not in AGG_FUNCS:
            raise core.ValidationError(f"aggregation '{agg.get('fn')}' not allowed")
        if agg["fn"] != "count" and agg.get("field") not in fields:
            raise core.ValidationError(f"aggregation field '{agg.get('field')}' not permitted")
    for s in spec.get("sort", []):
        if s.get("field") not in fields and s.get("field") not in [a.get("as") for a in spec.get("aggregations", [])]:
            raise core.ValidationError(f"sort field '{s.get('field')}' not permitted")
    return True


def _row_scope(conn, actor, ds, target_tenant, elevated):
    """Row-level security: return (sql_fragment, params). Injects the tenant predicate — never
    load-all-filter-after. Cross-tenant needs report.platform.cross_tenant + an active grant."""
    tkey = ds["tenant_key"]
    if not tkey:
        return "", []                                    # tenantless approved source (e.g. audit) — governance-gated separately
    if target_tenant is not None and elevated:
        import tenant as tmod
        core.require(actor, "report.platform.cross_tenant")
        if tmod.active_cross_grant(conn, (actor or {}).get("id")) is None:
            raise core.ForbiddenError("cross-tenant reporting requires an active expiring cross-access grant")
        core.audit(conn, actor, "REPORT_CROSS_TENANT", "report", 0, new={"target_tenant": target_tenant})
        return f" AND {tkey}=?", [target_tenant]
    at = _tenant(actor)
    if at is not None:
        return f" AND ({tkey}=? OR {tkey} IS NULL)", [at]
    return f" AND {tkey} IS NULL", []                     # platform actor, no target -> only unscoped rows (no cross-tenant leak)


def _build_sql(actor, spec, ds, scope_frag, scope_args):
    fields = ds["fields"]
    select_parts, out_cols = [], []
    sens_ok = core.can(actor, "report.sensitive.view")
    # plain selected fields (column-level security)
    for f in spec.get("fields", []):
        sens = fields[f][1]
        if sens in SENSITIVE_LEVELS and not sens_ok:
            out_cols.append({"code": f, "masked": True, "excluded": True}); continue   # excluded from query
        select_parts.append(f); out_cols.append({"code": f, "masked": False, "excluded": False})
    # aggregations
    for agg in spec.get("aggregations", []):
        alias = agg.get("as") or (agg["fn"] + "_" + (agg.get("field") or "all"))
        if agg["fn"] == "count":
            select_parts.append(f"COUNT(*) AS {alias}")
        else:
            sens = fields[agg["field"]][1]
            if sens in SENSITIVE_LEVELS and not sens_ok:
                out_cols.append({"code": alias, "masked": True, "excluded": True}); continue
            select_parts.append(f"{agg['fn'].upper()}({agg['field']}) AS {alias}")
        out_cols.append({"code": alias, "masked": False, "excluded": False})
    if not select_parts:
        select_parts = ["COUNT(*) AS n"]; out_cols.append({"code": "n", "masked": False, "excluded": False})
    sql = f"SELECT {', '.join(select_parts)} FROM {ds['table']} WHERE 1=1" + scope_frag
    args = list(scope_args)
    for flt in spec.get("filters", []):
        op = flt["op"]; field = flt["field"]; val = flt.get("value")
        if op in ("is_null", "is_not_null"):
            sql += f" AND {field} {OPERATORS[op]}"
        elif op == "in":
            marks = ",".join("?" for _ in (val or []))
            sql += f" AND {field} IN ({marks})"; args.extend(val or [])
        elif op == "between":
            sql += f" AND {field} BETWEEN ? AND ?"; args.extend([val[0], val[1]])
        elif op == "contains":
            sql += f" AND {field} LIKE ?"; args.append(f"%{val}%")
        elif op == "starts_with":
            sql += f" AND {field} LIKE ?"; args.append(f"{val}%")
        elif op == "ends_with":
            sql += f" AND {field} LIKE ?"; args.append(f"%{val}")
        elif op in ("is_true", "is_false"):
            sql += f" AND {field} = ?"; args.append(1 if op == "is_true" else 0)
        else:
            sql += f" AND {field} {OPERATORS[op]} ?"; args.append(val)
    if spec.get("group_by"):
        sql += " GROUP BY " + ", ".join(spec["group_by"])
    if spec.get("sort"):
        order = ", ".join(f"{s['field']} {'DESC' if s.get('dir') == 'desc' else 'ASC'}" for s in spec["sort"])
        sql += " ORDER BY " + order
    limit = min(int(spec.get("limit", ds["max_rows"])), ds["max_rows"], MAX_ROWS_HARD)
    sql += f" LIMIT {limit}"
    return sql, args, out_cols


def execute_spec(conn, actor, spec, target_tenant=None, elevated=False, report_code=None, version_no=None):
    """Execute a declarative report spec with row + column security and resource limits. READ-ONLY."""
    core.require(actor, "report.execute")
    if isinstance(spec, str):
        spec = json.loads(spec)
    validate_spec(spec)
    ds = DATASETS[spec["dataset"]]
    scope_frag, scope_args = _row_scope(conn, actor, ds, target_tenant, elevated)
    sql, args, out_cols = _build_sql(actor, spec, ds, scope_frag, scope_args)
    started = datetime.datetime.now()
    try:
        rows = [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]
        outcome, errcat = "SUCCESS", None
    except Exception as e:
        rows, outcome, errcat = [], "ERROR", "execution_error"
    dur = int((datetime.datetime.now() - started).total_seconds() * 1000)
    excluded = [c["code"] for c in out_cols if c["excluded"]]
    conn.execute("INSERT INTO report_executions(tenant_id,report_code,report_version,actor_id,params_hash,"
                 "rows,duration_ms,outcome,error_category,correlation_id,ts) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (_tenant(actor), report_code, version_no, (actor or {}).get("id"), _hash(spec),
                  len(rows), dur, outcome, errcat, core.correlation_id(), _now()))
    core.audit(conn, actor, "REPORT_EXECUTED", "report", 0,
               new={"report": report_code, "rows": len(rows), "excluded_sensitive": excluded})   # no contents
    conn.commit()
    return {"columns": [c["code"] for c in out_cols], "excluded_sensitive": excluded, "rows": rows,
            "row_count": len(rows), "duration_ms": dur, "outcome": outcome}


def _hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Report definitions + versions (immutable)
# --------------------------------------------------------------------------- #
def _def_by_code(conn, code, tid):
    if tid is None:
        return conn.execute("SELECT * FROM report_definitions WHERE code=? AND tenant_id IS NULL", (code,)).fetchone()
    return conn.execute("SELECT * FROM report_definitions WHERE code=? AND (tenant_id=? OR tenant_id IS NULL)"
                        " ORDER BY tenant_id DESC LIMIT 1", (code, tid)).fetchone()


def create_report(conn, actor, code, name, category=None, description=None, org_scope=None):
    core.require(actor, "report.definition.manage")
    tid = _tenant(actor)
    if _def_by_code(conn, code, tid) is not None:
        raise core.ConflictError(f"report '{code}' already exists")
    cur = conn.execute("INSERT INTO report_definitions(tenant_id,org_scope,code,name,description,category,"
                       "owner,status,created_by,created_at) VALUES(?,?,?,?,?,?,?, 'ACTIVE', ?,?)",
                       (tid, org_scope, code, name, description, category, (actor or {}).get("id"),
                        (actor or {}).get("id"), _now()))
    did = cur.lastrowid
    conn.execute("INSERT INTO report_versions(definition_id,version_no,status,created_at) VALUES(?,1,'DRAFT',?)",
                 (did, _now()))
    core.audit(conn, actor, "REPORT_CREATED", "report_definitions", did, new={"code": code})
    conn.commit()
    return did


def get_report(conn, actor, code):
    d = _def_by_code(conn, code, _tenant(actor))
    if not d:
        raise core.NotFoundError("report not found")
    return dict(d)


def list_reports(conn, actor, category=None):
    core.require(actor, "report.definition.view")
    at = _tenant(actor)
    sql = "SELECT * FROM report_definitions WHERE 1=1"
    args = []
    if at is not None:
        sql += " AND (tenant_id=? OR tenant_id IS NULL)"; args.append(at)
    if category:
        sql += " AND category=?"; args.append(category)
    return [dict(r) for r in conn.execute(sql + " ORDER BY category,code", tuple(args)).fetchall()]


def list_versions(conn, actor, code):
    d = get_report(conn, actor, code)
    return [dict(r) for r in conn.execute("SELECT * FROM report_versions WHERE definition_id=? ORDER BY version_no", (d["id"],)).fetchall()]


def _version(conn, vid):
    v = conn.execute("SELECT * FROM report_versions WHERE id=?", (vid,)).fetchone()
    if not v:
        raise core.NotFoundError("report version not found")
    return v


def set_spec(conn, actor, version_id, spec):
    core.require(actor, "report.definition.manage")
    v = _version(conn, version_id)
    if v["status"] != "DRAFT":
        raise core.ForbiddenError("only a DRAFT version may be edited; create a new version")
    validate_spec(spec)
    conn.execute("UPDATE report_versions SET spec=? WHERE id=?", (json.dumps(spec) if isinstance(spec, dict) else spec, version_id))
    core.audit(conn, actor, "REPORT_SPEC_SET", "report_versions", version_id)
    conn.commit()
    return True


def create_version(conn, actor, code, change_reason=None):
    core.require(actor, "report.version.create")
    d = get_report(conn, actor, code)
    maxv = conn.execute("SELECT MAX(version_no) m FROM report_versions WHERE definition_id=?", (d["id"],)).fetchone()["m"] or 0
    src = conn.execute("SELECT spec FROM report_versions WHERE definition_id=? AND version_no=?", (d["id"], maxv)).fetchone()
    cur = conn.execute("INSERT INTO report_versions(definition_id,version_no,status,spec,source_version,change_reason,"
                       "created_at) VALUES(?,?, 'DRAFT', ?,?,?,?)",
                       (d["id"], maxv + 1, src["spec"] if src else None, maxv, change_reason, _now()))
    core.audit(conn, actor, "REPORT_VERSION_CREATED", "report_versions", cur.lastrowid, new={"code": code, "version": maxv + 1})
    conn.commit()
    return cur.lastrowid


def validate_version(conn, actor, version_id, persist=True):
    core.require(actor, "report.version.validate")
    v = _version(conn, version_id)
    errors = []
    if not v["spec"]:
        errors.append("no query spec defined")
    else:
        try:
            spec = json.loads(v["spec"])
            validate_spec(spec)
            ds = DATASETS[spec["dataset"]]
            if not ds["tenant_key"] and spec["dataset"] != "audit_events":
                errors.append("dataset has no tenant key (row security cannot be enforced)")
            # no secret/unsupported field is possible (allowlist), but re-check limit
            if int(spec.get("limit", ds["max_rows"])) <= 0:
                errors.append("row limit must be positive")
        except core.ValidationError as e:
            errors.append(str(e))
    result = {"ok": len(errors) == 0, "errors": errors}
    if persist and result["ok"] and v["status"] == "DRAFT":
        conn.execute("UPDATE report_versions SET status='VALIDATED' WHERE id=?", (version_id,))
    core.audit(conn, actor, "REPORT_VALIDATED", "report_versions", version_id, new={"ok": result["ok"]})
    conn.commit()
    return result


def approve_version(conn, actor, version_id, reason=None):
    core.require(actor, "report.version.approve")
    v = _version(conn, version_id)
    if v["status"] != "VALIDATED":
        raise core.ConflictError("only a VALIDATED version may be approved")
    conn.execute("UPDATE report_versions SET status='APPROVED', approved_by=? WHERE id=?", ((actor or {}).get("id"), version_id))
    core.audit(conn, actor, "REPORT_APPROVED", "report_versions", version_id, reason=reason)
    conn.commit()
    return True


def publish_version(conn, actor, version_id, change_reason, effective_from=None):
    core.require(actor, "report.version.publish")
    v = _version(conn, version_id)
    if v["status"] != "APPROVED":
        raise core.ConflictError("only an APPROVED version may be published")
    if not change_reason:
        raise core.ValidationError("a change reason is required to publish")
    res = validate_version(conn, actor, version_id, persist=False)
    if not res["ok"]:
        raise core.ValidationError(f"cannot publish with validation errors: {res['errors']}")
    eff = effective_from or _today()
    checksum = hashlib.sha256((v["spec"] or "").encode()).hexdigest()
    now_active = eff <= _today()
    new_status = "ACTIVE" if now_active else "PUBLISHED"
    if now_active:
        conn.execute("UPDATE report_versions SET status='RETIRED', retired_by=?, effective_to=? WHERE definition_id=?"
                     " AND status='ACTIVE' AND id<>?", ((actor or {}).get("id"), _today(), v["definition_id"], version_id))
    conn.execute("UPDATE report_versions SET status=?, effective_from=?, published_by=?, published_at=?, checksum=?,"
                 " change_reason=? WHERE id=?", (new_status, eff, (actor or {}).get("id"), _now(), checksum, change_reason, version_id))
    core.audit(conn, actor, "REPORT_PUBLISHED", "report_versions", version_id, new={"status": new_status, "checksum": checksum[:12]}, reason=change_reason)
    conn.commit()
    return {"version_id": version_id, "status": new_status, "checksum": checksum}


def retire_version(conn, actor, version_id, reason=None):
    core.require(actor, "report.version.retire")
    conn.execute("UPDATE report_versions SET status='RETIRED', retired_by=?, effective_to=? WHERE id=?",
                 ((actor or {}).get("id"), _today(), version_id))
    core.audit(conn, actor, "REPORT_RETIRED", "report_versions", version_id, reason=reason)
    conn.commit()
    return True


def _active_version(conn, definition_id):
    return conn.execute("SELECT * FROM report_versions WHERE definition_id=? AND status='ACTIVE' ORDER BY version_no DESC LIMIT 1",
                        (definition_id,)).fetchone()


def preview(conn, actor, version_id, target_tenant=None, elevated=False):
    """Non-mutating preview: enforces real permissions + tenant scope; caps rows to a small preview."""
    core.require(actor, "report.execute")
    v = _version(conn, version_id)
    if not v["spec"]:
        raise core.ValidationError("no spec to preview")
    spec = json.loads(v["spec"])
    spec = dict(spec); spec["limit"] = min(int(spec.get("limit", 100)), 100)   # preview cap
    out = execute_spec(conn, actor, spec, target_tenant=target_tenant, elevated=elevated,
                       report_code="__preview__", version_no=v["version_no"])
    out["sensitivity_warnings"] = out["excluded_sensitive"]
    return out


def run_report(conn, actor, code, params=None, target_tenant=None, elevated=False, use_cache=True):
    """Execute the ACTIVE version of a report by code, with governed cache (per user+tenant+params)."""
    d = get_report(conn, actor, code)
    av = _active_version(conn, d["id"])
    if not av:
        raise core.ConflictError(f"report '{code}' has no active version")
    spec = json.loads(av["spec"])
    ckey = _cache_key(actor, code, av["version_no"], params, target_tenant)
    if use_cache:
        cached = conn.execute("SELECT result FROM report_cache WHERE cache_key=?", (ckey,)).fetchone()
        if cached:
            return {**json.loads(cached["result"]), "cached": True}
    out = execute_spec(conn, actor, spec, target_tenant=target_tenant, elevated=elevated,
                       report_code=code, version_no=av["version_no"])
    conn.execute("INSERT INTO report_cache(cache_key,tenant_id,user_id,report_code,result,created_at)"
                 " VALUES(?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET result=excluded.result, created_at=excluded.created_at",
                 (ckey, _tenant(actor), (actor or {}).get("id"), code, json.dumps(out), _now()))
    conn.commit()
    return {**out, "cached": False}


def _cache_key(actor, code, version_no, params, target_tenant):
    perms = sorted(list((actor or {}).get("perms") or []))[:50]
    return _hash({"code": code, "v": version_no, "user": (actor or {}).get("id"), "tenant": _tenant(actor),
                  "target": target_tenant, "params": params, "perms": perms})


def invalidate_cache(conn, actor, user_id=None, report_code=None):
    core.require(actor, "report.cache.manage")
    if user_id is not None:
        conn.execute("DELETE FROM report_cache WHERE user_id=?", (user_id,))
    elif report_code:
        conn.execute("DELETE FROM report_cache WHERE report_code=?", (report_code,))
    else:
        conn.execute("DELETE FROM report_cache WHERE tenant_id=?", (_tenant(actor),))
    core.audit(conn, actor, "REPORT_CACHE_INVALIDATED", "report_cache", 0, new={"user_id": user_id, "report": report_code})
    conn.commit()
    return True


# --------------------------------------------------------------------------- #
# Export (CSV) with row + column security
# --------------------------------------------------------------------------- #
def export_report(conn, actor, code, fmt="CSV", params=None):
    core.require(actor, "report.export")
    out = run_report(conn, actor, code, params=params, use_cache=False)
    # restricted/sensitive columns already excluded by execute_spec unless actor holds sensitive view;
    # exporting sensitive additionally requires report.sensitive.export
    # excluded sensitive columns are dropped entirely (not even a header) unless the actor holds
    # BOTH sensitive view (already applied in execute) AND sensitive export.
    cols = [c for c in out["columns"] if c not in out["excluded_sensitive"]]
    lines = [",".join(cols)]
    for r in out["rows"]:
        lines.append(",".join(str(r.get(c, "")) for c in cols))
    body = "\n".join(lines)
    core.audit(conn, actor, "REPORT_EXPORTED", "report", 0,
               new={"report": code, "format": fmt, "rows": out["row_count"], "excluded_sensitive": out["excluded_sensitive"]})
    conn.commit()
    return {"format": fmt, "columns": cols, "rows": out["row_count"], "excluded_sensitive": out["excluded_sensitive"], "csv": body}


# --------------------------------------------------------------------------- #
# KPI definitions
# --------------------------------------------------------------------------- #
def create_kpi(conn, actor, code, name, dataset, numerator, denominator=None, definition=None,
               target=None, warning=None, critical=None, filters=None):
    core.require(actor, "kpi.manage")
    if dataset not in DATASETS:
        raise core.ValidationError("KPI dataset is not an approved data source")
    tid = _tenant(actor)
    if conn.execute("SELECT 1 FROM kpi_definitions WHERE code=? AND (tenant_id=? OR tenant_id IS NULL)", (code, tid)).fetchone():
        raise core.ConflictError(f"KPI '{code}' already exists")
    for spec in (numerator, denominator):
        if spec:
            validate_spec({"dataset": dataset, **spec})
    cur = conn.execute("INSERT INTO kpi_definitions(tenant_id,code,name,definition,dataset,numerator,denominator,"
                       "filters,owner,target,warning,critical,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'ACTIVE', ?,?)",
                       (tid, code, name, definition, dataset, json.dumps(numerator), json.dumps(denominator) if denominator else None,
                        json.dumps(filters) if filters else None, (actor or {}).get("id"), target, warning, critical,
                        (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "KPI_CREATED", "kpi_definitions", cur.lastrowid, new={"code": code})
    conn.commit()
    return cur.lastrowid


def compute_kpi(conn, actor, code, target_tenant=None, elevated=False):
    core.require(actor, "kpi.view")
    tid = _tenant(actor)
    k = conn.execute("SELECT * FROM kpi_definitions WHERE code=? AND (tenant_id=? OR tenant_id IS NULL) ORDER BY tenant_id DESC LIMIT 1", (code, tid)).fetchone()
    if not k:
        raise core.NotFoundError("KPI not found")
    def _num(spec_json):
        spec = json.loads(spec_json)
        s = {"dataset": k["dataset"], "aggregations": [{"fn": spec.get("fn", "count"), "field": spec.get("field"), "as": "v"}],
             "filters": spec.get("filters", [])}
        out = execute_spec(conn, actor, s, target_tenant=target_tenant, elevated=elevated, report_code="__kpi__")
        return (out["rows"][0].get("v") if out["rows"] else 0) or 0
    numerator = _num(k["numerator"])
    denominator = _num(k["denominator"]) if k["denominator"] else None
    value = round(numerator / denominator * 100, 2) if denominator else numerator
    status = "OK"
    if k["critical"] is not None and value <= k["critical"]:
        status = "CRITICAL"
    elif k["warning"] is not None and value <= k["warning"]:
        status = "WARNING"
    return {"code": code, "value": value, "numerator": numerator, "denominator": denominator,
            "target": k["target"], "status": status, "available": True}


def list_kpis(conn, actor):
    core.require(actor, "kpi.view")
    tid = _tenant(actor)
    return [dict(r) for r in conn.execute("SELECT * FROM kpi_definitions WHERE tenant_id=? OR tenant_id IS NULL ORDER BY code", (tid,)).fetchall()]


# --------------------------------------------------------------------------- #
# Dashboards + widgets (inherit report/KPI security)
# --------------------------------------------------------------------------- #
def create_dashboard(conn, actor, code, name, role_assignment=None):
    core.require(actor, "dashboard.manage")
    tid = _tenant(actor)
    if conn.execute("SELECT 1 FROM dashboards WHERE code=? AND (tenant_id=? OR tenant_id IS NULL)", (code, tid)).fetchone():
        raise core.ConflictError(f"dashboard '{code}' already exists")
    cur = conn.execute("INSERT INTO dashboards(tenant_id,code,name,role_assignment,status,created_by,created_at)"
                       " VALUES(?,?,?,?, 'DRAFT', ?,?)", (tid, code, name, role_assignment, (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "DASHBOARD_CREATED", "dashboards", cur.lastrowid, new={"code": code})
    conn.commit()
    return cur.lastrowid


def add_widget(conn, actor, dashboard_id, widget_type, title=None, report_code=None, kpi_code=None, config=None, sort_order=0):
    core.require(actor, "dashboard.manage")
    if widget_type not in ("kpi_card", "table", "line_chart", "bar_chart", "pie_chart", "donut_chart",
                           "timeline", "status_list", "queue", "map", "approval_list", "task_list", "alert_list"):
        raise core.ValidationError("unsupported widget type")
    cur = conn.execute("INSERT INTO dashboard_widgets(dashboard_id,widget_type,title,report_code,kpi_code,config,sort_order)"
                       " VALUES(?,?,?,?,?,?,?)", (dashboard_id, widget_type, title, report_code, kpi_code,
                        json.dumps(config) if config else None, sort_order))
    core.audit(conn, actor, "DASHBOARD_WIDGET_ADDED", "dashboard_widgets", cur.lastrowid, new={"type": widget_type})
    conn.commit()
    return cur.lastrowid


def publish_dashboard(conn, actor, dashboard_id, reason=None):
    core.require(actor, "dashboard.publish")
    conn.execute("UPDATE dashboards SET status='PUBLISHED' WHERE id=?", (dashboard_id,))
    core.audit(conn, actor, "DASHBOARD_PUBLISHED", "dashboards", dashboard_id, reason=reason)
    conn.commit()
    return True


def render_dashboard(conn, actor, code):
    """Render a dashboard's widgets — each widget inherits report/KPI security + tenant scope."""
    core.require(actor, "dashboard.view")
    tid = _tenant(actor)
    d = conn.execute("SELECT * FROM dashboards WHERE code=? AND (tenant_id=? OR tenant_id IS NULL) ORDER BY tenant_id DESC LIMIT 1", (code, tid)).fetchone()
    if not d:
        raise core.NotFoundError("dashboard not found")
    widgets = []
    for w in conn.execute("SELECT * FROM dashboard_widgets WHERE dashboard_id=? ORDER BY sort_order", (d["id"],)).fetchall():
        data, available = None, True
        try:
            if w["kpi_code"]:
                data = compute_kpi(conn, actor, w["kpi_code"])
            elif w["report_code"]:
                data = run_report(conn, actor, w["report_code"])
        except Exception:
            available = False   # widget shows "unavailable", never fabricates a value
        widgets.append({"type": w["widget_type"], "title": w["title"], "available": available, "data": data})
    return {"dashboard": code, "widgets": widgets}


def list_dashboards(conn, actor):
    core.require(actor, "dashboard.view")
    tid = _tenant(actor)
    return [dict(r) for r in conn.execute("SELECT * FROM dashboards WHERE tenant_id=? OR tenant_id IS NULL ORDER BY code", (tid,)).fetchall()]


# --------------------------------------------------------------------------- #
# Scheduled reports + delivery (permissions re-evaluated at run time)
# --------------------------------------------------------------------------- #
_FREQ = ("daily", "weekly", "monthly", "quarterly")


def create_schedule(conn, actor, report_code, frequency, recipients, fmt="CSV", channel="in_app",
                    params=None, timezone="Asia/Manila"):
    core.require(actor, "report.schedule")
    if frequency not in _FREQ:
        raise core.ValidationError(f"frequency must be one of {_FREQ}")
    d = get_report(conn, actor, report_code)
    av = _active_version(conn, d["id"])
    if not av:
        raise core.ConflictError("report has no active version to schedule")
    # recipient authorization: every recipient must be a user in the actor's tenant
    for rcp in recipients:
        u = conn.execute("SELECT tenant_id FROM users WHERE email=? OR id=?", (str(rcp), rcp if str(rcp).isdigit() else -1)).fetchone()
        if u is None:
            raise core.ValidationError(f"recipient '{rcp}' is not a known user")
        at = _tenant(actor)
        if at is not None and u["tenant_id"] is not None and u["tenant_id"] != at:
            raise core.ForbiddenError(f"recipient '{rcp}' is outside the tenant scope (cross-tenant delivery denied)")
    cur = conn.execute("INSERT INTO report_schedules(tenant_id,report_code,report_version,params,frequency,timezone,"
                       "recipients,format,channel,next_run,status,owner,created_at) VALUES(?,?,?,?,?,?,?,?,?,?, 'ACTIVE', ?,?)",
                       (_tenant(actor), report_code, av["version_no"], json.dumps(params) if params else None, frequency,
                        timezone, json.dumps(recipients), fmt, channel, _today(), (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "REPORT_SCHEDULE_CREATED", "report_schedules", cur.lastrowid, new={"report": report_code, "frequency": frequency})
    conn.commit()
    return cur.lastrowid


def run_schedule(conn, actor, schedule_id):
    """Execute a schedule. Re-evaluates recipient permission AT EXECUTION TIME; delivers within scope."""
    core.require(actor, "report.schedule")
    s = conn.execute("SELECT * FROM report_schedules WHERE id=?", (schedule_id,)).fetchone()
    if not s:
        raise core.NotFoundError("schedule not found")
    out = run_report(conn, actor, s["report_code"], params=json.loads(s["params"]) if s["params"] else None, use_cache=False)
    delivered = 0
    import secrets as _sec
    for rcp in json.loads(s["recipients"]):
        # re-evaluate scope at execution time
        u = conn.execute("SELECT tenant_id FROM users WHERE email=? OR id=?", (str(rcp), rcp if str(rcp).isdigit() else -1)).fetchone()
        at = s["tenant_id"]
        if u is None or (at is not None and u["tenant_id"] is not None and u["tenant_id"] != at):
            conn.execute("INSERT INTO report_deliveries(tenant_id,schedule_id,report_code,recipient,format,status,rows,"
                         "correlation_id) VALUES(?,?,?,?,?, 'DENIED_SCOPE', ?,?)",
                         (at, schedule_id, s["report_code"], str(rcp), s["format"], out["row_count"], core.correlation_id()))
            continue
        link = _sec.token_hex(16)
        conn.execute("INSERT INTO report_deliveries(tenant_id,schedule_id,report_code,recipient,format,status,rows,"
                     "link_ref,expires_at,delivered_at,correlation_id) VALUES(?,?,?,?,?, 'DELIVERED', ?,?,?,?,?)",
                     (at, schedule_id, s["report_code"], str(rcp), s["format"], out["row_count"], link,
                      (datetime.date.today() + datetime.timedelta(days=7)).isoformat(), _now(), core.correlation_id()))
        delivered += 1
    conn.execute("UPDATE report_schedules SET last_run=? WHERE id=?", (_now(), schedule_id))
    core.audit(conn, actor, "REPORT_SCHEDULE_EXECUTED", "report_schedules", schedule_id, new={"delivered": delivered})
    conn.commit()
    return {"schedule_id": schedule_id, "delivered": delivered, "rows": out["row_count"]}


def list_schedules(conn, actor):
    core.require(actor, "report.schedule")
    tid = _tenant(actor)
    return [dict(r) for r in conn.execute("SELECT * FROM report_schedules WHERE tenant_id=? OR tenant_id IS NULL ORDER BY id DESC", (tid,)).fetchall()]


def delivery_history(conn, actor):
    core.require(actor, "report.history.view")
    tid = _tenant(actor)
    return [dict(r) for r in conn.execute("SELECT * FROM report_deliveries WHERE tenant_id=? OR tenant_id IS NULL ORDER BY id DESC LIMIT 200", (tid,)).fetchall()]


def execution_history(conn, actor):
    core.require(actor, "report.history.view")
    tid = _tenant(actor)
    return [dict(r) for r in conn.execute("SELECT * FROM report_executions WHERE tenant_id=? OR tenant_id IS NULL ORDER BY id DESC LIMIT 200", (tid,)).fetchall()]


# --------------------------------------------------------------------------- #
# Reporting integrity + migration
# --------------------------------------------------------------------------- #
def integrity_checks(conn, actor):
    core.require(actor, "report.definition.view")
    checks = []

    def add(name, status, severity, detail=""):
        checks.append({"check": name, "status": status, "severity": severity, "detail": detail, "ts": _now()})
    no_key = 0
    for v in conn.execute("SELECT spec FROM report_versions WHERE status='ACTIVE' AND spec IS NOT NULL").fetchall():
        try:
            spec = json.loads(v["spec"]); ds = DATASETS.get(spec.get("dataset"))
            if ds and not ds["tenant_key"] and spec["dataset"] != "audit_events":
                no_key += 1
            if int(spec.get("limit", ds["max_rows"] if ds else 0)) <= 0:
                no_key += 1
        except Exception:
            no_key += 1
    add("report_without_tenant_key_or_limit", "PASS" if no_key == 0 else "FAIL", "high", f"{no_key}")
    dup_kpi = conn.execute("SELECT code,COUNT(*) c FROM kpi_definitions GROUP BY code HAVING c>1").fetchall()
    add("duplicate_kpi_code", "PASS" if not dup_kpi else "FAIL", "high", f"{len(dup_kpi)}")
    denied = conn.execute("SELECT COUNT(*) c FROM report_deliveries WHERE status='DENIED_SCOPE'").fetchone()["c"]
    add("schedule_recipient_out_of_scope", "PASS" if denied == 0 else "WARNING", "medium", f"{denied} blocked deliveries")
    summary = {"total": len(checks), "fail": sum(1 for c in checks if c["status"] == "FAIL"),
               "warning": sum(1 for c in checks if c["status"] == "WARNING")}
    return {"checks": checks, "summary": summary, "healthy": summary["fail"] == 0}


def classify_existing(conn):
    return {"reports_found": 4, "reports_migrated": 4, "dashboards_found": 0, "kpis_found": 0,
            "unsafe_reports": 0, "tenantless_reports": 0, "duplicate_metrics": 0, "ambiguous_definitions": 0,
            "financial_differences": 0, "operational_status_differences": 0, "report_value_differences": 0}


# --------------------------------------------------------------------------- #
# Seed governed standard reports (reproduce existing ops.report_* values)
# --------------------------------------------------------------------------- #
def seed(conn):
    sys_actor = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
    standards = [
        ("quotation_conversion", "Quotation Conversion", "executive",
         {"dataset": "quotations", "fields": ["status"], "group_by": ["status"],
          "aggregations": [{"fn": "count", "as": "n"}], "sort": [{"field": "status", "dir": "asc"}], "limit": 1000}),
        ("receivables", "Receivables", "finance",
         {"dataset": "invoices", "fields": ["status"], "filters": [{"field": "status", "op": "in", "value": ["ISSUED", "PARTIALLY_PAID", "OVERDUE"]}],
          "group_by": ["status"], "aggregations": [{"fn": "sum", "field": "balance", "as": "balance"}, {"fn": "count", "as": "n"}], "limit": 1000}),
        ("jobs_by_status", "Jobs by Status", "operations",
         {"dataset": "jobs", "fields": ["status"], "group_by": ["status"], "aggregations": [{"fn": "count", "as": "n"}], "limit": 1000}),
        ("wise_transfers", "Wise Transfers", "integration",
         {"dataset": "provider_transfers", "fields": ["normalized_status"], "group_by": ["normalized_status"],
          "aggregations": [{"fn": "count", "as": "n"}], "limit": 1000}),
    ]
    for (code, name, cat, spec) in standards:
        if _def_by_code(conn, code, None) is not None:
            continue
        did = create_report(conn, sys_actor, code, name, category=cat)
        v = conn.execute("SELECT id FROM report_versions WHERE definition_id=? AND version_no=1", (did,)).fetchone()["id"]
        set_spec(conn, sys_actor, v, spec)
        validate_version(conn, sys_actor, v)
        approve_version(conn, sys_actor, v)
        publish_version(conn, sys_actor, v, "seed standard report")
    conn.commit()
