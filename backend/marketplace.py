"""LiftHaul OS — Nationwide Marketplace & Production Launch Program, Increment 1.

The DETERMINISTIC marketplace foundation every downstream workstream depends on:

  1. Governed VEHICLE taxonomy  — Philippine vehicle categories + capabilities (payload,
     volume, opening dimensions, refrigeration, lifting, hazmat, port eligibility) with a
     status lifecycle and an immutable checksum per record.
  2. Governed CARGO taxonomy    — cargo classes + handling flags (fragile / perishable /
     refrigerated / oversized / overweight / hazardous / regulated / PROHIBITED).
  3. Deterministic cargo→vehicle ELIGIBILITY — pure, rule-based, fully testable, and
     evaluated BEFORE any AI ranking. AI may re-rank an eligible pool; it may never widen it.
  4. LANE & COVERAGE model — Philippine island-group corridors with a serviceability status
     and a DETERMINISTIC lane-activation gate. The platform may ACCEPT INTEREST for a lane
     that is not yet active, but it must NEVER PROMISE SERVICE until the lane passes every
     activation criterion under separation of duties.

Design invariants (blueprint §5, §6, §14):
  * "Build deterministic cargo-to-vehicle eligibility before AI ranking."       -> eligible_vehicles()
  * "The platform may accept interest for inactive lanes but must not promise
     immediate service."                                                        -> serviceability()
  * "A lane may be commercially activated only when it has sufficient verified
     carriers, backup capacity, validated price model, operational support,
     payment capability, dispute process, service monitoring, launch approval." -> activate_lane()
  * Nothing here touches Phase 1-10 financials, statuses, or tenants — this is additive
    reference + eligibility logic (0 drift).

This increment is intentionally deterministic and offline: it has NO dependency on any
owner-controlled blocker (live Wise, live AI, licensed payment partner, production infra,
regulatory sign-off). Those remain separate, honestly-tracked workstreams.
"""
from __future__ import annotations

import datetime
import hashlib
import json

import core

# --------------------------------------------------------------------------- #
# Status vocabularies
# --------------------------------------------------------------------------- #
CATALOG_STATUSES = ("DRAFT", "ACTIVE", "RETIRED")

# Lane serviceability lifecycle. Only PILOT / ACTIVE may PROMISE service. ASSESSING /
# INTEREST_ONLY accept interest but promise nothing. SUSPENDED / CLOSED promise nothing.
LANE_STATUSES = ("DRAFT", "ASSESSING", "INTEREST_ONLY", "PILOT", "ACTIVE", "SUSPENDED", "CLOSED")
_PROMISE_STATUSES = ("PILOT", "ACTIVE")
_INTEREST_STATUSES = ("ASSESSING", "INTEREST_ONLY", "PILOT", "ACTIVE")

ISLAND_GROUPS = ("LUZON", "VISAYAS", "MINDANAO")

# The seven deterministic lane-activation criteria (blueprint §14 "Lane activation rule").
# Each maps to a stored column; verified_carriers is a threshold, the rest are booleans.
LANE_CRITERIA = (
    "verified_carriers",       # >= min_carriers
    "backup_capacity",         # a backup carrier pool exists
    "price_model_validated",   # a validated price/quotation model for the lane
    "ops_support",             # operational support coverage assigned
    "payment_capable",         # protected-payment funding+release proven for the lane
    "dispute_process",         # dispute/claims process live for the lane
    "monitoring",              # service monitoring live for the lane
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS mkt_vehicle_categories(
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  class_group TEXT NOT NULL,          -- MOTORCYCLE_SMALL | LIGHT_COMMERCIAL | MEDIUM_HEAVY | SPECIALIZED
  body_type TEXT,                     -- closed_van | wing_van | dropside | flatbed | lowbed | container_chassis | ...
  axle_config TEXT,
  payload_kg REAL NOT NULL DEFAULT 0,
  volume_cbm REAL NOT NULL DEFAULT 0,
  length_cm REAL, width_cm REAL, height_cm REAL,          -- internal usable
  opening_length_cm REAL, opening_width_cm REAL, opening_height_cm REAL,  -- rear/side opening
  lifting_capable INTEGER DEFAULT 0,
  lifting_capacity_kg REAL DEFAULT 0,
  refrigerated INTEGER DEFAULT 0,
  hazmat_allowed INTEGER DEFAULT 0,
  port_eligible INTEGER DEFAULT 0,
  requires_special_permit INTEGER DEFAULT 0,
  safety_allowance_pct REAL NOT NULL DEFAULT 0.10,
  status TEXT NOT NULL DEFAULT 'DRAFT',
  checksum TEXT,
  created_by INTEGER, created_at TEXT,
  updated_by INTEGER, updated_at TEXT,
  correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_cargo_types(
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  cargo_class TEXT NOT NULL,          -- GENERAL | SPECIALIZED | REGULATED
  fragile INTEGER DEFAULT 0,
  perishable INTEGER DEFAULT 0,
  refrigerated INTEGER DEFAULT 0,     -- requires temperature control
  high_value INTEGER DEFAULT 0,
  oversized INTEGER DEFAULT 0,        -- needs flatbed/lowbed/lifting
  overweight INTEGER DEFAULT 0,
  machinery INTEGER DEFAULT 0,        -- treated as oversized for eligibility
  hazardous INTEGER DEFAULT 0,        -- requires hazmat_allowed vehicle
  regulated INTEGER DEFAULT 0,        -- needs permit/manual review (advisory)
  prohibited INTEGER DEFAULT 0,       -- may NEVER be booked
  default_permit_required INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'DRAFT',
  checksum TEXT,
  created_by INTEGER, created_at TEXT,
  updated_by INTEGER, updated_at TEXT,
  correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_lanes(
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  origin_group TEXT NOT NULL,
  dest_group TEXT NOT NULL,
  origin_zone TEXT NOT NULL,
  dest_zone TEXT NOT NULL,
  corridor TEXT,
  requires_sea_leg INTEGER DEFAULT 0,
  distance_km REAL,
  min_carriers INTEGER DEFAULT 3,
  verified_carriers INTEGER DEFAULT 0,
  backup_capacity INTEGER DEFAULT 0,
  price_model_validated INTEGER DEFAULT 0,
  ops_support INTEGER DEFAULT 0,
  payment_capable INTEGER DEFAULT 0,
  dispute_process INTEGER DEFAULT 0,
  monitoring INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'DRAFT',
  assessed_by INTEGER, assessed_at TEXT,
  approved_by INTEGER, approved_at TEXT,
  notes TEXT,
  created_by INTEGER, created_at TEXT,
  updated_by INTEGER, updated_at TEXT,
  correlation_id TEXT,
  UNIQUE(origin_zone, dest_zone));
"""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _checksum(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _cid():
    return core.correlation_id()


def init(conn):
    conn.executescript(SCHEMA)
    try:
        conn.execute("ALTER TABLE mkt_vehicle_categories ADD COLUMN safety_allowance_pct REAL NOT NULL DEFAULT 0.10")
    except Exception:
        pass
    conn.commit()


# --------------------------------------------------------------------------- #
# Vehicle taxonomy
# --------------------------------------------------------------------------- #
_VEHICLE_FIELDS = (
    "name", "class_group", "body_type", "axle_config", "payload_kg", "volume_cbm",
    "length_cm", "width_cm", "height_cm", "opening_length_cm", "opening_width_cm",
    "opening_height_cm", "lifting_capable", "lifting_capacity_kg", "refrigerated",
    "hazmat_allowed", "port_eligible", "requires_special_permit", "safety_allowance_pct",
)


def _vehicle_checksum(row: dict) -> str:
    return _checksum({k: row.get(k) for k in ("code",) + _VEHICLE_FIELDS})


def create_vehicle_category(conn, actor, code, name, class_group, **attrs):
    core.require(actor, "marketplace.vehicle.manage")
    if class_group not in ("MOTORCYCLE_SMALL", "LIGHT_COMMERCIAL", "MEDIUM_HEAVY", "SPECIALIZED"):
        raise ValueError(f"invalid class_group: {class_group}")
    if conn.execute("SELECT 1 FROM mkt_vehicle_categories WHERE code=?", (code,)).fetchone():
        raise ValueError(f"vehicle category '{code}' already exists")
    row = {"code": code, "name": name, "class_group": class_group}
    for f in _VEHICLE_FIELDS:
        if f in ("name", "class_group"):
            continue
        row[f] = attrs.get(f, 0.10 if f == "safety_allowance_pct" else
                           (0 if f.endswith(("_kg", "_cbm", "capable", "allowed", "eligible", "permit")) else None))
    cs = _vehicle_checksum(row)
    now = _now()
    cur = conn.execute(
        "INSERT INTO mkt_vehicle_categories(code,name,class_group,body_type,axle_config,payload_kg,"
        "volume_cbm,length_cm,width_cm,height_cm,opening_length_cm,opening_width_cm,opening_height_cm,"
        "lifting_capable,lifting_capacity_kg,refrigerated,hazmat_allowed,port_eligible,"
        "requires_special_permit,safety_allowance_pct,status,checksum,created_by,created_at,correlation_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'DRAFT',?,?,?,?)",
        (code, name, class_group, row["body_type"], row["axle_config"], row["payload_kg"],
         row["volume_cbm"], row["length_cm"], row["width_cm"], row["height_cm"],
         row["opening_length_cm"], row["opening_width_cm"], row["opening_height_cm"],
         int(row["lifting_capable"] or 0), row["lifting_capacity_kg"] or 0, int(row["refrigerated"] or 0),
         int(row["hazmat_allowed"] or 0), int(row["port_eligible"] or 0),
         int(row["requires_special_permit"] or 0), float(row["safety_allowance_pct"] or 0),
         cs, actor["id"], now, _cid()))
    vid = cur.lastrowid
    core.audit(conn, actor, "MKT_VEHICLE_CREATED", "mkt_vehicle_categories", vid, None, {"code": code})
    conn.commit()
    return vid


def set_vehicle_status(conn, actor, vehicle_id, status):
    core.require(actor, "marketplace.vehicle.manage")
    if status not in CATALOG_STATUSES:
        raise ValueError(f"invalid status: {status}")
    row = conn.execute("SELECT * FROM mkt_vehicle_categories WHERE id=?", (vehicle_id,)).fetchone()
    if not row:
        raise ValueError("vehicle category not found")
    conn.execute("UPDATE mkt_vehicle_categories SET status=?,updated_by=?,updated_at=? WHERE id=?",
                 (status, actor["id"], _now(), vehicle_id))
    core.audit(conn, actor, "MKT_VEHICLE_STATUS", "mkt_vehicle_categories", vehicle_id,
               {"status": row["status"]}, {"status": status})
    conn.commit()
    return True


def list_vehicle_categories(conn, status=None, active_only=False):
    q = "SELECT * FROM mkt_vehicle_categories"
    args = []
    if active_only:
        q += " WHERE status='ACTIVE'"
    elif status:
        q += " WHERE status=?"
        args.append(status)
    q += " ORDER BY payload_kg ASC, id ASC"
    return [dict(r) for r in conn.execute(q, args).fetchall()]


# --------------------------------------------------------------------------- #
# Cargo taxonomy
# --------------------------------------------------------------------------- #
_CARGO_FLAGS = ("fragile", "perishable", "refrigerated", "high_value", "oversized",
                "overweight", "machinery", "hazardous", "regulated", "prohibited",
                "default_permit_required")


def create_cargo_type(conn, actor, code, name, cargo_class, **flags):
    core.require(actor, "marketplace.cargo.manage")
    if cargo_class not in ("GENERAL", "SPECIALIZED", "REGULATED"):
        raise ValueError(f"invalid cargo_class: {cargo_class}")
    if conn.execute("SELECT 1 FROM mkt_cargo_types WHERE code=?", (code,)).fetchone():
        raise ValueError(f"cargo type '{code}' already exists")
    vals = {f: int(bool(flags.get(f, 0))) for f in _CARGO_FLAGS}
    cs = _checksum({"code": code, "cargo_class": cargo_class, **vals})
    cur = conn.execute(
        "INSERT INTO mkt_cargo_types(code,name,cargo_class,fragile,perishable,refrigerated,"
        "high_value,oversized,overweight,machinery,hazardous,regulated,prohibited,"
        "default_permit_required,status,checksum,created_by,created_at,correlation_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'DRAFT',?,?,?,?)",
        (code, name, cargo_class, vals["fragile"], vals["perishable"], vals["refrigerated"],
         vals["high_value"], vals["oversized"], vals["overweight"], vals["machinery"],
         vals["hazardous"], vals["regulated"], vals["prohibited"], vals["default_permit_required"],
         cs, actor["id"], _now(), _cid()))
    cgid = cur.lastrowid
    core.audit(conn, actor, "MKT_CARGO_CREATED", "mkt_cargo_types", cgid, None, {"code": code})
    conn.commit()
    return cgid


def set_cargo_status(conn, actor, cargo_id, status):
    core.require(actor, "marketplace.cargo.manage")
    if status not in CATALOG_STATUSES:
        raise ValueError(f"invalid status: {status}")
    row = conn.execute("SELECT * FROM mkt_cargo_types WHERE id=?", (cargo_id,)).fetchone()
    if not row:
        raise ValueError("cargo type not found")
    conn.execute("UPDATE mkt_cargo_types SET status=?,updated_by=?,updated_at=? WHERE id=?",
                 (status, actor["id"], _now(), cargo_id))
    core.audit(conn, actor, "MKT_CARGO_STATUS", "mkt_cargo_types", cargo_id,
               {"status": row["status"]}, {"status": status})
    conn.commit()
    return True


def get_cargo_type(conn, code):
    r = conn.execute("SELECT * FROM mkt_cargo_types WHERE code=?", (code,)).fetchone()
    return dict(r) if r else None


def list_cargo_types(conn, active_only=False):
    q = "SELECT * FROM mkt_cargo_types"
    if active_only:
        q += " WHERE status='ACTIVE'"
    q += " ORDER BY id ASC"
    return [dict(r) for r in conn.execute(q).fetchall()]


# --------------------------------------------------------------------------- #
# DETERMINISTIC cargo -> vehicle eligibility (runs BEFORE any AI ranking)
# --------------------------------------------------------------------------- #
def _vehicle_denials(cargo: dict, veh: dict, weight_kg, volume_cbm, dims):
    """Return a list of deterministic reasons this vehicle is INELIGIBLE (empty = eligible)."""
    reasons = []
    if veh["status"] != "ACTIVE":
        reasons.append("vehicle_not_active")
    if weight_kg is not None and (veh["payload_kg"] or 0) < weight_kg:
        reasons.append("payload_exceeded")
    if volume_cbm is not None and (veh["volume_cbm"] or 0) < volume_cbm:
        reasons.append("volume_exceeded")
    if cargo["refrigerated"] and not veh["refrigerated"]:
        reasons.append("refrigeration_required")
    if (cargo["oversized"] or cargo["machinery"]):
        if not (veh["lifting_capable"] or (veh["body_type"] or "") in ("flatbed", "lowbed", "container_chassis")):
            reasons.append("oversized_handling_required")
    if cargo["hazardous"] and not veh["hazmat_allowed"]:
        reasons.append("hazmat_not_permitted")
    if dims:
        # dims = (length_cm, width_cm, height_cm) of the largest single item
        L, W, H = dims
        ol, ow, oh = veh.get("opening_length_cm"), veh.get("opening_width_cm"), veh.get("opening_height_cm")
        if ol is not None and L is not None and L > ol:
            reasons.append("item_too_long")
        if ow is not None and W is not None and W > ow:
            reasons.append("item_too_wide")
        if oh is not None and H is not None and H > oh:
            reasons.append("item_too_tall")
    return reasons


def eligible_vehicles(conn, cargo_code, weight_kg=None, volume_cbm=None, dims=None):
    """DETERMINISTIC eligible-vehicle pool. AI may re-rank this pool later; it may never widen it.

    Returns {"eligible": [veh...], "cargo": cargo, "blocked": <reason or None>,
             "rejected": [{code, reasons}...]}.
    A PROHIBITED cargo yields an empty pool and blocked='cargo_prohibited'.
    """
    cargo = get_cargo_type(conn, cargo_code)
    if not cargo:
        raise ValueError(f"unknown cargo type '{cargo_code}'")
    if cargo["status"] != "ACTIVE":
        return {"eligible": [], "cargo": cargo, "blocked": "cargo_not_active", "rejected": []}
    if cargo["prohibited"]:
        return {"eligible": [], "cargo": cargo, "blocked": "cargo_prohibited", "rejected": []}
    eligible, rejected = [], []
    for veh in list_vehicle_categories(conn):
        reasons = _vehicle_denials(cargo, veh, weight_kg, volume_cbm, dims)
        if reasons:
            rejected.append({"code": veh["code"], "reasons": reasons})
        else:
            eligible.append(veh)
    return {"eligible": eligible, "cargo": cargo, "blocked": None, "rejected": rejected}


def is_vehicle_eligible(conn, cargo_code, vehicle_code, weight_kg=None, volume_cbm=None, dims=None):
    """Guard used by matching/assignment: a specific vehicle for a specific cargo."""
    cargo = get_cargo_type(conn, cargo_code)
    veh = conn.execute("SELECT * FROM mkt_vehicle_categories WHERE code=?", (vehicle_code,)).fetchone()
    if not cargo or not veh:
        return {"eligible": False, "reasons": ["unknown_cargo_or_vehicle"]}
    if cargo["prohibited"]:
        return {"eligible": False, "reasons": ["cargo_prohibited"]}
    reasons = _vehicle_denials(cargo, dict(veh), weight_kg, volume_cbm, dims)
    return {"eligible": not reasons, "reasons": reasons}


# --------------------------------------------------------------------------- #
# Lane & coverage model + deterministic activation gate
# --------------------------------------------------------------------------- #
def create_lane(conn, actor, code, origin_group, dest_group, origin_zone, dest_zone,
                corridor=None, requires_sea_leg=None, distance_km=None, min_carriers=3):
    core.require(actor, "marketplace.lane.manage")
    for g in (origin_group, dest_group):
        if g not in ISLAND_GROUPS:
            raise ValueError(f"invalid island group: {g}")
    if conn.execute("SELECT 1 FROM mkt_lanes WHERE origin_zone=? AND dest_zone=?",
                    (origin_zone, dest_zone)).fetchone():
        raise ValueError(f"lane {origin_zone}->{dest_zone} already exists")
    if requires_sea_leg is None:
        requires_sea_leg = 1 if origin_group != dest_group else 0
    cur = conn.execute(
        "INSERT INTO mkt_lanes(code,origin_group,dest_group,origin_zone,dest_zone,corridor,"
        "requires_sea_leg,distance_km,min_carriers,status,created_by,created_at,correlation_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,'DRAFT',?,?,?)",
        (code, origin_group, dest_group, origin_zone, dest_zone, corridor,
         int(bool(requires_sea_leg)), distance_km, int(min_carriers), actor["id"], _now(), _cid()))
    lid = cur.lastrowid
    core.audit(conn, actor, "MKT_LANE_CREATED", "mkt_lanes", lid, None,
               {"code": code, "route": f"{origin_zone}->{dest_zone}"})
    conn.commit()
    return lid


def assess_lane(conn, actor, lane_id, **inputs):
    """Record the lane-readiness inputs. Moves DRAFT->ASSESSING (or keeps INTEREST_ONLY).
    Does NOT activate — activation is a separate governed gate under SoD."""
    core.require(actor, "marketplace.lane.manage")
    row = conn.execute("SELECT * FROM mkt_lanes WHERE id=?", (lane_id,)).fetchone()
    if not row:
        raise ValueError("lane not found")
    if row["status"] in ("ACTIVE", "PILOT"):
        raise ValueError("cannot re-assess an active/pilot lane; suspend it first")
    fields = {}
    if "verified_carriers" in inputs:
        fields["verified_carriers"] = int(inputs["verified_carriers"])
    if "min_carriers" in inputs:
        fields["min_carriers"] = int(inputs["min_carriers"])
    for b in ("backup_capacity", "price_model_validated", "ops_support",
              "payment_capable", "dispute_process", "monitoring"):
        if b in inputs:
            fields[b] = int(bool(inputs[b]))
    new_status = inputs.get("status")
    if new_status and new_status not in ("ASSESSING", "INTEREST_ONLY"):
        raise ValueError("assess_lane may only set ASSESSING or INTEREST_ONLY")
    fields["status"] = new_status or ("ASSESSING" if row["status"] == "DRAFT" else row["status"])
    fields["assessed_by"] = actor["id"]
    fields["assessed_at"] = _now()
    fields["updated_by"] = actor["id"]
    fields["updated_at"] = _now()
    sets = ",".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE mkt_lanes SET {sets} WHERE id=?", (*fields.values(), lane_id))
    core.audit(conn, actor, "MKT_LANE_ASSESSED", "mkt_lanes", lane_id, None,
               {k: fields[k] for k in fields if k not in ("updated_by", "updated_at")})
    conn.commit()
    return lane_activation_status(conn, lane_id)


def lane_activation_status(conn, lane_id):
    """DETERMINISTIC gate. Returns {ready, unmet:[...], criteria:{...}} — no side effects."""
    row = conn.execute("SELECT * FROM mkt_lanes WHERE id=?", (lane_id,)).fetchone()
    if not row:
        raise ValueError("lane not found")
    row = dict(row)
    criteria = {}
    unmet = []
    ok_carriers = (row["verified_carriers"] or 0) >= (row["min_carriers"] or 0)
    criteria["verified_carriers"] = {"have": row["verified_carriers"], "need": row["min_carriers"],
                                     "ok": ok_carriers}
    if not ok_carriers:
        unmet.append("verified_carriers")
    for b in LANE_CRITERIA[1:]:
        ok = bool(row[b])
        criteria[b] = {"ok": ok}
        if not ok:
            unmet.append(b)
    return {"ready": not unmet, "unmet": unmet, "criteria": criteria, "status": row["status"]}


def activate_lane(conn, actor, lane_id, target="ACTIVE", note=None):
    """Governed activation. Requires: every criterion met (deterministic gate) AND an
    approver who is NOT the assessor (separation of duties) AND the activate permission.
    target may be PILOT or ACTIVE."""
    core.require(actor, "marketplace.lane.activate")
    if target not in ("PILOT", "ACTIVE"):
        raise ValueError("activation target must be PILOT or ACTIVE")
    row = conn.execute("SELECT * FROM mkt_lanes WHERE id=?", (lane_id,)).fetchone()
    if not row:
        raise ValueError("lane not found")
    status = lane_activation_status(conn, lane_id)
    if not status["ready"]:
        raise ValueError(f"lane not ready for activation; unmet: {status['unmet']}")
    if row["assessed_by"] is not None and row["assessed_by"] == actor["id"]:
        raise PermissionError("separation of duties: the assessor may not approve lane activation")
    conn.execute("UPDATE mkt_lanes SET status=?,approved_by=?,approved_at=?,notes=?,"
                 "updated_by=?,updated_at=? WHERE id=?",
                 (target, actor["id"], _now(), note, actor["id"], _now(), lane_id))
    core.audit(conn, actor, "MKT_LANE_ACTIVATED", "mkt_lanes", lane_id,
               {"status": row["status"]}, {"status": target, "approver": actor["id"]}, reason=note)
    conn.commit()
    return True


def set_lane_status(conn, actor, lane_id, status, note=None):
    """Suspend/close/reopen. Cannot jump straight to PILOT/ACTIVE — that is activate_lane's job."""
    core.require(actor, "marketplace.lane.manage")
    if status not in ("SUSPENDED", "CLOSED", "INTEREST_ONLY", "ASSESSING"):
        raise ValueError("use activate_lane() to reach PILOT/ACTIVE")
    row = conn.execute("SELECT * FROM mkt_lanes WHERE id=?", (lane_id,)).fetchone()
    if not row:
        raise ValueError("lane not found")
    conn.execute("UPDATE mkt_lanes SET status=?,notes=?,updated_by=?,updated_at=? WHERE id=?",
                 (status, note, actor["id"], _now(), lane_id))
    core.audit(conn, actor, "MKT_LANE_STATUS", "mkt_lanes", lane_id,
               {"status": row["status"]}, {"status": status}, reason=note)
    conn.commit()
    return True


def serviceability(conn, origin_zone, dest_zone):
    """The public promise boundary. NEVER promises service for a lane that is not PILOT/ACTIVE.

    Returns {found, status, accepts_interest, promises_service, requires_sea_leg}.
    An unknown lane is serviceable=False but STILL accepts interest (we can capture demand)
    and NEVER promises service — exactly the blueprint rule.
    """
    row = conn.execute("SELECT * FROM mkt_lanes WHERE origin_zone=? AND dest_zone=?",
                       (origin_zone, dest_zone)).fetchone()
    if not row:
        return {"found": False, "status": "UNSERVICED", "accepts_interest": True,
                "promises_service": False, "requires_sea_leg": None}
    row = dict(row)
    return {"found": True, "status": row["status"],
            "accepts_interest": row["status"] in _INTEREST_STATUSES,
            "promises_service": row["status"] in _PROMISE_STATUSES,
            "requires_sea_leg": bool(row["requires_sea_leg"])}


def list_lanes(conn, status=None):
    q = "SELECT * FROM mkt_lanes"
    args = []
    if status:
        q += " WHERE status=?"
        args.append(status)
    q += " ORDER BY origin_group, dest_group, id"
    return [dict(r) for r in conn.execute(q, args).fetchall()]


# --------------------------------------------------------------------------- #
# Seed — Philippine vehicle + cargo taxonomy and the recommended pilot lanes.
# Pilot lanes are seeded ASSESSING / INTEREST_ONLY — NOT active. We never seed a
# lane straight to ACTIVE; activation must pass the governed gate under SoD.
# --------------------------------------------------------------------------- #
_SEED_ACTOR = {"id": 0, "role": "system", "perms": {"*"}, "tenant_id": None}

# code, name, class_group, attrs
_VEHICLES = [
    ("motorcycle", "Motorcycle", "MOTORCYCLE_SMALL",
     dict(payload_kg=30, volume_cbm=0.06, opening_length_cm=40, opening_width_cm=40, opening_height_cm=40)),
    ("motorcycle_box", "Motorcycle w/ Cargo Box", "MOTORCYCLE_SMALL",
     dict(payload_kg=50, volume_cbm=0.15, opening_length_cm=50, opening_width_cm=45, opening_height_cm=45)),
    ("sedan", "Sedan / Hatchback", "MOTORCYCLE_SMALL",
     dict(payload_kg=200, volume_cbm=0.4, opening_length_cm=90, opening_width_cm=90, opening_height_cm=50)),
    ("mpv", "MPV / SUV", "MOTORCYCLE_SMALL",
     dict(payload_kg=300, volume_cbm=1.0, opening_length_cm=110, opening_width_cm=100, opening_height_cm=80)),
    ("multicab", "Multicab", "LIGHT_COMMERCIAL",
     dict(payload_kg=500, volume_cbm=1.8, opening_length_cm=180, opening_width_cm=130, opening_height_cm=120)),
    ("pickup", "Pickup Truck", "LIGHT_COMMERCIAL",
     dict(payload_kg=1000, volume_cbm=2.5, opening_length_cm=180, opening_width_cm=140, opening_height_cm=50)),
    ("small_van", "Small Closed Van", "LIGHT_COMMERCIAL",
     dict(payload_kg=800, volume_cbm=3.5, opening_length_cm=200, opening_width_cm=140, opening_height_cm=150)),
    ("l300_van", "L300 / Closed Van", "LIGHT_COMMERCIAL",
     dict(payload_kg=1000, volume_cbm=5.0, opening_length_cm=250, opening_width_cm=150, opening_height_cm=150)),
    ("ref_van_light", "Light Refrigerated Van", "LIGHT_COMMERCIAL",
     dict(payload_kg=900, volume_cbm=4.0, refrigerated=1, opening_length_cm=250, opening_width_cm=150, opening_height_cm=150)),
    ("elf_4w", "4-Wheel Elf (Dropside/Closed)", "LIGHT_COMMERCIAL",
     dict(payload_kg=2000, volume_cbm=8.0, opening_length_cm=300, opening_width_cm=170, opening_height_cm=180)),
    ("truck_6w", "6-Wheel Truck", "MEDIUM_HEAVY",
     dict(payload_kg=8000, volume_cbm=24.0, opening_length_cm=500, opening_width_cm=210, opening_height_cm=220, port_eligible=1)),
    ("truck_6w_wing", "6-Wheel Wing Van", "MEDIUM_HEAVY",
     dict(payload_kg=7500, volume_cbm=30.0, body_type="wing_van", opening_length_cm=560, opening_width_cm=220, opening_height_cm=230, port_eligible=1)),
    ("truck_6w_ref", "6-Wheel Refrigerated Truck", "MEDIUM_HEAVY",
     dict(payload_kg=7000, volume_cbm=22.0, refrigerated=1, opening_length_cm=500, opening_width_cm=210, opening_height_cm=220, port_eligible=1)),
    ("truck_10w", "10-Wheel Truck", "MEDIUM_HEAVY",
     dict(payload_kg=15000, volume_cbm=40.0, opening_length_cm=730, opening_width_cm=230, opening_height_cm=240, port_eligible=1)),
    ("truck_10w_wing", "10-Wheel Wing Van", "MEDIUM_HEAVY",
     dict(payload_kg=14000, volume_cbm=45.0, body_type="wing_van", opening_length_cm=800, opening_width_cm=240, opening_height_cm=250, port_eligible=1)),
    ("truck_12w", "12-Wheel Truck", "MEDIUM_HEAVY",
     dict(payload_kg=20000, volume_cbm=50.0, opening_length_cm=900, opening_width_cm=240, opening_height_cm=250, port_eligible=1)),
    ("flatbed_10w", "10-Wheel Flatbed", "MEDIUM_HEAVY",
     dict(payload_kg=15000, volume_cbm=0, body_type="flatbed", port_eligible=1)),
    ("container_chassis", "Container Truck (Chassis)", "MEDIUM_HEAVY",
     dict(payload_kg=25000, volume_cbm=67.0, body_type="container_chassis", port_eligible=1)),
    ("lowbed_trailer", "Low-Bed Trailer", "SPECIALIZED",
     dict(payload_kg=40000, volume_cbm=0, body_type="lowbed", requires_special_permit=1, port_eligible=1)),
    ("boom_truck", "Boom Truck", "SPECIALIZED",
     dict(payload_kg=8000, volume_cbm=0, lifting_capable=1, lifting_capacity_kg=10000)),
    ("crane_truck", "Crane Truck", "SPECIALIZED",
     dict(payload_kg=10000, volume_cbm=0, lifting_capable=1, lifting_capacity_kg=25000, requires_special_permit=1)),
]

# code, name, cargo_class, flags
_CARGO = [
    ("general", "General Cargo", "GENERAL", {}),
    ("packaged_goods", "Packaged Goods", "GENERAL", {"fragile": 1}),
    ("retail_stock", "Retail Stock", "GENERAL", {}),
    ("construction_material", "Construction Material", "GENERAL", {"overweight": 1}),
    ("agricultural", "Agricultural Produce", "GENERAL", {"perishable": 1}),
    ("perishable_chilled", "Perishable / Chilled", "SPECIALIZED", {"perishable": 1, "refrigerated": 1}),
    ("high_value", "High-Value Goods", "SPECIALIZED", {"high_value": 1, "fragile": 1}),
    ("machinery", "Machinery / Equipment", "SPECIALIZED", {"machinery": 1, "oversized": 1, "overweight": 1, "default_permit_required": 1}),
    ("vehicle_cargo", "Vehicle (as cargo)", "SPECIALIZED", {"oversized": 1}),
    ("oversized_cargo", "Oversized Cargo", "SPECIALIZED", {"oversized": 1, "overweight": 1, "default_permit_required": 1}),
    ("regulated_goods", "Regulated Goods", "REGULATED", {"regulated": 1, "default_permit_required": 1}),
    ("hazardous", "Hazardous Materials", "REGULATED", {"hazardous": 1, "regulated": 1, "default_permit_required": 1}),
    ("prohibited", "Prohibited Cargo", "REGULATED", {"prohibited": 1}),
]

# recommended pilot corridors (blueprint §15): Metro Manila + CALABARZON + Bulacan/Pampanga.
# Seeded as ASSESSING (demand-capture only, promises NOTHING) — activation is earned.
_LANES = [
    ("MM-MM", "LUZON", "LUZON", "METRO_MANILA", "METRO_MANILA", "NCR core", 15),
    ("MM-CAV", "LUZON", "LUZON", "METRO_MANILA", "CAVITE", "CALABARZON", 35),
    ("MM-LAG", "LUZON", "LUZON", "METRO_MANILA", "LAGUNA", "CALABARZON", 45),
    ("MM-BAT", "LUZON", "LUZON", "METRO_MANILA", "BATANGAS", "CALABARZON", 110),
    ("MM-RIZ", "LUZON", "LUZON", "METRO_MANILA", "RIZAL", "CALABARZON", 30),
    ("MM-BUL", "LUZON", "LUZON", "METRO_MANILA", "BULACAN", "Central Luzon", 40),
    ("MM-PAM", "LUZON", "LUZON", "METRO_MANILA", "PAMPANGA", "Central Luzon", 75),
]


def seed(conn, actor=None):
    a = actor or _SEED_ACTOR
    if not conn.execute("SELECT 1 FROM mkt_vehicle_categories LIMIT 1").fetchone():
        for code, name, cg, attrs in _VEHICLES:
            vid = create_vehicle_category(conn, a, code, name, cg, **attrs)
            set_vehicle_status(conn, a, vid, "ACTIVE")
    if not conn.execute("SELECT 1 FROM mkt_cargo_types LIMIT 1").fetchone():
        for code, name, cc, flags in _CARGO:
            cid = create_cargo_type(conn, a, code, name, cc, **flags)
            set_cargo_status(conn, a, cid, "ACTIVE")
    if not conn.execute("SELECT 1 FROM mkt_lanes LIMIT 1").fetchone():
        for code, og, dg, oz, dz, corr, dist in _LANES:
            lid = create_lane(conn, a, code, og, dg, oz, dz, corridor=corr, distance_km=dist)
            assess_lane(conn, a, lid, status="ASSESSING")
    conn.commit()
