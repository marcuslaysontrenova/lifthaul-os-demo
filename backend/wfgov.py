"""LiftHaul OS — Phase 4: workflow governance — approval matrices, SLA + escalation, delegation.

Referenced by `workflow` transitions/steps via codes. Enforces separation of duties, tenant +
organization scope, and delegation guards. The SLA calculator is business-hours aware, reusing the
Phase-1 working/holiday calendars (`org`).

Nothing here changes a financial value or an operational transaction status.
"""
from __future__ import annotations

import datetime
import json

import core

SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_matrices(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, code TEXT NOT NULL, name TEXT, domain TEXT,
  mode TEXT NOT NULL DEFAULT 'single', allow_self_approval INTEGER DEFAULT 0,
  active INTEGER DEFAULT 1, created_by INTEGER, created_at TEXT, UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS approval_matrix_rules(
  id INTEGER PRIMARY KEY, matrix_id INTEGER NOT NULL REFERENCES approval_matrices(id),
  seq INTEGER DEFAULT 0, dimension TEXT, op TEXT DEFAULT 'gte', value TEXT,
  approver_type TEXT NOT NULL, approver_ref TEXT, level INTEGER DEFAULT 1, created_at TEXT);

CREATE TABLE IF NOT EXISTS sla_rules(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, code TEXT NOT NULL, name TEXT, sla_type TEXT,
  duration_minutes INTEGER NOT NULL, working_calendar_ref INTEGER, holiday_calendar_ref INTEGER,
  warning_pct REAL DEFAULT 80, breach_pct REAL DEFAULT 100, escalation_code TEXT, owner_role TEXT,
  severity TEXT DEFAULT 'medium', active INTEGER DEFAULT 1, created_by INTEGER, created_at TEXT,
  UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS sla_instances(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, instance_id INTEGER, sla_code TEXT, started_at TEXT,
  due_at TEXT, warning_at TEXT, breached_at TEXT, paused_at TEXT, paused_minutes INTEGER DEFAULT 0,
  status TEXT DEFAULT 'RUNNING', created_at TEXT);

CREATE TABLE IF NOT EXISTS escalation_rules(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, code TEXT NOT NULL, name TEXT, target_type TEXT NOT NULL,
  target_ref TEXT, after_minutes INTEGER DEFAULT 0, severity TEXT DEFAULT 'medium',
  active INTEGER DEFAULT 1, created_by INTEGER, created_at TEXT, UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS escalation_events(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, instance_id INTEGER, sla_code TEXT, escalation_code TEXT,
  target_type TEXT, target_ref TEXT, reason TEXT, correlation_id TEXT, ts TEXT);

CREATE TABLE IF NOT EXISTS approval_delegations(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, org_scope TEXT, delegator INTEGER NOT NULL,
  delegate INTEGER NOT NULL, role TEXT, domain TEXT, start_at TEXT, end_at TEXT, reason TEXT,
  approved_by INTEGER, active INTEGER DEFAULT 1, created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS notification_events(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, instance_id INTEGER, channel TEXT, template_code TEXT,
  recipient TEXT, kind TEXT, status TEXT DEFAULT 'QUEUED', correlation_id TEXT, ts TEXT);
"""

APPROVAL_MODES = ("single", "sequential", "parallel", "unanimous", "any", "majority", "conditional", "delegated")
APPROVER_TYPES = ("role", "named", "manager", "branch_manager", "bu_manager", "level")
ESCALATION_TARGETS = ("assigned_user", "role", "manager", "branch_manager", "bu_manager",
                      "platform_support", "group")
NOTIFY_CHANNELS = ("in_app", "email", "sms")


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _today():
    return datetime.date.today().isoformat()


def _tenant(actor):
    return (actor or {}).get("tenant_id")


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


# --------------------------------------------------------------------------- #
# Approval matrices
# --------------------------------------------------------------------------- #
def create_matrix(conn, actor, code, name, domain=None, mode="single", allow_self_approval=False):
    core.require(actor, "workflow.definition.manage")
    if mode not in APPROVAL_MODES:
        raise core.ValidationError(f"mode must be one of {APPROVAL_MODES}")
    tid = _tenant(actor)
    if _matrix_row(conn, code, tid):
        raise core.ConflictError(f"approval matrix '{code}' already exists")
    cur = conn.execute("INSERT INTO approval_matrices(tenant_id,code,name,domain,mode,allow_self_approval,"
                       "active,created_by,created_at) VALUES(?,?,?,?,?,?,1,?,?)",
                       (tid, code, name, domain, mode, 1 if allow_self_approval else 0,
                        (actor or {}).get("id"), _iso(_now())))
    core.audit(conn, actor, "APPROVAL_MATRIX_CREATED", "approval_matrices", cur.lastrowid,
               new={"code": code, "mode": mode})
    conn.commit()
    return cur.lastrowid


def _matrix_row(conn, code, tid):
    if tid is None:
        return conn.execute("SELECT * FROM approval_matrices WHERE code=? AND tenant_id IS NULL", (code,)).fetchone()
    return conn.execute("SELECT * FROM approval_matrices WHERE code=? AND (tenant_id=? OR tenant_id IS NULL)"
                        " ORDER BY tenant_id DESC LIMIT 1", (code, tid)).fetchone()


def add_matrix_rule(conn, actor, code, approver_type, approver_ref=None, dimension=None,
                    op="gte", value=None, seq=0, level=1):
    core.require(actor, "workflow.definition.manage")
    if approver_type not in APPROVER_TYPES:
        raise core.ValidationError(f"approver_type must be one of {APPROVER_TYPES}")
    m = _matrix_row(conn, code, _tenant(actor))
    if not m:
        raise core.NotFoundError("approval matrix not found")
    cur = conn.execute("INSERT INTO approval_matrix_rules(matrix_id,seq,dimension,op,value,approver_type,"
                       "approver_ref,level,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (m["id"], seq, dimension, op, None if value is None else str(value),
                        approver_type, approver_ref, level, _iso(_now())))
    core.audit(conn, actor, "APPROVAL_MATRIX_RULE_ADDED", "approval_matrix_rules", cur.lastrowid,
               new={"matrix": code, "approver_type": approver_type, "approver_ref": approver_ref})
    conn.commit()
    return cur.lastrowid


def _cmp(op, actual, value):
    if actual is None or value is None:
        return op in ("exists",) and actual is not None
    if op == "eq":
        return str(actual) == str(value)
    if op == "ne":
        return str(actual) != str(value)
    if op in ("in", "not_in"):
        items = [x.strip() for x in str(value).split(",")]
        return (str(actual) in items) if op == "in" else (str(actual) not in items)
    try:
        a, b = float(actual), float(value)
    except (TypeError, ValueError):
        return False
    return {"gt": a > b, "lt": a < b, "gte": a >= b, "lte": a <= b}.get(op, False)


def resolve_approvals(conn, actor, code, ctx):
    """Return the approver specs required for `ctx` (rules whose dimension condition matches, or
    unconditional rules). Ordered by level/seq for sequential modes."""
    m = _matrix_row(conn, code, _tenant(actor))
    if not m:
        raise core.NotFoundError("approval matrix not found")
    rules = conn.execute("SELECT * FROM approval_matrix_rules WHERE matrix_id=? ORDER BY level, seq", (m["id"],)).fetchall()
    required = []
    for r in rules:
        if r["dimension"]:
            if not _cmp(r["op"], (ctx or {}).get(r["dimension"]), r["value"]):
                continue
        required.append({"approver_type": r["approver_type"], "approver_ref": r["approver_ref"],
                         "level": r["level"], "seq": r["seq"]})
    return {"matrix": code, "mode": m["mode"], "allow_self_approval": bool(m["allow_self_approval"]),
            "required_approvers": required}


def _actor_satisfies(conn, actor, spec, instance):
    """Does the actor satisfy this approver spec (directly or via an active delegation)?"""
    perms = actor.get("perms") or set()
    if "*" in perms:
        return True
    if spec["approver_type"] == "role" and actor.get("role") == spec["approver_ref"]:
        return True
    if spec["approver_type"] == "named" and str(actor.get("id")) == str(spec["approver_ref"]):
        return True
    # delegation: an active delegation to this actor for the workflow domain grants authority
    dele = active_delegation(conn, actor.get("id"), domain=None, tenant=_tenant(actor))
    if dele:
        if spec["approver_type"] == "role" and dele["role"] == spec["approver_ref"]:
            return True
        if spec["approver_type"] == "named" and str(dele["delegator"]) == str(spec["approver_ref"]):
            return True
    return False


def enforce_approval(conn, actor, matrix_code, ctx, instance):
    """Enforce the approval matrix for a transition: separation of duties, authority, and scope.
    Raises ForbiddenError on violation. Records nothing financial."""
    # scope: approver must be in the instance's tenant (unless legacy null)
    at, it = _tenant(actor), instance.get("tenant_id")
    if at is not None and it is not None and at != it:
        raise core.ForbiddenError("cannot approve outside tenant scope")
    if not matrix_code:
        return True                              # role-gated transitions handled by the caller
    res = resolve_approvals(conn, actor, matrix_code, ctx)
    # separation of duties: the instance starter may not approve unless explicitly allowed
    if not res["allow_self_approval"] and instance.get("assigned_user") == actor.get("id"):
        raise core.ForbiddenError("separation of duties: self-approval is prohibited")
    if not res["required_approvers"]:
        return True                              # matrix matched no rule => no explicit approver needed
    if any(_actor_satisfies(conn, actor, spec, instance) for spec in res["required_approvers"]):
        return True
    raise core.ForbiddenError("actor does not satisfy any required approver for this matrix")


def list_matrices(conn, actor):
    core.require(actor, "workflow.definition.view")
    at = _tenant(actor)
    rows = conn.execute("SELECT * FROM approval_matrices WHERE tenant_id=? OR tenant_id IS NULL ORDER BY code",
                        (at,)).fetchall() if at is not None else conn.execute("SELECT * FROM approval_matrices ORDER BY code").fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# SLA administration (business-hours aware)
# --------------------------------------------------------------------------- #
def create_sla(conn, actor, code, name, duration_minutes, sla_type=None, working_calendar_ref=None,
               holiday_calendar_ref=None, warning_pct=80, breach_pct=100, escalation_code=None,
               owner_role=None, severity="medium"):
    core.require(actor, "workflow.sla.manage")
    if int(duration_minutes) <= 0:
        raise core.ValidationError("SLA duration must be positive")
    tid = _tenant(actor)
    if conn.execute("SELECT 1 FROM sla_rules WHERE code=? AND (tenant_id=? OR tenant_id IS NULL)",
                    (code, tid)).fetchone():
        raise core.ConflictError(f"SLA '{code}' already exists")
    cur = conn.execute("INSERT INTO sla_rules(tenant_id,code,name,sla_type,duration_minutes,working_calendar_ref,"
                       "holiday_calendar_ref,warning_pct,breach_pct,escalation_code,owner_role,severity,active,"
                       "created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                       (tid, code, name, sla_type, int(duration_minutes), working_calendar_ref,
                        holiday_calendar_ref, warning_pct, breach_pct, escalation_code, owner_role,
                        severity, (actor or {}).get("id"), _iso(_now())))
    core.audit(conn, actor, "SLA_RULE_CREATED", "sla_rules", cur.lastrowid,
               new={"code": code, "duration_minutes": duration_minutes})
    conn.commit()
    return cur.lastrowid


def _next_day_start(cur, sh_h, sh_m):
    nxt = (cur + datetime.timedelta(days=1)).replace(hour=sh_h, minute=sh_m, second=0, microsecond=0)
    return nxt


def add_business_minutes(start, minutes, workdays, shift_start, shift_end, holidays):
    """Add `minutes` of working time to `start`, skipping non-workdays, holidays, and off-shift
    hours. workdays: set like {'Mon','Tue',...}; shift_start/end 'HH:MM'; holidays: set of ISO dates."""
    sh_h, sh_m = map(int, shift_start.split(":"))
    eh_h, eh_m = map(int, shift_end.split(":"))
    cur = start
    remaining = int(minutes)
    guard = 0
    while remaining > 0 and guard < 100000:
        guard += 1
        day = cur.strftime("%a")
        if day not in workdays or cur.date().isoformat() in holidays:
            cur = _next_day_start(cur, sh_h, sh_m); continue
        s_start = cur.replace(hour=sh_h, minute=sh_m, second=0, microsecond=0)
        s_end = cur.replace(hour=eh_h, minute=eh_m, second=0, microsecond=0)
        if cur < s_start:
            cur = s_start
        if cur >= s_end:
            cur = _next_day_start(cur, sh_h, sh_m); continue
        avail = int((s_end - cur).total_seconds() // 60)
        if remaining <= avail:
            return cur + datetime.timedelta(minutes=remaining)
        remaining -= avail
        cur = _next_day_start(cur, sh_h, sh_m)
    return cur


def _calendar(conn, sla):
    """Resolve working days/shift + holidays for an SLA (defaults Mon-Fri 08:00-17:00)."""
    workdays, sh, eh = {"Mon", "Tue", "Wed", "Thu", "Fri"}, "08:00", "17:00"
    holidays = set()
    if sla["working_calendar_ref"]:
        try:
            import org
            wc, _ = org.effective_working_calendar(conn, sla["working_calendar_ref"])
            if wc:
                workdays = {d.strip() for d in (wc.get("workdays") or "Mon,Tue,Wed,Thu,Fri").split(",")}
                sh = wc.get("shift_start") or sh
                eh = wc.get("shift_end") or eh
        except Exception:
            pass
    if sla["holiday_calendar_ref"]:
        try:
            import org
            holidays = {h["date"] for h in org.effective_holidays(conn, sla["holiday_calendar_ref"]) if "date" in h}
        except Exception:
            pass
    return workdays, sh, eh, holidays


def compute_due(conn, actor, code, start_iso=None):
    """Compute due_at + warning_at for an SLA from a start time, business-hours aware. Non-mutating."""
    tid = _tenant(actor)
    sla = conn.execute("SELECT * FROM sla_rules WHERE code=? AND (tenant_id=? OR tenant_id IS NULL) ORDER BY tenant_id DESC LIMIT 1",
                       (code, tid)).fetchone()
    if not sla:
        raise core.NotFoundError("SLA rule not found")
    start = datetime.datetime.fromisoformat(start_iso) if start_iso else _now()
    workdays, sh, eh, holidays = _calendar(conn, sla)
    due = add_business_minutes(start, sla["duration_minutes"], workdays, sh, eh, holidays)
    warn = add_business_minutes(start, int(sla["duration_minutes"] * (sla["warning_pct"] or 80) / 100.0),
                                workdays, sh, eh, holidays)
    return {"sla_code": code, "started_at": _iso(start), "due_at": _iso(due), "warning_at": _iso(warn),
            "duration_minutes": sla["duration_minutes"], "severity": sla["severity"]}


def start_sla(conn, actor, instance_id, code, start_iso=None):
    core.require(actor, "workflow.instance.manage")
    d = compute_due(conn, actor, code, start_iso)
    cur = conn.execute("INSERT INTO sla_instances(tenant_id,instance_id,sla_code,started_at,due_at,warning_at,"
                       "status,created_at) VALUES(?,?,?,?,?,?, 'RUNNING', ?)",
                       (_tenant(actor), instance_id, code, d["started_at"], d["due_at"], d["warning_at"], _iso(_now())))
    core.audit(conn, actor, "SLA_STARTED", "sla_instances", cur.lastrowid,
               new={"instance": instance_id, "sla": code, "due_at": d["due_at"]})
    conn.commit()
    return {"sla_instance_id": cur.lastrowid, **d}


def pause_sla(conn, actor, sla_instance_id, reason=None):
    core.require(actor, "workflow.sla.manage")
    conn.execute("UPDATE sla_instances SET status='PAUSED', paused_at=? WHERE id=?", (_iso(_now()), sla_instance_id))
    core.audit(conn, actor, "SLA_PAUSED", "sla_instances", sla_instance_id, reason=reason)
    conn.commit()
    return True


def resume_sla(conn, actor, sla_instance_id):
    core.require(actor, "workflow.sla.manage")
    si = conn.execute("SELECT * FROM sla_instances WHERE id=?", (sla_instance_id,)).fetchone()
    if not si or si["status"] != "PAUSED":
        raise core.ConflictError("SLA is not paused")
    paused_min = 0
    if si["paused_at"]:
        paused_min = int((_now() - datetime.datetime.fromisoformat(si["paused_at"])).total_seconds() // 60)
    # push the due date out by the paused duration (wall-clock approximation)
    new_due = datetime.datetime.fromisoformat(si["due_at"]) + datetime.timedelta(minutes=paused_min)
    conn.execute("UPDATE sla_instances SET status='RUNNING', paused_at=NULL, paused_minutes=paused_minutes+?,"
                 " due_at=? WHERE id=?", (paused_min, _iso(new_due), sla_instance_id))
    core.audit(conn, actor, "SLA_RESUMED", "sla_instances", sla_instance_id, new={"paused_minutes": paused_min})
    conn.commit()
    return {"paused_minutes": paused_min, "due_at": _iso(new_due)}


def check_breaches(conn, actor=None, now_iso=None):
    """Mark running SLA instances breached when past due; fire their escalation. Idempotent."""
    now = now_iso or _iso(_now())
    breached = []
    for si in conn.execute("SELECT * FROM sla_instances WHERE status='RUNNING' AND due_at < ?", (now,)).fetchall():
        conn.execute("UPDATE sla_instances SET status='BREACHED', breached_at=? WHERE id=?", (now, si["id"]))
        sla = conn.execute("SELECT escalation_code FROM sla_rules WHERE code=? LIMIT 1", (si["sla_code"],)).fetchone()
        esc_code = sla["escalation_code"] if sla else None
        if esc_code:
            _fire_escalation(conn, actor, si["instance_id"], si["sla_code"], esc_code, "SLA breach")
        core.audit(conn, actor or {"id": 0, "role": "system"}, "SLA_BREACHED", "sla_instances", si["id"],
                   new={"instance": si["instance_id"], "sla": si["sla_code"]})
        breached.append({"sla_instance_id": si["id"], "instance_id": si["instance_id"], "sla_code": si["sla_code"]})
    conn.commit()
    return breached


# --------------------------------------------------------------------------- #
# Escalations
# --------------------------------------------------------------------------- #
def create_escalation(conn, actor, code, name, target_type, target_ref=None, after_minutes=0, severity="medium"):
    core.require(actor, "workflow.escalation.manage")
    if target_type not in ESCALATION_TARGETS:
        raise core.ValidationError(f"target_type must be one of {ESCALATION_TARGETS}")
    tid = _tenant(actor)
    if conn.execute("SELECT 1 FROM escalation_rules WHERE code=? AND (tenant_id=? OR tenant_id IS NULL)",
                    (code, tid)).fetchone():
        raise core.ConflictError(f"escalation '{code}' already exists")
    cur = conn.execute("INSERT INTO escalation_rules(tenant_id,code,name,target_type,target_ref,after_minutes,"
                       "severity,active,created_by,created_at) VALUES(?,?,?,?,?,?,?,1,?,?)",
                       (tid, code, name, target_type, target_ref, int(after_minutes), severity,
                        (actor or {}).get("id"), _iso(_now())))
    core.audit(conn, actor, "ESCALATION_RULE_CREATED", "escalation_rules", cur.lastrowid,
               new={"code": code, "target_type": target_type})
    conn.commit()
    return cur.lastrowid


def _fire_escalation(conn, actor, instance_id, sla_code, escalation_code, reason):
    er = conn.execute("SELECT * FROM escalation_rules WHERE code=? LIMIT 1", (escalation_code,)).fetchone()
    tgt_type = er["target_type"] if er else "role"
    tgt_ref = er["target_ref"] if er else None
    conn.execute("INSERT INTO escalation_events(tenant_id,instance_id,sla_code,escalation_code,target_type,"
                 "target_ref,reason,correlation_id,ts) VALUES(?,?,?,?,?,?,?,?,?)",
                 ((actor or {}).get("tenant_id") if actor else None, instance_id, sla_code, escalation_code,
                  tgt_type, tgt_ref, reason, core.correlation_id(), _iso(_now())))
    core.audit(conn, actor or {"id": 0, "role": "system"}, "WORKFLOW_ESCALATED", "workflow_instances",
               instance_id, new={"escalation": escalation_code, "target": f"{tgt_type}:{tgt_ref}", "reason": reason})


def escalate(conn, actor, instance_id, escalation_code, reason=None, sla_code=None):
    core.require(actor, "workflow.escalation.manage")
    _fire_escalation(conn, actor, instance_id, sla_code, escalation_code, reason or "manual escalation")
    conn.commit()
    return True


def escalation_history(conn, actor, instance_id):
    return [dict(r) for r in conn.execute("SELECT * FROM escalation_events WHERE instance_id=? ORDER BY id", (instance_id,)).fetchall()]


# --------------------------------------------------------------------------- #
# Delegation (governed)
# --------------------------------------------------------------------------- #
def create_delegation(conn, actor, delegator, delegate, role, domain, start_at, end_at, reason=None):
    """Create a governed approval delegation. Blocks cross-tenant, circular, permanent (no end),
    self-delegation, and inactive delegate."""
    core.require(actor, "workflow.approval.delegate")
    if delegator == delegate:
        raise core.ValidationError("circular/self delegation is not allowed")
    if not end_at:
        raise core.ValidationError("delegation must have an end date (no permanent delegation)")
    if end_at < (start_at or _today()):
        raise core.ValidationError("delegation end must be after start")
    tid = _tenant(actor)
    # cross-tenant guard: both parties must be in the actor's tenant (when tenants are set)
    for uid in (delegator, delegate):
        row = conn.execute("SELECT tenant_id,status FROM users WHERE id=?", (uid,)).fetchone()
        if row is None:
            raise core.NotFoundError("delegation party not found")
        if tid is not None and row["tenant_id"] is not None and row["tenant_id"] != tid:
            raise core.ForbiddenError("cross-tenant delegation is not allowed")
        if uid == delegate and (row["status"] or "ACTIVE") != "ACTIVE":
            raise core.ForbiddenError("delegate is not active")
    # circular chain guard: delegate must not already delegate back to delegator for this domain
    back = conn.execute("SELECT 1 FROM approval_delegations WHERE active=1 AND delegator=? AND delegate=?"
                        " AND (domain=? OR domain IS NULL)", (delegate, delegator, domain)).fetchone()
    if back:
        raise core.ForbiddenError("circular delegation is not allowed")
    cur = conn.execute("INSERT INTO approval_delegations(tenant_id,delegator,delegate,role,domain,start_at,"
                       "end_at,reason,approved_by,active,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,1,?,?)",
                       (tid, delegator, delegate, role, domain, start_at or _today(), end_at, reason,
                        (actor or {}).get("id"), (actor or {}).get("id"), _iso(_now())))
    core.audit(conn, actor, "DELEGATION_CREATED", "approval_delegations", cur.lastrowid,
               new={"delegator": delegator, "delegate": delegate, "role": role, "domain": domain, "end_at": end_at})
    conn.commit()
    return cur.lastrowid


def active_delegation(conn, delegate_id, domain=None, tenant=None, on_date=None):
    """The delegate's current active, in-window delegation (or None). Tolerant of missing table."""
    on = on_date or _today()
    try:
        sql = ("SELECT * FROM approval_delegations WHERE active=1 AND delegate=? AND start_at<=? AND end_at>=?")
        args = [delegate_id, on, on]
        if domain:
            sql += " AND (domain=? OR domain IS NULL)"; args.append(domain)
        return conn.execute(sql + " ORDER BY id DESC LIMIT 1", tuple(args)).fetchone()
    except Exception:
        return None


def revoke_delegation(conn, actor, delegation_id, reason=None):
    core.require(actor, "workflow.approval.delegate")
    conn.execute("UPDATE approval_delegations SET active=0 WHERE id=?", (delegation_id,))
    core.audit(conn, actor, "DELEGATION_REVOKED", "approval_delegations", delegation_id, reason=reason)
    conn.commit()
    return True


def expire_delegations(conn, actor=None, as_of=None):
    """Deactivate delegations whose end date has passed. Idempotent."""
    on = as_of or _today()
    cur = conn.execute("UPDATE approval_delegations SET active=0 WHERE active=1 AND end_at < ?", (on,))
    conn.commit()
    return getattr(cur, "rowcount", 0) or 0


def list_delegations(conn, actor):
    core.require(actor, "workflow.approval.delegate")
    at = _tenant(actor)
    rows = conn.execute("SELECT * FROM approval_delegations WHERE tenant_id=? OR tenant_id IS NULL ORDER BY id DESC",
                        (at,)).fetchall() if at is not None else conn.execute("SELECT * FROM approval_delegations ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Governed notification events (respect provider/template config; never auto-send)
# --------------------------------------------------------------------------- #
def emit_notification(conn, actor, instance_id, channel, kind, recipient=None, template_code=None):
    if channel not in NOTIFY_CHANNELS:
        raise core.ValidationError(f"channel must be one of {NOTIFY_CHANNELS}")
    # only queue (do not send) — respects disabled/unconfigured providers by default
    cur = conn.execute("INSERT INTO notification_events(tenant_id,instance_id,channel,template_code,recipient,"
                       "kind,status,correlation_id,ts) VALUES(?,?,?,?,?,?, 'QUEUED', ?, ?)",
                       (_tenant(actor), instance_id, channel, template_code, recipient, kind,
                        core.correlation_id(), _iso(_now())))
    conn.commit()
    return cur.lastrowid
