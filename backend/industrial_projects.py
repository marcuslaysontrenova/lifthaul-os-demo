"""Governed industrial-project controls over canonical marketplace bookings.

This module deliberately does not create a second booking domain. Surveys, specialized
resources, package plans and reservations all point to ``mkt_bookings``.  The implementation
is deterministic and fail-closed: no AI decision can approve a survey, certify a resource or
override a capacity / availability / expiry conflict.
"""
from __future__ import annotations

import datetime
import json

import core
import tenant


SURVEY_STATUSES = {"SCHEDULED", "IN_PROGRESS", "COMPLETED", "APPROVED", "REJECTED"}
SPECIALIZED_RESOURCE_TYPES = {
    "CRANE", "FORKLIFT", "LOWBED_TRAILER", "RIGGING_CREW", "SAFETY_OFFICER",
    "HELPERS", "ESCORT", "EQUIPMENT_OPERATOR",
}
PLAN_RESOURCE_TYPES = SPECIALIZED_RESOURCE_TYPES | {"VEHICLE", "DRIVER"}
CONTROL_REQUIREMENTS = {
    "PERMITS": "permit_reference",
    "CARGO_INSURANCE": "insurance_review_reference",
    "INTER_ISLAND": "inter_island_plan_reference",
}
REQUEST_TO_PLAN_TYPES = {
    "TRANSPORT": {"VEHICLE"},
    "LOWBED_TRAILER": {"LOWBED_TRAILER", "VEHICLE"},
    "CRANE": {"CRANE"},
    "FORKLIFT": {"FORKLIFT"},
    "RIGGING_CREW": {"RIGGING_CREW"},
    "EQUIPMENT_OPERATOR": {"EQUIPMENT_OPERATOR", "DRIVER"},
    "HELPERS": {"HELPERS"},
    "ESCORT": {"ESCORT"},
    "SAFETY_OFFICER": {"SAFETY_OFFICER"},
}


SCHEMA = """
CREATE TABLE IF NOT EXISTS mkt_project_surveys(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, booking_id INTEGER NOT NULL,
  revision INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'SCHEDULED',
  assigned_surveyor INTEGER, scheduled_at TEXT, started_at TEXT, completed_at TEXT,
  access_conditions TEXT, ground_conditions TEXT, clearances TEXT, hazards TEXT,
  power_lines INTEGER DEFAULT 0, measurements TEXT, findings TEXT,
  required_resources TEXT, evidence_refs TEXT,
  completed_by INTEGER, approved_by INTEGER, approved_at TEXT, approval_note TEXT,
  created_by INTEGER, created_at TEXT, updated_at TEXT,
  UNIQUE(booking_id, revision));

CREATE TABLE IF NOT EXISTS mkt_specialized_resources(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, carrier_id INTEGER,
  code TEXT NOT NULL, name TEXT NOT NULL, resource_type TEXT NOT NULL,
  capacity_kg REAL, service_areas TEXT, specifications TEXT,
  certification_expiry TEXT, inspection_valid_until TEXT,
  maintenance_status TEXT NOT NULL DEFAULT 'SERVICEABLE',
  verification_status TEXT NOT NULL DEFAULT 'SUBMITTED',
  verification_source TEXT, verified_by INTEGER, verified_at TEXT,
  status TEXT NOT NULL DEFAULT 'ACTIVE', created_by INTEGER, created_at TEXT, updated_at TEXT,
  UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS mkt_project_resource_plans(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, booking_id INTEGER NOT NULL,
  version INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT',
  start_at TEXT NOT NULL, end_at TEXT NOT NULL, control_evidence TEXT,
  validated_by INTEGER, validated_at TEXT, approved_by INTEGER, approved_at TEXT,
  created_by INTEGER, created_at TEXT, updated_at TEXT,
  UNIQUE(booking_id, version));

CREATE TABLE IF NOT EXISTS mkt_project_resource_plan_items(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, plan_id INTEGER NOT NULL,
  resource_type TEXT NOT NULL, resource_id INTEGER NOT NULL,
  requirement_code TEXT, required_capacity_kg REAL, required_qualification TEXT,
  validation_result TEXT, created_at TEXT,
  UNIQUE(plan_id, resource_type, resource_id, requirement_code));

CREATE TABLE IF NOT EXISTS mkt_project_resource_reservations(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, booking_id INTEGER NOT NULL,
  plan_id INTEGER NOT NULL, item_id INTEGER NOT NULL,
  resource_type TEXT NOT NULL, resource_id INTEGER NOT NULL,
  start_at TEXT NOT NULL, end_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'RESERVED', created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS mkt_project_stage_history(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, booking_id INTEGER NOT NULL,
  from_stage TEXT, to_stage TEXT NOT NULL, reason TEXT,
  actor INTEGER, actor_role TEXT, created_at TEXT);
"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today():
    return datetime.date.today().isoformat()


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn):
    return 0


def _grant_permissions():
    grants = {
        "operations_manager": {"industrial.survey.manage", "industrial.resource.manage",
                               "industrial.plan.manage", "industrial.project.read"},
        "estimator": {"industrial.survey.manage", "industrial.project.read"},
        "dispatcher": {"industrial.plan.manage", "industrial.project.read"},
        "safety_officer": {"industrial.survey.approve", "industrial.plan.approve",
                           "industrial.project.read"},
    }
    for role, permissions in grants.items():
        core.PERMISSIONS.setdefault(role, set()).update(permissions)


_grant_permissions()


def _booking(conn, actor, booking_id):
    row = conn.execute("SELECT * FROM mkt_bookings WHERE id=?", (booking_id,)).fetchone()
    if not row:
        raise core.NotFoundError("managed project not found")
    tenant.guard(actor, row)
    if row["booking_mode"] != "MANAGED_PROJECT":
        raise core.ConflictError("industrial controls require a Managed Project booking")
    return row


def _survey(conn, actor, survey_id):
    row = conn.execute("SELECT * FROM mkt_project_surveys WHERE id=?", (survey_id,)).fetchone()
    if not row:
        raise core.NotFoundError("site survey not found")
    tenant.guard(actor, row)
    return row


def _plan(conn, actor, plan_id):
    row = conn.execute("SELECT * FROM mkt_project_resource_plans WHERE id=?", (plan_id,)).fetchone()
    if not row:
        raise core.NotFoundError("resource plan not found")
    tenant.guard(actor, row)
    return row


def _json(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _stage(conn, actor, booking_id, to_stage, reason):
    row = conn.execute("SELECT project_stage,tenant_id FROM mkt_bookings WHERE id=?", (booking_id,)).fetchone()
    previous = row["project_stage"] if row else None
    if previous == to_stage:
        return
    conn.execute("UPDATE mkt_bookings SET project_stage=?,updated_at=? WHERE id=?",
                 (to_stage, _now(), booking_id))
    cur = conn.execute(
        "INSERT INTO mkt_project_stage_history(tenant_id,booking_id,from_stage,to_stage,reason,actor,actor_role,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (row["tenant_id"] if row else actor.get("tenant_id"), booking_id, previous, to_stage,
         str(reason or "")[:500], actor["id"], actor.get("role"), _now()))
    core.audit(conn, actor, "INDUSTRIAL_PROJECT_STAGE_CHANGED", "mkt_project_stage_history",
               cur.lastrowid, old={"stage": previous}, new={"stage": to_stage, "reason": reason})


# ---------------------------------------------------------------------------
# Versioned site survey
# ---------------------------------------------------------------------------
def schedule_survey(conn, actor, booking_id, scheduled_at, assigned_surveyor=None):
    core.require(actor, "industrial.survey.manage")
    booking = _booking(conn, actor, booking_id)
    if not booking["site_survey_required"]:
        raise core.ConflictError("this project does not currently require a site survey")
    if not str(scheduled_at or "").strip():
        raise core.ValidationError("scheduled_at is required")
    latest = conn.execute("SELECT MAX(revision) revision FROM mkt_project_surveys WHERE booking_id=?",
                          (booking_id,)).fetchone()
    revision = int((latest["revision"] if latest else 0) or 0) + 1
    cur = conn.execute(
        "INSERT INTO mkt_project_surveys(tenant_id,booking_id,revision,status,assigned_surveyor,"
        "scheduled_at,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (booking["tenant_id"], booking_id, revision, "SCHEDULED", assigned_surveyor,
         str(scheduled_at), actor["id"], _now(), _now()))
    core.audit(conn, actor, "INDUSTRIAL_SURVEY_SCHEDULED", "mkt_project_surveys", cur.lastrowid,
               new={"booking_id": booking_id, "revision": revision, "scheduled_at": scheduled_at,
                    "assigned_surveyor": assigned_surveyor})
    _stage(conn, actor, booking_id, "SURVEY_SCHEDULED", "site survey scheduled")
    conn.commit()
    return {"survey_id": cur.lastrowid, "booking_id": booking_id, "revision": revision,
            "status": "SCHEDULED"}


def complete_survey(conn, actor, survey_id, *, measurements, findings, evidence_refs,
                    access_conditions=None, ground_conditions=None, clearances=None,
                    hazards=None, power_lines=False, required_resources=None):
    core.require(actor, "industrial.survey.manage")
    survey = _survey(conn, actor, survey_id)
    if survey["status"] not in ("SCHEDULED", "IN_PROGRESS"):
        raise core.ConflictError("only a scheduled or in-progress survey may be completed")
    if not isinstance(measurements, dict) or not measurements:
        raise core.ValidationError("structured survey measurements are required")
    if not str(findings or "").strip():
        raise core.ValidationError("survey findings are required")
    if not isinstance(evidence_refs, list) or not evidence_refs or any(not str(x).strip() for x in evidence_refs):
        raise core.ValidationError("at least one evidence reference is required")
    if len(evidence_refs) > 50:
        raise core.ValidationError("too many survey evidence references")
    required_resources = required_resources or []
    conn.execute(
        "UPDATE mkt_project_surveys SET status='COMPLETED',completed_at=?,completed_by=?,"
        "access_conditions=?,ground_conditions=?,clearances=?,hazards=?,power_lines=?,measurements=?,"
        "findings=?,required_resources=?,evidence_refs=?,updated_at=? WHERE id=?",
        (_now(), actor["id"], str(access_conditions or "")[:2000],
         str(ground_conditions or "")[:2000], str(clearances or "")[:2000],
         str(hazards or "")[:2000], int(bool(power_lines)), json.dumps(measurements),
         str(findings)[:5000], json.dumps(required_resources), json.dumps(evidence_refs), _now(), survey_id))
    core.audit(conn, actor, "INDUSTRIAL_SURVEY_COMPLETED", "mkt_project_surveys", survey_id,
               old={"status": survey["status"]},
               new={"status": "COMPLETED", "evidence_count": len(evidence_refs)})
    _stage(conn, actor, survey["booking_id"], "SURVEY_COMPLETED", "survey evidence submitted for approval")
    conn.commit()
    return {"survey_id": survey_id, "status": "COMPLETED", "approval_required": True}


def approve_survey(conn, actor, survey_id, decision, note=None, override_reason=None):
    core.require(actor, "industrial.survey.approve")
    survey = _survey(conn, actor, survey_id)
    decision = str(decision or "").upper()
    if decision not in ("APPROVED", "REJECTED"):
        raise core.ValidationError("decision must be APPROVED or REJECTED")
    if survey["status"] != "COMPLETED":
        raise core.ConflictError("only a completed survey may be reviewed")
    same_actor = actor["id"] in {survey["created_by"], survey["completed_by"]}
    privileged = actor.get("role") in ("super_admin", "super_platform_admin")
    if same_actor and not privileged:
        raise core.ForbiddenError("separation of duties: survey completer cannot approve the survey")
    if same_actor and privileged and not str(override_reason or "").strip():
        raise core.ValidationError("privileged survey-review override reason is required")
    conn.execute("UPDATE mkt_project_surveys SET status=?,approved_by=?,approved_at=?,approval_note=?,updated_at=? WHERE id=?",
                 (decision, actor["id"], _now(), str(note or "")[:2000], _now(), survey_id))
    if decision == "APPROVED":
        conn.execute("UPDATE mkt_bookings SET site_survey_required=0,assessment_status='SURVEY_APPROVED',"
                     "project_stage='ESTIMATION',updated_at=? WHERE id=?", (_now(), survey["booking_id"]))
        next_stage = "ESTIMATION"
    else:
        conn.execute("UPDATE mkt_bookings SET assessment_status='SURVEY_REJECTED',site_survey_required=1,"
                     "project_stage='SURVEY_REQUIRED',updated_at=? WHERE id=?", (_now(), survey["booking_id"]))
        next_stage = "SURVEY_REQUIRED"
    core.audit(conn, actor, "INDUSTRIAL_SURVEY_REVIEWED", "mkt_project_surveys", survey_id,
               old={"status": "COMPLETED"}, new={"status": decision, "note": note,
                                                        "override_reason": override_reason})
    _stage(conn, actor, survey["booking_id"], next_stage, f"survey {decision.lower()}")
    conn.commit()
    return {"survey_id": survey_id, "status": decision, "booking_id": survey["booking_id"]}


def latest_approved_survey(conn, booking_id):
    return conn.execute("SELECT * FROM mkt_project_surveys WHERE booking_id=? AND status='APPROVED' "
                        "ORDER BY revision DESC LIMIT 1", (booking_id,)).fetchone()


# ---------------------------------------------------------------------------
# Specialized resource registry
# ---------------------------------------------------------------------------
def register_specialized_resource(conn, actor, *, code, name, resource_type, carrier_id=None,
                                  capacity_kg=None, service_areas=None, specifications=None,
                                  certification_expiry=None, inspection_valid_until=None):
    core.require(actor, "industrial.resource.manage")
    resource_type = str(resource_type or "").upper()
    if resource_type not in SPECIALIZED_RESOURCE_TYPES:
        raise core.ValidationError("unsupported specialized resource type")
    if not str(code or "").strip() or not str(name or "").strip():
        raise core.ValidationError("resource code and name are required")
    if capacity_kg is not None and float(capacity_kg) <= 0:
        raise core.ValidationError("capacity_kg must be positive")
    cur = conn.execute(
        "INSERT INTO mkt_specialized_resources(tenant_id,carrier_id,code,name,resource_type,capacity_kg,"
        "service_areas,specifications,certification_expiry,inspection_valid_until,created_by,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (actor.get("tenant_id"), carrier_id, str(code).strip(), str(name).strip(), resource_type,
         float(capacity_kg) if capacity_kg is not None else None, json.dumps(service_areas or []),
         json.dumps(specifications or {}), certification_expiry, inspection_valid_until,
         actor["id"], _now(), _now()))
    core.audit(conn, actor, "INDUSTRIAL_RESOURCE_REGISTERED", "mkt_specialized_resources", cur.lastrowid,
               new={"code": code, "resource_type": resource_type, "verification_status": "SUBMITTED"})
    conn.commit()
    return cur.lastrowid


def verify_specialized_resource(conn, actor, resource_id, decision, source, *,
                                maintenance_status="SERVICEABLE"):
    core.require(actor, "industrial.resource.manage")
    row = conn.execute("SELECT * FROM mkt_specialized_resources WHERE id=?", (resource_id,)).fetchone()
    if not row:
        raise core.NotFoundError("specialized resource not found")
    tenant.guard(actor, row)
    decision = str(decision or "").upper()
    if decision not in ("VERIFIED", "REJECTED"):
        raise core.ValidationError("decision must be VERIFIED or REJECTED")
    if decision == "VERIFIED" and not str(source or "").strip():
        raise core.ValidationError("verification source is required")
    conn.execute("UPDATE mkt_specialized_resources SET verification_status=?,verification_source=?,"
                 "maintenance_status=?,verified_by=?,verified_at=?,updated_at=? WHERE id=?",
                 (decision, str(source or "")[:1000], str(maintenance_status).upper(), actor["id"],
                  _now(), _now(), resource_id))
    core.audit(conn, actor, "INDUSTRIAL_RESOURCE_VERIFIED", "mkt_specialized_resources", resource_id,
               old={"status": row["verification_status"]},
               new={"status": decision, "source": source, "maintenance_status": maintenance_status})
    conn.commit()
    return decision


def _specialized_gate(conn, resource_id, required_capacity_kg=None):
    row = conn.execute("SELECT * FROM mkt_specialized_resources WHERE id=?", (resource_id,)).fetchone()
    reasons = []
    if not row:
        return None, ["unknown_specialized_resource"]
    if row["status"] != "ACTIVE":
        reasons.append("resource_not_active")
    if row["verification_status"] != "VERIFIED":
        reasons.append("resource_not_verified")
    if row["maintenance_status"] in ("UNSAFE", "GROUNDED", "OVERDUE", "OUT_OF_SERVICE"):
        reasons.append("maintenance_or_inspection_block")
    if row["certification_expiry"] and row["certification_expiry"][:10] < _today():
        reasons.append("certification_expired")
    if row["inspection_valid_until"] and row["inspection_valid_until"][:10] < _today():
        reasons.append("inspection_expired")
    if required_capacity_kg is not None and float(row["capacity_kg"] or 0) < float(required_capacity_kg):
        reasons.append("capacity_insufficient")
    return row, reasons


def _resource_gate(conn, booking, item):
    rtype = str(item.get("resource_type") or "").upper()
    try:
        rid = int(item.get("resource_id"))
    except (TypeError, ValueError):
        raise core.ValidationError("resource_id must be an integer")
    if rtype not in PLAN_RESOURCE_TYPES:
        raise core.ValidationError("unsupported plan resource type")
    required_capacity = item.get("required_capacity_kg")
    if required_capacity is not None and float(required_capacity) <= 0:
        raise core.ValidationError("required_capacity_kg must be positive")
    reasons = []
    if rtype == "VEHICLE":
        row = conn.execute("SELECT * FROM mkt_vehicles WHERE id=?", (rid,)).fetchone()
        if not row:
            reasons.append("unknown_vehicle")
        else:
            if row["status"] != "ACTIVE" or not row["verified_at"]:
                reasons.append("vehicle_not_active_and_verified")
            import marketplace_trust_closure as trust
            cargo_required = required_capacity
            if cargo_required is None and booking["weight_kg"] is not None:
                cargo_required = float(booking["weight_kg"]) * (1 + float(booking["safety_allowance_pct"] or .10))
            reasons.extend(trust.vehicle_legality_gate(conn, rid, cargo_required)["reasons"])
            import availability
            reasons.extend(availability.compute_status(conn, "VEHICLE", rid)["reasons"])
    elif rtype == "DRIVER":
        import marketplace_trust_closure as trust
        reasons.extend(trust.driver_assignment_gate(
            conn, rid, equipment_type=item.get("required_qualification"))["reasons"])
        import availability
        reasons.extend(availability.compute_status(conn, "DRIVER", rid)["reasons"])
    else:
        row, reasons = _specialized_gate(conn, rid, required_capacity)
        if row and row["resource_type"] != rtype:
            reasons.append("specialized_resource_type_mismatch")
    return {"resource_type": rtype, "resource_id": rid, "ok": not reasons,
            "reasons": list(dict.fromkeys(reasons))}


def _validate_window(start_at, end_at):
    try:
        start = datetime.datetime.fromisoformat(str(start_at).replace("Z", "+00:00"))
        end = datetime.datetime.fromisoformat(str(end_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise core.ValidationError("start_at and end_at must be ISO-8601 timestamps")
    if end <= start:
        raise core.ValidationError("resource-plan end_at must be after start_at")


def reserve_resource_package(conn, actor, booking_id, *, start_at, end_at, items, control_evidence=None):
    """Validate and reserve the complete package in one database transaction.

    Any failed verification, missing requested resource, control-evidence gap or overlap rolls back
    the whole package; partial reservations are never retained.
    """
    core.require(actor, "industrial.plan.manage")
    booking = _booking(conn, actor, booking_id)
    _validate_window(start_at, end_at)
    if booking["site_survey_required"] or not latest_approved_survey(conn, booking_id) and \
            "SURVEY" in str(booking["assessment_status"] or ""):
        raise core.ConflictError("approved site survey is required before resource reservation")
    if not isinstance(items, list) or not items:
        raise core.ValidationError("resource plan needs at least one item")
    if len(items) > 50:
        raise core.ValidationError("resource plan exceeds 50 items")
    control_evidence = control_evidence or {}
    requested = set(_json(booking["requested_resources"], []))
    provided_types = {str(item.get("resource_type") or "").upper() for item in items}
    missing = [req for req, acceptable in REQUEST_TO_PLAN_TYPES.items()
               if req in requested and not provided_types.intersection(acceptable)]
    for req, key in CONTROL_REQUIREMENTS.items():
        if req in requested and not str(control_evidence.get(key) or "").strip():
            missing.append(req)
    if missing:
        raise core.ValidationError("resource package is incomplete: " + ", ".join(sorted(missing)))
    results = [_resource_gate(conn, booking, item) for item in items]
    failed = [result for result in results if not result["ok"]]
    if failed:
        raise core.ConflictError("resource package failed verification: " + json.dumps(failed, sort_keys=True))
    for result in results:
        conflict = conn.execute(
            "SELECT booking_id FROM mkt_project_resource_reservations WHERE resource_type=? AND resource_id=? "
            "AND status='RESERVED' AND start_at < ? AND end_at > ? LIMIT 1",
            (result["resource_type"], result["resource_id"], str(end_at), str(start_at))).fetchone()
        if conflict and conflict["booking_id"] != booking_id:
            raise core.ConflictError(
                f"{result['resource_type']} {result['resource_id']} is already reserved by booking {conflict['booking_id']}")
    latest = conn.execute("SELECT MAX(version) version FROM mkt_project_resource_plans WHERE booking_id=?",
                          (booking_id,)).fetchone()
    version = int((latest["version"] if latest else 0) or 0) + 1
    conn.execute("SAVEPOINT industrial_resource_package")
    try:
        cur = conn.execute(
            "INSERT INTO mkt_project_resource_plans(tenant_id,booking_id,version,status,start_at,end_at,"
            "control_evidence,validated_by,validated_at,created_by,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (booking["tenant_id"], booking_id, version, "RESERVED", str(start_at), str(end_at),
             json.dumps(control_evidence), actor["id"], _now(), actor["id"], _now(), _now()))
        plan_id = cur.lastrowid
        for item, result in zip(items, results):
            icur = conn.execute(
                "INSERT INTO mkt_project_resource_plan_items(tenant_id,plan_id,resource_type,resource_id,"
                "requirement_code,required_capacity_kg,required_qualification,validation_result,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (booking["tenant_id"], plan_id, result["resource_type"], result["resource_id"],
                 item.get("requirement_code"), item.get("required_capacity_kg"),
                 item.get("required_qualification"), json.dumps(result), _now()))
            conn.execute(
                "INSERT INTO mkt_project_resource_reservations(tenant_id,booking_id,plan_id,item_id,"
                "resource_type,resource_id,start_at,end_at,status,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (booking["tenant_id"], booking_id, plan_id, icur.lastrowid, result["resource_type"],
                 result["resource_id"], str(start_at), str(end_at), "RESERVED", actor["id"], _now()))
        _stage(conn, actor, booking_id, "RESOURCES_RESERVED", "complete verified package reserved")
        core.audit(conn, actor, "INDUSTRIAL_RESOURCE_PACKAGE_RESERVED", "mkt_project_resource_plans", plan_id,
                   new={"booking_id": booking_id, "version": version, "items": results,
                        "start_at": start_at, "end_at": end_at})
        conn.execute("RELEASE SAVEPOINT industrial_resource_package")
        conn.commit()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT industrial_resource_package")
        conn.execute("RELEASE SAVEPOINT industrial_resource_package")
        conn.rollback()
        raise
    return {"plan_id": plan_id, "booking_id": booking_id, "version": version,
            "status": "RESERVED", "items": results, "approval_required": True}


def approve_resource_plan(conn, actor, plan_id, note=None):
    core.require(actor, "industrial.plan.approve")
    plan = _plan(conn, actor, plan_id)
    if plan["status"] != "RESERVED":
        raise core.ConflictError("only a reserved resource plan may be approved")
    if actor["id"] == plan["created_by"] and actor.get("role") not in ("super_admin", "super_platform_admin"):
        raise core.ForbiddenError("separation of duties: plan creator cannot approve the resource plan")
    conn.execute("UPDATE mkt_project_resource_plans SET status='APPROVED',approved_by=?,approved_at=?,updated_at=? WHERE id=?",
                 (actor["id"], _now(), _now(), plan_id))
    _stage(conn, actor, plan["booking_id"], "SAFETY_REVIEW", "resource package approved; safety review required")
    core.audit(conn, actor, "INDUSTRIAL_RESOURCE_PACKAGE_APPROVED", "mkt_project_resource_plans", plan_id,
               old={"status": "RESERVED"}, new={"status": "APPROVED", "note": note})
    conn.commit()
    return {"plan_id": plan_id, "status": "APPROVED", "booking_id": plan["booking_id"]}


def project_summary(conn, booking_id, *, customer_safe=False):
    survey = conn.execute("SELECT id,revision,status,scheduled_at,completed_at,approved_at FROM mkt_project_surveys "
                          "WHERE booking_id=? ORDER BY revision DESC LIMIT 1", (booking_id,)).fetchone()
    plan = conn.execute("SELECT id,version,status,start_at,end_at FROM mkt_project_resource_plans "
                        "WHERE booking_id=? ORDER BY version DESC LIMIT 1", (booking_id,)).fetchone()
    history = conn.execute("SELECT from_stage,to_stage,created_at FROM mkt_project_stage_history "
                           "WHERE booking_id=? ORDER BY id", (booking_id,)).fetchall()
    result = {
        "survey": dict(survey) if survey else None,
        "resource_plan": dict(plan) if plan else None,
        "stage_history": [dict(row) for row in history],
    }
    if customer_safe:
        if result["survey"]:
            result["survey"].pop("id", None)
        if result["resource_plan"]:
            result["resource_plan"].pop("id", None)
    return result


def get_project_control(conn, actor, booking_id):
    core.require(actor, "industrial.project.read")
    booking = _booking(conn, actor, booking_id)
    result = project_summary(conn, booking_id)
    result["booking"] = {"id": booking_id, "stage": booking["project_stage"],
                         "assessment_status": booking["assessment_status"],
                         "site_survey_required": bool(booking["site_survey_required"]),
                         "requested_resources": _json(booking["requested_resources"], [])}
    return result
