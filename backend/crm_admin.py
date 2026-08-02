"""LiftHaul OS — Phase 3: CRM Administration (governed, dedicated tables).

Covers the CRM-specific governed capabilities that need dedicated logic (the generic
reference lists live in `masterdata`):
  * customer numbering  — governed, configurable, concurrency-safe;
  * duplicate detection — configurable weighted rules, advisory only (never auto-merge);
  * customer merge      — governed survivor selection + reference redirect (cross-tenant blocked);
  * credit policy       — effective-dated, evidence-persisting, enforcement OFF by default
                          (evidence_only) so historical documents are never mutated;
  * CRM custom fields    — declarative definitions (NO executable code) + values.

Financial/operational safety: numbering is additive; credit enforcement defaults to
`evidence_only`; nothing here recomputes an existing financial document.
"""
from __future__ import annotations

import datetime
import json
import re

import core
import admin_platform as ap

SCHEMA = """
CREATE TABLE IF NOT EXISTS customer_number_sequences(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, scope_key TEXT NOT NULL,
  next_value INTEGER NOT NULL DEFAULT 1, updated_at TEXT,
  UNIQUE(tenant_id, scope_key));

CREATE TABLE IF NOT EXISTS customer_duplicate_rules(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, name TEXT NOT NULL, dimension TEXT NOT NULL,
  match_type TEXT NOT NULL DEFAULT 'exact', weight REAL NOT NULL DEFAULT 1.0,
  active INTEGER NOT NULL DEFAULT 1, created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS customer_duplicate_candidates(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, customer_a INTEGER NOT NULL, customer_b INTEGER NOT NULL,
  score REAL, matched_dimensions TEXT, status TEXT NOT NULL DEFAULT 'POSSIBLE_DUPLICATE',
  reviewed_by INTEGER, reviewed_at TEXT, correlation_id TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS customer_merges(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, survivor_id INTEGER NOT NULL, merged_id INTEGER NOT NULL,
  redirected TEXT, external_ids TEXT, executed_by INTEGER, executed_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS credit_policies(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, code TEXT NOT NULL, name TEXT,
  credit_status TEXT DEFAULT 'GOOD', credit_limit REAL, payment_terms TEXT,
  deposit_required_pct REAL, overdue_restriction INTEGER DEFAULT 0, booking_restriction INTEGER DEFAULT 0,
  service_suspension INTEGER DEFAULT 0, effective_from TEXT, effective_to TEXT,
  active INTEGER NOT NULL DEFAULT 1, created_by INTEGER, created_at TEXT,
  UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS credit_evaluations(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, customer_id INTEGER, action TEXT, amount REAL,
  policy_code TEXT, decision TEXT, enforcement TEXT, evidence TEXT, correlation_id TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS custom_field_defs(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, org_scope TEXT, entity TEXT NOT NULL, code TEXT NOT NULL,
  label TEXT NOT NULL, description TEXT, data_type TEXT NOT NULL, required INTEGER DEFAULT 0,
  default_value TEXT, validation TEXT, selection_source TEXT, visibility TEXT DEFAULT 'visible',
  editability TEXT DEFAULT 'editable', effective_from TEXT, effective_to TEXT,
  sensitivity TEXT DEFAULT 'normal', searchable INTEGER DEFAULT 0, reportable INTEGER DEFAULT 0,
  exportable INTEGER DEFAULT 1, audit_behavior TEXT DEFAULT 'standard', status TEXT DEFAULT 'ACTIVE',
  created_by INTEGER, created_at TEXT, updated_by INTEGER, updated_at TEXT,
  UNIQUE(tenant_id, entity, code));

CREATE TABLE IF NOT EXISTS custom_field_values(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, entity TEXT NOT NULL, entity_id INTEGER NOT NULL,
  field_code TEXT NOT NULL, value TEXT, updated_by INTEGER, updated_at TEXT,
  UNIQUE(tenant_id, entity, entity_id, field_code));

CREATE UNIQUE INDEX IF NOT EXISTS ux_customer_number ON customers(customer_number);
"""

CRM_ENTITIES = ("customer", "contact", "address", "lead", "opportunity", "booking")
FIELD_TYPES = ("text", "multiline", "integer", "decimal", "currency", "date", "datetime",
               "boolean", "single_select", "multi_select", "email", "telephone", "url", "reference")
DUP_STATUSES = ("POSSIBLE_DUPLICATE", "REVIEWED_NOT_DUPLICATE", "APPROVED_FOR_MERGE", "MERGED")

# governed customer-numbering defaults (admin-owned via the config cascade)
NUMBERING_DEFAULTS = {
    "crm.numbering.enabled": "true",
    "crm.numbering.prefix": "CUS",
    "crm.numbering.suffix": "",
    "crm.numbering.padding": "6",
    "crm.numbering.include_year": "true",
    "crm.numbering.include_branch": "false",
    "crm.numbering.reset": "yearly",          # yearly | never
    "crm.credit.enforcement": "evidence_only",  # evidence_only | block  (default never blocks)
    "crm.duplicate.threshold": "1.0",
}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today():
    return datetime.date.today().isoformat()


def _year():
    return datetime.date.today().strftime("%Y")


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    """Seed numbering/credit defaults + a starter credit policy + a starter duplicate ruleset."""
    for k, v in NUMBERING_DEFAULTS.items():
        try:
            if ap.resolve_config(conn, k, tenant="")[0] is None:
                ap.set_config(conn, "platform", "", k, v, actor=actor)
        except Exception:
            pass
    # a default duplicate ruleset (platform scope, tenant_id NULL) if none exists
    if conn.execute("SELECT COUNT(*) c FROM customer_duplicate_rules").fetchone()["c"] == 0:
        for dim, mt, w in [("legal_name", "normalized", 1.0), ("email", "exact", 0.6),
                           ("phone", "exact", 0.5), ("tax_id", "exact", 1.0)]:
            conn.execute("INSERT INTO customer_duplicate_rules(tenant_id,name,dimension,match_type,"
                         "weight,active,created_at) VALUES(NULL,?,?,?,?,1,?)",
                         ("default", dim, mt, w, _now()))
    conn.commit()


# --------------------------------------------------------------------------- #
# Customer numbering (governed, concurrency-safe)
# --------------------------------------------------------------------------- #
def _numbering_config(conn, tenant_code=""):
    def g(key):
        v, _ = ap.resolve_config(conn, key, tenant=tenant_code)
        return v if v is not None else NUMBERING_DEFAULTS.get(key)
    return {"enabled": str(g("crm.numbering.enabled")).lower() == "true",
            "prefix": g("crm.numbering.prefix") or "CUS",
            "suffix": g("crm.numbering.suffix") or "",
            "padding": int(g("crm.numbering.padding") or 6),
            "include_year": str(g("crm.numbering.include_year")).lower() == "true",
            "include_branch": str(g("crm.numbering.include_branch")).lower() == "true",
            "reset": g("crm.numbering.reset") or "yearly"}


def _scope_key(cfg, branch=None):
    parts = []
    if cfg["include_year"] and cfg["reset"] == "yearly":
        parts.append(_year())
    if cfg["include_branch"] and branch:
        parts.append(str(branch))
    return "|".join(parts) or "GLOBAL"


def _format_number(cfg, seq, branch=None):
    body = []
    if cfg["prefix"]:
        body.append(cfg["prefix"])
    if cfg["include_branch"] and branch:
        body.append(str(branch))
    if cfg["include_year"]:
        body.append(_year())
    body.append(str(seq).zfill(cfg["padding"]))
    num = "-".join(body)
    return num + (cfg["suffix"] or "")


def _allocate(conn, tenant_id, scope_key):
    """Atomically allocate the next sequence value for (tenant, scope). Serialized by the
    server DB lock; a UNIQUE(customer_number) index is the final race backstop."""
    row = conn.execute("SELECT id,next_value FROM customer_number_sequences WHERE tenant_id IS ? AND scope_key=?"
                       if tenant_id is None else
                       "SELECT id,next_value FROM customer_number_sequences WHERE tenant_id=? AND scope_key=?",
                       (tenant_id, scope_key)).fetchone()
    if row is None:
        conn.execute("INSERT INTO customer_number_sequences(tenant_id,scope_key,next_value,updated_at)"
                     " VALUES(?,?,?,?)", (tenant_id, scope_key, 2, _now()))
        return 1
    seq = row["next_value"]
    conn.execute("UPDATE customer_number_sequences SET next_value=?, updated_at=? WHERE id=?",
                 (seq + 1, _now(), row["id"]))
    return seq


def preview_number(conn, actor, branch=None):
    cfg = _numbering_config(conn)
    tid = (actor or {}).get("tenant_id")
    scope_key = _scope_key(cfg, branch)
    row = conn.execute("SELECT next_value FROM customer_number_sequences WHERE tenant_id IS ? AND scope_key=?"
                       if tid is None else
                       "SELECT next_value FROM customer_number_sequences WHERE tenant_id=? AND scope_key=?",
                       (tid, scope_key)).fetchone()
    seq = row["next_value"] if row else 1
    return {"enabled": cfg["enabled"], "preview": _format_number(cfg, seq, branch),
            "next_sequence": seq, "scope_key": scope_key, "config": cfg}


def assign_customer_number(conn, actor, customer_id, branch=None):
    """Generate + persist a governed customer number (called from core.create_customer).
    No-op if numbering is disabled. Retries once on a uniqueness race."""
    cfg = _numbering_config(conn)
    if not cfg["enabled"]:
        return None
    tid = (actor or {}).get("tenant_id")
    scope_key = _scope_key(cfg, branch)
    for _ in range(3):
        seq = _allocate(conn, tid, scope_key)
        number = _format_number(cfg, seq, branch)
        try:
            conn.execute("UPDATE customers SET customer_number=? WHERE id=?", (number, customer_id))
            conn.commit()
            return number
        except Exception:
            try: conn.rollback()
            except Exception: pass
            continue
    return None


# --------------------------------------------------------------------------- #
# Duplicate detection (advisory) + governed merge
# --------------------------------------------------------------------------- #
def create_duplicate_rule(conn, actor, name, dimension, match_type="exact", weight=1.0):
    core.require(actor, "crm.admin.duplicate_rule.manage")
    if match_type not in ("exact", "normalized", "weighted"):
        raise core.ValidationError("match_type must be exact|normalized|weighted")
    tid = (actor or {}).get("tenant_id")
    cur = conn.execute("INSERT INTO customer_duplicate_rules(tenant_id,name,dimension,match_type,weight,"
                       "active,created_by,created_at) VALUES(?,?,?,?,?,1,?,?)",
                       (tid, name, dimension, match_type, float(weight), (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "DUPLICATE_RULE_CREATED", "customer_duplicate_rules", cur.lastrowid,
               new={"dimension": dimension, "match_type": match_type, "weight": weight})
    conn.commit()
    return cur.lastrowid


def list_duplicate_rules(conn, actor):
    core.require(actor, "crm.admin.duplicate_rule.view")
    tid = (actor or {}).get("tenant_id")
    rows = conn.execute("SELECT * FROM customer_duplicate_rules WHERE active=1 AND (tenant_id=? OR tenant_id IS NULL)"
                        if tid is not None else "SELECT * FROM customer_duplicate_rules WHERE active=1",
                        (tid,) if tid is not None else ()).fetchall()
    return [dict(r) for r in rows]


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _dimension_value(cust, dimension):
    m = {"legal_name": cust.get("name"), "email": cust.get("email"),
         "phone": cust.get("contact"), "tax_id": (cust.get("metadata") or {}).get("tax_id"),
         "external_id": (cust.get("metadata") or {}).get("external_id")}
    return m.get(dimension)


def _match_dim(rule, a, b):
    va, vb = _dimension_value(a, rule["dimension"]), _dimension_value(b, rule["dimension"])
    if not va or not vb:
        return False
    if rule["match_type"] == "normalized":
        return _norm(va) == _norm(vb)
    return str(va).strip().lower() == str(vb).strip().lower()


def detect_duplicates(conn, actor, customer_id):
    """Score `customer_id` against other customers using active rules. Advisory only.
    Records POSSIBLE_DUPLICATE candidates above the configured threshold; never merges."""
    core.require(actor, "crm.admin.duplicate_rule.view")
    import tenant as tenant_mod
    target = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    if not target:
        raise core.NotFoundError("customer not found")
    tenant_mod.guard(actor, target)
    a = dict(target)
    rules = list_duplicate_rules(conn, actor)
    threshold = float(ap.resolve_config(conn, "crm.duplicate.threshold", tenant="")[0] or 1.0)
    frag, args = tenant_mod.predicate(actor)
    others = conn.execute("SELECT * FROM customers WHERE id<>? AND (merged_into IS NULL)" + frag,
                          (customer_id,) + args).fetchall()
    found = []
    for o in others:
        b = dict(o)
        matched, score = [], 0.0
        for r in rules:
            if _match_dim(r, a, b):
                matched.append(r["dimension"]); score += float(r["weight"])
        if score >= threshold and matched:
            found.append({"customer_id": o["id"], "name": o["name"], "score": round(score, 3),
                          "matched_dimensions": matched})
    # exclude pairs already dismissed as NOT_DUPLICATE
    saved = []
    for f in found:
        prior = conn.execute(
            "SELECT status FROM customer_duplicate_candidates WHERE"
            " ((customer_a=? AND customer_b=?) OR (customer_a=? AND customer_b=?))"
            " ORDER BY id DESC LIMIT 1",
            (customer_id, f["customer_id"], f["customer_id"], customer_id)).fetchone()
        if prior and prior["status"] == "REVIEWED_NOT_DUPLICATE":
            continue
        cur = conn.execute(
            "INSERT INTO customer_duplicate_candidates(tenant_id,customer_a,customer_b,score,"
            "matched_dimensions,status,correlation_id,created_at) VALUES(?,?,?,?,?, 'POSSIBLE_DUPLICATE', ?, ?)",
            ((actor or {}).get("tenant_id"), customer_id, f["customer_id"], f["score"],
             json.dumps(f["matched_dimensions"]), core.correlation_id(), _now()))
        f["candidate_id"] = cur.lastrowid
        saved.append(f)
    if saved:
        core.audit(conn, actor, "DUPLICATE_DETECTED", "customers", customer_id,
                   new={"candidates": len(saved), "threshold": threshold})
    conn.commit()
    return {"customer_id": customer_id, "threshold": threshold, "candidates": saved}


def review_candidate(conn, actor, candidate_id, status, reason=None):
    """Move a candidate through the review lifecycle (not a duplicate / approved for merge)."""
    core.require(actor, "crm.admin.duplicate_rule.manage")
    if status not in ("REVIEWED_NOT_DUPLICATE", "APPROVED_FOR_MERGE", "POSSIBLE_DUPLICATE"):
        raise core.ValidationError("invalid candidate status")
    row = conn.execute("SELECT * FROM customer_duplicate_candidates WHERE id=?", (candidate_id,)).fetchone()
    if not row:
        raise core.NotFoundError("candidate not found")
    conn.execute("UPDATE customer_duplicate_candidates SET status=?, reviewed_by=?, reviewed_at=? WHERE id=?",
                 (status, (actor or {}).get("id"), _now(), candidate_id))
    core.audit(conn, actor, "DUPLICATE_REVIEWED", "customer_duplicate_candidates", candidate_id,
               old={"status": row["status"]}, new={"status": status}, reason=reason)
    conn.commit()
    return True


# tables/columns that reference a customer and must be redirected on merge
_CUSTOMER_REFS = [("bookings", "customer_id"), ("invoices", "customer_id"),
                  ("contacts", "customer_id"), ("addresses", "customer_id"),
                  ("users", "customer_id")]


def merge_preview(conn, actor, survivor_id, merged_id):
    """Impact preview for a merge: reference counts that would be redirected. Read-only."""
    core.require(actor, "crm.admin.duplicate_rule.view")
    import tenant as tenant_mod
    s = conn.execute("SELECT * FROM customers WHERE id=?", (survivor_id,)).fetchone()
    m = conn.execute("SELECT * FROM customers WHERE id=?", (merged_id,)).fetchone()
    if not s or not m:
        raise core.NotFoundError("customer not found")
    tenant_mod.guard(actor, s); tenant_mod.guard(actor, m)
    if s["tenant_id"] is not None and m["tenant_id"] is not None and s["tenant_id"] != m["tenant_id"]:
        raise core.ForbiddenError("cross-tenant merge is not allowed")
    impact = []
    for table, col in _CUSTOMER_REFS:
        try:
            n = conn.execute(f"SELECT COUNT(*) c FROM {table} WHERE {col}=?", (merged_id,)).fetchone()["c"]
        except Exception:
            n = 0
        impact.append({"table": table, "column": col, "records": n})
    return {"survivor": {"id": survivor_id, "name": s["name"]},
            "merged": {"id": merged_id, "name": m["name"]},
            "relationships": impact, "cross_tenant": False}


def merge_customers(conn, actor, survivor_id, merged_id, reason=None):
    """Governed merge: redirect references to the survivor, preserve the merged record
    (status MERGED + merged_into), keep external identifiers, audit. Cross-tenant blocked."""
    core.require(actor, "crm.admin.merge.execute")
    import tenant as tenant_mod
    s = conn.execute("SELECT * FROM customers WHERE id=?", (survivor_id,)).fetchone()
    m = conn.execute("SELECT * FROM customers WHERE id=?", (merged_id,)).fetchone()
    if not s or not m:
        raise core.NotFoundError("customer not found")
    if survivor_id == merged_id:
        raise core.ValidationError("survivor and merged customer must differ")
    tenant_mod.guard(actor, s); tenant_mod.guard(actor, m)
    if s["tenant_id"] is not None and m["tenant_id"] is not None and s["tenant_id"] != m["tenant_id"]:
        raise core.ForbiddenError("cross-tenant merge is not allowed")
    redirected = {}
    for table, col in _CUSTOMER_REFS:
        try:
            cur = conn.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (survivor_id, merged_id))
            redirected[table] = getattr(cur, "rowcount", None)
        except Exception:
            redirected[table] = 0
    ext = {"merged_customer_number": m["customer_number"] if "customer_number" in m.keys() else None,
           "merged_id": merged_id}
    conn.execute("UPDATE customers SET status='MERGED', merged_into=? WHERE id=?", (survivor_id, merged_id))
    cur = conn.execute("INSERT INTO customer_merges(tenant_id,survivor_id,merged_id,redirected,external_ids,"
                       "executed_by,executed_at,correlation_id) VALUES(?,?,?,?,?,?,?,?)",
                       ((actor or {}).get("tenant_id"), survivor_id, merged_id, json.dumps(redirected),
                        json.dumps(ext), (actor or {}).get("id"), _now(), core.correlation_id()))
    conn.execute("UPDATE customer_duplicate_candidates SET status='MERGED', reviewed_by=?, reviewed_at=?"
                 " WHERE (customer_a=? AND customer_b=?) OR (customer_a=? AND customer_b=?)",
                 ((actor or {}).get("id"), _now(), survivor_id, merged_id, merged_id, survivor_id))
    core.audit(conn, actor, "CUSTOMER_MERGED", "customers", survivor_id,
               new={"merged_id": merged_id, "redirected": redirected}, reason=reason)
    conn.commit()
    return {"survivor_id": survivor_id, "merged_id": merged_id, "redirected": redirected,
            "merge_id": cur.lastrowid, "preserved_external_ids": ext}


# --------------------------------------------------------------------------- #
# Credit policy (effective-dated, evidence-persisting, enforcement OFF by default)
# --------------------------------------------------------------------------- #
def create_credit_policy(conn, actor, code, name, credit_limit=None, payment_terms=None,
                         deposit_required_pct=None, credit_status="GOOD", overdue_restriction=False,
                         booking_restriction=False, service_suspension=False,
                         effective_from=None, effective_to=None):
    core.require(actor, "crm.admin.credit_policy.manage")
    tid = (actor or {}).get("tenant_id")
    if conn.execute("SELECT 1 FROM credit_policies WHERE tenant_id IS ? AND code=?" if tid is None
                    else "SELECT 1 FROM credit_policies WHERE tenant_id=? AND code=?",
                    (tid, code)).fetchone():
        raise core.ConflictError(f"credit policy '{code}' already exists")
    cur = conn.execute(
        "INSERT INTO credit_policies(tenant_id,code,name,credit_status,credit_limit,payment_terms,"
        "deposit_required_pct,overdue_restriction,booking_restriction,service_suspension,"
        "effective_from,effective_to,active,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
        (tid, code, name, credit_status, credit_limit, payment_terms, deposit_required_pct,
         1 if overdue_restriction else 0, 1 if booking_restriction else 0,
         1 if service_suspension else 0, effective_from, effective_to, (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "CREDIT_POLICY_CREATED", "credit_policies", cur.lastrowid,
               new={"code": code, "credit_limit": credit_limit})
    conn.commit()
    return cur.lastrowid


def list_credit_policies(conn, actor):
    core.require(actor, "crm.admin.credit_policy.view")
    tid = (actor or {}).get("tenant_id")
    rows = conn.execute("SELECT * FROM credit_policies WHERE tenant_id=? OR tenant_id IS NULL" if tid is not None
                        else "SELECT * FROM credit_policies", (tid,) if tid is not None else ()).fetchall()
    return [dict(r) for r in rows]


def _applicable_policy(conn, tid, code, on_date):
    rows = conn.execute("SELECT * FROM credit_policies WHERE active=1 AND (tenant_id=? OR tenant_id IS NULL)"
                        if tid is not None else "SELECT * FROM credit_policies WHERE active=1",
                        (tid,) if tid is not None else ()).fetchall()
    for r in rows:
        if code and r["code"] != code:
            continue
        if r["effective_from"] and r["effective_from"] > on_date:
            continue
        if r["effective_to"] and r["effective_to"] < on_date:
            continue
        return r
    return None


def evaluate_credit(conn, actor, customer_id, action, amount=0, policy_code=None, persist=True):
    """Evaluate the applicable credit policy for a customer action and PERSIST the evidence.
    Enforcement is governed by `crm.credit.enforcement`:
      * evidence_only (DEFAULT) — always ALLOW; only record the evidence (no behavior change);
      * block — return decision BLOCK when a restriction applies (caller may enforce).
    NEVER mutates an existing financial document."""
    tid = (actor or {}).get("tenant_id")
    on_date = _today()
    pol = _applicable_policy(conn, tid, policy_code, on_date)
    enforcement = (ap.resolve_config(conn, "crm.credit.enforcement", tenant="")[0] or "evidence_only")
    decision, reasons = "ALLOW", []
    if pol:
        if pol["credit_limit"] is not None and amount and float(amount) > float(pol["credit_limit"]):
            reasons.append("over_credit_limit")
        if pol["booking_restriction"] and action == "booking":
            reasons.append("booking_restricted")
        if pol["service_suspension"] and action in ("job_activation", "booking"):
            reasons.append("service_suspended")
        if reasons and enforcement == "block":
            decision = "BLOCK"
    evidence = {"policy_code": (pol["code"] if pol else None),
                "credit_status": (pol["credit_status"] if pol else None),
                "credit_limit": (pol["credit_limit"] if pol else None),
                "deposit_required_pct": (pol["deposit_required_pct"] if pol else None),
                "reasons": reasons, "enforcement": enforcement, "evaluated_on": on_date}
    if persist:
        conn.execute("INSERT INTO credit_evaluations(tenant_id,customer_id,action,amount,policy_code,"
                     "decision,enforcement,evidence,correlation_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (tid, customer_id, action, amount, (pol["code"] if pol else None), decision,
                      enforcement, json.dumps(evidence), core.correlation_id(), _now()))
        conn.commit()
    return {"decision": decision, "evidence": evidence}


# --------------------------------------------------------------------------- #
# CRM custom fields (declarative — NO executable code)
# --------------------------------------------------------------------------- #
def create_custom_field(conn, actor, entity, code, label, data_type, required=False,
                        default_value=None, validation=None, selection_source=None,
                        visibility="visible", editability="editable", sensitivity="normal",
                        searchable=False, reportable=False, exportable=True,
                        effective_from=None, effective_to=None):
    core.require(actor, "crm.admin.custom_field.manage")
    if entity not in CRM_ENTITIES:
        raise core.ValidationError(f"entity must be one of {CRM_ENTITIES}")
    if data_type not in FIELD_TYPES:
        raise core.ValidationError(f"data_type must be one of {FIELD_TYPES}")
    if validation is not None and not isinstance(validation, dict):
        raise core.ValidationError("validation must be a declarative object (no executable code)")
    tid = (actor or {}).get("tenant_id")
    if conn.execute("SELECT 1 FROM custom_field_defs WHERE tenant_id IS ? AND entity=? AND code=?" if tid is None
                    else "SELECT 1 FROM custom_field_defs WHERE tenant_id=? AND entity=? AND code=?",
                    (tid, entity, code)).fetchone():
        raise core.ConflictError(f"custom field '{code}' already exists for {entity}")
    cur = conn.execute(
        "INSERT INTO custom_field_defs(tenant_id,entity,code,label,data_type,required,default_value,"
        "validation,selection_source,visibility,editability,sensitivity,searchable,reportable,exportable,"
        "effective_from,effective_to,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',?,?)",
        (tid, entity, code, label, data_type, 1 if required else 0, default_value,
         json.dumps(validation) if validation is not None else None, selection_source, visibility,
         editability, sensitivity, 1 if searchable else 0, 1 if reportable else 0, 1 if exportable else 0,
         effective_from, effective_to, (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "CUSTOM_FIELD_CREATED", "custom_field_defs", cur.lastrowid,
               new={"entity": entity, "code": code, "data_type": data_type})
    conn.commit()
    return cur.lastrowid


def list_custom_fields(conn, actor, entity=None, include_inactive=True):
    core.require(actor, "crm.admin.custom_field.view")
    tid = (actor or {}).get("tenant_id")
    sql = "SELECT * FROM custom_field_defs WHERE 1=1"
    args = []
    if tid is not None:
        sql += " AND (tenant_id=? OR tenant_id IS NULL)"; args.append(tid)
    if entity:
        sql += " AND entity=?"; args.append(entity)
    if not include_inactive:
        sql += " AND status='ACTIVE'"
    sql += " ORDER BY entity, code"
    return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def set_custom_field_status(conn, actor, field_id, status):
    core.require(actor, "crm.admin.custom_field.manage")
    if status not in ("ACTIVE", "INACTIVE", "ARCHIVED"):
        raise core.ValidationError("status must be ACTIVE|INACTIVE|ARCHIVED")
    row = conn.execute("SELECT * FROM custom_field_defs WHERE id=?", (field_id,)).fetchone()
    if not row:
        raise core.NotFoundError("custom field not found")
    conn.execute("UPDATE custom_field_defs SET status=?, updated_by=?, updated_at=? WHERE id=?",
                 (status, (actor or {}).get("id"), _now(), field_id))
    core.audit(conn, actor, "CUSTOM_FIELD_STATUS_CHANGED", "custom_field_defs", field_id,
               old={"status": row["status"]}, new={"status": status})
    conn.commit()
    return True


def validate_custom_value(defn, value):
    """Declarative validation only — data_type + validation rules (min/max/pattern/enum/required).
    No code execution. Raises ValidationError on failure; returns the coerced value."""
    dt = defn["data_type"] if not isinstance(defn, dict) else defn.get("data_type")
    required = (defn["required"] if not isinstance(defn, dict) else defn.get("required"))
    rules = defn["validation"] if not isinstance(defn, dict) else defn.get("validation")
    if isinstance(rules, str):
        rules = json.loads(rules) if rules else {}
    rules = rules or {}
    if value in (None, ""):
        if required:
            raise core.ValidationError("value is required")
        return value
    v = value
    if dt in ("integer",):
        try: v = int(value)
        except Exception: raise core.ValidationError("must be an integer")
    elif dt in ("decimal", "currency"):
        try: v = float(value)
        except Exception: raise core.ValidationError("must be a number")
    elif dt == "boolean":
        if str(value).lower() not in ("true", "false"):
            raise core.ValidationError("must be true/false")
    elif dt == "email":
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(value)):
            raise core.ValidationError("invalid email")
    elif dt == "url":
        if not re.match(r"^https?://", str(value)):
            raise core.ValidationError("invalid url")
    if "min" in rules and float(v) < float(rules["min"]):
        raise core.ValidationError(f"below minimum {rules['min']}")
    if "max" in rules and float(v) > float(rules["max"]):
        raise core.ValidationError(f"above maximum {rules['max']}")
    if "pattern" in rules and not re.match(rules["pattern"], str(value)):
        raise core.ValidationError("does not match required pattern")
    if "enum" in rules and str(value) not in [str(x) for x in rules["enum"]]:
        raise core.ValidationError("not an allowed value")
    return v


def set_custom_value(conn, actor, entity, entity_id, field_code, value):
    """Set a custom-field value after declarative validation. Audited when audit_behavior!='none'."""
    core.require(actor, "customer.edit" if entity == "customer" else "crm.admin.custom_field.manage")
    tid = (actor or {}).get("tenant_id")
    defn = conn.execute("SELECT * FROM custom_field_defs WHERE entity=? AND code=? AND (tenant_id=? OR tenant_id IS NULL)"
                        " AND status='ACTIVE'" if tid is not None else
                        "SELECT * FROM custom_field_defs WHERE entity=? AND code=? AND status='ACTIVE'",
                        (entity, field_code, tid) if tid is not None else (entity, field_code)).fetchone()
    if not defn:
        raise core.NotFoundError("active custom field not found")
    coerced = validate_custom_value(defn, value)
    conn.execute("INSERT INTO custom_field_values(tenant_id,entity,entity_id,field_code,value,updated_by,updated_at)"
                 " VALUES(?,?,?,?,?,?,?) ON CONFLICT(tenant_id,entity,entity_id,field_code)"
                 " DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                 (tid, entity, entity_id, field_code, str(coerced), (actor or {}).get("id"), _now()))
    if defn["audit_behavior"] != "none":
        core.audit(conn, actor, "CUSTOM_VALUE_SET", entity, entity_id,
                   new={"field": field_code, "value": str(coerced)})
    conn.commit()
    return True


def get_custom_values(conn, actor, entity, entity_id):
    tid = (actor or {}).get("tenant_id")
    rows = conn.execute("SELECT field_code,value FROM custom_field_values WHERE entity=? AND entity_id=?"
                        " AND (tenant_id=? OR tenant_id IS NULL)" if tid is not None else
                        "SELECT field_code,value FROM custom_field_values WHERE entity=? AND entity_id=?",
                        (entity, entity_id, tid) if tid is not None else (entity, entity_id)).fetchall()
    return {r["field_code"]: r["value"] for r in rows}
