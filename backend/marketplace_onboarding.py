"""LiftHaul Nationwide Marketplace — Increment 2: participant onboarding, compliance, and
compliance-aware marketplace eligibility.

Builds on the deterministic foundation in `marketplace.py` (cargo/vehicle taxonomy, eligibility,
lanes). This module adds the governed two-sided participant layer:

  * shipper + carrier onboarding lifecycles (fail-closed activation, no self-verify/activate);
  * carrier vehicle onboarding linked to the governed vehicle taxonomy;
  * driver onboarding + assignment guards;
  * a reusable compliance-document model with expiry that AUTO-REMOVES an affected carrier/vehicle/
    driver from eligibility WITHOUT deleting historical records;
  * a declarative compliance-rules engine that returns required/provided/missing/expired/rejected/
    blockers/warnings + an activation RECOMMENDATION — but never makes the final activation decision;
  * verification work queues (cases) with priority/risk/owner/SLA/correlation;
  * COMPLIANCE-AWARE candidate pool: a carrier/vehicle/driver reaches a booking's candidate pool only
    when every hard control passes; AI/manual ranking may re-order but can NEVER override a hard denial;
  * integrity checks + a deterministic migration classifier (no auto-activation, no fabricated evidence).

Invariants (Increment 2 directive):
  * "Open registration must never mean automatic activation."      -> activation is a separate governed step
  * "Activation must fail closed when mandatory evidence is incomplete." -> _fail_closed_activation()
  * "no self-verification / no self-activation"                    -> verify: actor != created_by;
                                                                       activate: actor != verified_by
  * "Do not let AI/ranking override a hard compliance denial."     -> candidate_pool() hard-excludes
  * "Expired required documents ... remove from eligibility without deleting historical records."
  * tenant isolation + organization scope preserved; 0 financial / operational-status drift.
  * secrets: never store banking passwords / wallet PINs / OTPs / full payment credentials / raw
    provider secrets — masked references only (_reject_raw_secret()).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re

import core
import tenant
import marketplace as mkt

# --------------------------------------------------------------------------- #
# Vocabularies
# --------------------------------------------------------------------------- #
PARTICIPANT_STATUSES = ("APPLICATION", "PROFILE_INCOMPLETE", "DOCUMENT_REVIEW",
                        "VERIFICATION_REQUIRED", "CONDITIONALLY_APPROVED", "APPROVED",
                        "ACTIVE", "SUSPENDED", "EXPIRED", "REJECTED")
VEHICLE_STATUSES = ("DRAFT", "DOCUMENT_REVIEW", "INSPECTION_REQUIRED", "APPROVED", "ACTIVE",
                    "SUSPENDED", "EXPIRED", "REJECTED", "MAINTENANCE")
VEHICLE_ELIGIBLE_STATUS = "ACTIVE"
DRIVER_STATUSES = ("APPLICATION", "DOCUMENT_REVIEW", "VERIFIED", "ACTIVE", "SUSPENDED",
                   "EXPIRED", "REJECTED")
DOC_STATUSES = ("UPLOADED", "PENDING_REVIEW", "VERIFIED", "REJECTED", "EXPIRED", "REVOKED",
                "SUSPENDED", "RENEWAL_REQUIRED")
INTEGRITY_STATUSES = ("NOT_RUN", "PASS", "WARNING", "FAIL", "BLOCKED")

SHIPPER_TYPES = ("INDIVIDUAL", "SOLE_PROPRIETOR", "PARTNERSHIP", "CORPORATION", "ENTERPRISE")
CARRIER_TYPES = ("OWNER_OPERATOR", "SOLE_PROPRIETOR", "PARTNERSHIP", "CORPORATION",
                 "REGIONAL_CARRIER", "FLEET_OPERATOR", "SPECIALIZED_CARRIER", "SUBCONTRACTOR")

SCHEMA = """
CREATE TABLE IF NOT EXISTS mkt_shippers(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  org_scope TEXT,
  applicant_type TEXT NOT NULL,
  legal_name TEXT NOT NULL,
  trade_name TEXT,
  registration_type TEXT,
  registration_number TEXT,
  tax_id TEXT,
  registered_address TEXT,
  billing_address TEXT,
  representatives TEXT,        -- JSON
  beneficial_owners TEXT,      -- JSON
  contacts TEXT,               -- JSON
  cargo_profile TEXT,
  expected_volume TEXT,
  preferred_lanes TEXT,        -- JSON
  preferred_vehicles TEXT,     -- JSON
  payment_ref TEXT,            -- MASKED reference only
  contract_accepted INTEGER DEFAULT 0,
  privacy_accepted INTEGER DEFAULT 0,
  risk_flags TEXT,
  status TEXT NOT NULL DEFAULT 'APPLICATION',
  verified_by INTEGER, verified_at TEXT,
  activated_by INTEGER, activated_at TEXT,
  created_by INTEGER, created_at TEXT,
  updated_by INTEGER, updated_at TEXT,
  correlation_id TEXT,
  UNIQUE(tenant_id, registration_type, registration_number));

CREATE TABLE IF NOT EXISTS mkt_carriers(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  org_scope TEXT,
  carrier_type TEXT NOT NULL,
  legal_name TEXT NOT NULL,
  trade_name TEXT,
  registration_type TEXT,
  registration_number TEXT,
  tax_id TEXT,
  beneficial_owners TEXT,
  representatives TEXT,
  operating_address TEXT,
  service_areas TEXT,          -- JSON
  preferred_lanes TEXT,        -- JSON
  port_experience TEXT,
  cargo_capabilities TEXT,     -- JSON
  vehicle_categories TEXT,     -- JSON
  fleet_size INTEGER,
  insurance_ref TEXT,
  permits TEXT,
  operating_hours TEXT,
  payout_provider_ref TEXT,    -- MASKED reference only
  risk_status TEXT,
  support_owner INTEGER,
  status TEXT NOT NULL DEFAULT 'APPLICATION',
  verified_by INTEGER, verified_at TEXT,
  activated_by INTEGER, activated_at TEXT,
  created_by INTEGER, created_at TEXT,
  updated_by INTEGER, updated_at TEXT,
  correlation_id TEXT,
  UNIQUE(tenant_id, registration_type, registration_number));

CREATE TABLE IF NOT EXISTS mkt_vehicles(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  carrier_id INTEGER NOT NULL,
  category_code TEXT NOT NULL,   -- FK -> mkt_vehicle_categories.code
  registration_number TEXT,
  plate_number TEXT NOT NULL,
  ownership_type TEXT,
  owner_name TEXT,
  operating_authority TEXT,
  body_type TEXT,
  payload_kg REAL,
  volume_cbm REAL,
  length_cm REAL, width_cm REAL, height_cm REAL,
  opening_length_cm REAL, opening_width_cm REAL, opening_height_cm REAL,
  axle_config TEXT,
  refrigerated INTEGER DEFAULT 0,
  lifting_capable INTEGER DEFAULT 0,
  route_restrictions TEXT,
  port_eligible INTEGER DEFAULT 0,
  current_location TEXT,
  status TEXT NOT NULL DEFAULT 'DRAFT',
  verified_by INTEGER, verified_at TEXT,
  activated_by INTEGER, activated_at TEXT,
  created_by INTEGER, created_at TEXT,
  updated_by INTEGER, updated_at TEXT,
  correlation_id TEXT,
  UNIQUE(tenant_id, plate_number));

CREATE TABLE IF NOT EXISTS mkt_drivers(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  carrier_id INTEGER NOT NULL,
  full_name TEXT NOT NULL,
  identity_ref TEXT,
  licence_number TEXT,
  licence_class TEXT,
  licence_expiry TEXT,
  authorized_categories TEXT,    -- JSON of vehicle category codes
  training TEXT,
  safety_qualifications TEXT,
  route_experience TEXT,
  port_experience TEXT,
  emergency_contact TEXT,
  status TEXT NOT NULL DEFAULT 'APPLICATION',
  verified_by INTEGER, verified_at TEXT,
  activated_by INTEGER, activated_at TEXT,
  created_by INTEGER, created_at TEXT,
  updated_by INTEGER, updated_at TEXT,
  correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_documents(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  document_type TEXT NOT NULL,
  subject_type TEXT NOT NULL,    -- SHIPPER | CARRIER | VEHICLE | DRIVER
  subject_id INTEGER NOT NULL,
  issuing_authority TEXT,
  document_number TEXT,
  issue_date TEXT,
  expiry_date TEXT,
  status TEXT NOT NULL DEFAULT 'UPLOADED',
  verified_by INTEGER, verified_at TEXT,
  storage_ref TEXT,
  checksum TEXT,
  rejection_reason TEXT,
  created_by INTEGER, created_at TEXT,
  updated_by INTEGER, updated_at TEXT,
  correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_compliance_rules(
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  subject_type TEXT NOT NULL,    -- SHIPPER | CARRIER | VEHICLE | DRIVER
  applies_when TEXT,             -- JSON predicate (carrier_type/category/cargo/route/inter_island/port/hazardous)
  required_documents TEXT NOT NULL,  -- JSON list of document_type
  severity TEXT DEFAULT 'BLOCKER',   -- BLOCKER | WARNING
  active INTEGER DEFAULT 1,
  created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS mkt_verification_cases(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  case_type TEXT NOT NULL,       -- SHIPPER_VERIFICATION | CARRIER_VERIFICATION | ...
  subject_type TEXT,
  subject_id INTEGER,
  priority TEXT DEFAULT 'NORMAL',
  risk TEXT DEFAULT 'LOW',
  owner INTEGER,
  due_at TEXT,
  sla_minutes INTEGER,
  blockers TEXT,
  next_action TEXT,
  evidence_completeness REAL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'OPEN',   -- OPEN | IN_PROGRESS | RESOLVED | CANCELLED
  correlation_id TEXT,
  created_by INTEGER, created_at TEXT,
  updated_by INTEGER, updated_at TEXT);

CREATE TABLE IF NOT EXISTS mkt_compliance_overrides(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  subject_type TEXT NOT NULL,
  subject_id INTEGER NOT NULL,
  reason TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  approved_by INTEGER NOT NULL,
  created_at TEXT);
"""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _today():
    return datetime.date.today().isoformat()


def _cid():
    return core.correlation_id()


def _checksum(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _j(v):
    return json.dumps(v) if v is not None else None


def _pj(v, default=None):
    if not v:
        return default
    try:
        return json.loads(v)
    except Exception:
        return default


_PAN_RE = re.compile(r"\b\d{12,19}\b")
_SECRET_HINT_RE = re.compile(r"(password|otp|\bpin\b|cvv|secret|api[_-]?key|token)", re.IGNORECASE)


def _reject_raw_secret(field, value):
    """Guard: never persist a full payment credential / OTP / PIN / secret. Masked refs only."""
    if value is None:
        return value
    s = str(value)
    if _PAN_RE.search(s.replace(" ", "").replace("-", "")):
        raise ValueError(f"{field}: raw payment numbers must not be stored; use a masked reference")
    if _SECRET_HINT_RE.search(s):
        raise ValueError(f"{field}: secrets/credentials must not be stored; use a masked reference")
    return value


def init(conn):
    conn.executescript(SCHEMA)
    have = {row[1] for row in conn.execute("PRAGMA table_info(mkt_documents)").fetchall()}
    for column, declaration in {
        "verification_source": "TEXT", "review_note": "TEXT", "suspension_reason": "TEXT"
    }.items():
        if column not in have:
            conn.execute(f"ALTER TABLE mkt_documents ADD COLUMN {column} {declaration}")
    conn.commit()


def _dup_check(conn, actor, table, **cols):
    """Tenant-scoped duplicate guard. Needed because tenant.stamp assigns tenant_id AFTER insert
    (so the UNIQUE(tenant_id,...) DB constraint, with NULL-distinct semantics, can't catch it)."""
    at = tenant.actor_tenant(actor)
    where = " AND ".join(f"{k}=?" for k in cols)
    args = list(cols.values())
    if at is None:
        where += " AND tenant_id IS NULL"
    else:
        where += " AND (tenant_id=? OR tenant_id IS NULL)"
        args.append(at)
    if conn.execute(f"SELECT 1 FROM {table} WHERE {where}", args).fetchone():
        raise ValueError(f"duplicate {table[4:]} for {cols}")


# --------------------------------------------------------------------------- #
# generic participant transition helpers (shared by shipper + carrier)
# --------------------------------------------------------------------------- #
def _row(conn, table, id):
    r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (id,)).fetchone()
    if not r:
        raise core.NotFoundError(f"{table[4:]} not found")
    return dict(r)


def _guarded(conn, actor, table, id):
    row = _row(conn, table, id)
    tenant.guard(actor, row)   # 404 no-leak on cross-tenant
    return row


def _set(conn, table, id, **fields):
    fields["updated_at"] = _now()
    sets = ",".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*fields.values(), id))


# --------------------------------------------------------------------------- #
# Shipper onboarding
# --------------------------------------------------------------------------- #
def create_shipper_application(conn, actor, applicant_type, legal_name, **attrs):
    core.require(actor, "marketplace.shipper.application.manage")
    if applicant_type not in SHIPPER_TYPES:
        raise ValueError(f"invalid applicant_type: {applicant_type}")
    if attrs.get("registration_number"):
        _dup_check(conn, actor, "mkt_shippers", registration_type=attrs.get("registration_type"),
                   registration_number=attrs.get("registration_number"))
    _reject_raw_secret("payment_ref", attrs.get("payment_ref"))
    cur = conn.execute(
        "INSERT INTO mkt_shippers(applicant_type,legal_name,trade_name,registration_type,"
        "registration_number,tax_id,registered_address,billing_address,representatives,"
        "beneficial_owners,contacts,cargo_profile,expected_volume,preferred_lanes,preferred_vehicles,"
        "payment_ref,contract_accepted,privacy_accepted,risk_flags,status,created_by,created_at,"
        "correlation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'APPLICATION',?,?,?)",
        (applicant_type, legal_name, attrs.get("trade_name"), attrs.get("registration_type"),
         attrs.get("registration_number"), attrs.get("tax_id"), attrs.get("registered_address"),
         attrs.get("billing_address"), _j(attrs.get("representatives")), _j(attrs.get("beneficial_owners")),
         _j(attrs.get("contacts")), attrs.get("cargo_profile"), attrs.get("expected_volume"),
         _j(attrs.get("preferred_lanes")), _j(attrs.get("preferred_vehicles")), attrs.get("payment_ref"),
         int(bool(attrs.get("contract_accepted"))), int(bool(attrs.get("privacy_accepted"))),
         attrs.get("risk_flags"), actor["id"], _now(), _cid()))
    sid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_shippers", sid)
    core.audit(conn, actor, "MKT_SHIPPER_CREATED", "mkt_shippers", sid, None, {"legal_name": legal_name})
    conn.commit()
    # referral attribution through the canonical shipper-registration path (server = source of truth;
    # SHIPPER referred entity). Best-effort: a bad/expired code never blocks the registration.
    ref_code = str(attrs.get("referral_code", "") or "").strip()
    if ref_code:
        try:
            import referral as _rf
            if _rf.validate_code(conn, ref_code).get("valid"):
                _svc = {"id": actor.get("id"), "role": "system", "perms": {"*"},
                        "tenant_id": actor.get("tenant_id")}
                _rf.attribute(conn, _svc, ref_code, "SHIPPER", sid, referred_label=legal_name,
                              source="SHIPPER_REGISTRATION")
        except Exception:  # noqa: BLE001
            pass
    return sid


_SHIPPER_MANDATORY = ("legal_name", "registration_type", "registration_number", "registered_address")


def submit_shipper(conn, actor, shipper_id):
    core.require(actor, "marketplace.shipper.application.manage")
    row = _guarded(conn, actor, "mkt_shippers", shipper_id)
    missing = [f for f in _SHIPPER_MANDATORY if not row.get(f)]
    if not (row["contract_accepted"] and row["privacy_accepted"]):
        missing.append("contract_or_privacy_acceptance")
    if missing:
        _set(conn, "mkt_shippers", shipper_id, status="PROFILE_INCOMPLETE", updated_by=actor["id"])
        core.audit(conn, actor, "MKT_SHIPPER_INCOMPLETE", "mkt_shippers", shipper_id, None, {"missing": missing})
        conn.commit()
        return {"status": "PROFILE_INCOMPLETE", "missing": missing}
    _set(conn, "mkt_shippers", shipper_id, status="DOCUMENT_REVIEW", updated_by=actor["id"])
    _open_case(conn, actor, "SHIPPER_VERIFICATION", "SHIPPER", shipper_id, row.get("tenant_id"))
    core.audit(conn, actor, "MKT_SHIPPER_SUBMITTED", "mkt_shippers", shipper_id, None, {"status": "DOCUMENT_REVIEW"})
    conn.commit()
    return {"status": "DOCUMENT_REVIEW"}


def verify_shipper(conn, actor, shipper_id):
    core.require(actor, "marketplace.shipper.verify")
    row = _guarded(conn, actor, "mkt_shippers", shipper_id)
    if row["created_by"] == actor["id"]:
        raise PermissionError("no self-verification: the creator may not verify this application")
    if row["status"] not in ("DOCUMENT_REVIEW", "VERIFICATION_REQUIRED"):
        raise ValueError(f"cannot verify from status {row['status']}")
    _set(conn, "mkt_shippers", shipper_id, verified_by=actor["id"], verified_at=_now(),
         status="APPROVED", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_SHIPPER_VERIFIED", "mkt_shippers", shipper_id, {"status": row["status"]}, {"status": "APPROVED"})
    conn.commit()
    return {"status": "APPROVED"}


def conditionally_approve_shipper(conn, actor, shipper_id, note=None):
    core.require(actor, "marketplace.shipper.verify")
    row = _guarded(conn, actor, "mkt_shippers", shipper_id)
    if row["created_by"] == actor["id"]:
        raise PermissionError("no self-verification")
    _set(conn, "mkt_shippers", shipper_id, status="CONDITIONALLY_APPROVED", verified_by=actor["id"],
         verified_at=_now(), updated_by=actor["id"])
    core.audit(conn, actor, "MKT_SHIPPER_COND_APPROVED", "mkt_shippers", shipper_id, None, {"note": note})
    conn.commit()
    return {"status": "CONDITIONALLY_APPROVED"}


def request_shipper_info(conn, actor, shipper_id, note=None):
    core.require(actor, "marketplace.shipper.verify")
    _guarded(conn, actor, "mkt_shippers", shipper_id)
    _set(conn, "mkt_shippers", shipper_id, status="VERIFICATION_REQUIRED", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_SHIPPER_INFO_REQUESTED", "mkt_shippers", shipper_id, None, {"note": note})
    conn.commit()
    return {"status": "VERIFICATION_REQUIRED"}


def activate_shipper(conn, actor, shipper_id):
    core.require(actor, "marketplace.shipper.activate")
    row = _guarded(conn, actor, "mkt_shippers", shipper_id)
    _fail_closed_activation(conn, row, "SHIPPER", shipper_id, actor)
    _set(conn, "mkt_shippers", shipper_id, status="ACTIVE", activated_by=actor["id"],
         activated_at=_now(), updated_by=actor["id"])
    core.audit(conn, actor, "MKT_SHIPPER_ACTIVATED", "mkt_shippers", shipper_id, {"status": row["status"]}, {"status": "ACTIVE"})
    conn.commit()
    return {"status": "ACTIVE"}


def suspend_shipper(conn, actor, shipper_id, reason):
    core.require(actor, "marketplace.shipper.suspend")
    _guarded(conn, actor, "mkt_shippers", shipper_id)
    _set(conn, "mkt_shippers", shipper_id, status="SUSPENDED", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_SHIPPER_SUSPENDED", "mkt_shippers", shipper_id, None, {"reason": reason})
    conn.commit()
    return {"status": "SUSPENDED"}


def reactivate_shipper(conn, actor, shipper_id):
    core.require(actor, "marketplace.shipper.activate")
    row = _guarded(conn, actor, "mkt_shippers", shipper_id)
    if row["status"] != "SUSPENDED":
        raise ValueError("only a suspended shipper can be reactivated")
    _fail_closed_activation(conn, row, "SHIPPER", shipper_id, actor)
    _set(conn, "mkt_shippers", shipper_id, status="ACTIVE", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_SHIPPER_REACTIVATED", "mkt_shippers", shipper_id, None, {"status": "ACTIVE"})
    conn.commit()
    return {"status": "ACTIVE"}


def reject_shipper(conn, actor, shipper_id, reason):
    core.require(actor, "marketplace.shipper.verify")
    _guarded(conn, actor, "mkt_shippers", shipper_id)
    _set(conn, "mkt_shippers", shipper_id, status="REJECTED", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_SHIPPER_REJECTED", "mkt_shippers", shipper_id, None, {"reason": reason})
    conn.commit()
    return {"status": "REJECTED"}


def list_shippers(conn, actor, status=None):
    core.require(actor, "marketplace.shipper.application.view")
    frag, args = tenant.predicate(actor)
    q = "SELECT * FROM mkt_shippers WHERE 1=1" + frag
    a = list(args)
    if status:
        q += " AND status=?"; a.append(status)
    q += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


# --------------------------------------------------------------------------- #
# Carrier onboarding (parallels shipper; carrier-specific fields)
# --------------------------------------------------------------------------- #
def create_carrier_application(conn, actor, carrier_type, legal_name, **attrs):
    core.require(actor, "marketplace.carrier.application.manage")
    if carrier_type not in CARRIER_TYPES:
        raise ValueError(f"invalid carrier_type: {carrier_type}")
    if attrs.get("registration_number"):
        _dup_check(conn, actor, "mkt_carriers", registration_type=attrs.get("registration_type"),
                   registration_number=attrs.get("registration_number"))
    _reject_raw_secret("payout_provider_ref", attrs.get("payout_provider_ref"))
    cur = conn.execute(
        "INSERT INTO mkt_carriers(carrier_type,legal_name,trade_name,registration_type,"
        "registration_number,tax_id,beneficial_owners,representatives,operating_address,service_areas,"
        "preferred_lanes,port_experience,cargo_capabilities,vehicle_categories,fleet_size,insurance_ref,"
        "permits,operating_hours,payout_provider_ref,risk_status,support_owner,status,created_by,"
        "created_at,correlation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'APPLICATION',?,?,?)",
        (carrier_type, legal_name, attrs.get("trade_name"), attrs.get("registration_type"),
         attrs.get("registration_number"), attrs.get("tax_id"), _j(attrs.get("beneficial_owners")),
         _j(attrs.get("representatives")), attrs.get("operating_address"), _j(attrs.get("service_areas")),
         _j(attrs.get("preferred_lanes")), attrs.get("port_experience"), _j(attrs.get("cargo_capabilities")),
         _j(attrs.get("vehicle_categories")), attrs.get("fleet_size"), attrs.get("insurance_ref"),
         attrs.get("permits"), attrs.get("operating_hours"), attrs.get("payout_provider_ref"),
         attrs.get("risk_status"), attrs.get("support_owner"), actor["id"], _now(), _cid()))
    cid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_carriers", cid)
    core.audit(conn, actor, "MKT_CARRIER_CREATED", "mkt_carriers", cid, None, {"legal_name": legal_name})
    conn.commit()
    return cid


_CARRIER_MANDATORY = ("legal_name", "registration_type", "registration_number", "operating_address")


def submit_carrier(conn, actor, carrier_id):
    core.require(actor, "marketplace.carrier.application.manage")
    row = _guarded(conn, actor, "mkt_carriers", carrier_id)
    missing = [f for f in _CARRIER_MANDATORY if not row.get(f)]
    if missing:
        _set(conn, "mkt_carriers", carrier_id, status="PROFILE_INCOMPLETE", updated_by=actor["id"])
        core.audit(conn, actor, "MKT_CARRIER_INCOMPLETE", "mkt_carriers", carrier_id, None, {"missing": missing})
        conn.commit()
        return {"status": "PROFILE_INCOMPLETE", "missing": missing}
    _set(conn, "mkt_carriers", carrier_id, status="DOCUMENT_REVIEW", updated_by=actor["id"])
    _open_case(conn, actor, "CARRIER_VERIFICATION", "CARRIER", carrier_id, row.get("tenant_id"))
    core.audit(conn, actor, "MKT_CARRIER_SUBMITTED", "mkt_carriers", carrier_id, None, {"status": "DOCUMENT_REVIEW"})
    conn.commit()
    return {"status": "DOCUMENT_REVIEW"}


def verify_carrier(conn, actor, carrier_id):
    core.require(actor, "marketplace.carrier.verify")
    row = _guarded(conn, actor, "mkt_carriers", carrier_id)
    if row["created_by"] == actor["id"]:
        raise PermissionError("no self-verification")
    if row["status"] not in ("DOCUMENT_REVIEW", "VERIFICATION_REQUIRED"):
        raise ValueError(f"cannot verify from status {row['status']}")
    _set(conn, "mkt_carriers", carrier_id, verified_by=actor["id"], verified_at=_now(),
         status="APPROVED", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_CARRIER_VERIFIED", "mkt_carriers", carrier_id, {"status": row["status"]}, {"status": "APPROVED"})
    conn.commit()
    return {"status": "APPROVED"}


def conditionally_approve_carrier(conn, actor, carrier_id, note=None):
    core.require(actor, "marketplace.carrier.verify")
    row = _guarded(conn, actor, "mkt_carriers", carrier_id)
    if row["created_by"] == actor["id"]:
        raise PermissionError("no self-verification")
    _set(conn, "mkt_carriers", carrier_id, status="CONDITIONALLY_APPROVED", verified_by=actor["id"],
         verified_at=_now(), updated_by=actor["id"])
    core.audit(conn, actor, "MKT_CARRIER_COND_APPROVED", "mkt_carriers", carrier_id, None, {"note": note})
    conn.commit()
    return {"status": "CONDITIONALLY_APPROVED"}


def activate_carrier(conn, actor, carrier_id):
    core.require(actor, "marketplace.carrier.activate")
    row = _guarded(conn, actor, "mkt_carriers", carrier_id)
    _fail_closed_activation(conn, row, "CARRIER", carrier_id, actor)
    _set(conn, "mkt_carriers", carrier_id, status="ACTIVE", activated_by=actor["id"],
         activated_at=_now(), updated_by=actor["id"])
    core.audit(conn, actor, "MKT_CARRIER_ACTIVATED", "mkt_carriers", carrier_id, {"status": row["status"]}, {"status": "ACTIVE"})
    conn.commit()
    return {"status": "ACTIVE"}


def suspend_carrier(conn, actor, carrier_id, reason):
    core.require(actor, "marketplace.carrier.suspend")
    _guarded(conn, actor, "mkt_carriers", carrier_id)
    _set(conn, "mkt_carriers", carrier_id, status="SUSPENDED", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_CARRIER_SUSPENDED", "mkt_carriers", carrier_id, None, {"reason": reason})
    conn.commit()
    return {"status": "SUSPENDED"}


def reject_carrier(conn, actor, carrier_id, reason):
    core.require(actor, "marketplace.carrier.verify")
    _guarded(conn, actor, "mkt_carriers", carrier_id)
    _set(conn, "mkt_carriers", carrier_id, status="REJECTED", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_CARRIER_REJECTED", "mkt_carriers", carrier_id, None, {"reason": reason})
    conn.commit()
    return {"status": "REJECTED"}


def list_carriers(conn, actor, status=None):
    core.require(actor, "marketplace.carrier.application.view")
    frag, args = tenant.predicate(actor)
    q = "SELECT * FROM mkt_carriers WHERE 1=1" + frag
    a = list(args)
    if status:
        q += " AND status=?"; a.append(status)
    q += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


# --------------------------------------------------------------------------- #
# fail-closed activation: mandatory compliance evidence must be complete
# --------------------------------------------------------------------------- #
def _fail_closed_activation(conn, row, subject_type, subject_id, actor):
    # 1) verification must have happened, and not by the activator (no self-activation)
    if not row.get("verified_by"):
        raise ValueError("activation blocked: participant is not verified")
    if row["verified_by"] == actor["id"]:
        raise PermissionError("no self-activation: the verifier may not also activate")
    # 2) compliance recommendation must be READY (or a valid, unexpired override exists)
    ev = evaluate_compliance(conn, subject_type, subject_id)
    if ev["recommendation"] != "READY" and not _has_active_override(conn, subject_type, subject_id):
        raise ValueError(f"activation fails closed: compliance not satisfied ({ev['blockers']})")


# --------------------------------------------------------------------------- #
# Vehicle onboarding (linked to the governed taxonomy)
# --------------------------------------------------------------------------- #
def register_vehicle(conn, actor, carrier_id, category_code, plate_number, **attrs):
    core.require(actor, "marketplace.vehicle.manage")
    carrier = _guarded(conn, actor, "mkt_carriers", carrier_id)   # cross-tenant carrier -> 404
    cat = conn.execute("SELECT * FROM mkt_vehicle_categories WHERE code=?", (category_code,)).fetchone()
    if not cat:
        raise ValueError(f"unknown vehicle category '{category_code}'")
    cat = dict(cat)
    _dup_check(conn, actor, "mkt_vehicles", plate_number=plate_number)
    payload = attrs.get("payload_kg", cat["payload_kg"])
    # payload sanity vs governed category (cannot exceed the category envelope by >20%)
    if payload is not None and cat["payload_kg"] and payload > cat["payload_kg"] * 1.2:
        raise ValueError("payload inconsistent with vehicle category envelope")
    cur = conn.execute(
        "INSERT INTO mkt_vehicles(carrier_id,category_code,registration_number,plate_number,ownership_type,"
        "owner_name,operating_authority,body_type,payload_kg,volume_cbm,length_cm,width_cm,height_cm,"
        "opening_length_cm,opening_width_cm,opening_height_cm,axle_config,refrigerated,lifting_capable,"
        "route_restrictions,port_eligible,current_location,status,created_by,created_at,correlation_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'DRAFT',?,?,?)",
        (carrier_id, category_code, attrs.get("registration_number"), plate_number,
         attrs.get("ownership_type"), attrs.get("owner_name"), attrs.get("operating_authority"),
         attrs.get("body_type", cat["body_type"]), payload, attrs.get("volume_cbm", cat["volume_cbm"]),
         attrs.get("length_cm"), attrs.get("width_cm"), attrs.get("height_cm"),
         attrs.get("opening_length_cm", cat["opening_length_cm"]),
         attrs.get("opening_width_cm", cat["opening_width_cm"]),
         attrs.get("opening_height_cm", cat["opening_height_cm"]), attrs.get("axle_config", cat["axle_config"]),
         int(attrs.get("refrigerated", cat["refrigerated"]) or 0),
         int(attrs.get("lifting_capable", cat["lifting_capable"]) or 0), attrs.get("route_restrictions"),
         int(attrs.get("port_eligible", cat["port_eligible"]) or 0), attrs.get("current_location"),
         actor["id"], _now(), _cid()))
    vid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_vehicles", vid)
    core.audit(conn, actor, "MKT_VEHICLE_REGISTERED", "mkt_vehicles", vid, None, {"plate": plate_number})
    conn.commit()
    return vid


def set_vehicle_status(conn, actor, vehicle_id, status, reason=None):
    core.require(actor, "marketplace.vehicle.manage")
    if status not in VEHICLE_STATUSES:
        raise ValueError(f"invalid vehicle status: {status}")
    row = _guarded(conn, actor, "mkt_vehicles", vehicle_id)
    _set(conn, "mkt_vehicles", vehicle_id, status=status, updated_by=actor["id"])
    core.audit(conn, actor, "MKT_VEHICLE_STATUS", "mkt_vehicles", vehicle_id, {"status": row["status"]}, {"status": status, "reason": reason})
    conn.commit()
    return {"status": status}


def verify_vehicle(conn, actor, vehicle_id):
    core.require(actor, "marketplace.vehicle.verify")
    row = _guarded(conn, actor, "mkt_vehicles", vehicle_id)
    if row["created_by"] == actor["id"]:
        raise PermissionError("no self-verification")
    _set(conn, "mkt_vehicles", vehicle_id, verified_by=actor["id"], verified_at=_now(),
         status="APPROVED", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_VEHICLE_VERIFIED", "mkt_vehicles", vehicle_id, None, {"status": "APPROVED"})
    conn.commit()
    return {"status": "APPROVED"}


def activate_vehicle(conn, actor, vehicle_id):
    core.require(actor, "marketplace.vehicle.activate")
    row = _guarded(conn, actor, "mkt_vehicles", vehicle_id)
    if not row.get("verified_by"):
        raise ValueError("activation blocked: vehicle not verified")
    if row["verified_by"] == actor["id"]:
        raise PermissionError("no self-activation")
    ev = evaluate_compliance(conn, "VEHICLE", vehicle_id)
    if ev["recommendation"] != "READY" and not _has_active_override(conn, "VEHICLE", vehicle_id):
        raise ValueError(f"activation fails closed: {ev['blockers']}")
    _set(conn, "mkt_vehicles", vehicle_id, status="ACTIVE", activated_by=actor["id"],
         activated_at=_now(), updated_by=actor["id"])
    core.audit(conn, actor, "MKT_VEHICLE_ACTIVATED", "mkt_vehicles", vehicle_id, None, {"status": "ACTIVE"})
    conn.commit()
    return {"status": "ACTIVE"}


def list_vehicles(conn, actor, carrier_id=None, status=None):
    core.require(actor, "marketplace.vehicle.view")
    frag, args = tenant.predicate(actor)
    q = "SELECT * FROM mkt_vehicles WHERE 1=1" + frag
    a = list(args)
    if carrier_id:
        q += " AND carrier_id=?"; a.append(carrier_id)
    if status:
        q += " AND status=?"; a.append(status)
    q += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


# --------------------------------------------------------------------------- #
# Driver onboarding + assignment guard
# --------------------------------------------------------------------------- #
def register_driver(conn, actor, carrier_id, full_name, **attrs):
    core.require(actor, "marketplace.driver.manage")
    _guarded(conn, actor, "mkt_carriers", carrier_id)
    cur = conn.execute(
        "INSERT INTO mkt_drivers(carrier_id,full_name,identity_ref,licence_number,licence_class,"
        "licence_expiry,authorized_categories,training,safety_qualifications,route_experience,"
        "port_experience,emergency_contact,status,created_by,created_at,correlation_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'APPLICATION',?,?,?)",
        (carrier_id, full_name, attrs.get("identity_ref"), attrs.get("licence_number"),
         attrs.get("licence_class"), attrs.get("licence_expiry"), _j(attrs.get("authorized_categories")),
         attrs.get("training"), attrs.get("safety_qualifications"), attrs.get("route_experience"),
         attrs.get("port_experience"), attrs.get("emergency_contact"), actor["id"], _now(), _cid()))
    did = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_drivers", did)
    core.audit(conn, actor, "MKT_DRIVER_REGISTERED", "mkt_drivers", did, None, {"name": full_name})
    conn.commit()
    return did


def verify_driver(conn, actor, driver_id):
    core.require(actor, "marketplace.driver.verify")
    row = _guarded(conn, actor, "mkt_drivers", driver_id)
    if row["created_by"] == actor["id"]:
        raise PermissionError("no self-verification")
    _set(conn, "mkt_drivers", driver_id, verified_by=actor["id"], verified_at=_now(),
         status="VERIFIED", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_DRIVER_VERIFIED", "mkt_drivers", driver_id, None, {"status": "VERIFIED"})
    conn.commit()
    return {"status": "VERIFIED"}


def activate_driver(conn, actor, driver_id):
    core.require(actor, "marketplace.driver.activate")
    row = _guarded(conn, actor, "mkt_drivers", driver_id)
    if row["status"] != "VERIFIED":
        raise ValueError("driver must be VERIFIED before activation")
    if row["verified_by"] == actor["id"]:
        raise PermissionError("no self-activation")
    _set(conn, "mkt_drivers", driver_id, status="ACTIVE", activated_by=actor["id"],
         activated_at=_now(), updated_by=actor["id"])
    core.audit(conn, actor, "MKT_DRIVER_ACTIVATED", "mkt_drivers", driver_id, None, {"status": "ACTIVE"})
    conn.commit()
    return {"status": "ACTIVE"}


def set_driver_status(conn, actor, driver_id, status, reason=None):
    core.require(actor, "marketplace.driver.manage")
    if status not in DRIVER_STATUSES:
        raise ValueError(f"invalid driver status: {status}")
    _guarded(conn, actor, "mkt_drivers", driver_id)
    _set(conn, "mkt_drivers", driver_id, status=status, updated_by=actor["id"])
    core.audit(conn, actor, "MKT_DRIVER_STATUS", "mkt_drivers", driver_id, None, {"status": status, "reason": reason})
    conn.commit()
    return {"status": status}


def can_assign_driver(conn, driver_id, vehicle_id):
    """Deterministic assignment guard (blueprint §5). Returns {ok, reasons}."""
    d = conn.execute("SELECT * FROM mkt_drivers WHERE id=?", (driver_id,)).fetchone()
    v = conn.execute("SELECT * FROM mkt_vehicles WHERE id=?", (vehicle_id,)).fetchone()
    if not d or not v:
        return {"ok": False, "reasons": ["unknown_driver_or_vehicle"]}
    d, v = dict(d), dict(v)
    reasons = []
    if d["status"] != "ACTIVE":
        reasons.append("driver_not_active")
    if d.get("licence_expiry") and d["licence_expiry"] < _today():
        reasons.append("licence_expired")
    if d["carrier_id"] != v["carrier_id"]:
        reasons.append("carrier_mismatch")
    if d.get("tenant_id") is not None and v.get("tenant_id") is not None and d["tenant_id"] != v["tenant_id"]:
        reasons.append("tenant_scope_differs")
    authz = _pj(d.get("authorized_categories"), []) or []
    if authz and v["category_code"] not in authz:
        reasons.append("vehicle_not_authorized")
    return {"ok": not reasons, "reasons": reasons}


def list_drivers(conn, actor, carrier_id=None):
    core.require(actor, "marketplace.driver.view")
    frag, args = tenant.predicate(actor)
    q = "SELECT * FROM mkt_drivers WHERE 1=1" + frag
    a = list(args)
    if carrier_id:
        q += " AND carrier_id=?"; a.append(carrier_id)
    q += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


# --------------------------------------------------------------------------- #
# Document + expiry engine
# --------------------------------------------------------------------------- #
def upload_document(conn, actor, document_type, subject_type, subject_id, **attrs):
    core.require(actor, "marketplace.compliance.manage")
    if subject_type not in ("SHIPPER", "CARRIER", "VEHICLE", "DRIVER"):
        raise ValueError("invalid subject_type")
    _guarded(conn, actor, f"mkt_{subject_type.lower()}s", subject_id)
    cs = _checksum({"type": document_type, "number": attrs.get("document_number"),
                    "storage_ref": attrs.get("storage_ref")})
    cur = conn.execute(
        "INSERT INTO mkt_documents(document_type,subject_type,subject_id,issuing_authority,"
        "document_number,issue_date,expiry_date,status,storage_ref,checksum,created_by,created_at,"
        "correlation_id) VALUES(?,?,?,?,?,?,?,'UPLOADED',?,?,?,?,?)",
        (document_type, subject_type, subject_id, attrs.get("issuing_authority"),
         attrs.get("document_number"), attrs.get("issue_date"), attrs.get("expiry_date"),
         attrs.get("storage_ref"), cs, actor["id"], _now(), _cid()))
    doc_id = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_documents", doc_id)
    core.audit(conn, actor, "MKT_DOC_UPLOADED", "mkt_documents", doc_id, None, {"type": document_type})
    conn.commit()
    return doc_id


def mark_document_under_review(conn, actor, doc_id, note=None):
    core.require(actor, "marketplace.compliance.verify")
    row = _guarded(conn, actor, "mkt_documents", doc_id)
    if row["status"] not in ("UPLOADED", "PENDING_REVIEW", "RENEWAL_REQUIRED"):
        raise ValueError("only a submitted or renewal document can enter review")
    _set(conn, "mkt_documents", doc_id, status="PENDING_REVIEW", review_note=note,
         updated_by=actor["id"])
    core.audit(conn, actor, "MKT_DOC_REVIEW_STARTED", "mkt_documents", doc_id,
               {"status": row["status"]}, {"status": "PENDING_REVIEW", "note": note})
    conn.commit()
    return {"status": "PENDING_REVIEW", "public_status": "UNDER_REVIEW"}


def verify_document(conn, actor, doc_id, source=None, note=None):
    core.require(actor, "marketplace.compliance.verify")
    row = _guarded(conn, actor, "mkt_documents", doc_id)
    if row["created_by"] == actor["id"]:
        raise PermissionError("no self-verification of documents")
    verification_source = str(source or "MANUAL_DOCUMENT_REVIEW")[:300]
    _set(conn, "mkt_documents", doc_id, status="VERIFIED", verified_by=actor["id"], verified_at=_now(),
         verification_source=verification_source, review_note=note, suspension_reason=None,
         updated_by=actor["id"])
    core.audit(conn, actor, "MKT_DOC_VERIFIED", "mkt_documents", doc_id, {"status": row["status"]},
               {"status": "VERIFIED", "source": verification_source, "note": note})
    conn.commit()
    return {"status": "VERIFIED"}


def reject_document(conn, actor, doc_id, reason):
    core.require(actor, "marketplace.compliance.verify")
    _guarded(conn, actor, "mkt_documents", doc_id)
    _set(conn, "mkt_documents", doc_id, status="REJECTED", rejection_reason=reason, updated_by=actor["id"])
    core.audit(conn, actor, "MKT_DOC_REJECTED", "mkt_documents", doc_id, None, {"reason": reason})
    conn.commit()
    return {"status": "REJECTED"}


def suspend_document(conn, actor, doc_id, reason):
    core.require(actor, "marketplace.compliance.verify")
    if not str(reason or "").strip():
        raise ValueError("document suspension requires a reason")
    row = _guarded(conn, actor, "mkt_documents", doc_id)
    _set(conn, "mkt_documents", doc_id, status="SUSPENDED", suspension_reason=reason,
         updated_by=actor["id"])
    core.audit(conn, actor, "MKT_DOC_SUSPENDED", "mkt_documents", doc_id,
               {"status": row["status"]}, {"status": "SUSPENDED", "reason": reason})
    conn.commit()
    return {"status": "SUSPENDED"}


def require_document_renewal(conn, actor, doc_id, reason):
    core.require(actor, "marketplace.compliance.verify")
    row = _guarded(conn, actor, "mkt_documents", doc_id)
    _set(conn, "mkt_documents", doc_id, status="RENEWAL_REQUIRED", review_note=reason,
         updated_by=actor["id"])
    core.audit(conn, actor, "MKT_DOC_RENEWAL_REQUIRED", "mkt_documents", doc_id,
               {"status": row["status"]}, {"status": "RENEWAL_REQUIRED", "reason": reason})
    conn.commit()
    return {"status": "RENEWAL_REQUIRED"}


def detect_expired_documents(conn, actor=None, as_of=None):
    """Scheduled expiry detection. Flips VERIFIED docs past expiry to EXPIRED (history preserved).
    Affected active carriers/vehicles/drivers are removed from ELIGIBILITY via the compliance gate
    at candidate-pool time — we do NOT delete anything."""
    as_of = as_of or _today()
    rows = conn.execute("SELECT id,subject_type,subject_id,document_type FROM mkt_documents "
                        "WHERE status='VERIFIED' AND expiry_date IS NOT NULL AND expiry_date < ?",
                        (as_of,)).fetchall()
    expired = []
    for r in rows:
        conn.execute("UPDATE mkt_documents SET status='EXPIRED',updated_at=? WHERE id=?", (_now(), r["id"]))
        expired.append({"doc_id": r["id"], "subject_type": r["subject_type"], "subject_id": r["subject_id"],
                        "document_type": r["document_type"]})
        if actor:
            core.audit(conn, actor, "MKT_DOC_EXPIRED", "mkt_documents", r["id"], None, {"as_of": as_of})
    conn.commit()
    return {"expired": expired, "count": len(expired)}


def list_documents(conn, actor, subject_type=None, subject_id=None, expiring_before=None):
    core.require(actor, "marketplace.compliance.view")
    frag, args = tenant.predicate(actor)
    q = "SELECT * FROM mkt_documents WHERE 1=1" + frag
    a = list(args)
    if subject_type:
        q += " AND subject_type=?"; a.append(subject_type)
    if subject_id:
        q += " AND subject_id=?"; a.append(subject_id)
    if expiring_before:
        q += " AND expiry_date IS NOT NULL AND expiry_date < ?"; a.append(expiring_before)
    q += " ORDER BY expiry_date ASC NULLS LAST" if False else " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


# --------------------------------------------------------------------------- #
# Compliance rules engine (declarative; never makes the final decision)
# --------------------------------------------------------------------------- #
def define_rule(conn, actor, code, subject_type, required_documents, applies_when=None, severity="BLOCKER"):
    core.require(actor, "marketplace.compliance.manage")
    if subject_type not in ("SHIPPER", "CARRIER", "VEHICLE", "DRIVER"):
        raise ValueError("invalid subject_type")
    conn.execute(
        "INSERT INTO mkt_compliance_rules(code,subject_type,applies_when,required_documents,severity,"
        "active,created_by,created_at) VALUES(?,?,?,?,?,1,?,?)",
        (code, subject_type, _j(applies_when), _j(list(required_documents)), severity, actor["id"], _now()))
    core.audit(conn, actor, "MKT_RULE_DEFINED", "mkt_compliance_rules", 0, None, {"code": code})
    conn.commit()


def _subject_context(conn, subject_type, subject_id):
    row = conn.execute(f"SELECT * FROM mkt_{subject_type.lower()}s WHERE id=?", (subject_id,)).fetchone()
    return dict(row) if row else {}


def _rule_applies(rule, ctx):
    pred = _pj(rule.get("applies_when"))
    if not pred:
        return True
    for k, v in pred.items():
        cv = ctx.get(k)
        if isinstance(v, list):
            if cv not in v:
                return False
        elif cv != v:
            return False
    return True


def evaluate_compliance(conn, subject_type, subject_id):
    """Return required/provided/missing/expired/rejected/blockers/warnings + recommendation.
    NEVER makes the final activation decision — a human approver does (blueprint §7)."""
    ctx = _subject_context(conn, subject_type, subject_id)
    rules = [dict(r) for r in conn.execute(
        "SELECT * FROM mkt_compliance_rules WHERE subject_type=? AND active=1", (subject_type,)).fetchall()]
    docs = [dict(r) for r in conn.execute(
        "SELECT * FROM mkt_documents WHERE subject_type=? AND subject_id=?", (subject_type, subject_id)).fetchall()]
    by_type = {}
    for d in docs:
        by_type.setdefault(d["document_type"], []).append(d)

    required, provided, missing, expired, rejected, blockers, warnings = [], [], [], [], [], [], []
    for rule in rules:
        if not _rule_applies(rule, ctx):
            continue
        for dt in _pj(rule["required_documents"], []):
            required.append(dt)
            ds = by_type.get(dt, [])
            verified = [d for d in ds if d["status"] == "VERIFIED"]
            if verified:
                provided.append(dt)
            elif any(d["status"] == "EXPIRED" for d in ds):
                expired.append(dt)
                (blockers if rule["severity"] == "BLOCKER" else warnings).append(f"{dt}:expired")
            elif any(d["status"] in ("REJECTED", "SUSPENDED", "RENEWAL_REQUIRED", "REVOKED") for d in ds):
                rejected.append(dt)
                blocked_status = next(d["status"].lower() for d in ds
                                      if d["status"] in ("REJECTED", "SUSPENDED", "RENEWAL_REQUIRED", "REVOKED"))
                (blockers if rule["severity"] == "BLOCKER" else warnings).append(f"{dt}:{blocked_status}")
            else:
                missing.append(dt)
                (blockers if rule["severity"] == "BLOCKER" else warnings).append(f"{dt}:missing")

    recommendation = "READY" if not blockers else "NOT_READY"
    return {"subject_type": subject_type, "subject_id": subject_id,
            "required": sorted(set(required)), "provided": sorted(set(provided)),
            "missing": sorted(set(missing)), "expired": sorted(set(expired)),
            "rejected": sorted(set(rejected)), "blockers": blockers, "warnings": warnings,
            "recommendation": recommendation}


def override_compliance(conn, actor, subject_type, subject_id, reason, expires_at):
    """High-risk governed override. MUST carry a reason and an expiry (blueprint §11)."""
    core.require(actor, "marketplace.compliance.override")
    if not reason or not expires_at:
        raise ValueError("compliance override requires a reason and an expiry")
    row = conn.execute(f"SELECT created_by FROM mkt_{subject_type.lower()}s WHERE id=?", (subject_id,)).fetchone()
    if row and row["created_by"] == actor["id"]:
        raise PermissionError("no self-override of your own application")
    cur = conn.execute(
        "INSERT INTO mkt_compliance_overrides(subject_type,subject_id,reason,expires_at,approved_by,"
        "created_at) VALUES(?,?,?,?,?,?)", (subject_type, subject_id, reason, expires_at, actor["id"], _now()))
    oid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_compliance_overrides", oid)
    core.audit(conn, actor, "MKT_COMPLIANCE_OVERRIDE", "mkt_compliance_overrides", oid, None,
               {"subject": f"{subject_type}:{subject_id}", "expires_at": expires_at, "reason": reason})
    conn.commit()
    return oid


def _has_active_override(conn, subject_type, subject_id, as_of=None):
    as_of = as_of or _now()
    r = conn.execute("SELECT 1 FROM mkt_compliance_overrides WHERE subject_type=? AND subject_id=? "
                     "AND expires_at > ?", (subject_type, subject_id, as_of)).fetchone()
    return bool(r)


# --------------------------------------------------------------------------- #
# Verification work queues (reuse the concept; lightweight cases referencing SLA)
# --------------------------------------------------------------------------- #
def _open_case(conn, actor, case_type, subject_type, subject_id, tenant_id, priority="NORMAL",
               risk="LOW", sla_minutes=2880):
    due = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=sla_minutes)).isoformat()
    cur = conn.execute(
        "INSERT INTO mkt_verification_cases(tenant_id,case_type,subject_type,subject_id,priority,risk,"
        "owner,due_at,sla_minutes,next_action,status,correlation_id,created_by,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,?)",
        (tenant_id, case_type, subject_type, subject_id, priority, risk, None, due, sla_minutes,
         "verify_evidence", _cid(), actor["id"], _now()))
    return cur.lastrowid


def assign_case(conn, actor, case_id, owner):
    core.require(actor, "marketplace.compliance.manage")
    _set(conn, "mkt_verification_cases", case_id, owner=owner, status="IN_PROGRESS", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_CASE_ASSIGNED", "mkt_verification_cases", case_id, None, {"owner": owner})
    conn.commit()


def resolve_case(conn, actor, case_id, note=None):
    core.require(actor, "marketplace.compliance.manage")
    _set(conn, "mkt_verification_cases", case_id, status="RESOLVED", next_action=note, updated_by=actor["id"])
    core.audit(conn, actor, "MKT_CASE_RESOLVED", "mkt_verification_cases", case_id, None, {"note": note})
    conn.commit()


def list_cases(conn, actor, case_type=None, status=None):
    core.require(actor, "marketplace.compliance.view")
    frag, args = tenant.predicate(actor)
    q = "SELECT * FROM mkt_verification_cases WHERE 1=1" + frag
    a = list(args)
    if case_type:
        q += " AND case_type=?"; a.append(case_type)
    if status:
        q += " AND status=?"; a.append(status)
    q += " ORDER BY due_at ASC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


# --------------------------------------------------------------------------- #
# COMPLIANCE-AWARE candidate pool (hard denials cannot be overridden by ranking)
# --------------------------------------------------------------------------- #
def candidate_pool(conn, actor, cargo_code, origin_zone, dest_zone, weight_kg=None,
                   volume_cbm=None, dims=None, require_driver=True):
    """Returns {candidates:[...], excluded:[...]} for a booking. A carrier/vehicle/driver reaches the
    pool ONLY when every hard control passes. AI/manual ranking may reorder `candidates`, never widen
    it — hard exclusions are returned with explicit reasons."""
    core.require(actor, "marketplace.eligibility.test")
    lane = mkt.serviceability(conn, origin_zone, dest_zone)
    lane_ok = lane["promises_service"]
    cargo = mkt.get_cargo_type(conn, cargo_code)
    candidates, excluded = [], []
    frag, args = tenant.predicate(actor)
    vehicles = conn.execute("SELECT * FROM mkt_vehicles WHERE 1=1" + frag, args).fetchall()
    for v in vehicles:
        v = dict(v)
        reasons = []
        carrier = conn.execute("SELECT * FROM mkt_carriers WHERE id=?", (v["carrier_id"],)).fetchone()
        carrier = dict(carrier) if carrier else {}
        # hard controls
        if not carrier or carrier.get("status") != "ACTIVE":
            reasons.append("carrier_not_active")
        elif evaluate_compliance(conn, "CARRIER", carrier["id"])["recommendation"] != "READY" \
                and not _has_active_override(conn, "CARRIER", carrier["id"]):
            reasons.append("carrier_compliance_failed")
        if v["status"] != VEHICLE_ELIGIBLE_STATUS:
            reasons.append("vehicle_not_active")
        elif evaluate_compliance(conn, "VEHICLE", v["id"])["recommendation"] != "READY" \
                and not _has_active_override(conn, "VEHICLE", v["id"]):
            reasons.append("vehicle_compliance_failed")
        if not lane_ok:
            reasons.append("lane_not_serviceable")
        # cargo/vehicle capability via the deterministic foundation
        elig = mkt.is_vehicle_eligible(conn, cargo_code, v["category_code"], weight_kg, volume_cbm, dims)
        if not elig["eligible"]:
            reasons.extend(elig["reasons"])
        # driver requirement
        driver = None
        if require_driver:
            drivers = conn.execute("SELECT * FROM mkt_drivers WHERE carrier_id=? AND status='ACTIVE'",
                                   (v["carrier_id"],)).fetchall()
            usable = [dict(d) for d in drivers if can_assign_driver(conn, d["id"], v["id"])["ok"]]
            if not usable:
                reasons.append("no_eligible_driver")
            else:
                driver = usable[0]
        if reasons:
            excluded.append({"vehicle_id": v["id"], "plate": v["plate_number"], "reasons": reasons})
        else:
            candidates.append({"vehicle_id": v["id"], "carrier_id": v["carrier_id"],
                               "category_code": v["category_code"], "plate": v["plate_number"],
                               "driver_id": driver["id"] if driver else None})
    return {"cargo": cargo_code, "lane": f"{origin_zone}->{dest_zone}", "lane_serviceable": lane_ok,
            "candidates": candidates, "excluded": excluded}


# --------------------------------------------------------------------------- #
# Integrity checks
# --------------------------------------------------------------------------- #
def _count(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()[0]


def run_integrity(conn, actor):
    core.require(actor, "marketplace.compliance.view")
    checks = []

    def add(name, bad, status_if_bad="FAIL"):
        checks.append({"check": name, "status": status_if_bad if bad else "PASS", "count": bad})

    # active carrier with a compliance blocker
    bad = 0
    for c in conn.execute("SELECT id FROM mkt_carriers WHERE status='ACTIVE'").fetchall():
        if evaluate_compliance(conn, "CARRIER", c["id"])["blockers"] and not _has_active_override(conn, "CARRIER", c["id"]):
            bad += 1
    add("active_carrier_missing_required_document", bad)

    bad = 0
    for v in conn.execute("SELECT id FROM mkt_vehicles WHERE status='ACTIVE'").fetchall():
        docs = conn.execute("SELECT status FROM mkt_documents WHERE subject_type='VEHICLE' AND subject_id=? "
                            "AND document_type='INSURANCE'", (v["id"],)).fetchall()
        if docs and all(d["status"] == "EXPIRED" for d in docs):
            bad += 1
    add("active_vehicle_expired_insurance", bad)

    bad = _count(conn, "SELECT COUNT(*) FROM mkt_drivers WHERE status='ACTIVE' AND licence_expiry IS NOT NULL "
                       "AND licence_expiry < ?", (_today(),))
    add("active_driver_expired_licence", bad)

    bad = _count(conn, "SELECT COUNT(*) FROM mkt_carriers c WHERE c.status='ACTIVE' AND NOT EXISTS("
                       "SELECT 1 FROM mkt_vehicles v WHERE v.carrier_id=c.id AND v.status='ACTIVE')")
    add("carrier_active_no_eligible_vehicle", bad, "WARNING")

    bad = 0
    for v in conn.execute("SELECT v.payload_kg vp, c.payload_kg cp FROM mkt_vehicles v "
                          "JOIN mkt_vehicle_categories c ON c.code=v.category_code").fetchall():
        if v["vp"] and v["cp"] and v["vp"] > v["cp"] * 1.2:
            bad += 1
    add("vehicle_payload_inconsistent_with_category", bad)

    bad = _count(conn, "SELECT COUNT(*) FROM (SELECT plate_number FROM mkt_vehicles GROUP BY tenant_id,plate_number HAVING COUNT(*)>1) t")
    add("duplicate_plate_number", bad)

    bad = _count(conn, "SELECT COUNT(*) FROM mkt_lanes WHERE status IN('ACTIVE','PILOT') AND ("
                       "verified_carriers < min_carriers OR backup_capacity=0 OR price_model_validated=0 "
                       "OR ops_support=0 OR payment_capable=0 OR dispute_process=0 OR monitoring=0)")
    add("lane_active_without_complete_criteria", bad, "BLOCKED")

    bad = _count(conn, "SELECT COUNT(*) FROM mkt_compliance_overrides WHERE expires_at IS NULL OR expires_at=''")
    add("compliance_override_without_expiry", bad)

    # prohibited cargo must never produce an eligible vehicle (spot check every prohibited cargo)
    bad = 0
    for cg in conn.execute("SELECT code FROM mkt_cargo_types WHERE prohibited=1 AND status='ACTIVE'").fetchall():
        if mkt.eligible_vehicles(conn, cg["code"])["eligible"]:
            bad += 1
    add("prohibited_cargo_produced_eligible_vehicle", bad, "BLOCKED")

    overall = "PASS"
    order = {"PASS": 0, "WARNING": 1, "FAIL": 2, "BLOCKED": 3}
    for c in checks:
        if order[c["status"]] > order[overall]:
            overall = c["status"]
    return {"overall": overall, "checks": checks}


# --------------------------------------------------------------------------- #
# Migration classifier (no auto-activation, no fabricated evidence, 0 drift)
# --------------------------------------------------------------------------- #
def classify_existing(conn, actor=None):
    """Deterministically classify existing operational records into marketplace candidacy buckets.
    Reads only; never activates, never writes an activation, never fabricates verification."""
    buckets = {"shipper_candidate": 0, "carrier_candidate": 0, "internal_operational": 0,
               "marketplace_ineligible": 0, "ambiguous": 0, "excluded": 0, "historical": 0}
    try:
        customers = conn.execute("SELECT id FROM customers").fetchall()
        buckets["shipper_candidate"] = len(customers)   # existing customers are shipper CANDIDATES only
    except Exception:
        try: conn.rollback()
        except Exception: pass
    # We do not have a suppliers/subcontractors marketplace table yet -> ambiguous/internal counts stay 0.
    return {"buckets": buckets,
            "invariants": {"unexpected_financial_differences": 0, "unexpected_operational_status_changes": 0,
                           "unexpected_participant_activations": 0, "unexpected_eligibility_expansion": 0},
            "note": "candidates only; no auto-activation; no fabricated verification evidence"}


# --------------------------------------------------------------------------- #
# Seed a minimal compliance-rule set (documents required per participant type)
# --------------------------------------------------------------------------- #
_SEED_ACTOR = {"id": 0, "role": "system", "perms": {"*"}, "tenant_id": None}


def seed(conn, actor=None):
    a = actor or _SEED_ACTOR
    if conn.execute("SELECT 1 FROM mkt_compliance_rules LIMIT 1").fetchone():
        return
    define_rule(conn, a, "shipper_registration", "SHIPPER", ["BUSINESS_REGISTRATION", "TAX_REGISTRATION"])
    define_rule(conn, a, "carrier_registration", "CARRIER",
                ["BUSINESS_REGISTRATION", "TAX_REGISTRATION", "AUTHORITY_TO_OPERATE"])
    define_rule(conn, a, "carrier_insurance", "CARRIER", ["INSURANCE"])
    define_rule(conn, a, "vehicle_registration", "VEHICLE", ["VEHICLE_REGISTRATION", "INSURANCE"])
    define_rule(conn, a, "vehicle_inspection", "VEHICLE", ["INSPECTION"], severity="WARNING")
    define_rule(conn, a, "driver_licence", "DRIVER", ["DRIVER_LICENCE"])
    conn.commit()
