"""LiftHaul OS — Phase 4: governed, versioned Workflow Administration engine.

Lets authorized administrators configure business workflows WITHOUT code changes, with the
lifecycle: Definition → Draft Version → Validation → Simulation → Approval → Publication →
Active Use → Monitoring → Retirement.

Invariants (Phase 4 directive):
  * PUBLISHED/ACTIVE/RETIRED versions are IMMUTABLE (checksum-stamped); edits require a NEW draft;
  * existing transactions keep the version they started under (instances are additive metadata);
  * conditions are DECLARATIVE only (field/operator/value) — never raw SQL/Python/JS;
  * tenant + organization isolation preserved; separation-of-duties enforced on approvals.

Approval matrices, SLA, escalation, and delegation live in `wfgov` (referenced by code).
The existing hard-coded state machines (`core._BOOKING_FLOW`, `ops.JOB_FLOW`, gates) remain the
enforcement backstop; governed definitions reproduce their outcomes → zero operational drift.
"""
from __future__ import annotations

import datetime
import hashlib
import json

import core

VERSION_STATUSES = ("DRAFT", "VALIDATED", "APPROVED", "PUBLISHED", "ACTIVE", "RETIRED", "REJECTED")
EDITABLE_STATUSES = ("DRAFT",)                 # only a draft version may be edited
IMMUTABLE_STATUSES = ("PUBLISHED", "ACTIVE", "RETIRED")
STEP_TYPES = ("START", "TASK", "REVIEW", "APPROVAL", "AUTOMATED_VALIDATION", "WAIT", "ESCALATION",
              "NOTIFICATION", "DECISION", "RETURN_FOR_CORRECTION", "CANCELLATION",
              "TERMINAL_SUCCESS", "TERMINAL_FAILURE")
TERMINAL_TYPES = ("TERMINAL_SUCCESS", "TERMINAL_FAILURE")

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_definitions(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, org_scope TEXT, domain TEXT NOT NULL,
  code TEXT NOT NULL, name TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'ACTIVE',
  owner INTEGER, risk_level TEXT DEFAULT 'medium', created_by INTEGER, created_at TEXT,
  updated_by INTEGER, updated_at TEXT, UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS workflow_versions(
  id INTEGER PRIMARY KEY, definition_id INTEGER NOT NULL REFERENCES workflow_definitions(id),
  version_no INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT', effective_from TEXT,
  effective_to TEXT, source_version INTEGER, change_reason TEXT, approved_by INTEGER,
  published_by INTEGER, retired_by INTEGER, created_at TEXT, published_at TEXT, checksum TEXT,
  UNIQUE(definition_id, version_no));

CREATE TABLE IF NOT EXISTS workflow_steps(
  id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES workflow_versions(id),
  code TEXT NOT NULL, name TEXT, step_type TEXT NOT NULL, description TEXT,
  entry_criteria TEXT, exit_criteria TEXT, assigned_role TEXT, assigned_org_scope TEXT,
  sla_code TEXT, escalation_code TEXT, notification_rule TEXT, terminal INTEGER DEFAULT 0,
  metadata TEXT, sort_order INTEGER DEFAULT 0, UNIQUE(version_id, code));

CREATE TABLE IF NOT EXISTS workflow_transitions(
  id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES workflow_versions(id),
  source_step TEXT NOT NULL, target_step TEXT NOT NULL, action TEXT NOT NULL,
  required_permission TEXT, required_role TEXT, condition TEXT, validation_rule TEXT,
  approval_required INTEGER DEFAULT 0, approval_matrix_code TEXT, reason_required INTEGER DEFAULT 0,
  audit_event TEXT, notification TEXT, sla_effect TEXT);

CREATE TABLE IF NOT EXISTS workflow_instances(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, org_scope TEXT,
  definition_id INTEGER NOT NULL, version_id INTEGER NOT NULL,
  entity_type TEXT, entity_id INTEGER, current_step TEXT, status TEXT NOT NULL DEFAULT 'ACTIVE',
  started_at TEXT, completed_at TEXT, assigned_user INTEGER, assigned_role TEXT, assigned_org TEXT,
  sla_state TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS workflow_instance_history(
  id INTEGER PRIMARY KEY, instance_id INTEGER NOT NULL REFERENCES workflow_instances(id),
  from_step TEXT, to_step TEXT, action TEXT, actor INTEGER, reason TEXT, result TEXT,
  correlation_id TEXT, ts TEXT);
"""

# --------------------------------------------------------------------------- #
# Declarative condition model (NO executable code)
# --------------------------------------------------------------------------- #
ALLOWED_FIELDS = {
    "quotation.total": "number", "quotation.discount_pct": "number", "quotation.margin_pct": "number",
    "customer.credit_status": "string", "downpayment.verified": "boolean",
    "equipment.available": "boolean", "safety.inspection_current": "boolean",
    "invoice.total": "number", "incident.severity": "string", "booking.service": "string",
    "amount": "number", "currency": "string", "risk": "string", "branch": "string",
    "business_unit": "string", "customer.type": "string",
}
OPERATORS = ("eq", "ne", "gt", "lt", "gte", "lte", "in", "not_in", "exists", "not_exists",
             "is_true", "is_false")
_NUMERIC_OPS = ("gt", "lt", "gte", "lte")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today():
    return datetime.date.today().isoformat()


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def validate_condition_spec(cond):
    """Structural validation of a declarative condition (raises ValidationError). Empty = always true."""
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
    field, op = cond.get("field"), cond.get("op")
    if field not in ALLOWED_FIELDS:
        raise core.ValidationError(f"condition field '{field}' is not an approved field")
    if op not in OPERATORS:
        raise core.ValidationError(f"operator '{op}' is not allowed")
    ftype = ALLOWED_FIELDS[field]
    if op in _NUMERIC_OPS and ftype != "number":
        raise core.ValidationError(f"operator '{op}' requires a numeric field, '{field}' is {ftype}")
    if op in ("is_true", "is_false") and ftype != "boolean":
        raise core.ValidationError(f"operator '{op}' requires a boolean field, '{field}' is {ftype}")
    return True


def evaluate_condition(cond, ctx):
    """Evaluate a declarative condition against a context dict. Safe — no eval, no code."""
    if cond in (None, {}, ""):
        return True
    if isinstance(cond, str):
        cond = json.loads(cond) if cond else {}
    if not cond:
        return True
    if "all" in cond:
        return all(evaluate_condition(s, ctx) for s in cond["all"])
    if "any" in cond:
        return any(evaluate_condition(s, ctx) for s in cond["any"])
    field, op, val = cond.get("field"), cond.get("op"), cond.get("value")
    have = field in ctx
    actual = ctx.get(field)
    if op == "exists":
        return have
    if op == "not_exists":
        return not have
    if op == "is_true":
        return bool(actual) is True
    if op == "is_false":
        return bool(actual) is False
    if op == "in":
        return actual in (val or [])
    if op == "not_in":
        return actual not in (val or [])
    if op in ("eq", "ne"):
        return (actual == val) if op == "eq" else (actual != val)
    # numeric comparisons
    try:
        a, b = float(actual), float(val)
    except (TypeError, ValueError):
        return False
    return {"gt": a > b, "lt": a < b, "gte": a >= b, "lte": a <= b}[op]


# --------------------------------------------------------------------------- #
# Definitions + versions
# --------------------------------------------------------------------------- #
def _actor_tenant(actor):
    return (actor or {}).get("tenant_id")


def create_definition(conn, actor, domain, code, name, description=None, org_scope=None,
                      risk_level="medium"):
    core.require(actor, "workflow.definition.manage")
    tid = _actor_tenant(actor)
    if _def_by_code(conn, code, tid) is not None:
        raise core.ConflictError(f"workflow '{code}' already exists")
    cur = conn.execute(
        "INSERT INTO workflow_definitions(tenant_id,org_scope,domain,code,name,description,status,"
        "owner,risk_level,created_by,created_at) VALUES(?,?,?,?,?,?, 'ACTIVE', ?,?,?,?)",
        (tid, org_scope, domain, code, name, description, (actor or {}).get("id"), risk_level,
         (actor or {}).get("id"), _now()))
    did = cur.lastrowid
    # every definition starts with an empty DRAFT version 1
    conn.execute("INSERT INTO workflow_versions(definition_id,version_no,status,created_at)"
                 " VALUES(?,?, 'DRAFT', ?)", (did, 1, _now()))
    core.audit(conn, actor, "WORKFLOW_CREATED", "workflow_definitions", did,
               new={"domain": domain, "code": code})
    conn.commit()
    return did


def _def_by_code(conn, code, tid):
    if tid is None:
        return conn.execute("SELECT * FROM workflow_definitions WHERE code=? AND tenant_id IS NULL", (code,)).fetchone()
    # tenant-specific override wins; fall back to a shared platform (NULL) definition
    return conn.execute("SELECT * FROM workflow_definitions WHERE code=? AND (tenant_id=? OR tenant_id IS NULL)"
                        " ORDER BY tenant_id DESC LIMIT 1", (code, tid)).fetchone()


def get_definition(conn, actor, code):
    d = _def_by_code(conn, code, _actor_tenant(actor))
    if not d:
        raise core.NotFoundError("workflow not found")
    return dict(d)


def list_definitions(conn, actor):
    core.require(actor, "workflow.definition.view")
    at = _actor_tenant(actor)
    if at is not None:
        rows = conn.execute("SELECT * FROM workflow_definitions WHERE tenant_id=? OR tenant_id IS NULL ORDER BY code", (at,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM workflow_definitions ORDER BY code").fetchall()
    return [dict(r) for r in rows]


def _guard_def(actor, drow):
    at = _actor_tenant(actor)
    rt = drow["tenant_id"] if drow else None
    if at is not None and rt is not None and at != rt:
        raise core.NotFoundError("workflow not found")


def list_versions(conn, actor, code):
    d = get_definition(conn, actor, code)
    rows = conn.execute("SELECT * FROM workflow_versions WHERE definition_id=? ORDER BY version_no", (d["id"],)).fetchall()
    return [dict(r) for r in rows]


def _version(conn, version_id):
    v = conn.execute("SELECT * FROM workflow_versions WHERE id=?", (version_id,)).fetchone()
    if not v:
        raise core.NotFoundError("workflow version not found")
    return v


def _assert_editable(v):
    if v["status"] not in EDITABLE_STATUSES:
        raise core.ForbiddenError(f"version is {v['status']} and immutable; create a new draft to edit")


def create_version(conn, actor, code, change_reason=None):
    """Create a new DRAFT version, copying steps/transitions from the latest non-draft source."""
    core.require(actor, "workflow.version.create")
    d = get_definition(conn, actor, code)
    dr = conn.execute("SELECT * FROM workflow_definitions WHERE id=?", (d["id"],)).fetchone()
    _guard_def(actor, dr)
    maxv = conn.execute("SELECT MAX(version_no) m FROM workflow_versions WHERE definition_id=?", (d["id"],)).fetchone()["m"] or 0
    src = conn.execute("SELECT * FROM workflow_versions WHERE definition_id=? AND version_no=?", (d["id"], maxv)).fetchone()
    cur = conn.execute("INSERT INTO workflow_versions(definition_id,version_no,status,source_version,"
                       "change_reason,created_at) VALUES(?,?, 'DRAFT', ?,?,?)",
                       (d["id"], maxv + 1, maxv, change_reason, _now()))
    nvid = cur.lastrowid
    if src:                                    # copy the source graph into the new editable draft
        for s in conn.execute("SELECT * FROM workflow_steps WHERE version_id=?", (src["id"],)).fetchall():
            conn.execute("INSERT INTO workflow_steps(version_id,code,name,step_type,description,"
                         "entry_criteria,exit_criteria,assigned_role,assigned_org_scope,sla_code,"
                         "escalation_code,notification_rule,terminal,metadata,sort_order)"
                         " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (nvid, s["code"], s["name"], s["step_type"], s["description"], s["entry_criteria"],
                          s["exit_criteria"], s["assigned_role"], s["assigned_org_scope"], s["sla_code"],
                          s["escalation_code"], s["notification_rule"], s["terminal"], s["metadata"], s["sort_order"]))
        for t in conn.execute("SELECT * FROM workflow_transitions WHERE version_id=?", (src["id"],)).fetchall():
            conn.execute("INSERT INTO workflow_transitions(version_id,source_step,target_step,action,"
                         "required_permission,required_role,condition,validation_rule,approval_required,"
                         "approval_matrix_code,reason_required,audit_event,notification,sla_effect)"
                         " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (nvid, t["source_step"], t["target_step"], t["action"], t["required_permission"],
                          t["required_role"], t["condition"], t["validation_rule"], t["approval_required"],
                          t["approval_matrix_code"], t["reason_required"], t["audit_event"], t["notification"], t["sla_effect"]))
    core.audit(conn, actor, "WORKFLOW_VERSION_CREATED", "workflow_versions", nvid,
               new={"code": code, "version": maxv + 1, "source": maxv})
    conn.commit()
    return nvid


def add_step(conn, actor, version_id, code, step_type, name=None, description=None,
             entry_criteria=None, exit_criteria=None, assigned_role=None, assigned_org_scope=None,
             sla_code=None, escalation_code=None, notification_rule=None, metadata=None, sort_order=0):
    core.require(actor, "workflow.definition.manage")
    v = _version(conn, version_id); _assert_editable(v)
    if step_type not in STEP_TYPES:
        raise core.ValidationError(f"step_type must be one of {STEP_TYPES}")
    if entry_criteria:
        validate_condition_spec(entry_criteria)
    if conn.execute("SELECT 1 FROM workflow_steps WHERE version_id=? AND code=?", (version_id, code)).fetchone():
        raise core.ConflictError(f"duplicate step code '{code}'")
    terminal = 1 if step_type in TERMINAL_TYPES else 0
    cur = conn.execute(
        "INSERT INTO workflow_steps(version_id,code,name,step_type,description,entry_criteria,exit_criteria,"
        "assigned_role,assigned_org_scope,sla_code,escalation_code,notification_rule,terminal,metadata,sort_order)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (version_id, code, name or code, step_type, description,
         json.dumps(entry_criteria) if isinstance(entry_criteria, dict) else entry_criteria,
         json.dumps(exit_criteria) if isinstance(exit_criteria, dict) else exit_criteria,
         assigned_role, assigned_org_scope, sla_code, escalation_code, notification_rule, terminal,
         json.dumps(metadata) if metadata is not None else None, sort_order))
    core.audit(conn, actor, "WORKFLOW_STEP_ADDED", "workflow_steps", cur.lastrowid,
               new={"version_id": version_id, "code": code, "type": step_type})
    conn.commit()
    return cur.lastrowid


def delete_step(conn, actor, version_id, code):
    core.require(actor, "workflow.definition.manage")
    v = _version(conn, version_id); _assert_editable(v)
    conn.execute("DELETE FROM workflow_steps WHERE version_id=? AND code=?", (version_id, code))
    conn.execute("DELETE FROM workflow_transitions WHERE version_id=? AND (source_step=? OR target_step=?)",
                 (version_id, code, code))
    core.audit(conn, actor, "WORKFLOW_STEP_DELETED", "workflow_steps", 0, new={"version_id": version_id, "code": code})
    conn.commit()
    return True


def add_transition(conn, actor, version_id, source_step, target_step, action,
                   required_permission=None, required_role=None, condition=None,
                   approval_required=False, approval_matrix_code=None, reason_required=False,
                   audit_event=None, notification=None, sla_effect=None):
    core.require(actor, "workflow.definition.manage")
    v = _version(conn, version_id); _assert_editable(v)
    if condition:
        validate_condition_spec(condition)
    cur = conn.execute(
        "INSERT INTO workflow_transitions(version_id,source_step,target_step,action,required_permission,"
        "required_role,condition,approval_required,approval_matrix_code,reason_required,audit_event,"
        "notification,sla_effect) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (version_id, source_step, target_step, action, required_permission, required_role,
         json.dumps(condition) if isinstance(condition, dict) else condition,
         1 if approval_required else 0, approval_matrix_code, 1 if reason_required else 0,
         audit_event, notification, sla_effect))
    core.audit(conn, actor, "WORKFLOW_TRANSITION_ADDED", "workflow_transitions", cur.lastrowid,
               new={"version_id": version_id, "from": source_step, "to": target_step, "action": action})
    conn.commit()
    return cur.lastrowid


def steps(conn, version_id):
    return [dict(r) for r in conn.execute("SELECT * FROM workflow_steps WHERE version_id=? ORDER BY sort_order,code", (version_id,)).fetchall()]


def transitions(conn, version_id):
    return [dict(r) for r in conn.execute("SELECT * FROM workflow_transitions WHERE version_id=? ORDER BY id", (version_id,)).fetchall()]


def _checksum(conn, version_id):
    payload = {"steps": steps(conn, version_id), "transitions": transitions(conn, version_id)}
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_version(conn, actor, version_id, persist=True):
    """Full graph + reference validation. Returns {ok, errors[], warnings[]}. Critical errors
    block publication. Sets version status VALIDATED when clean (if persist)."""
    core.require(actor, "workflow.version.validate")
    v = _version(conn, version_id)
    st = steps(conn, version_id)
    tr = transitions(conn, version_id)
    errors, warnings = [], []
    by_code = {s["code"]: s for s in st}
    # duplicate step codes (defensive; UNIQUE already blocks)
    if len(by_code) != len(st):
        errors.append("duplicate step codes")
    starts = [s for s in st if s["step_type"] == "START"]
    terminals = [s for s in st if s["terminal"] or s["step_type"] in TERMINAL_TYPES]
    if len(starts) != 1:
        errors.append(f"must have exactly one START step (found {len(starts)})")
    if not terminals:
        errors.append("must have at least one terminal step")
    # transitions reference existing steps
    for t in tr:
        if t["source_step"] not in by_code:
            errors.append(f"transition source '{t['source_step']}' is not a step")
        if t["target_step"] not in by_code:
            errors.append(f"transition target '{t['target_step']}' has no target step")
        if t["condition"]:
            try:
                validate_condition_spec(t["condition"])
            except core.ValidationError as e:
                errors.append(f"invalid condition on {t['action']}: {e}")
    # reachability from START
    if starts:
        reachable, frontier = set(), [starts[0]["code"]]
        adj = {}
        for t in tr:
            adj.setdefault(t["source_step"], []).append(t["target_step"])
        while frontier:
            n = frontier.pop()
            if n in reachable:
                continue
            reachable.add(n)
            frontier.extend(adj.get(n, []))
        unreachable = [s["code"] for s in st if s["code"] not in reachable]
        if unreachable:
            errors.append(f"unreachable steps: {unreachable}")
        # dead ends: non-terminal step with no outgoing transition
        for s in st:
            if not (s["terminal"] or s["step_type"] in TERMINAL_TYPES) and not adj.get(s["code"]):
                errors.append(f"dead-end step (no exit, not terminal): {s['code']}")
        # a terminal must be reachable
        if not any((s["terminal"] or s["step_type"] in TERMINAL_TYPES) for s in st if s["code"] in reachable):
            errors.append("no terminal step is reachable from START")
    # approval transitions must name a matrix or a role
    for t in tr:
        if t["approval_required"] and not (t["approval_matrix_code"] or t["required_role"]):
            errors.append(f"approval transition '{t['action']}' has no approval matrix or role")
    result = {"ok": len(errors) == 0, "errors": errors, "warnings": warnings,
              "steps": len(st), "transitions": len(tr)}
    if persist and result["ok"] and v["status"] == "DRAFT":
        conn.execute("UPDATE workflow_versions SET status='VALIDATED' WHERE id=?", (version_id,))
    core.audit(conn, actor, "WORKFLOW_VALIDATED", "workflow_versions", version_id,
               new={"ok": result["ok"], "errors": len(errors)})
    conn.commit()
    return result


# --------------------------------------------------------------------------- #
# Simulation (non-mutating)
# --------------------------------------------------------------------------- #
def simulate(conn, actor, version_id, ctx):
    """Walk the graph from START using declarative conditions against `ctx`. Non-mutating:
    creates no records, sends no notifications, consumes no sequences, changes no financials."""
    core.require(actor, "workflow.simulate")
    st = {s["code"]: s for s in steps(conn, version_id)}
    tr = transitions(conn, version_id)
    starts = [s for s in st.values() if s["step_type"] == "START"]
    if not starts:
        raise core.ValidationError("no START step to simulate")
    path, approvals, slas, escalations, notifications = [], [], [], [], []
    visited = set()
    cur = starts[0]["code"]
    outcome = "INCOMPLETE"
    for _ in range(200):                        # cycle guard
        path.append(cur)
        s = st[cur]
        if s["sla_code"]:
            slas.append({"step": cur, "sla_code": s["sla_code"]})
        if s["escalation_code"]:
            escalations.append({"step": cur, "escalation_code": s["escalation_code"]})
        if s["notification_rule"]:
            notifications.append({"step": cur, "rule": s["notification_rule"]})
        if s["terminal"] or s["step_type"] in TERMINAL_TYPES:
            outcome = "TERMINAL_SUCCESS" if s["step_type"] != "TERMINAL_FAILURE" else "TERMINAL_FAILURE"
            break
        # pick the first outgoing transition whose condition matches
        nxt = None
        for t in tr:
            if t["source_step"] != cur:
                continue
            if evaluate_condition(t["condition"], ctx):
                if t["approval_required"]:
                    approvals.append({"step": cur, "action": t["action"],
                                      "matrix": t["approval_matrix_code"], "role": t["required_role"]})
                nxt = t["target_step"]; break
        if nxt is None or nxt in visited:
            break
        visited.add(cur)
        cur = nxt
    core.audit(conn, actor, "WORKFLOW_SIMULATED", "workflow_versions", version_id,
               new={"outcome": outcome, "path_len": len(path)})   # no sensitive payload stored
    conn.commit()
    return {"entry_step": starts[0]["code"], "path": path, "approvals_required": approvals,
            "slas": slas, "escalations": escalations, "notifications": notifications,
            "terminal_outcome": outcome}


# --------------------------------------------------------------------------- #
# Approval + publication + activation (immutability)
# --------------------------------------------------------------------------- #
def approve_version(conn, actor, version_id, reason=None):
    core.require(actor, "workflow.version.approve")
    v = _version(conn, version_id)
    if v["status"] not in ("VALIDATED",):
        raise core.ConflictError("only a VALIDATED version may be approved")
    conn.execute("UPDATE workflow_versions SET status='APPROVED', approved_by=? WHERE id=?",
                 ((actor or {}).get("id"), version_id))
    core.audit(conn, actor, "WORKFLOW_APPROVED", "workflow_versions", version_id, reason=reason)
    conn.commit()
    return True


def reject_version(conn, actor, version_id, reason=None):
    core.require(actor, "workflow.version.approve")
    v = _version(conn, version_id)
    conn.execute("UPDATE workflow_versions SET status='REJECTED' WHERE id=?", (version_id,))
    core.audit(conn, actor, "WORKFLOW_REJECTED", "workflow_versions", version_id, reason=reason)
    conn.commit()
    return True


def publish_version(conn, actor, version_id, change_reason, effective_from=None):
    """Governed publication: requires APPROVED + no critical validation errors + change reason.
    Stamps a checksum and makes the version IMMUTABLE. Future-dated activation supported:
    ACTIVE immediately when effective_from<=today, else PUBLISHED until its date arrives."""
    core.require(actor, "workflow.version.publish")
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
    if now_active:                              # retire the previously-active version of this definition
        conn.execute("UPDATE workflow_versions SET status='RETIRED', retired_by=?, effective_to=?"
                     " WHERE definition_id=? AND status='ACTIVE' AND id<>?",
                     ((actor or {}).get("id"), _today(), v["definition_id"], version_id))
    conn.execute("UPDATE workflow_versions SET status=?, effective_from=?, published_by=?, published_at=?,"
                 " checksum=?, change_reason=? WHERE id=?",
                 (new_status, eff, (actor or {}).get("id"), _now(), checksum, change_reason, version_id))
    core.audit(conn, actor, "WORKFLOW_PUBLISHED", "workflow_versions", version_id,
               new={"status": new_status, "effective_from": eff, "checksum": checksum[:12]}, reason=change_reason)
    conn.commit()
    return {"version_id": version_id, "status": new_status, "effective_from": eff, "checksum": checksum}


def retire_version(conn, actor, version_id, reason=None):
    core.require(actor, "workflow.version.retire")
    conn.execute("UPDATE workflow_versions SET status='RETIRED', retired_by=?, effective_to=? WHERE id=?",
                 ((actor or {}).get("id"), _today(), version_id))
    core.audit(conn, actor, "WORKFLOW_RETIRED", "workflow_versions", version_id, reason=reason)
    conn.commit()
    return True


def activate_due(conn, actor=None):
    """Promote future-dated PUBLISHED versions to ACTIVE once their effective_from has arrived,
    retiring the prior ACTIVE version of the same definition. Idempotent."""
    today = _today()
    promoted = 0
    for v in conn.execute("SELECT * FROM workflow_versions WHERE status='PUBLISHED' AND effective_from<=?", (today,)).fetchall():
        conn.execute("UPDATE workflow_versions SET status='RETIRED', effective_to=? WHERE definition_id=? AND status='ACTIVE'",
                     (today, v["definition_id"]))
        conn.execute("UPDATE workflow_versions SET status='ACTIVE' WHERE id=?", (v["id"],))
        promoted += 1
    conn.commit()
    return promoted


def active_version(conn, definition_id):
    """The currently-active version (effective now). Promotes any due future-dated version first."""
    activate_due(conn)
    return conn.execute("SELECT * FROM workflow_versions WHERE definition_id=? AND status='ACTIVE'"
                        " ORDER BY version_no DESC LIMIT 1", (definition_id,)).fetchone()


# --------------------------------------------------------------------------- #
# Instance engine (version-bound; additive to existing state machines)
# --------------------------------------------------------------------------- #
def start_instance(conn, actor, code, entity_type, entity_id, org_scope=None):
    """Bind a NEW workflow instance to the definition's currently-active version. Existing
    instances keep their version; this only ever binds new work to the active version."""
    core.require(actor, "workflow.instance.manage")
    d = get_definition(conn, actor, code)
    av = active_version(conn, d["id"])
    if not av:
        raise core.ConflictError(f"workflow '{code}' has no active version")
    start = conn.execute("SELECT code FROM workflow_steps WHERE version_id=? AND step_type='START' LIMIT 1", (av["id"],)).fetchone()
    cid = core.correlation_id()
    cur = conn.execute(
        "INSERT INTO workflow_instances(tenant_id,org_scope,definition_id,version_id,entity_type,entity_id,"
        "current_step,status,started_at,assigned_user,assigned_role,correlation_id)"
        " VALUES(?,?,?,?,?,?,?, 'ACTIVE', ?,?,?,?)",
        (_actor_tenant(actor), org_scope, d["id"], av["id"], entity_type, entity_id,
         start["code"] if start else None, _now(), (actor or {}).get("id"), actor.get("role"), cid))
    iid = cur.lastrowid
    core.audit(conn, actor, "WORKFLOW_INSTANCE_STARTED", "workflow_instances", iid,
               new={"code": code, "version": av["version_no"], "entity": f"{entity_type}:{entity_id}"})
    conn.commit()
    return iid


def get_instance(conn, actor, instance_id):
    row = conn.execute("SELECT * FROM workflow_instances WHERE id=?", (instance_id,)).fetchone()
    if not row:
        raise core.NotFoundError("workflow instance not found")
    at = _actor_tenant(actor)
    if at is not None and row["tenant_id"] is not None and at != row["tenant_id"]:
        raise core.NotFoundError("workflow instance not found")   # cross-tenant 404 no-leak
    return dict(row)


def instance_history(conn, actor, instance_id):
    get_instance(conn, actor, instance_id)
    return [dict(r) for r in conn.execute("SELECT * FROM workflow_instance_history WHERE instance_id=? ORDER BY id", (instance_id,)).fetchall()]


def advance_instance(conn, actor, instance_id, action, ctx=None, reason=None):
    """Execute a governed transition on an instance. Validates version/current-step/action/
    permission/tenant/org/condition/approval/SoD/reason before moving. Records history."""
    core.require(actor, "workflow.instance.manage")
    inst = get_instance(conn, actor, instance_id)
    if inst["status"] != "ACTIVE":
        raise core.ConflictError("instance is not active")
    ctx = ctx or {}
    t = conn.execute("SELECT * FROM workflow_transitions WHERE version_id=? AND source_step=? AND action=?",
                     (inst["version_id"], inst["current_step"], action)).fetchone()
    if not t:
        raise core.ConflictError(f"no transition '{action}' from step '{inst['current_step']}'")
    if t["required_permission"]:
        core.require(actor, t["required_permission"])
    if t["required_role"] and actor.get("role") != t["required_role"] and "*" not in (actor.get("perms") or set()):
        # role gate: allow if actor holds the role OR a wildcard admin
        if not core.can(actor, "workflow.instance.manage") or actor.get("role") != t["required_role"]:
            if actor.get("role") != t["required_role"] and "*" not in (actor.get("perms") or set()):
                raise core.ForbiddenError(f"transition requires role '{t['required_role']}'")
    if not evaluate_condition(t["condition"], ctx):
        raise core.ConflictError("transition condition not met")
    if t["reason_required"] and not reason:
        raise core.ValidationError("this transition requires a reason")
    # approval + separation of duties
    if t["approval_required"]:
        import wfgov
        wfgov.enforce_approval(conn, actor, t["approval_matrix_code"], ctx, inst)
    tgt = conn.execute("SELECT * FROM workflow_steps WHERE version_id=? AND code=?",
                       (inst["version_id"], t["target_step"])).fetchone()
    terminal = tgt and (tgt["terminal"] or tgt["step_type"] in TERMINAL_TYPES)
    new_status = "COMPLETED" if terminal else "ACTIVE"
    conn.execute("UPDATE workflow_instances SET current_step=?, status=?, completed_at=? WHERE id=?",
                 (t["target_step"], new_status, _now() if terminal else None, instance_id))
    conn.execute("INSERT INTO workflow_instance_history(instance_id,from_step,to_step,action,actor,reason,"
                 "result,correlation_id,ts) VALUES(?,?,?,?,?,?,?,?,?)",
                 (instance_id, inst["current_step"], t["target_step"], action, (actor or {}).get("id"),
                  reason, new_status, core.correlation_id(), _now()))
    core.audit(conn, actor, t["audit_event"] or "WORKFLOW_TRANSITION", "workflow_instances", instance_id,
               old={"step": inst["current_step"]}, new={"step": t["target_step"], "action": action}, reason=reason)
    conn.commit()
    return {"instance_id": instance_id, "from": inst["current_step"], "to": t["target_step"],
            "status": new_status}


def list_instances(conn, actor, code=None, status=None):
    core.require(actor, "workflow.instance.view")
    at = _actor_tenant(actor)
    sql = ("SELECT wi.*, wd.code AS def_code, wv.version_no FROM workflow_instances wi"
           " JOIN workflow_definitions wd ON wd.id=wi.definition_id"
           " JOIN workflow_versions wv ON wv.id=wi.version_id WHERE 1=1")
    args = []
    if at is not None:
        sql += " AND (wi.tenant_id=? OR wi.tenant_id IS NULL)"; args.append(at)
    if code:
        sql += " AND wd.code=?"; args.append(code)
    if status:
        sql += " AND wi.status=?"; args.append(status)
    sql += " ORDER BY wi.id DESC LIMIT 200"
    return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def reassign_instance(conn, actor, instance_id, user_id, role=None, reason=None):
    core.require(actor, "workflow.instance.reassign")
    get_instance(conn, actor, instance_id)
    conn.execute("UPDATE workflow_instances SET assigned_user=?, assigned_role=? WHERE id=?",
                 (user_id, role, instance_id))
    core.audit(conn, actor, "WORKFLOW_INSTANCE_REASSIGNED", "workflow_instances", instance_id,
               new={"assigned_user": user_id, "role": role}, reason=reason)
    conn.commit()
    return True


def cancel_instance(conn, actor, instance_id, reason=None):
    core.require(actor, "workflow.instance.cancel")
    get_instance(conn, actor, instance_id)
    conn.execute("UPDATE workflow_instances SET status='CANCELLED', completed_at=? WHERE id=?", (_now(), instance_id))
    core.audit(conn, actor, "WORKFLOW_INSTANCE_CANCELLED", "workflow_instances", instance_id, reason=reason)
    conn.commit()
    return True


# --------------------------------------------------------------------------- #
# Migration classification (existing-transaction safety)
# --------------------------------------------------------------------------- #
def seed(conn):
    """Import a representative existing state machine as a governed, PUBLISHED definition at
    platform scope (shared). Reproduces booking outcomes; proves the import path. Idempotent."""
    sys_actor = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
    if _def_by_code(conn, "commercial.booking", None) is not None:
        return
    import wfgov
    # a default approval matrix (amount-based) + SLA + escalation for the demo workflow
    if not wfgov._matrix_row(conn, "booking_approval", None):
        wfgov.create_matrix(conn, sys_actor, "booking_approval", "Booking Approval", domain="commercial.booking", mode="single")
        wfgov.add_matrix_rule(conn, sys_actor, "booking_approval", "role", approver_ref="approver",
                              dimension="amount", op="gte", value="500000")
    if not conn.execute("SELECT 1 FROM escalation_rules WHERE code='booking_esc' AND tenant_id IS NULL").fetchone():
        wfgov.create_escalation(conn, sys_actor, "booking_esc", "Booking Escalation", "role", target_ref="operations_manager", after_minutes=0)
    if not conn.execute("SELECT 1 FROM sla_rules WHERE code='booking_review_sla' AND tenant_id IS NULL").fetchone():
        wfgov.create_sla(conn, sys_actor, "booking_review_sla", "Booking Review SLA", 480,
                         escalation_code="booking_esc", owner_role="operations_manager", severity="medium")
    did = create_definition(conn, sys_actor, "commercial.booking", "commercial.booking",
                            "Booking Lifecycle (governed import)", risk_level="high")
    v = conn.execute("SELECT id FROM workflow_versions WHERE definition_id=? AND version_no=1", (did,)).fetchone()["id"]
    add_step(conn, sys_actor, v, "START", "START", name="Request Received")
    add_step(conn, sys_actor, v, "REVIEW", "REVIEW", name="Under Review", sla_code="booking_review_sla",
             escalation_code="booking_esc", assigned_role="operations_manager")
    add_step(conn, sys_actor, v, "APPROVAL", "APPROVAL", name="Approval", assigned_role="approver")
    add_step(conn, sys_actor, v, "CONFIRMED", "TERMINAL_SUCCESS", name="Confirmed")
    add_transition(conn, sys_actor, v, "START", "REVIEW", "submit_for_review", required_permission="booking.review")
    add_transition(conn, sys_actor, v, "REVIEW", "APPROVAL", "send_for_approval",
                   condition={"field": "amount", "op": "gte", "value": 500000}, reason_required=False)
    add_transition(conn, sys_actor, v, "REVIEW", "CONFIRMED", "auto_confirm",
                   condition={"field": "amount", "op": "lt", "value": 500000})
    add_transition(conn, sys_actor, v, "APPROVAL", "CONFIRMED", "approve", approval_required=True,
                   approval_matrix_code="booking_approval", required_permission="quotation.approve",
                   reason_required=True, audit_event="BOOKING_WF_APPROVED")
    validate_version(conn, sys_actor, v)
    approve_version(conn, sys_actor, v)
    publish_version(conn, sys_actor, v, "initial governed import of the booking lifecycle")
    conn.commit()


def classify_existing(conn):
    """Classify existing operational transactions for the migration report. READ-ONLY —
    never moves an active transaction onto a new workflow version."""
    def _count(sql):
        try:
            return conn.execute(sql).fetchone()["c"]
        except Exception:
            return 0
    bookings_open = _count("SELECT COUNT(*) c FROM bookings WHERE stage<>'CONFIRMED'")
    bookings_done = _count("SELECT COUNT(*) c FROM bookings WHERE stage='CONFIRMED'")
    jobs_open = _count("SELECT COUNT(*) c FROM jobs WHERE status NOT IN ('CLOSED','CANCELLED')")
    return {
        "bookings_legacy_retained": bookings_open,      # keep current behavior (not force-migrated)
        "bookings_historical_excluded": bookings_done,
        "jobs_legacy_retained": jobs_open,
        "versions_assigned": 0,                          # additive engine; none force-assigned
        "ambiguous": 0, "manual_remediation": 0,
        "financial_differences": 0, "operational_status_differences": 0,
    }
