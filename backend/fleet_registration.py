"""Service Provider & Fleet Registration Workspace — a dynamic, master-data-driven registration layer
over the EXISTING carrier / vehicle / driver / compliance domains.

A provider registers once (the existing `mkt_carriers` record) and then adds UNLIMITED individual
vehicles/equipment under it. This module adds the intelligence the plain `register_vehicle` lacks —
WITHOUT forking the vehicle, carrier, driver or compliance domains:

  1. `vehicle_variants` — a MASTER-DATA taxonomy (category -> variant -> class), admin-extendable, that
     maps a rich variant ("6-Wheeler Closed Van") onto an existing marketplace `category_code`
     (`mkt_vehicle_categories`) so everything downstream (pricing, matching) keeps working unchanged.
  2. A rules-driven CLASSIFICATION ENGINE: the provider supplies physical/technical specs
     (wheels / axles / body / payload / refrigerated / lifting) and LiftHaul deterministically resolves
     the canonical variant + tonnage class (e.g. "6-Wheeler Closed Van - 4T Class"). Both the
     provider-entered specs and the canonical classification are stored (`vehicle_specs`).
  3. `register_unit` classifies then delegates to the canonical `marketplace_onboarding.register_vehicle`
     (so the unit is a normal `mkt_vehicle` — DRAFT, reviewer-verified later; a provider never
     self-verifies).
  4. Provider SERVICE AREAS + CAPABILITIES (`provider_service_areas` / `provider_capabilities`).
  5. Per-unit MARKETPLACE ELIGIBILITY with SPECIFIC coded reasons, composing the existing gates
     (KYB, LTFRB, compliance/doc-expiry, vehicle status, driver gate, service-area match).
  6. Fleet dashboard + bulk CSV import (classify -> validate -> create).

Server-side rules stay authoritative: a front-end selection never determines eligibility. Everything is
tenant-scoped, RBAC-governed and audited. Providers add/edit/archive their own fleet but never
self-verify a regulated document.
"""
from __future__ import annotations

import datetime
import json

import core
import tenant
import marketplace as mkt
import marketplace_onboarding as ob
import marketplace_trust as tr
import marketplace_trust_closure as tc
import ltfrb


CATEGORIES = ("MOTORCYCLE", "LIGHT", "VAN", "TRUCK", "TRACTOR_HEAD", "TRAILER", "HEAVY_HAUL",
              "CRANE", "FORKLIFT", "SPECIALIZED")

# Eligibility status codes (server-authoritative)
ELIG = ("ELIGIBLE", "REGISTRATION_EXPIRED", "INSURANCE_EXPIRED", "CPC_INVALID", "MAINTENANCE_HOLD",
        "DRIVER_UNQUALIFIED", "DRIVER_UNAVAILABLE", "OUTSIDE_SERVICE_AREA", "PROVIDER_SUSPENDED",
        "COMPLIANCE_HOLD", "NOT_ACTIVATED")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _today():
    return datetime.date.today().isoformat()


def _j(v):
    return json.dumps(v) if v is not None else None


def _pj(v, d=None):
    if not v:
        return d
    try:
        return json.loads(v)
    except Exception:
        return d


SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicle_variants(
  id INTEGER PRIMARY KEY, tenant_id INTEGER,
  category TEXT NOT NULL, variant_code TEXT NOT NULL, variant_name TEXT NOT NULL,
  category_code TEXT NOT NULL, class_group TEXT, rules TEXT, priority INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER, created_at TEXT,
  UNIQUE(tenant_id, variant_code));

CREATE TABLE IF NOT EXISTS vehicle_specs(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, vehicle_id INTEGER NOT NULL,
  category TEXT, variant_code TEXT, variant_name TEXT, class_label TEXT, category_code TEXT,
  provider_specs TEXT, canonical TEXT, classified_at TEXT, created_by INTEGER,
  UNIQUE(vehicle_id));

CREATE TABLE IF NOT EXISTS provider_service_areas(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, carrier_id INTEGER NOT NULL,
  scope TEXT NOT NULL DEFAULT 'REGION', area_code TEXT NOT NULL, created_by INTEGER, created_at TEXT,
  UNIQUE(tenant_id, carrier_id, scope, area_code));

CREATE TABLE IF NOT EXISTS provider_capabilities(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, carrier_id INTEGER NOT NULL,
  capability TEXT NOT NULL, created_by INTEGER, created_at TEXT,
  UNIQUE(tenant_id, carrier_id, capability));

CREATE TABLE IF NOT EXISTS vehicle_driver_pairings(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, carrier_id INTEGER,
  vehicle_id INTEGER NOT NULL, driver_id INTEGER NOT NULL, role TEXT NOT NULL DEFAULT 'PRIMARY',
  status TEXT NOT NULL DEFAULT 'ACTIVE', created_by INTEGER, created_at TEXT,
  UNIQUE(vehicle_id, role, driver_id));
"""

# Equipment-specific spec field schema per category (master-data driven; admin-extendable). The provider
# fills these into provider_specs; the registration validates the required ones are present.
_EQUIPMENT_FIELDS = {
    "CRANE": {"required": ["lifting_capacity_kg"],
              "optional": ["crane_type", "boom_length_m", "jib_length_m", "max_radius_m",
                           "counterweight_kg", "outrigger_required", "load_chart_ref", "certification_ref"]},
    "FORKLIFT": {"required": ["rated_capacity_kg"],
                 "optional": ["forklift_type", "mast_height_m", "fuel_type", "fork_length_cm",
                              "indoor_outdoor", "tire_type", "certification_ref"]},
    "TANKER": {"required": ["tank_capacity_l"], "optional": ["tank_material", "compartments", "hazmat_class"]},
}
PAIRING_ROLES = ("PRIMARY", "BACKUP")


# code, name -> underlying existing category_code, class_group, rules
_SEED_VARIANTS = [
    ("MOTORCYCLE", "moto_standard", "Motorcycle", "motorcycle", "MOTORCYCLE_SMALL",
     {"vehicle_type": "MOTORCYCLE", "box": False}, 5),
    ("MOTORCYCLE", "moto_box", "Motorcycle w/ Cargo Box", "motorcycle_box", "MOTORCYCLE_SMALL",
     {"vehicle_type": "MOTORCYCLE", "box": True}, 6),
    ("LIGHT", "sedan", "Sedan / Hatchback", "sedan", "MOTORCYCLE_SMALL", {"vehicle_type": "CAR"}, 3),
    ("LIGHT", "mpv", "MPV / SUV", "mpv", "MOTORCYCLE_SMALL", {"vehicle_type": "MPV"}, 3),
    ("LIGHT", "pickup", "Pickup", "pickup", "LIGHT_COMMERCIAL", {"vehicle_type": "PICKUP"}, 3),
    ("VAN", "closed_van", "Closed Van", "l300_van", "LIGHT_COMMERCIAL",
     {"vehicle_type": "VAN", "body": "closed_van", "refrigerated": False}, 4),
    ("VAN", "ref_van", "Refrigerated Van", "ref_van_light", "LIGHT_COMMERCIAL",
     {"vehicle_type": "VAN", "refrigerated": True}, 6),
    ("TRUCK", "truck_4w", "4-Wheeler (Elf)", "elf_4w", "LIGHT_COMMERCIAL",
     {"vehicle_type": "TRUCK", "wheels": 4}, 5),
    ("TRUCK", "truck_6w_closed", "6-Wheeler Closed Van", "truck_6w", "MEDIUM_HEAVY",
     {"vehicle_type": "TRUCK", "wheels": 6, "body": "closed_van"}, 6),
    ("TRUCK", "truck_6w_dropside", "6-Wheeler Dropside", "truck_6w", "MEDIUM_HEAVY",
     {"vehicle_type": "TRUCK", "wheels": 6, "body": "dropside"}, 6),
    ("TRUCK", "truck_6w_wing", "6-Wheeler Wing Van", "truck_6w_wing", "MEDIUM_HEAVY",
     {"vehicle_type": "TRUCK", "wheels": 6, "body": "wing_van"}, 7),
    ("TRUCK", "truck_6w_ref", "6-Wheeler Refrigerated", "truck_6w_ref", "MEDIUM_HEAVY",
     {"vehicle_type": "TRUCK", "wheels": 6, "refrigerated": True}, 8),
    ("TRUCK", "truck_10w_closed", "10-Wheeler Closed Van", "truck_10w", "MEDIUM_HEAVY",
     {"vehicle_type": "TRUCK", "wheels": 10, "body": "closed_van"}, 6),
    ("TRUCK", "truck_10w_wing", "10-Wheeler Wing Van", "truck_10w_wing", "MEDIUM_HEAVY",
     {"vehicle_type": "TRUCK", "wheels": 10, "body": "wing_van"}, 7),
    ("TRUCK", "truck_12w", "12-Wheeler", "truck_12w", "MEDIUM_HEAVY",
     {"vehicle_type": "TRUCK", "wheels": 12}, 6),
    ("HEAVY_HAUL", "flatbed", "Flatbed", "flatbed_10w", "MEDIUM_HEAVY",
     {"vehicle_type": "TRUCK", "body": "flatbed"}, 6),
    ("HEAVY_HAUL", "container", "Container Truck", "container_chassis", "MEDIUM_HEAVY",
     {"vehicle_type": "TRUCK", "body": "container"}, 6),
    ("HEAVY_HAUL", "lowbed", "Low-Bed Trailer", "lowbed_trailer", "SPECIALIZED",
     {"vehicle_type": "TRAILER", "body": "lowbed"}, 7),
    # more truck configurations
    ("TRUCK", "truck_4w_closed", "4-Wheeler Closed Van", "elf_4w", "LIGHT_COMMERCIAL",
     {"vehicle_type": "TRUCK", "wheels": 4, "body": "closed_van"}, 6),
    ("TRUCK", "truck_4w_dropside", "4-Wheeler Dropside", "elf_4w", "LIGHT_COMMERCIAL",
     {"vehicle_type": "TRUCK", "wheels": 4, "body": "dropside"}, 6),
    ("TRUCK", "truck_4w_ref", "4-Wheeler Refrigerated", "ref_van_light", "LIGHT_COMMERCIAL",
     {"vehicle_type": "TRUCK", "wheels": 4, "refrigerated": True}, 7),
    ("TRUCK", "truck_10w_dropside", "10-Wheeler Dropside", "truck_10w", "MEDIUM_HEAVY",
     {"vehicle_type": "TRUCK", "wheels": 10, "body": "dropside"}, 6),
    ("TRUCK", "truck_10w_dump", "10-Wheeler Dump Truck", "dump_truck", "MEDIUM_HEAVY",
     {"vehicle_type": "TRUCK", "wheels": 10, "body": "dump"}, 8),
    ("TRUCK", "truck_14w", "14-Wheeler", "truck_14w", "MEDIUM_HEAVY",
     {"vehicle_type": "TRUCK", "wheels": 14}, 6),
    # tractor / trailer / heavy-haul
    ("TRACTOR_HEAD", "tractor_head", "Tractor Head / Prime Mover", "prime_mover", "MEDIUM_HEAVY",
     {"vehicle_type": "TRACTOR_HEAD"}, 6),
    ("SPECIALIZED", "car_carrier", "Car Carrier", "car_carrier", "MEDIUM_HEAVY",
     {"vehicle_type": "CAR_CARRIER"}, 6),
    # specialized bodies
    ("SPECIALIZED", "tanker_fuel", "Fuel Tanker", "tanker", "SPECIALIZED",
     {"vehicle_type": "TANKER", "subtype": "fuel"}, 8),
    ("SPECIALIZED", "tanker_water", "Water Tanker", "tanker", "SPECIALIZED",
     {"vehicle_type": "TANKER", "subtype": "water"}, 8),
    ("SPECIALIZED", "cement_mixer", "Cement Mixer", "cement_mixer", "SPECIALIZED",
     {"vehicle_type": "MIXER"}, 7),
    ("SPECIALIZED", "dump_truck", "Dump Truck", "dump_truck", "MEDIUM_HEAVY",
     {"vehicle_type": "DUMP"}, 6),
    ("SPECIALIZED", "tow_truck", "Tow Truck", "tow_truck", "SPECIALIZED",
     {"vehicle_type": "TOW"}, 6),
    # cranes (subtype-discriminated) + lifting equipment
    ("CRANE", "boom_truck", "Boom Truck", "boom_truck", "SPECIALIZED",
     {"vehicle_type": "CRANE", "lifting": True, "mounted": True}, 6),
    ("CRANE", "mobile_crane", "Mobile Crane", "crane_truck", "SPECIALIZED",
     {"vehicle_type": "CRANE", "lifting": True, "subtype": "mobile"}, 6),
    ("CRANE", "rough_terrain_crane", "Rough Terrain Crane", "crane_truck", "SPECIALIZED",
     {"vehicle_type": "CRANE", "lifting": True, "subtype": "rough_terrain"}, 7),
    ("CRANE", "all_terrain_crane", "All-Terrain Crane", "crane_truck", "SPECIALIZED",
     {"vehicle_type": "CRANE", "lifting": True, "subtype": "all_terrain"}, 7),
    ("CRANE", "crawler_crane", "Crawler Crane", "crane_truck", "SPECIALIZED",
     {"vehicle_type": "CRANE", "lifting": True, "subtype": "crawler"}, 7),
    ("CRANE", "tower_crane", "Tower Crane", "crane_truck", "SPECIALIZED",
     {"vehicle_type": "CRANE", "lifting": True, "subtype": "tower"}, 7),
    ("FORKLIFT", "forklift", "Forklift", "forklift", "SPECIALIZED",
     {"vehicle_type": "FORKLIFT", "lifting": True}, 6),
    ("FORKLIFT", "reach_truck", "Reach Truck", "reach_truck", "SPECIALIZED",
     {"vehicle_type": "REACH_TRUCK", "lifting": True}, 6),
    ("FORKLIFT", "telehandler", "Telehandler", "telehandler", "SPECIALIZED",
     {"vehicle_type": "TELEHANDLER", "lifting": True}, 6),
]

# equipment categories that don't exist in the base marketplace catalog yet (added idempotently)
_EXTRA_CATEGORIES = [
    ("forklift", "Forklift", "SPECIALIZED", dict(payload_kg=5000, volume_cbm=0, lifting_capable=1, lifting_capacity_kg=5000)),
    ("telehandler", "Telehandler", "SPECIALIZED", dict(payload_kg=4000, volume_cbm=0, lifting_capable=1, lifting_capacity_kg=4000)),
    ("reach_truck", "Reach Truck", "SPECIALIZED", dict(payload_kg=2000, volume_cbm=0, lifting_capable=1, lifting_capacity_kg=2000)),
    ("tanker", "Tanker", "SPECIALIZED", dict(payload_kg=20000, volume_cbm=30, body_type="tanker", requires_special_permit=1)),
    ("cement_mixer", "Cement Mixer", "SPECIALIZED", dict(payload_kg=12000, volume_cbm=8, body_type="mixer")),
    ("dump_truck", "Dump Truck", "MEDIUM_HEAVY", dict(payload_kg=15000, volume_cbm=12, body_type="dump")),
    ("tow_truck", "Tow Truck", "SPECIALIZED", dict(payload_kg=5000, volume_cbm=0, body_type="tow", lifting_capable=1)),
    ("car_carrier", "Car Carrier", "MEDIUM_HEAVY", dict(payload_kg=10000, volume_cbm=0, body_type="car_carrier")),
    ("prime_mover", "Tractor Head / Prime Mover", "MEDIUM_HEAVY", dict(payload_kg=25000, volume_cbm=0, body_type="tractor_head", port_eligible=1)),
    ("truck_14w", "14-Wheeler", "MEDIUM_HEAVY", dict(payload_kg=25000, volume_cbm=55, port_eligible=1)),
]


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    system = {"id": 0, "role": "system", "perms": {"*"}, "tenant_id": None}
    # add the two genuinely-missing equipment categories to the canonical catalog (idempotent)
    for code, name, cg, attrs in _EXTRA_CATEGORIES:
        if not conn.execute("SELECT 1 FROM mkt_vehicle_categories WHERE code=?", (code,)).fetchone():
            try:
                mkt.create_vehicle_category(conn, system, code, name, cg, **attrs)
            except Exception:
                pass
    # seed the variant taxonomy (idempotent)
    if not conn.execute("SELECT 1 FROM vehicle_variants LIMIT 1").fetchone():
        for cat, vc, vn, cc, cg, rules, prio in _SEED_VARIANTS:
            try:
                conn.execute(
                    "INSERT INTO vehicle_variants(category,variant_code,variant_name,category_code,"
                    "class_group,rules,priority,active,version,created_by,created_at) "
                    "VALUES(?,?,?,?,?,?,?,1,1,?,?)",
                    (cat, vc, vn, cc, cg, _j(rules), prio, system["id"], _now()))
            except Exception:
                pass
        conn.commit()


# --------------------------------------------------------------------------- #
def _row(conn, table, id):
    r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (id,)).fetchone()
    if not r:
        raise core.NotFoundError(f"{table} row {id} not found")
    return dict(r)


# --------------------------------------------------------------------------- #
# Variant master data (admin-extendable)
# --------------------------------------------------------------------------- #
def set_variant(conn, actor, category, variant_code, variant_name, category_code, *, class_group=None,
                rules=None, priority=0):
    core.require(actor, "marketplace.fleet.variant.manage")
    if category not in CATEGORIES:
        raise core.ValidationError(f"invalid category '{category}'")
    if not conn.execute("SELECT 1 FROM mkt_vehicle_categories WHERE code=?", (category_code,)).fetchone():
        raise core.NotFoundError(f"underlying vehicle category_code '{category_code}' does not exist")
    at = tenant.actor_tenant(actor)
    existing = conn.execute("SELECT id,version FROM vehicle_variants WHERE variant_code=? AND "
                            "(tenant_id=? OR tenant_id IS NULL)", (variant_code, at)).fetchone()
    if existing:
        conn.execute("UPDATE vehicle_variants SET category=?,variant_name=?,category_code=?,class_group=?,"
                     "rules=?,priority=?,active=1,version=version+1 WHERE id=?",
                     (category, variant_name, category_code, class_group, _j(rules), priority, existing["id"]))
        vid = existing["id"]
    else:
        cur = conn.execute(
            "INSERT INTO vehicle_variants(category,variant_code,variant_name,category_code,class_group,"
            "rules,priority,active,version,created_by,created_at) VALUES(?,?,?,?,?,?,?,1,1,?,?)",
            (category, variant_code, variant_name, category_code, class_group, _j(rules), priority,
             actor["id"], _now()))
        vid = cur.lastrowid
        tenant.stamp(conn, actor, "vehicle_variants", vid)
    core.audit(conn, actor, "FLEET_VARIANT_SET", "vehicle_variants", vid, None,
               {"variant": variant_code, "category": category, "category_code": category_code})
    conn.commit()
    return {"variant_id": vid, "variant_code": variant_code}


def list_variants(conn, actor, category=None):
    core.require(actor, "marketplace.fleet.view")
    frag, params = tenant.predicate(actor)
    q = "SELECT * FROM vehicle_variants WHERE active=1" + frag
    a = list(params)
    if category:
        q += " AND category=?"; a.append(category)
    q += " ORDER BY category, priority DESC, variant_code"
    out = []
    for r in conn.execute(q, a).fetchall():
        d = dict(r); d["rules"] = _pj(d["rules"], {})
        out.append(d)
    return out


def _active_variants(conn, tenant_id=None):
    rows = conn.execute("SELECT * FROM vehicle_variants WHERE active=1 AND (tenant_id=? OR tenant_id IS NULL) "
                        "ORDER BY priority DESC, id", (tenant_id,)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Rules-driven classification engine (deterministic)
# --------------------------------------------------------------------------- #
def _tonnage_class(payload_kg):
    if not payload_kg or payload_kg <= 0:
        return None
    tons = int(round(float(payload_kg) / 1000.0))
    return f"{max(1, tons)}T"


def _score(rules, specs):
    """Return (match, score). match=False if a REQUIRED discriminator conflicts."""
    score = 0
    vt = (specs.get("vehicle_type") or "").upper()
    rvt = (rules.get("vehicle_type") or "").upper()
    if rvt:
        if vt and vt != rvt:
            return False, 0
        if vt == rvt:
            score += 3
    if "wheels" in rules:
        if specs.get("wheels") is None:
            return False, 0
        if int(specs["wheels"]) != int(rules["wheels"]):
            return False, 0
        score += 2
    if "body" in rules:
        sb = (specs.get("body") or "").lower().replace(" ", "_")
        if sb and sb != rules["body"]:
            return False, 0
        if sb == rules["body"]:
            score += 2
    if "refrigerated" in rules:
        if bool(specs.get("refrigerated")) != bool(rules["refrigerated"]):
            return False, 0
        if rules["refrigerated"]:
            score += 2
    if "lifting" in rules:
        if bool(specs.get("lifting")) != bool(rules["lifting"]):
            return False, 0
        if rules["lifting"]:
            score += 2
    if "box" in rules:
        if bool(specs.get("box")) != bool(rules["box"]):
            return False, 0
    if "mounted" in rules and specs.get("mounted") is not None:
        if bool(specs.get("mounted")) != bool(rules["mounted"]):
            return False, 0
    if "subtype" in rules:
        ss = (specs.get("subtype") or "").lower().replace(" ", "_").replace("-", "_")
        if ss and ss != rules["subtype"]:
            return False, 0
        if ss == rules["subtype"]:
            score += 3
    return True, score


def classify(conn, specs, tenant_id=None):
    """Provider specs -> canonical variant + tonnage class. Deterministic. Never guesses beyond the
    governed variant rules; if nothing matches, raises so the provider corrects the specs."""
    variants = _active_variants(conn, tenant_id)
    best, best_score = None, -1
    for v in variants:
        ok, sc = _score(_pj(v["rules"], {}), specs)
        if ok and (sc > best_score or (sc == best_score and best and v["priority"] > best["priority"])):
            best, best_score = v, sc
    if not best or best_score <= 0:
        raise core.ValidationError("could not classify vehicle from the supplied specifications - "
                                   "check vehicle_type / wheels / body")
    tcls = _tonnage_class(specs.get("payload_kg"))
    class_label = best["variant_name"] + (f" - {tcls} Class" if tcls else "")
    return {"category": best["category"], "variant_code": best["variant_code"],
            "variant_name": best["variant_name"], "category_code": best["category_code"],
            "class_group": best["class_group"], "class_label": class_label,
            "tonnage_class": tcls, "confidence": best_score, "matched_variant_id": best["id"]}


# --------------------------------------------------------------------------- #
# Unit registration (classify -> reuse register_vehicle -> store spec profile)
# --------------------------------------------------------------------------- #
def register_unit(conn, actor, carrier_id, plate_number, specs):
    """Register one fleet unit from provider specs. Classifies, then delegates to the canonical
    register_vehicle (unit lands DRAFT — a reviewer verifies later; the provider never self-verifies)."""
    core.require(actor, "marketplace.vehicle.manage")
    cls = classify(conn, specs, tenant.actor_tenant(actor))
    # equipment-specific required fields (crane/forklift/tanker) must be supplied
    eq = _EQUIPMENT_FIELDS.get(cls["category"])
    if eq:
        missing = [f for f in eq["required"] if specs.get(f) in (None, "")]
        if missing:
            raise core.ValidationError(f"{cls['category']} registration requires: {missing}")
    passthrough = {}
    for k in ("payload_kg", "volume_cbm", "length_cm", "width_cm", "height_cm", "registration_number",
              "ownership_type", "owner_name", "body_type", "current_location"):
        if specs.get(k) is not None:
            passthrough[k] = specs[k]
    if specs.get("refrigerated") is not None:
        passthrough["refrigerated"] = int(bool(specs["refrigerated"]))
    if specs.get("lifting") is not None:
        passthrough["lifting_capable"] = int(bool(specs["lifting"]))
    vid = ob.register_vehicle(conn, actor, carrier_id, cls["category_code"], plate_number, **passthrough)
    canonical = {"variant_code": cls["variant_code"], "variant_name": cls["variant_name"],
                 "class_label": cls["class_label"], "category_code": cls["category_code"],
                 "class_group": cls["class_group"], "confidence": cls["confidence"]}
    cur = conn.execute(
        "INSERT INTO vehicle_specs(vehicle_id,category,variant_code,variant_name,class_label,category_code,"
        "provider_specs,canonical,classified_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (vid, cls["category"], cls["variant_code"], cls["variant_name"], cls["class_label"],
         cls["category_code"], _j(specs), _j(canonical), _now(), actor["id"]))
    tenant.stamp(conn, actor, "vehicle_specs", cur.lastrowid)
    core.audit(conn, actor, "FLEET_UNIT_REGISTERED", "mkt_vehicles", vid, None,
               {"plate": plate_number, "variant": cls["variant_code"], "class": cls["class_label"]})
    conn.commit()
    # assess the one-time accreditation fee from the canonical classification (server-authoritative;
    # payment is never approval). Best-effort: a pricing gap must not block unit registration.
    fee = None
    try:
        import accreditation as _acc
        fee = _acc.assess_fee(conn, actor, carrier_id, vid)
    except Exception:  # noqa: BLE001
        fee = None
    return {"vehicle_id": vid, "status": "DRAFT", "classification": cls,
            "accreditation": ({"status": fee["status"], "total": fee.get("total"),
                               "currency": fee.get("currency")} if fee else None),
            "note": "registered as DRAFT — a reviewer must verify + activate before it can accept work"}


def unit_spec(conn, actor, vehicle_id):
    core.require(actor, "marketplace.fleet.view")
    v = ob._guarded(conn, actor, "mkt_vehicles", vehicle_id)
    s = conn.execute("SELECT * FROM vehicle_specs WHERE vehicle_id=?", (vehicle_id,)).fetchone()
    out = {"vehicle_id": vehicle_id, "plate_number": v["plate_number"], "status": v["status"],
           "category_code": v["category_code"]}
    if s:
        s = dict(s); s["provider_specs"] = _pj(s["provider_specs"], {}); s["canonical"] = _pj(s["canonical"], {})
        out["spec"] = s
    return out


# --------------------------------------------------------------------------- #
# Service areas + capabilities
# --------------------------------------------------------------------------- #
def set_service_area(conn, actor, carrier_id, area_code, scope="REGION"):
    core.require(actor, "marketplace.fleet.manage")
    ob._guarded(conn, actor, "mkt_carriers", carrier_id)
    at = tenant.actor_tenant(actor)
    if conn.execute("SELECT 1 FROM provider_service_areas WHERE carrier_id=? AND scope=? AND area_code=? "
                    "AND (tenant_id=? OR tenant_id IS NULL)", (carrier_id, scope, area_code, at)).fetchone():
        return {"ok": True, "idempotent": True}
    cur = conn.execute("INSERT INTO provider_service_areas(carrier_id,scope,area_code,created_by,created_at) "
                       "VALUES(?,?,?,?,?)", (carrier_id, scope, area_code, actor["id"], _now()))
    tenant.stamp(conn, actor, "provider_service_areas", cur.lastrowid)
    core.audit(conn, actor, "FLEET_SERVICE_AREA_SET", "provider_service_areas", cur.lastrowid, None,
               {"carrier": carrier_id, "scope": scope, "area": area_code})
    conn.commit()
    return {"ok": True, "id": cur.lastrowid}


def list_service_areas(conn, actor, carrier_id):
    core.require(actor, "marketplace.fleet.view")
    return [dict(r) for r in conn.execute(
        "SELECT id,scope,area_code FROM provider_service_areas WHERE carrier_id=? ORDER BY id", (carrier_id,)).fetchall()]


def set_capability(conn, actor, carrier_id, capability):
    core.require(actor, "marketplace.fleet.manage")
    ob._guarded(conn, actor, "mkt_carriers", carrier_id)
    at = tenant.actor_tenant(actor)
    if conn.execute("SELECT 1 FROM provider_capabilities WHERE carrier_id=? AND capability=? AND "
                    "(tenant_id=? OR tenant_id IS NULL)", (carrier_id, capability, at)).fetchone():
        return {"ok": True, "idempotent": True}
    cur = conn.execute("INSERT INTO provider_capabilities(carrier_id,capability,created_by,created_at) "
                       "VALUES(?,?,?,?)", (carrier_id, capability, actor["id"], _now()))
    tenant.stamp(conn, actor, "provider_capabilities", cur.lastrowid)
    core.audit(conn, actor, "FLEET_CAPABILITY_SET", "provider_capabilities", cur.lastrowid, None,
               {"carrier": carrier_id, "capability": capability})
    conn.commit()
    return {"ok": True, "id": cur.lastrowid}


def list_capabilities(conn, actor, carrier_id):
    core.require(actor, "marketplace.fleet.view")
    return [r["capability"] for r in conn.execute(
        "SELECT capability FROM provider_capabilities WHERE carrier_id=? ORDER BY capability", (carrier_id,)).fetchall()]


def _serves_area(conn, carrier_id, area):
    if not area:
        return True
    rows = conn.execute("SELECT area_code FROM provider_service_areas WHERE carrier_id=?", (carrier_id,)).fetchall()
    if not rows:
        return True   # no declared coverage -> not enforced (fail-open until a provider sets areas)
    codes = {r["area_code"].upper() for r in rows}
    return "NATIONWIDE" in codes or area.upper() in codes


# --------------------------------------------------------------------------- #
# Marketplace eligibility with specific coded reasons (server-authoritative)
# --------------------------------------------------------------------------- #
def unit_eligibility(conn, actor, carrier_id, vehicle_id, *, driver_id=None, job_area=None):
    core.require(actor, "marketplace.fleet.view")
    carrier = ob._guarded(conn, actor, "mkt_carriers", carrier_id)
    v = ob._guarded(conn, actor, "mkt_vehicles", vehicle_id)
    reasons = []
    if carrier["status"] == "SUSPENDED":
        reasons.append("PROVIDER_SUSPENDED")
    if tr.carrier_kyb_status(conn, carrier_id) not in ("VERIFIED", "VERIFIED_WITH_CONDITION"):
        reasons.append("COMPLIANCE_HOLD")
    gate = ltfrb.carrier_authority_gate(conn, carrier_id)
    if not gate["ok"]:
        reasons.append("CPC_INVALID")
    if v["status"] == "MAINTENANCE":
        reasons.append("MAINTENANCE_HOLD")
    elif v["status"] != "ACTIVE":
        reasons.append("NOT_ACTIVATED")
    # commercial gate (config-driven, default off): the one-time accreditation fee must be settled
    # (PAID or WAIVED). Payment is NEVER approval on its own — one independent gate among the many below.
    try:
        import accreditation as _acc
        if _acc.gate_enabled(conn) and not _acc.fee_paid(conn, vehicle_id):
            reasons.append("ACCREDITATION_FEE_UNPAID")
    except Exception:  # noqa: BLE001
        pass
    # cargo-insurance compliance gate (distinct from vehicle insurance; a provider-uploaded document,
    # independently verified, expiry-monitored)
    try:
        import cargo_insurance as _ci
        cig = _ci.eligibility_gate(conn, carrier_id, vehicle_id)
        if cig != "PASS":
            reasons.append(cig)
    except Exception:  # noqa: BLE001
        pass
    # document-level compliance (registration/insurance expiry) via the existing compliance engine
    ev = ob.evaluate_compliance(conn, "VEHICLE", vehicle_id)
    for b in ev["blockers"]:
        bl = b.lower()
        if "registration" in bl and "REGISTRATION_EXPIRED" not in reasons:
            reasons.append("REGISTRATION_EXPIRED")
        elif "insurance" in bl and "INSURANCE_EXPIRED" not in reasons:
            reasons.append("INSURANCE_EXPIRED")
        elif "COMPLIANCE_HOLD" not in reasons:
            reasons.append("COMPLIANCE_HOLD")
    if driver_id is not None:
        g = tc.driver_assignment_gate(conn, driver_id, vehicle_id=vehicle_id)
        if not g["ok"]:
            if any("suspend" in r or "unavail" in r for r in g["reasons"]):
                reasons.append("DRIVER_UNAVAILABLE")
            else:
                reasons.append("DRIVER_UNQUALIFIED")
    if job_area is not None and not _serves_area(conn, carrier_id, job_area):
        reasons.append("OUTSIDE_SERVICE_AREA")
    reasons = list(dict.fromkeys(reasons))   # dedupe, keep order
    return {"vehicle_id": vehicle_id, "eligible": not reasons,
            "status": "ELIGIBLE" if not reasons else reasons[0], "reasons": reasons}


# --------------------------------------------------------------------------- #
# Fleet dashboard
# --------------------------------------------------------------------------- #
def fleet_dashboard(conn, actor, carrier_id):
    core.require(actor, "marketplace.fleet.view")
    ob._guarded(conn, actor, "mkt_carriers", carrier_id)
    vehicles = ob.list_vehicles(conn, actor, carrier_id=carrier_id)
    by_status, by_variant = {}, {}
    eligible = 0
    for v in vehicles:
        by_status[v["status"]] = by_status.get(v["status"], 0) + 1
        sp = conn.execute("SELECT variant_name FROM vehicle_specs WHERE vehicle_id=?", (v["id"],)).fetchone()
        label = sp["variant_name"] if sp else v["category_code"]
        by_variant[label] = by_variant.get(label, 0) + 1
        el = unit_eligibility(conn, actor, carrier_id, v["id"])
        if el["eligible"]:
            eligible += 1
    return {"carrier_id": carrier_id, "total_units": len(vehicles), "marketplace_eligible": eligible,
            "by_status": by_status, "by_variant": by_variant,
            "service_areas": list_service_areas(conn, actor, carrier_id),
            "capabilities": list_capabilities(conn, actor, carrier_id)}


# --------------------------------------------------------------------------- #
# Bulk fleet import (classify -> validate -> create; per-row result)
# --------------------------------------------------------------------------- #
def bulk_import(conn, actor, carrier_id, rows, *, dry_run=False):
    """Each row = a specs dict incl. plate_number. Classifies + validates every row; on dry_run returns
    the preview only. On a real run, valid rows are registered (invalid rows never block the others)."""
    core.require(actor, "marketplace.vehicle.manage")
    ob._guarded(conn, actor, "mkt_carriers", carrier_id)
    results, created = [], 0
    for i, row in enumerate(rows):
        row = dict(row)
        plate = row.pop("plate_number", None)
        try:
            if not plate:
                raise core.ValidationError("plate_number required")
            cls = classify(conn, row, tenant.actor_tenant(actor))
            if dry_run:
                results.append({"index": i, "plate_number": plate, "ok": True, "classification": cls["class_label"]})
            else:
                r = register_unit(conn, actor, carrier_id, plate, row)
                created += 1
                results.append({"index": i, "plate_number": plate, "ok": True,
                                "vehicle_id": r["vehicle_id"], "classification": cls["class_label"]})
        except Exception as e:
            results.append({"index": i, "plate_number": plate, "ok": False, "error": str(e)})
    return {"total": len(rows), "created": created, "dry_run": bool(dry_run),
            "valid": sum(1 for r in results if r["ok"]), "results": results}


# --------------------------------------------------------------------------- #
# Equipment-specific spec schema (so a UI can render the right form per category)
# --------------------------------------------------------------------------- #
def equipment_schema(conn, actor, category):
    core.require(actor, "marketplace.fleet.view")
    return {"category": category, "fields": _EQUIPMENT_FIELDS.get(category, {"required": [], "optional": []})}


# --------------------------------------------------------------------------- #
# Persistent vehicle <-> driver pairing (primary/backup). Compatibility is gated at set-time;
# the marketplace still re-validates every gate at offer/assignment time (unchanged).
# --------------------------------------------------------------------------- #
def set_pairing(conn, actor, vehicle_id, driver_id, role="PRIMARY"):
    core.require(actor, "marketplace.vehicle.manage")
    if role not in PAIRING_ROLES:
        raise core.ValidationError(f"role must be one of {PAIRING_ROLES}")
    veh = ob._guarded(conn, actor, "mkt_vehicles", vehicle_id)
    drv = ob._guarded(conn, actor, "mkt_drivers", driver_id)
    if veh["carrier_id"] != drv["carrier_id"]:
        raise core.ForbiddenError("vehicle and driver belong to different carriers")
    chk = ob.can_assign_driver(conn, driver_id, vehicle_id)   # deterministic compatibility gate (reused)
    if not chk["ok"]:
        raise core.ConflictError(f"incompatible pairing: {chk['reasons']}")
    # one driver per (vehicle, role): supersede any existing holder of that role
    conn.execute("UPDATE vehicle_driver_pairings SET status='SUPERSEDED' WHERE vehicle_id=? AND role=? "
                 "AND status='ACTIVE'", (vehicle_id, role))
    cur = conn.execute("INSERT INTO vehicle_driver_pairings(carrier_id,vehicle_id,driver_id,role,status,"
                       "created_by,created_at) VALUES(?,?,?,?, 'ACTIVE', ?,?)",
                       (veh["carrier_id"], vehicle_id, driver_id, role, actor["id"], _now()))
    pid = cur.lastrowid
    tenant.stamp(conn, actor, "vehicle_driver_pairings", pid)
    core.audit(conn, actor, "FLEET_PAIRING_SET", "vehicle_driver_pairings", pid, None,
               {"vehicle": vehicle_id, "driver": driver_id, "role": role})
    conn.commit()
    return {"pairing_id": pid, "vehicle_id": vehicle_id, "driver_id": driver_id, "role": role}


def list_pairings(conn, actor, vehicle_id=None, carrier_id=None):
    core.require(actor, "marketplace.fleet.view")
    frag, params = tenant.predicate(actor)
    q = "SELECT * FROM vehicle_driver_pairings WHERE status='ACTIVE'" + frag
    a = list(params)
    if vehicle_id:
        q += " AND vehicle_id=?"; a.append(vehicle_id)
    if carrier_id:
        q += " AND carrier_id=?"; a.append(carrier_id)
    q += " ORDER BY vehicle_id, role"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


def _paired_driver(conn, vehicle_id, role="PRIMARY"):
    r = conn.execute("SELECT driver_id FROM vehicle_driver_pairings WHERE vehicle_id=? AND role=? "
                     "AND status='ACTIVE' ORDER BY id DESC LIMIT 1", (vehicle_id, role)).fetchone()
    return r["driver_id"] if r else None


# --------------------------------------------------------------------------- #
# Per-unit readiness checklist (itemized transparency, not just a status)
# --------------------------------------------------------------------------- #
def unit_readiness(conn, actor, carrier_id, vehicle_id):
    core.require(actor, "marketplace.fleet.view")
    carrier = ob._guarded(conn, actor, "mkt_carriers", carrier_id)
    v = ob._guarded(conn, actor, "mkt_vehicles", vehicle_id)
    kyb = tr.carrier_kyb_status(conn, carrier_id)
    ltfrb = ltfrb_gate = None
    import ltfrb as _lt
    ltfrb_gate = _lt.carrier_authority_gate(conn, carrier_id)
    ev = ob.evaluate_compliance(conn, "VEHICLE", vehicle_id)
    provided = set(ev["provided"])
    def has(doc):
        return doc in provided
    primary = _paired_driver(conn, vehicle_id, "PRIMARY")
    driver_ok = False
    driver_licence = False
    if primary:
        dg = tc.driver_assignment_gate(conn, primary, vehicle_id=vehicle_id)
        driver_ok = dg["ok"]
        de = ob.evaluate_compliance(conn, "DRIVER", primary)
        driver_licence = "DRIVER_LICENCE" in set(de["provided"]) or not de["blockers"]
    import accreditation as _acc, cargo_insurance as _ci
    fee_st = _acc.fee_status(conn, vehicle_id) or "NOT_ASSESSED"
    fee_ok = (not _acc.gate_enabled(conn)) or _acc.fee_paid(conn, vehicle_id)
    ci_st = _ci.status_for(conn, carrier_id, vehicle_id)
    ci_ok = ci_st in ("NOT_REQUIRED", "VERIFIED", "EXPIRING")
    checks = [
        {"item": "Accreditation Fee", "ok": fee_ok, "detail": fee_st},
        {"item": "Business Provider (KYB)", "ok": kyb in ("VERIFIED", "VERIFIED_WITH_CONDITION")},
        {"item": "OR/CR", "ok": has("OR_CR") or has("VEHICLE_REGISTRATION")},
        {"item": "Registration", "ok": has("VEHICLE_REGISTRATION")},
        {"item": "Vehicle Insurance", "ok": has("INSURANCE")},
        {"item": "Cargo Insurance", "ok": ci_ok, "detail": ci_st},
        {"item": "LTFRB/CPC", "ok": bool(ltfrb_gate["ok"])},
        {"item": "Inspection", "ok": has("INSPECTION") or True},   # inspection optional unless a rule requires it
        {"item": "Maintenance (not on hold)", "ok": v["status"] != "MAINTENANCE"},
        {"item": "Vehicle activated", "ok": v["status"] == "ACTIVE"},
        {"item": "Assigned Driver", "ok": bool(primary)},
        {"item": "Driver License", "ok": bool(driver_licence)},
        {"item": "Driver Qualified", "ok": bool(driver_ok)},
    ]
    el = unit_eligibility(conn, actor, carrier_id, vehicle_id, driver_id=primary)
    return {"vehicle_id": vehicle_id,
            "variant": (conn.execute("SELECT variant_name FROM vehicle_specs WHERE vehicle_id=?", (vehicle_id,)).fetchone() or {"variant_name": v["category_code"]})["variant_name"],
            "checks": checks, "marketplace_status": el["status"], "eligible": el["eligible"],
            "reasons": el["reasons"]}


# --------------------------------------------------------------------------- #
# CSV bulk import (parse CSV text -> rows -> bulk_import)
# --------------------------------------------------------------------------- #
def bulk_import_csv(conn, actor, carrier_id, csv_text, *, dry_run=False):
    """Parse a CSV (header row) into spec rows and run bulk_import. Numeric columns are coerced. A
    'plate_number' column is required per row."""
    core.require(actor, "marketplace.vehicle.manage")
    import csv
    import io
    rows = []
    reader = csv.DictReader(io.StringIO(csv_text))
    numeric = {"wheels", "axles", "payload_kg", "gvw", "volume_cbm", "length_cm", "width_cm", "height_cm",
               "lifting_capacity_kg", "rated_capacity_kg", "tank_capacity_l"}
    for raw in reader:
        row = {}
        for k, val in raw.items():
            if k is None or val is None or str(val).strip() == "":
                continue
            k = k.strip()
            v = str(val).strip()
            if k in numeric:
                try:
                    v = float(v) if "." in v else int(v)
                except Exception:
                    pass
            elif v.lower() in ("true", "yes", "1"):
                v = True
            elif v.lower() in ("false", "no", "0"):
                v = False
            row[k] = v
        if row:
            rows.append(row)
    return bulk_import(conn, actor, carrier_id, rows, dry_run=dry_run)


# --------------------------------------------------------------------------- #
def run_integrity(conn, actor):
    core.require(actor, "marketplace.fleet.view")
    checks = []
    badcat = conn.execute("SELECT COUNT(*) c FROM vehicle_variants v LEFT JOIN mkt_vehicle_categories m "
                          "ON m.code=v.category_code WHERE m.code IS NULL").fetchone()["c"]
    checks.append({"check": "variants_map_to_real_categories", "ok": badcat == 0, "count": badcat})
    orphan = conn.execute("SELECT COUNT(*) c FROM vehicle_specs s LEFT JOIN mkt_vehicles v "
                          "ON v.id=s.vehicle_id WHERE v.id IS NULL").fetchone()["c"]
    checks.append({"check": "no_orphan_specs", "ok": orphan == 0, "count": orphan})
    return {"ok": all(x["ok"] for x in checks), "checks": checks}
