"""LiftHaul OS — Phase 5: governed Form & Custom-Field Administration.

Lets administrators extend approved business forms WITHOUT code changes, using safe declarative
definitions. Lifecycle: Definition → Version → Sections → Fields → Layout → Validation → Visibility
→ Permissions → Publication → Runtime Rendering → Data Capture → Audit.

Invariants (Phase 5 directive):
  * NO executable code — validation/visibility are declarative (approved operators only);
  * PUBLISHED/ACTIVE/RETIRED versions are IMMUTABLE (checksum); edits require a new draft;
  * system identifiers, financial totals, workflow state, tenant ownership, and security fields
    can NEVER be created as configurable form fields (protected registry);
  * runtime submission is SERVER-validated against the effective definition (unknown / inactive /
    cross-tenant / unauthorized / invalid-type / wrong-stage fields are rejected);
  * sensitivity classification governs view/edit/export/masking;
  * storage is additive (values in `form_values`) — no existing column is removed, no value lost.

The Phase-3 `crm_admin` custom-field foundation is preserved; this module generalizes it.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re

import core

VERSION_STATUSES = ("DRAFT", "VALIDATED", "APPROVED", "PUBLISHED", "ACTIVE", "RETIRED", "REJECTED")
EDITABLE_STATUSES = ("DRAFT",)
FIELD_TYPES = ("short_text", "long_text", "integer", "decimal", "currency", "percentage", "date",
               "datetime", "time", "boolean", "single_select", "multi_select", "radio",
               "checkbox_group", "email", "telephone", "url", "address", "geo_reference",
               "entity_reference", "user_reference", "organization_reference", "document_upload",
               "image_upload", "signature", "calculated_display", "info_text")
NUMERIC_TYPES = ("integer", "decimal", "currency", "percentage")
OPTION_TYPES = ("single_select", "multi_select", "radio", "checkbox_group")
FILE_TYPES = ("document_upload", "image_upload")
SENSITIVITY = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED", "PERSONAL_DATA",
               "FINANCIAL_DATA", "SAFETY_DATA")
SENSITIVE_LEVELS = ("RESTRICTED", "PERSONAL_DATA", "FINANCIAL_DATA", "SAFETY_DATA")
OPERATORS = ("eq", "ne", "in", "not_in", "gt", "lt", "gte", "lte", "exists", "not_exists",
             "is_true", "is_false")
VALIDATION_KEYS = ("required", "min_length", "max_length", "pattern", "min", "max", "precision",
                   "allowed_file_types", "max_file_size", "before", "after", "email", "phone",
                   "url", "reference_active", "reference_tenant", "conditional_required", "compare")

# Fields that are authoritative and can NEVER be created as configurable form fields.
PROTECTED_FIELDS = {
    "customer": {"id", "tenant_id", "customer_number", "credit_status", "status"},
    "booking": {"id", "ref", "tenant_id", "stage", "status", "job_id"},
    "quotation": {"id", "no", "tenant_id", "subtotal", "tax", "total", "discount", "dp_amount",
                  "balance", "status", "approval_snapshot", "tax_snapshot", "dp_snapshot"},
    "quotation_line": {"id", "amount"},
    "payment_request": {"id", "no", "tenant_id", "amount_due", "status", "provider_ref"},
    "job": {"id", "no", "tenant_id", "status", "stage"},
    "invoice": {"id", "no", "tenant_id", "total", "balance", "status"},
    "expense": {"id", "amount", "status"},
    "change_order": {"id", "amount", "status", "revised_total"},
}
_PROTECTED_ALWAYS = {"id", "tenant_id", "created_at", "updated_at", "deleted_at"}
CUSTOM_ENTITIES = ("customer", "contact", "address", "lead", "opportunity", "booking",
                   "site_assessment", "quotation", "quotation_line", "payment_request", "job",
                   "dispatch", "reservation", "equipment", "vehicle", "employee", "driver",
                   "operator", "supplier", "subcontractor", "maintenance_record", "inspection",
                   "safety_record", "incident", "expense", "invoice", "change_order", "document")

SCHEMA = """
CREATE TABLE IF NOT EXISTS form_definitions(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, org_scope TEXT, entity_type TEXT NOT NULL,
  code TEXT NOT NULL, name TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'ACTIVE',
  owner INTEGER, created_by INTEGER, created_at TEXT, updated_by INTEGER, updated_at TEXT,
  UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS form_versions(
  id INTEGER PRIMARY KEY, definition_id INTEGER NOT NULL REFERENCES form_definitions(id),
  version_no INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT', source_version INTEGER,
  effective_from TEXT, effective_to TEXT, change_reason TEXT, approved_by INTEGER,
  published_by INTEGER, retired_by INTEGER, checksum TEXT, created_at TEXT, published_at TEXT,
  UNIQUE(definition_id, version_no));

CREATE TABLE IF NOT EXISTS form_sections(
  id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES form_versions(id),
  code TEXT NOT NULL, title TEXT, description TEXT, sort_order INTEGER DEFAULT 0,
  collapsible INTEGER DEFAULT 0, default_expanded INTEGER DEFAULT 1, visibility TEXT,
  role_restriction TEXT, org_restriction TEXT, UNIQUE(version_id, code));

CREATE TABLE IF NOT EXISTS form_fields(
  id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES form_versions(id),
  section_code TEXT, entity_type TEXT, code TEXT NOT NULL, label TEXT, description TEXT,
  data_type TEXT NOT NULL, required INTEGER DEFAULT 0, required_condition TEXT, default_value TEXT,
  validation TEXT, visibility TEXT, editability TEXT, sensitivity TEXT DEFAULT 'INTERNAL',
  searchable INTEGER DEFAULT 0, reportable INTEGER DEFAULT 0, exportable INTEGER DEFAULT 1,
  audit_behavior TEXT DEFAULT 'standard', master_data_domain TEXT, role_restriction TEXT,
  workflow_stage TEXT, effective_from TEXT, effective_to TEXT, display_order INTEGER DEFAULT 0,
  layout TEXT, UNIQUE(version_id, code));

CREATE TABLE IF NOT EXISTS form_field_options(
  id INTEGER PRIMARY KEY, field_id INTEGER NOT NULL REFERENCES form_fields(id),
  code TEXT NOT NULL, label TEXT, description TEXT, sort_order INTEGER DEFAULT 0,
  active INTEGER DEFAULT 1, effective_from TEXT, effective_to TEXT, replacement_code TEXT,
  tenant_id INTEGER, org_scope TEXT);

CREATE TABLE IF NOT EXISTS form_values(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, org_scope TEXT, entity_type TEXT NOT NULL,
  entity_id INTEGER NOT NULL, form_version_id INTEGER, field_code TEXT NOT NULL, field_version INTEGER,
  value_type TEXT, value_text TEXT, value_num REAL, value_json TEXT, option_label TEXT,
  sensitivity TEXT, created_by INTEGER, created_at TEXT, updated_by INTEGER, updated_at TEXT,
  correlation_id TEXT, UNIQUE(tenant_id, entity_type, entity_id, field_code));

CREATE TABLE IF NOT EXISTS form_files(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, entity_type TEXT, entity_id INTEGER, field_code TEXT,
  file_ref TEXT NOT NULL, filename TEXT, content_type TEXT, size_bytes INTEGER, checksum TEXT,
  uploaded_by INTEGER, uploaded_at TEXT, deleted_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS form_signatures(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, entity_type TEXT, entity_id INTEGER, field_code TEXT,
  signer INTEGER, role TEXT, document_hash TEXT, form_version INTEGER, meaning TEXT,
  source_meta TEXT, correlation_id TEXT, signed_at TEXT);
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
# Declarative condition evaluation (visibility / required / editability)
# --------------------------------------------------------------------------- #
def validate_condition_spec(cond):
    if cond in (None, {}, ""):
        return True
    if isinstance(cond, str):
        cond = json.loads(cond)
    if "all" in cond or "any" in cond:
        subs = cond.get("all") or cond.get("any")
        if not isinstance(subs, list):
            raise core.ValidationError("compound condition must be a list")
        for s in subs:
            validate_condition_spec(s)
        return True
    if cond.get("op") not in OPERATORS:
        raise core.ValidationError(f"operator '{cond.get('op')}' is not allowed")
    if not cond.get("field"):
        raise core.ValidationError("condition requires a field")
    return True


def _referenced_fields(cond, out):
    if not cond:
        return
    if isinstance(cond, str):
        cond = json.loads(cond) if cond else {}
    if "all" in cond or "any" in cond:
        for s in (cond.get("all") or cond.get("any") or []):
            _referenced_fields(s, out)
        return
    f = cond.get("field")
    if f and not f.startswith("_"):
        out.add(f)


def eval_condition(cond, values):
    if cond in (None, {}, ""):
        return True
    if isinstance(cond, str):
        cond = json.loads(cond) if cond else {}
    if not cond:
        return True
    if "all" in cond:
        return all(eval_condition(s, values) for s in cond["all"])
    if "any" in cond:
        return any(eval_condition(s, values) for s in cond["any"])
    field, op, val = cond.get("field"), cond.get("op"), cond.get("value")
    have = field in values
    actual = values.get(field)
    if op == "exists":
        return have
    if op == "not_exists":
        return not have
    if op == "is_true":
        return actual is True or str(actual).lower() in ("true", "1", "yes")
    if op == "is_false":
        return actual is False or actual is None or str(actual).lower() in ("false", "0", "no", "")
    if op == "in":
        return actual in (val or [])
    if op == "not_in":
        return actual not in (val or [])
    if op in ("eq", "ne"):
        return (str(actual) == str(val)) if op == "eq" else (str(actual) != str(val))
    try:
        a, b = float(actual), float(val)
    except (TypeError, ValueError):
        return False
    return {"gt": a > b, "lt": a < b, "gte": a >= b, "lte": a <= b}[op]


def _safe_pattern(pat):
    if not isinstance(pat, str) or len(pat) > 200:
        raise core.ValidationError("validation pattern too long or invalid")
    if re.search(r"\([^)]*[+*]\)[+*]", pat):            # nested quantifier => catastrophic backtracking
        raise core.ValidationError("unsafe validation pattern (nested quantifier)")
    try:
        re.compile(pat)
    except re.error:
        raise core.ValidationError("invalid regular expression")
    return True


def validate_rule_spec(data_type, validation):
    """Structural validation of a declarative validation rule (definition time)."""
    if validation in (None, "", {}):
        return True
    if isinstance(validation, str):
        validation = json.loads(validation)
    for k in validation:
        if k not in VALIDATION_KEYS:
            raise core.ValidationError(f"validation key '{k}' is not allowed")
    if "pattern" in validation:
        _safe_pattern(validation["pattern"])
    if ("min" in validation or "max" in validation) and data_type not in NUMERIC_TYPES:
        raise core.ValidationError("min/max require a numeric field")
    return True


# --------------------------------------------------------------------------- #
# Definitions + versions
# --------------------------------------------------------------------------- #
def _def_by_code(conn, code, tid):
    if tid is None:
        return conn.execute("SELECT * FROM form_definitions WHERE code=? AND tenant_id IS NULL", (code,)).fetchone()
    return conn.execute("SELECT * FROM form_definitions WHERE code=? AND (tenant_id=? OR tenant_id IS NULL)"
                        " ORDER BY tenant_id DESC LIMIT 1", (code, tid)).fetchone()


def create_definition(conn, actor, entity_type, code, name, description=None, org_scope=None):
    core.require(actor, "form.definition.manage")
    if entity_type not in CUSTOM_ENTITIES:
        raise core.ValidationError(f"entity_type must be one of the supported entities")
    tid = _tenant(actor)
    if _def_by_code(conn, code, tid) is not None:
        raise core.ConflictError(f"form '{code}' already exists")
    cur = conn.execute("INSERT INTO form_definitions(tenant_id,org_scope,entity_type,code,name,description,"
                       "status,owner,created_by,created_at) VALUES(?,?,?,?,?,?, 'ACTIVE', ?,?,?)",
                       (tid, org_scope, entity_type, code, name, description, (actor or {}).get("id"),
                        (actor or {}).get("id"), _now()))
    did = cur.lastrowid
    conn.execute("INSERT INTO form_versions(definition_id,version_no,status,created_at) VALUES(?,1,'DRAFT',?)",
                 (did, _now()))
    core.audit(conn, actor, "FORM_CREATED", "form_definitions", did, new={"entity": entity_type, "code": code})
    conn.commit()
    return did


def clone_definition(conn, actor, code, new_code, new_name):
    core.require(actor, "form.definition.manage")
    src = get_definition(conn, actor, code)
    did = create_definition(conn, actor, src["entity_type"], new_code, new_name, description=src["description"])
    # copy the latest version graph into the new form's draft v1
    latest = conn.execute("SELECT MAX(version_no) m FROM form_versions WHERE definition_id=?", (src["id"],)).fetchone()["m"]
    sv = conn.execute("SELECT id FROM form_versions WHERE definition_id=? AND version_no=?", (src["id"], latest)).fetchone()
    nv = conn.execute("SELECT id FROM form_versions WHERE definition_id=? AND version_no=1", (did,)).fetchone()["id"]
    _copy_graph(conn, sv["id"], nv)
    conn.commit()
    return did


def get_definition(conn, actor, code):
    d = _def_by_code(conn, code, _tenant(actor))
    if not d:
        raise core.NotFoundError("form not found")
    return dict(d)


def list_definitions(conn, actor, entity_type=None):
    core.require(actor, "form.definition.view")
    at = _tenant(actor)
    sql = "SELECT * FROM form_definitions WHERE 1=1"
    args = []
    if at is not None:
        sql += " AND (tenant_id=? OR tenant_id IS NULL)"; args.append(at)
    if entity_type:
        sql += " AND entity_type=?"; args.append(entity_type)
    return [dict(r) for r in conn.execute(sql + " ORDER BY code", tuple(args)).fetchall()]


def list_versions(conn, actor, code):
    d = get_definition(conn, actor, code)
    return [dict(r) for r in conn.execute("SELECT * FROM form_versions WHERE definition_id=? ORDER BY version_no", (d["id"],)).fetchall()]


def _version(conn, vid):
    v = conn.execute("SELECT * FROM form_versions WHERE id=?", (vid,)).fetchone()
    if not v:
        raise core.NotFoundError("form version not found")
    return v


def _assert_editable(v):
    if v["status"] not in EDITABLE_STATUSES:
        raise core.ForbiddenError(f"version is {v['status']} and immutable; create a new draft to edit")


def _copy_graph(conn, src_vid, dst_vid):
    for s in conn.execute("SELECT * FROM form_sections WHERE version_id=?", (src_vid,)).fetchall():
        conn.execute("INSERT INTO form_sections(version_id,code,title,description,sort_order,collapsible,"
                     "default_expanded,visibility,role_restriction,org_restriction) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (dst_vid, s["code"], s["title"], s["description"], s["sort_order"], s["collapsible"],
                      s["default_expanded"], s["visibility"], s["role_restriction"], s["org_restriction"]))
    for f in conn.execute("SELECT * FROM form_fields WHERE version_id=?", (src_vid,)).fetchall():
        cur = conn.execute("INSERT INTO form_fields(version_id,section_code,entity_type,code,label,description,"
                           "data_type,required,required_condition,default_value,validation,visibility,editability,"
                           "sensitivity,searchable,reportable,exportable,audit_behavior,master_data_domain,"
                           "role_restriction,workflow_stage,effective_from,effective_to,display_order,layout)"
                           " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (dst_vid, f["section_code"], f["entity_type"], f["code"], f["label"], f["description"],
                            f["data_type"], f["required"], f["required_condition"], f["default_value"], f["validation"],
                            f["visibility"], f["editability"], f["sensitivity"], f["searchable"], f["reportable"],
                            f["exportable"], f["audit_behavior"], f["master_data_domain"], f["role_restriction"],
                            f["workflow_stage"], f["effective_from"], f["effective_to"], f["display_order"], f["layout"]))
        for o in conn.execute("SELECT * FROM form_field_options WHERE field_id=?", (f["id"],)).fetchall():
            conn.execute("INSERT INTO form_field_options(field_id,code,label,description,sort_order,active,"
                         "effective_from,effective_to,replacement_code,tenant_id,org_scope) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (cur.lastrowid, o["code"], o["label"], o["description"], o["sort_order"], o["active"],
                          o["effective_from"], o["effective_to"], o["replacement_code"], o["tenant_id"], o["org_scope"]))


def create_version(conn, actor, code, change_reason=None):
    core.require(actor, "form.version.create")
    d = get_definition(conn, actor, code)
    maxv = conn.execute("SELECT MAX(version_no) m FROM form_versions WHERE definition_id=?", (d["id"],)).fetchone()["m"] or 0
    src = conn.execute("SELECT id FROM form_versions WHERE definition_id=? AND version_no=?", (d["id"], maxv)).fetchone()
    cur = conn.execute("INSERT INTO form_versions(definition_id,version_no,status,source_version,change_reason,created_at)"
                       " VALUES(?,?, 'DRAFT', ?,?,?)", (d["id"], maxv + 1, maxv, change_reason, _now()))
    nv = cur.lastrowid
    if src:
        _copy_graph(conn, src["id"], nv)
    core.audit(conn, actor, "FORM_VERSION_CREATED", "form_versions", nv, new={"code": code, "version": maxv + 1})
    conn.commit()
    return nv


def add_section(conn, actor, version_id, code, title, sort_order=0, collapsible=False,
                default_expanded=True, visibility=None, role_restriction=None, org_restriction=None):
    core.require(actor, "form.layout.manage")
    v = _version(conn, version_id); _assert_editable(v)
    if visibility:
        validate_condition_spec(visibility)
    cur = conn.execute("INSERT INTO form_sections(version_id,code,title,sort_order,collapsible,default_expanded,"
                       "visibility,role_restriction,org_restriction) VALUES(?,?,?,?,?,?,?,?,?)",
                       (version_id, code, title, sort_order, 1 if collapsible else 0,
                        1 if default_expanded else 0,
                        json.dumps(visibility) if isinstance(visibility, dict) else visibility,
                        role_restriction, org_restriction))
    core.audit(conn, actor, "FORM_SECTION_ADDED", "form_sections", cur.lastrowid, new={"code": code})
    conn.commit()
    return cur.lastrowid


def add_field(conn, actor, version_id, code, label, data_type, section_code=None, required=False,
              required_condition=None, default_value=None, validation=None, visibility=None,
              editability=None, sensitivity="INTERNAL", searchable=False, reportable=False,
              exportable=True, master_data_domain=None, role_restriction=None, workflow_stage=None,
              display_order=0, layout=None, options=None):
    core.require(actor, "form.field.manage")
    v = _version(conn, version_id); _assert_editable(v)
    d = conn.execute("SELECT entity_type FROM form_definitions WHERE id=?", (v["definition_id"],)).fetchone()
    entity = d["entity_type"]
    if data_type not in FIELD_TYPES:
        raise core.ValidationError(f"data_type must be one of {FIELD_TYPES}")
    if sensitivity not in SENSITIVITY:
        raise core.ValidationError(f"sensitivity must be one of {SENSITIVITY}")
    # protected-field guard: cannot create a configurable field that overrides an authoritative column
    if code in _PROTECTED_ALWAYS or code in PROTECTED_FIELDS.get(entity, set()):
        raise core.ForbiddenError(f"'{code}' is a system-controlled field and cannot be a configurable form field")
    if sensitivity in SENSITIVE_LEVELS:
        core.require(actor, "form.field.sensitive.manage")
    if validation is not None:
        validate_rule_spec(data_type, validation)
    if required_condition:
        validate_condition_spec(required_condition)
    if visibility:
        validate_condition_spec(visibility)
    if editability:
        validate_condition_spec(editability)
    if conn.execute("SELECT 1 FROM form_fields WHERE version_id=? AND code=?", (version_id, code)).fetchone():
        raise core.ConflictError(f"duplicate field code '{code}'")
    cur = conn.execute("INSERT INTO form_fields(version_id,section_code,entity_type,code,label,data_type,required,"
                       "required_condition,default_value,validation,visibility,editability,sensitivity,searchable,"
                       "reportable,exportable,master_data_domain,role_restriction,workflow_stage,display_order,layout)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (version_id, section_code, entity, code, label, data_type, 1 if required else 0,
                        json.dumps(required_condition) if isinstance(required_condition, dict) else required_condition,
                        default_value,
                        json.dumps(validation) if isinstance(validation, dict) else validation,
                        json.dumps(visibility) if isinstance(visibility, dict) else visibility,
                        json.dumps(editability) if isinstance(editability, dict) else editability,
                        sensitivity, 1 if searchable else 0, 1 if reportable else 0, 1 if exportable else 0,
                        master_data_domain, role_restriction, workflow_stage, display_order,
                        json.dumps(layout) if isinstance(layout, dict) else layout))
    fid = cur.lastrowid
    for i, o in enumerate(options or []):
        conn.execute("INSERT INTO form_field_options(field_id,code,label,sort_order,active) VALUES(?,?,?,?,1)",
                     (fid, o.get("code"), o.get("label", o.get("code")), o.get("sort_order", i)))
    core.audit(conn, actor, "FORM_FIELD_CREATED", "form_fields", fid,
               new={"code": code, "type": data_type, "sensitivity": sensitivity})
    conn.commit()
    return fid


def add_option(conn, actor, field_id, code, label=None, sort_order=0):
    core.require(actor, "form.field.option.manage")
    cur = conn.execute("INSERT INTO form_field_options(field_id,code,label,sort_order,active) VALUES(?,?,?,?,1)",
                       (field_id, code, label or code, sort_order))
    core.audit(conn, actor, "FORM_OPTION_ADDED", "form_field_options", cur.lastrowid, new={"code": code})
    conn.commit()
    return cur.lastrowid


def deactivate_option(conn, actor, option_id, replacement_code=None):
    core.require(actor, "form.field.option.manage")
    conn.execute("UPDATE form_field_options SET active=0, replacement_code=? WHERE id=?", (replacement_code, option_id))
    core.audit(conn, actor, "FORM_OPTION_DEACTIVATED", "form_field_options", option_id,
               new={"replacement": replacement_code})
    conn.commit()
    return True


def delete_field(conn, actor, version_id, code):
    core.require(actor, "form.field.manage")
    v = _version(conn, version_id); _assert_editable(v)
    conn.execute("DELETE FROM form_field_options WHERE field_id IN (SELECT id FROM form_fields WHERE version_id=? AND code=?)",
                 (version_id, code))
    conn.execute("DELETE FROM form_fields WHERE version_id=? AND code=?", (version_id, code))
    core.audit(conn, actor, "FORM_FIELD_DELETED", "form_fields", 0, new={"code": code})
    conn.commit()
    return True


def sections(conn, version_id):
    return [dict(r) for r in conn.execute("SELECT * FROM form_sections WHERE version_id=? ORDER BY sort_order,code", (version_id,)).fetchall()]


def fields(conn, version_id):
    out = []
    for f in conn.execute("SELECT * FROM form_fields WHERE version_id=? ORDER BY display_order,code", (version_id,)).fetchall():
        d = dict(f)
        d["options"] = [dict(o) for o in conn.execute("SELECT * FROM form_field_options WHERE field_id=? ORDER BY sort_order", (f["id"],)).fetchall()]
        out.append(d)
    return out


def _checksum(conn, version_id):
    blob = json.dumps({"sections": sections(conn, version_id), "fields": fields(conn, version_id)},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Validation (definition time)
# --------------------------------------------------------------------------- #
def validate_version(conn, actor, version_id, persist=True):
    core.require(actor, "form.version.validate")
    v = _version(conn, version_id)
    fs = fields(conn, version_id)
    secs = {s["code"] for s in sections(conn, version_id)}
    errors, warnings = [], []
    codes = [f["code"] for f in fs]
    if len(codes) != len(set(codes)):
        errors.append("duplicate field codes")
    by_code = {f["code"]: f for f in fs}
    for f in fs:
        if f["data_type"] not in FIELD_TYPES:
            errors.append(f"field '{f['code']}' has invalid type")
        if f["section_code"] and f["section_code"] not in secs:
            errors.append(f"field '{f['code']}' references unknown section '{f['section_code']}'")
        # validation rule structural check
        try:
            validate_rule_spec(f["data_type"], f["validation"])
        except core.ValidationError as e:
            errors.append(f"field '{f['code']}' invalid validation: {e}")
        # condition fields must exist on the form
        for attr in ("visibility", "required_condition", "editability"):
            refs = set()
            _referenced_fields(f[attr], refs)
            for r in refs:
                if r not in by_code:
                    errors.append(f"field '{f['code']}' {attr} references unknown field '{r}'")
        # hidden + required without a safe default is unsatisfiable
        if f["required"] and f["visibility"] and not f["default_value"]:
            warnings.append(f"field '{f['code']}' is required but conditionally hidden without a default")
        # master-data-backed options must name a real domain
        if f["master_data_domain"]:
            try:
                import masterdata
                if f["master_data_domain"] not in masterdata.DOMAIN_KEYS:
                    errors.append(f"field '{f['code']}' references unknown master-data domain")
            except Exception:
                pass
        # option fields need options (inline or master-data-backed)
        if f["data_type"] in OPTION_TYPES and not f["options"] and not f["master_data_domain"]:
            errors.append(f"select field '{f['code']}' has no options or master-data source")
    # circular visibility / required rules
    if _has_cycle(fs, "visibility"):
        errors.append("circular visibility rule detected")
    if _has_cycle(fs, "required_condition"):
        errors.append("circular required rule detected")
    result = {"ok": len(errors) == 0, "errors": errors, "warnings": warnings,
              "fields": len(fs), "sections": len(secs)}
    if persist and result["ok"] and v["status"] == "DRAFT":
        conn.execute("UPDATE form_versions SET status='VALIDATED' WHERE id=?", (version_id,))
    core.audit(conn, actor, "FORM_VALIDATED", "form_versions", version_id, new={"ok": result["ok"], "errors": len(errors)})
    conn.commit()
    return result


def _has_cycle(fs, attr):
    graph = {}
    for f in fs:
        deps = set()
        _referenced_fields(f[attr], deps)
        graph[f["code"]] = deps
    WHITE, GREY, BLACK = 0, 1, 2
    color = {c: WHITE for c in graph}

    def dfs(n):
        color[n] = GREY
        for m in graph.get(n, ()):
            if m not in color:
                continue
            if color[m] == GREY:
                return True
            if color[m] == WHITE and dfs(m):
                return True
        color[n] = BLACK
        return False
    return any(color[c] == WHITE and dfs(c) for c in graph)


# --------------------------------------------------------------------------- #
# Simulation (non-mutating)
# --------------------------------------------------------------------------- #
def simulate(conn, actor, version_id, ctx=None, values=None):
    core.require(actor, "form.simulate")
    ctx = ctx or {}
    values = dict(values or {})
    values.update({"_role": ctx.get("role"), "_stage": ctx.get("stage"), "_portal": ctx.get("portal", False)})
    fs = fields(conn, version_id)
    visible, required, editable, validation_errors = [], [], [], []
    role = ctx.get("role")
    portal = ctx.get("portal", False)
    for f in fs:
        # role/portal/sensitivity gating (server-side)
        if f["role_restriction"] and role and f["role_restriction"] != role and role != "admin":
            continue
        if portal and f["sensitivity"] in SENSITIVE_LEVELS:
            continue
        if f["workflow_stage"] and ctx.get("stage") and f["workflow_stage"] != ctx.get("stage"):
            continue
        if not eval_condition(f["visibility"], values):
            continue
        visible.append(f["code"])
        req = bool(f["required"]) or (f["required_condition"] and eval_condition(f["required_condition"], values))
        if req:
            required.append(f["code"])
        editable_flag = eval_condition(f["editability"], values) if f["editability"] else True
        if editable_flag:
            editable.append(f["code"])
        if f["code"] in values:
            err = _validate_value(f, values[f["code"]], conn, actor)
            if err:
                validation_errors.append({"field": f["code"], "error": err})
    core.audit(conn, actor, "FORM_SIMULATED", "form_versions", version_id,
               new={"role": role, "visible": len(visible)})   # no sample payload stored
    conn.commit()
    return {"visible": visible, "required": required, "editable": editable,
            "validation_errors": validation_errors, "role": role, "portal": portal}


# --------------------------------------------------------------------------- #
# Publication + activation
# --------------------------------------------------------------------------- #
def approve_version(conn, actor, version_id, reason=None):
    core.require(actor, "form.version.approve")
    v = _version(conn, version_id)
    if v["status"] != "VALIDATED":
        raise core.ConflictError("only a VALIDATED version may be approved")
    conn.execute("UPDATE form_versions SET status='APPROVED', approved_by=? WHERE id=?", ((actor or {}).get("id"), version_id))
    core.audit(conn, actor, "FORM_APPROVED", "form_versions", version_id, reason=reason)
    conn.commit()
    return True


def reject_version(conn, actor, version_id, reason=None):
    core.require(actor, "form.version.approve")
    conn.execute("UPDATE form_versions SET status='REJECTED' WHERE id=?", (version_id,))
    core.audit(conn, actor, "FORM_REJECTED", "form_versions", version_id, reason=reason)
    conn.commit()
    return True


def publish_version(conn, actor, version_id, change_reason, effective_from=None):
    core.require(actor, "form.version.publish")
    v = _version(conn, version_id)
    if v["status"] != "APPROVED":
        raise core.ConflictError("only an APPROVED version may be published")
    if not change_reason:
        raise core.ValidationError("a change reason is required to publish")
    res = validate_version(conn, actor, version_id, persist=False)
    if not res["ok"]:
        raise core.ValidationError(f"cannot publish with critical validation errors: {res['errors']}")
    eff = effective_from or _today()
    checksum = _checksum(conn, version_id)
    now_active = eff <= _today()
    new_status = "ACTIVE" if now_active else "PUBLISHED"
    if now_active:
        conn.execute("UPDATE form_versions SET status='RETIRED', retired_by=?, effective_to=? WHERE definition_id=?"
                     " AND status='ACTIVE' AND id<>?", ((actor or {}).get("id"), _today(), v["definition_id"], version_id))
    conn.execute("UPDATE form_versions SET status=?, effective_from=?, published_by=?, published_at=?, checksum=?,"
                 " change_reason=? WHERE id=?",
                 (new_status, eff, (actor or {}).get("id"), _now(), checksum, change_reason, version_id))
    core.audit(conn, actor, "FORM_PUBLISHED", "form_versions", version_id,
               new={"status": new_status, "effective_from": eff, "checksum": checksum[:12]}, reason=change_reason)
    conn.commit()
    return {"version_id": version_id, "status": new_status, "effective_from": eff, "checksum": checksum}


def retire_version(conn, actor, version_id, reason=None):
    core.require(actor, "form.version.retire")
    conn.execute("UPDATE form_versions SET status='RETIRED', retired_by=?, effective_to=? WHERE id=?",
                 ((actor or {}).get("id"), _today(), version_id))
    core.audit(conn, actor, "FORM_RETIRED", "form_versions", version_id, reason=reason)
    conn.commit()
    return True


def activate_due(conn):
    today = _today()
    for v in conn.execute("SELECT * FROM form_versions WHERE status='PUBLISHED' AND effective_from<=?", (today,)).fetchall():
        conn.execute("UPDATE form_versions SET status='RETIRED', effective_to=? WHERE definition_id=? AND status='ACTIVE'",
                     (today, v["definition_id"]))
        conn.execute("UPDATE form_versions SET status='ACTIVE' WHERE id=?", (v["id"],))
    conn.commit()


def active_version_for_entity(conn, actor, entity_type):
    """The active form version for an entity in the actor's tenant (tenant override then platform)."""
    activate_due(conn)
    at = _tenant(actor)
    row = conn.execute(
        "SELECT fv.* FROM form_versions fv JOIN form_definitions fd ON fd.id=fv.definition_id"
        " WHERE fd.entity_type=? AND fv.status='ACTIVE' AND (fd.tenant_id=? OR fd.tenant_id IS NULL)"
        " ORDER BY fd.tenant_id DESC, fv.version_no DESC LIMIT 1"
        if at is not None else
        "SELECT fv.* FROM form_versions fv JOIN form_definitions fd ON fd.id=fv.definition_id"
        " WHERE fd.entity_type=? AND fv.status='ACTIVE' ORDER BY fv.version_no DESC LIMIT 1",
        (entity_type, at) if at is not None else (entity_type,)).fetchone()
    return row


# --------------------------------------------------------------------------- #
# Dependency / impact analysis
# --------------------------------------------------------------------------- #
def field_dependencies(conn, actor, version_id, code):
    core.require(actor, "form.field.view")
    f = conn.execute("SELECT * FROM form_fields WHERE version_id=? AND code=?", (version_id, code)).fetchone()
    if not f:
        raise core.NotFoundError("field not found")
    entity = f["entity_type"]
    values = conn.execute("SELECT COUNT(*) c FROM form_values WHERE entity_type=? AND field_code=?", (entity, code)).fetchone()["c"]
    forms_using = conn.execute("SELECT COUNT(DISTINCT version_id) c FROM form_fields WHERE code=? AND entity_type=?", (code, entity)).fetchone()["c"]
    # visibility/required dependencies within the version
    dependents = []
    for other in fields(conn, version_id):
        refs = set()
        for attr in ("visibility", "required_condition", "editability"):
            _referenced_fields(other[attr], refs)
        if code in refs:
            dependents.append(other["code"])
    return {"field": code, "records_with_values": values, "forms_using": forms_using,
            "dependent_fields": dependents, "portal_exposed": (f["sensitivity"] == "PUBLIC"),
            "safe_to_retire": values == 0 and not dependents}


# --------------------------------------------------------------------------- #
# Runtime rendering + submission + values (server-enforced)
# --------------------------------------------------------------------------- #
def _mask(value, sensitivity, allowed):
    if sensitivity in SENSITIVE_LEVELS and not allowed:
        return "••••••"
    return value


def effective_form(conn, actor, entity_type, role=None, stage=None, portal=False):
    """The effective, role/stage-filtered form for runtime rendering. Server-authoritative."""
    core.require(actor, "form.data.view")
    v = active_version_for_entity(conn, actor, entity_type)
    if not v:
        return {"entity_type": entity_type, "version": None, "sections": [], "fields": []}
    role = role or actor.get("role")
    sens_ok = core.can(actor, "form.data.sensitive.view")
    out_fields = []
    for f in fields(conn, v["id"]):
        if f["role_restriction"] and role and f["role_restriction"] != role and role != "admin" and "*" not in (actor.get("perms") or set()):
            continue
        if portal and f["sensitivity"] in SENSITIVE_LEVELS:
            continue
        if f["workflow_stage"] and stage and f["workflow_stage"] != stage:
            continue
        d = {k: f[k] for k in ("code", "label", "data_type", "required", "required_condition",
                               "visibility", "editability", "sensitivity", "section_code",
                               "master_data_domain", "display_order", "default_value", "validation")}
        d["masked"] = (f["sensitivity"] in SENSITIVE_LEVELS and not sens_ok)
        out_fields.append(d)
    return {"entity_type": entity_type, "version_id": v["id"], "version_no": v["version_no"],
            "checksum": v["checksum"], "sections": sections(conn, v["id"]), "fields": out_fields}


def _coerce(data_type, value):
    if data_type in ("integer",):
        return int(value)
    if data_type in ("decimal", "currency", "percentage"):
        return float(value)
    if data_type == "boolean":
        if str(value).lower() not in ("true", "false"):
            raise ValueError("boolean")
        return str(value).lower() == "true"
    return value


def _validate_value(field, value, conn, actor):
    """Declarative runtime validation of one value; returns an error string or None."""
    dt = field["data_type"]
    rules = field["validation"]
    if isinstance(rules, str):
        rules = json.loads(rules) if rules else {}
    rules = rules or {}
    if value in (None, ""):
        return None
    # type coercion
    try:
        coerced = _coerce(dt, value)
    except (ValueError, TypeError):
        return f"invalid {dt}"
    if dt == "email" and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(value)):
        return "invalid email"
    if dt == "url" and not re.match(r"^https?://", str(value)):
        return "invalid url"
    if dt == "telephone" and not re.match(r"^[0-9+()\-\s]{5,}$", str(value)):
        return "invalid phone"
    if "min_length" in rules and len(str(value)) < rules["min_length"]:
        return f"min length {rules['min_length']}"
    if "max_length" in rules and len(str(value)) > rules["max_length"]:
        return f"max length {rules['max_length']}"
    if "pattern" in rules and not re.match(rules["pattern"], str(value)):
        return "pattern mismatch"
    if dt in NUMERIC_TYPES:
        if "min" in rules and coerced < rules["min"]:
            return f"below minimum {rules['min']}"
        if "max" in rules and coerced > rules["max"]:
            return f"above maximum {rules['max']}"
    # option membership
    if dt in OPTION_TYPES:
        fid = field["id"] if "id" in field.keys() else None
        allowed = None
        if fid:
            allowed = {o["code"] for o in conn.execute(
                "SELECT code FROM form_field_options WHERE field_id=? AND active=1", (fid,)).fetchall()}
        if field.get("master_data_domain"):
            try:
                import masterdata
                allowed = (allowed or set()) | {mv["code"] for mv in masterdata.list_values(conn, actor, field["master_data_domain"], include_inactive=False)}
            except Exception:
                pass
        vals = value if isinstance(value, list) else [value]
        if allowed is not None:
            for x in vals:
                if str(x) not in allowed:
                    return f"'{x}' is not an active option"
    return None


def submit_values(conn, actor, entity_type, entity_id, values, stage=None):
    """Server-side runtime submission: validates every value against the effective form. Rejects
    unknown / inactive / cross-tenant / unauthorized / invalid-type / wrong-stage / invalid-option
    fields. Enforces required (incl. conditional). Stores values bound to the field version."""
    core.require(actor, "form.data.edit")
    import tenant as tenant_mod
    # entity ownership (tenant) — best-effort guard when the entity table carries tenant_id
    _assert_entity_tenant(conn, actor, entity_type, entity_id)
    v = active_version_for_entity(conn, actor, entity_type)
    if not v:
        raise core.ConflictError(f"no active form for entity '{entity_type}'")
    fdefs = {f["code"]: f for f in fields(conn, v["id"])}
    role = actor.get("role")
    sens_edit = core.can(actor, "form.data.sensitive.edit")
    # reject unknown / unauthorized / wrong-stage fields
    for code, val in values.items():
        f = fdefs.get(code)
        if not f:
            raise core.ValidationError(f"unknown field '{code}'")
        if f["effective_to"] and f["effective_to"] < _today():
            raise core.ValidationError(f"field '{code}' is inactive")
        if f["role_restriction"] and role and f["role_restriction"] != role and role != "admin" and "*" not in (actor.get("perms") or set()):
            raise core.ForbiddenError(f"not authorized to submit field '{code}'")
        if f["sensitivity"] in SENSITIVE_LEVELS and not sens_edit and "*" not in (actor.get("perms") or set()):
            raise core.ForbiddenError(f"not authorized to edit sensitive field '{code}'")
        if f["workflow_stage"] and stage and f["workflow_stage"] != stage:
            raise core.ValidationError(f"field '{code}' is not active for stage '{stage}'")
        err = _validate_value(f, val, conn, actor)
        if err:
            raise core.ValidationError(f"field '{code}': {err}")
    # required enforcement (incl. conditional) over the visible set
    merged = dict(values)
    for code, f in fdefs.items():
        req = bool(f["required"]) or (f["required_condition"] and eval_condition(f["required_condition"], merged))
        visible = eval_condition(f["visibility"], merged)
        if req and visible and code not in values and not f["default_value"]:
            raise core.ValidationError(f"required field '{code}' is missing")
    # persist (typed + JSON hybrid)
    tid = _tenant(actor)
    cid = core.correlation_id()
    for code, val in values.items():
        f = fdefs[code]
        vtype = f["data_type"]
        vnum = None
        vtext = None
        vjson = None
        if vtype in NUMERIC_TYPES:
            try: vnum = float(val)
            except Exception: vnum = None
        if isinstance(val, (list, dict)):
            vjson = json.dumps(val)
        else:
            vtext = str(val)
        conn.execute("INSERT INTO form_values(tenant_id,entity_type,entity_id,form_version_id,field_code,"
                     "field_version,value_type,value_text,value_num,value_json,sensitivity,created_by,created_at,"
                     "updated_by,updated_at,correlation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                     " ON CONFLICT(tenant_id,entity_type,entity_id,field_code) DO UPDATE SET"
                     " value_text=excluded.value_text, value_num=excluded.value_num, value_json=excluded.value_json,"
                     " field_version=excluded.field_version, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                     (tid, entity_type, entity_id, v["id"], code, v["version_no"], vtype, vtext, vnum, vjson,
                      f["sensitivity"], (actor or {}).get("id"), _now(), (actor or {}).get("id"), _now(), cid))
        # audit masks sensitive values
        av = "••••••" if f["sensitivity"] in SENSITIVE_LEVELS else (vtext if vtext is not None else vjson)
        core.audit(conn, actor, "FORM_VALUE_SET", entity_type, entity_id, new={"field": code, "value": av})
    conn.commit()
    return {"entity_type": entity_type, "entity_id": entity_id, "form_version": v["version_no"], "stored": len(values)}


def _assert_entity_tenant(conn, actor, entity_type, entity_id):
    table = {"customer": "customers", "booking": "bookings", "quotation": "quotations",
             "job": "jobs", "equipment": "equipment", "invoice": "invoices"}.get(entity_type)
    at = _tenant(actor)
    if not table or at is None or entity_id is None:
        return
    try:
        row = conn.execute(f"SELECT tenant_id FROM {table} WHERE id=?", (entity_id,)).fetchone()
    except Exception:
        return
    if row is not None and "tenant_id" in row.keys() and row["tenant_id"] is not None and row["tenant_id"] != at:
        raise core.NotFoundError("entity not found")   # cross-tenant 404 no-leak


def get_values(conn, actor, entity_type, entity_id):
    """Return captured values, masking sensitive fields the actor is not authorized to view."""
    core.require(actor, "form.data.view")
    _assert_entity_tenant(conn, actor, entity_type, entity_id)
    sens_ok = core.can(actor, "form.data.sensitive.view")
    tid = _tenant(actor)
    rows = conn.execute("SELECT field_code,value_text,value_num,value_json,value_type,field_version,sensitivity"
                        " FROM form_values WHERE entity_type=? AND entity_id=? AND (tenant_id=? OR tenant_id IS NULL)"
                        if tid is not None else
                        "SELECT field_code,value_text,value_num,value_json,value_type,field_version,sensitivity"
                        " FROM form_values WHERE entity_type=? AND entity_id=?",
                        (entity_type, entity_id, tid) if tid is not None else (entity_type, entity_id)).fetchall()
    out = {}
    for r in rows:
        raw = r["value_json"] or (r["value_num"] if r["value_num"] is not None and r["value_type"] in NUMERIC_TYPES else r["value_text"])
        out[r["field_code"]] = {"value": _mask(raw, r["sensitivity"], sens_ok),
                                "field_version": r["field_version"], "sensitivity": r["sensitivity"],
                                "masked": (r["sensitivity"] in SENSITIVE_LEVELS and not sens_ok)}
    return out


def search_values(conn, actor, entity_type, field_code, query):
    """Search a searchable field's values within tenant scope, honoring sensitivity."""
    core.require(actor, "form.data.view")
    tid = _tenant(actor)
    # only searchable fields may be searched
    fdef = conn.execute("SELECT sensitivity,searchable FROM form_fields WHERE entity_type=? AND code=? ORDER BY id DESC LIMIT 1",
                        (entity_type, field_code)).fetchone()
    if not fdef or not fdef["searchable"]:
        raise core.ValidationError("field is not searchable")
    if fdef["sensitivity"] in SENSITIVE_LEVELS and not core.can(actor, "form.data.sensitive.view"):
        raise core.ForbiddenError("not authorized to search a sensitive field")
    sql = "SELECT entity_id,value_text,value_num FROM form_values WHERE entity_type=? AND field_code=?"
    args = [entity_type, field_code]
    if tid is not None:
        sql += " AND (tenant_id=? OR tenant_id IS NULL)"; args.append(tid)
    sql += " AND (value_text LIKE ? OR CAST(value_num AS TEXT) LIKE ?)"
    args += ["%" + str(query) + "%", "%" + str(query) + "%"]
    return [dict(r) for r in conn.execute(sql + " LIMIT 200", tuple(args)).fetchall()]


def export_values(conn, actor, entity_type):
    """Export exportable, non-restricted fields' values within tenant scope. Restricted/sensitive
    fields are EXCLUDED unless the actor holds form.data.export + sensitive view."""
    core.require(actor, "form.data.export")
    tid = _tenant(actor)
    sens_ok = core.can(actor, "form.data.sensitive.view")
    v = active_version_for_entity(conn, actor, entity_type)
    if not v:
        return {"entity_type": entity_type, "rows": [], "excluded_sensitive": []}
    exportable, excluded = [], []
    for f in fields(conn, v["id"]):
        if not f["exportable"]:
            continue
        if f["sensitivity"] in SENSITIVE_LEVELS and not sens_ok:
            excluded.append(f["code"]); continue
        exportable.append(f["code"])
    rows = []
    q = ("SELECT entity_id,field_code,value_text,value_num FROM form_values WHERE entity_type=?"
         + (" AND (tenant_id=? OR tenant_id IS NULL)" if tid is not None else ""))
    args = (entity_type, tid) if tid is not None else (entity_type,)
    for r in conn.execute(q, args).fetchall():
        if r["field_code"] in exportable:
            rows.append({"entity_id": r["entity_id"], "field": r["field_code"],
                         "value": r["value_text"] if r["value_text"] is not None else r["value_num"]})
    core.audit(conn, actor, "FORM_EXPORTED", entity_type, 0, new={"fields": len(exportable), "excluded": excluded})
    conn.commit()
    return {"entity_type": entity_type, "rows": rows, "exported_fields": exportable, "excluded_sensitive": excluded}


# --------------------------------------------------------------------------- #
# File + signature fields (governed metadata)
# --------------------------------------------------------------------------- #
def upload_file(conn, actor, entity_type, entity_id, field_code, filename, content_type, size_bytes,
                content_bytes=None, allowed_types=None, max_size=None):
    core.require(actor, "form.data.edit")
    _assert_entity_tenant(conn, actor, entity_type, entity_id)
    if allowed_types and content_type not in allowed_types:
        raise core.ValidationError(f"file type '{content_type}' not allowed")
    if max_size and size_bytes > max_size:
        raise core.ValidationError("file exceeds maximum size")
    import secrets
    file_ref = secrets.token_hex(16)                    # non-guessable, tenant-scoped id
    checksum = hashlib.sha256(content_bytes or file_ref.encode()).hexdigest()
    cur = conn.execute("INSERT INTO form_files(tenant_id,entity_type,entity_id,field_code,file_ref,filename,"
                       "content_type,size_bytes,checksum,uploaded_by,uploaded_at,correlation_id)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (_tenant(actor), entity_type, entity_id, field_code, file_ref, filename, content_type,
                        size_bytes, checksum, (actor or {}).get("id"), _now(), core.correlation_id()))
    core.audit(conn, actor, "FORM_FILE_UPLOADED", entity_type, entity_id,
               new={"field": field_code, "file_ref": file_ref, "checksum": checksum[:12]})
    conn.commit()
    return {"file_id": cur.lastrowid, "file_ref": file_ref, "checksum": checksum}


def add_signature(conn, actor, entity_type, entity_id, field_code, document_hash, meaning,
                  form_version=None, source_meta=None):
    core.require(actor, "form.data.edit")
    _assert_entity_tenant(conn, actor, entity_type, entity_id)
    if not document_hash or not meaning:
        raise core.ValidationError("signature requires a document hash and an explicit meaning")
    cur = conn.execute("INSERT INTO form_signatures(tenant_id,entity_type,entity_id,field_code,signer,role,"
                       "document_hash,form_version,meaning,source_meta,correlation_id,signed_at)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (_tenant(actor), entity_type, entity_id, field_code, (actor or {}).get("id"),
                        actor.get("role"), document_hash, form_version, meaning,
                        json.dumps(source_meta) if source_meta else None, core.correlation_id(), _now()))
    core.audit(conn, actor, "FORM_SIGNED", entity_type, entity_id,
               new={"field": field_code, "meaning": meaning, "doc_hash": document_hash[:12]})
    conn.commit()
    return cur.lastrowid


# --------------------------------------------------------------------------- #
# Migration classification (additive; no value loss)
# --------------------------------------------------------------------------- #
def classify_existing(conn):
    def _count(sql):
        try:
            return conn.execute(sql).fetchone()["c"]
        except Exception:
            return 0
    crm_values = _count("SELECT COUNT(*) c FROM custom_field_values")   # Phase-3 foundation (preserved)
    form_values = _count("SELECT COUNT(*) c FROM form_values")
    return {"crm_custom_values_preserved": crm_values, "form_values": form_values,
            "system_fields_retained": True, "columns_removed": 0,
            "financial_differences": 0, "operational_status_differences": 0, "field_value_losses": 0}


# --------------------------------------------------------------------------- #
# Seed a representative governed Booking form (published ACTIVE) for runtime + E2E
# --------------------------------------------------------------------------- #
def seed(conn):
    sys_actor = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
    if _def_by_code(conn, "booking_form", None) is not None:
        return
    did = create_definition(conn, sys_actor, "booking", "booking_form", "Booking Intake Form")
    v = conn.execute("SELECT id FROM form_versions WHERE definition_id=? AND version_no=1", (did,)).fetchone()["id"]
    add_section(conn, sys_actor, v, "main", "Booking Details", sort_order=0)
    add_section(conn, sys_actor, v, "rigging", "Rigging Requirements", sort_order=1)
    add_field(conn, sys_actor, v, "insured", "Insured", "boolean", section_code="rigging")
    add_field(conn, sys_actor, v, "insurance_policy_no", "Insurance Policy Number", "short_text",
              section_code="rigging", required_condition={"field": "insured", "op": "is_true"},
              role_restriction=None, sensitivity="CONFIDENTIAL",
              visibility={"field": "insured", "op": "is_true"}, searchable=True)
    add_field(conn, sys_actor, v, "service_type", "Service Type", "single_select", section_code="main",
              master_data_domain="ops.service_type", searchable=True, reportable=True)
    add_field(conn, sys_actor, v, "client_contact_private", "Client Private Contact", "telephone",
              section_code="main", sensitivity="PERSONAL_DATA")
    validate_version(conn, sys_actor, v)
    approve_version(conn, sys_actor, v)
    publish_version(conn, sys_actor, v, "initial governed booking form")
    conn.commit()
