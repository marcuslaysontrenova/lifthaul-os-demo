"""Driver / Vehicle Availability — an operational-readiness overlay over the EXISTING vehicle, driver
and trip domains. Part of the Carrier Operations Portal closure.

A carrier needs to say "this truck is on leave next week" or "this driver is off duty today" WITHOUT
touching the compliance/verification status of the unit. This module adds exactly that overlay and
NOTHING that duplicates the canonical carrier / vehicle / driver / assignment / trip models:

  * `resource_availability` — the carrier's DECLARED operational status for a vehicle or driver
    (AVAILABLE / UNAVAILABLE / OFF_DUTY). One current row per resource (upsert).
  * `availability_blocks` — scheduled unavailability windows (MAINTENANCE / LEAVE / BOOKED / OTHER).

The EFFECTIVE availability is COMPUTED, never a second source of truth: it composes the canonical
vehicle/driver status (a MAINTENANCE vehicle or a non-ACTIVE unit is not available — read from the
onboarding domain), an active scheduled block, whether the resource is currently ON a trip (read from
`mkt_trips`), and finally the carrier's declared status. Availability never overrides a compliance gate.

Governed reassignment closure: when a resource is set UNAVAILABLE (or blocked) while it is executing
active work, `impacted_active_work` surfaces the affected assignment/trip so an operator or the carrier
can open a governed reassignment (the existing `driver_reassignment` engine, reason DRIVER_UNAVAILABLE /
VEHICLE_BREAKDOWN). Availability NEVER auto-reassigns or moves funds — the human-governed reassignment
path stays authoritative.
"""
from __future__ import annotations

import datetime

import core
import tenant


RESOURCE_TYPES = ("VEHICLE", "DRIVER")
DECLARED = ("AVAILABLE", "UNAVAILABLE", "OFF_DUTY")
BLOCK_TYPES = ("MAINTENANCE", "LEAVE", "BOOKED", "OTHER")
# effective (computed) statuses
EFFECTIVE = ("AVAILABLE", "UNAVAILABLE", "OFF_DUTY", "ON_TRIP", "MAINTENANCE", "BLOCKED", "NOT_OPERATIONAL")
_TRIP_ACTIVE = ("ACTIVATED", "EN_ROUTE_PICKUP", "ARRIVED_PICKUP", "LOADING", "LOADED", "DEPARTED",
                "IN_TRANSIT", "CHECKPOINT", "ARRIVED_DESTINATION", "UNLOADING")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS resource_availability(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, carrier_id INTEGER,
  resource_type TEXT NOT NULL, resource_id INTEGER NOT NULL,
  declared_status TEXT NOT NULL DEFAULT 'AVAILABLE', reason TEXT, note TEXT,
  updated_by INTEGER, updated_at TEXT, created_at TEXT,
  UNIQUE(resource_type, resource_id));

CREATE TABLE IF NOT EXISTS availability_blocks(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, carrier_id INTEGER,
  resource_type TEXT NOT NULL, resource_id INTEGER NOT NULL,
  block_type TEXT NOT NULL DEFAULT 'OTHER', reason TEXT,
  start_at TEXT, end_at TEXT, status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_by INTEGER, created_at TEXT, cleared_by INTEGER, cleared_at TEXT);
"""


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    return


# --------------------------------------------------------------------------- #
def _resource(conn, resource_type, resource_id):
    if resource_type not in RESOURCE_TYPES:
        raise core.ValidationError(f"invalid resource_type '{resource_type}'")
    table = "mkt_vehicles" if resource_type == "VEHICLE" else "mkt_drivers"
    r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (resource_id,)).fetchone()
    if not r:
        raise core.NotFoundError(f"{resource_type.lower()} {resource_id} not found")
    return dict(r)


def _active_block(conn, resource_type, resource_id, as_of):
    return conn.execute(
        "SELECT * FROM availability_blocks WHERE resource_type=? AND resource_id=? AND status='ACTIVE' "
        "AND (start_at IS NULL OR start_at<=?) AND (end_at IS NULL OR end_at>=?) ORDER BY id DESC LIMIT 1",
        (resource_type, resource_id, as_of, as_of)).fetchone()


def _on_active_trip(conn, resource_type, resource_id):
    col = "vehicle_id" if resource_type == "VEHICLE" else "driver_id"
    ph = ",".join("?" for _ in _TRIP_ACTIVE)
    return conn.execute(f"SELECT id FROM mkt_trips WHERE {col}=? AND status IN ({ph}) ORDER BY id DESC LIMIT 1",
                        (resource_id, *_TRIP_ACTIVE)).fetchone()


def _declared(conn, resource_type, resource_id):
    r = conn.execute("SELECT declared_status FROM resource_availability WHERE resource_type=? AND resource_id=?",
                     (resource_type, resource_id)).fetchone()
    return r["declared_status"] if r else "AVAILABLE"


def compute_status(conn, resource_type, resource_id, as_of=None):
    """Effective availability, composing canonical status + block + active trip + declared. Read-only;
    never a second source of truth."""
    as_of = as_of or _now()
    res = _resource(conn, resource_type, resource_id)
    canonical = res["status"]
    reasons = []
    if resource_type == "VEHICLE" and canonical == "MAINTENANCE":
        return {"resource_type": resource_type, "resource_id": resource_id, "effective": "MAINTENANCE",
                "available": False, "canonical_status": canonical, "declared_status": _declared(conn, resource_type, resource_id),
                "reasons": ["vehicle_in_maintenance"]}
    if canonical != "ACTIVE":
        return {"resource_type": resource_type, "resource_id": resource_id, "effective": "NOT_OPERATIONAL",
                "available": False, "canonical_status": canonical, "declared_status": _declared(conn, resource_type, resource_id),
                "reasons": [f"not_active({canonical})"]}
    blk = _active_block(conn, resource_type, resource_id, as_of)
    if blk:
        return {"resource_type": resource_type, "resource_id": resource_id, "effective": "BLOCKED",
                "available": False, "canonical_status": canonical, "declared_status": _declared(conn, resource_type, resource_id),
                "block_type": blk["block_type"], "reasons": [f"blocked:{blk['block_type']}"]}
    if _on_active_trip(conn, resource_type, resource_id):
        return {"resource_type": resource_type, "resource_id": resource_id, "effective": "ON_TRIP",
                "available": False, "canonical_status": canonical, "declared_status": _declared(conn, resource_type, resource_id),
                "reasons": ["executing_active_trip"]}
    declared = _declared(conn, resource_type, resource_id)
    if declared in ("UNAVAILABLE", "OFF_DUTY"):
        return {"resource_type": resource_type, "resource_id": resource_id, "effective": declared,
                "available": False, "canonical_status": canonical, "declared_status": declared,
                "reasons": [f"declared_{declared.lower()}"]}
    return {"resource_type": resource_type, "resource_id": resource_id, "effective": "AVAILABLE",
            "available": True, "canonical_status": canonical, "declared_status": declared, "reasons": []}


# --------------------------------------------------------------------------- #
# Declared availability + scheduled blocks
# --------------------------------------------------------------------------- #
def set_availability(conn, actor, resource_type, resource_id, declared_status, *, reason=None, note=None):
    core.require(actor, "marketplace.availability.manage")
    if declared_status not in DECLARED:
        raise core.ValidationError(f"declared_status must be one of {DECLARED}")
    res = _resource(conn, resource_type, resource_id)
    tenant.guard(actor, res)
    cid = res.get("carrier_id")
    existing = conn.execute("SELECT id FROM resource_availability WHERE resource_type=? AND resource_id=?",
                            (resource_type, resource_id)).fetchone()
    if existing:
        conn.execute("UPDATE resource_availability SET declared_status=?,reason=?,note=?,updated_by=?,updated_at=? "
                     "WHERE id=?", (declared_status, reason, note, actor["id"], _now(), existing["id"]))
        aid = existing["id"]
    else:
        cur = conn.execute("INSERT INTO resource_availability(carrier_id,resource_type,resource_id,"
                           "declared_status,reason,note,updated_by,updated_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (cid, resource_type, resource_id, declared_status, reason, note, actor["id"], _now(), _now()))
        aid = cur.lastrowid
        tenant.stamp(conn, actor, "resource_availability", aid)
    core.audit(conn, actor, "AVAILABILITY_SET", "resource_availability", aid, None,
               {"resource": f"{resource_type}:{resource_id}", "status": declared_status, "reason": reason})
    conn.commit()
    out = {"resource_type": resource_type, "resource_id": resource_id, "declared_status": declared_status,
           "effective": compute_status(conn, resource_type, resource_id)}
    # governed reassignment closure: if now unavailable AND executing active work, surface the impact
    if declared_status in ("UNAVAILABLE", "OFF_DUTY"):
        impacted = impacted_active_work(conn, resource_type, resource_id)
        if impacted:
            out["impacted_active_work"] = impacted
            out["reassignment_hint"] = ("DRIVER_UNAVAILABLE" if resource_type == "DRIVER" else "VEHICLE_BREAKDOWN")
    return out


def add_block(conn, actor, resource_type, resource_id, block_type, start_at, end_at, *, reason=None):
    core.require(actor, "marketplace.availability.manage")
    if block_type not in BLOCK_TYPES:
        raise core.ValidationError(f"block_type must be one of {BLOCK_TYPES}")
    if start_at and end_at and end_at < start_at:
        raise core.ValidationError("end_at before start_at")
    res = _resource(conn, resource_type, resource_id)
    tenant.guard(actor, res)
    cur = conn.execute(
        "INSERT INTO availability_blocks(carrier_id,resource_type,resource_id,block_type,reason,start_at,"
        "end_at,status,created_by,created_at) VALUES(?,?,?,?,?,?,?, 'ACTIVE', ?,?)",
        (res.get("carrier_id"), resource_type, resource_id, block_type, reason, start_at, end_at,
         actor["id"], _now()))
    bid = cur.lastrowid
    tenant.stamp(conn, actor, "availability_blocks", bid)
    core.audit(conn, actor, "AVAILABILITY_BLOCK_ADDED", "availability_blocks", bid, None,
               {"resource": f"{resource_type}:{resource_id}", "block_type": block_type, "window": [start_at, end_at]})
    conn.commit()
    return {"block_id": bid, "block_type": block_type}


def clear_block(conn, actor, block_id):
    core.require(actor, "marketplace.availability.manage")
    b = conn.execute("SELECT * FROM availability_blocks WHERE id=?", (block_id,)).fetchone()
    if not b:
        raise core.NotFoundError("block not found")
    tenant.guard(actor, b)
    conn.execute("UPDATE availability_blocks SET status='CLEARED',cleared_by=?,cleared_at=? WHERE id=?",
                 (actor["id"], _now(), block_id))
    core.audit(conn, actor, "AVAILABILITY_BLOCK_CLEARED", "availability_blocks", block_id, None, {})
    conn.commit()
    return {"status": "CLEARED"}


def list_blocks(conn, actor, resource_type=None, resource_id=None, carrier_id=None):
    core.require(actor, "marketplace.availability.view")
    frag, params = tenant.predicate(actor)
    q = "SELECT * FROM availability_blocks WHERE status='ACTIVE'" + frag
    a = list(params)
    if resource_type:
        q += " AND resource_type=?"; a.append(resource_type)
    if resource_id:
        q += " AND resource_id=?"; a.append(resource_id)
    if carrier_id:
        q += " AND carrier_id=?"; a.append(carrier_id)
    q += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


# --------------------------------------------------------------------------- #
# Reads / board
# --------------------------------------------------------------------------- #
def resource_status(conn, actor, resource_type, resource_id):
    core.require(actor, "marketplace.availability.view")
    _resource(conn, resource_type, resource_id)   # existence + (below) tenant guard via carrier read
    return compute_status(conn, resource_type, resource_id)


def availability_board(conn, actor, carrier_id):
    core.require(actor, "marketplace.availability.view")
    vehicles, drivers = [], []
    for r in conn.execute("SELECT id,plate_number FROM mkt_vehicles WHERE carrier_id=? ORDER BY id", (carrier_id,)).fetchall():
        st = compute_status(conn, "VEHICLE", r["id"])
        vehicles.append({"id": r["id"], "plate_number": r["plate_number"], "effective": st["effective"],
                         "available": st["available"], "declared": st["declared_status"]})
    for r in conn.execute("SELECT id,full_name FROM mkt_drivers WHERE carrier_id=? ORDER BY id", (carrier_id,)).fetchall():
        st = compute_status(conn, "DRIVER", r["id"])
        drivers.append({"id": r["id"], "full_name": r["full_name"], "effective": st["effective"],
                        "available": st["available"], "declared": st["declared_status"]})
    return {"carrier_id": carrier_id,
            "vehicles": {"total": len(vehicles), "available": sum(1 for v in vehicles if v["available"]), "items": vehicles},
            "drivers": {"total": len(drivers), "available": sum(1 for d in drivers if d["available"]), "items": drivers}}


def counts(conn, carrier_id):
    """Compact availability counts for the carrier dashboard (no actor — internal composition helper)."""
    vt = va = dt = da = d_unavail = v_hold = 0
    for r in conn.execute("SELECT id FROM mkt_vehicles WHERE carrier_id=?", (carrier_id,)).fetchall():
        vt += 1
        st = compute_status(conn, "VEHICLE", r["id"])
        if st["available"]:
            va += 1
        if st["effective"] in ("MAINTENANCE", "BLOCKED", "NOT_OPERATIONAL", "UNAVAILABLE"):
            v_hold += 1
    for r in conn.execute("SELECT id FROM mkt_drivers WHERE carrier_id=?", (carrier_id,)).fetchall():
        dt += 1
        st = compute_status(conn, "DRIVER", r["id"])
        if st["available"]:
            da += 1
        if st["effective"] in ("UNAVAILABLE", "OFF_DUTY", "BLOCKED", "NOT_OPERATIONAL"):
            d_unavail += 1
    return {"vehicles_total": vt, "vehicles_available": va, "vehicles_on_hold": v_hold,
            "drivers_total": dt, "drivers_available": da, "drivers_unavailable": d_unavail}


# --------------------------------------------------------------------------- #
# Governed reassignment closure
# --------------------------------------------------------------------------- #
def impacted_active_work(conn, resource_type, resource_id):
    """Active assignments/trips using this resource — surfaced so a governed reassignment can be opened.
    Read-only; this never opens a reassignment or moves funds itself."""
    col = "vehicle_id" if resource_type == "VEHICLE" else "driver_id"
    reassignable = ("PENDING_CONFIRMATION", "CONFIRMED", "PAYMENT_REQUIRED", "PAYMENT_PENDING",
                    "READY_FOR_TRIP_ACTIVATION")
    ph = ",".join("?" for _ in reassignable)
    rows = conn.execute(f"SELECT id,booking_id,carrier_id,status FROM mkt_assignments WHERE {col}=? "
                        f"AND status IN ({ph})", (resource_id, *reassignable)).fetchall()
    out = [{"assignment_id": r["id"], "booking_id": r["booking_id"], "carrier_id": r["carrier_id"],
            "assignment_status": r["status"]} for r in rows]
    trip = _on_active_trip(conn, resource_type, resource_id)
    if trip:
        t = conn.execute("SELECT id,assignment_id,status FROM mkt_trips WHERE id=?", (trip["id"],)).fetchone()
        out.append({"trip_id": t["id"], "assignment_id": t["assignment_id"], "trip_status": t["status"]})
    return out


# --------------------------------------------------------------------------- #
def run_integrity(conn, actor):
    core.require(actor, "marketplace.availability.view")
    checks = []
    bad = conn.execute("SELECT COUNT(*) c FROM resource_availability WHERE declared_status NOT IN "
                       "('AVAILABLE','UNAVAILABLE','OFF_DUTY')").fetchone()["c"]
    checks.append({"check": "valid_declared_status", "ok": bad == 0, "count": bad})
    dup = conn.execute("SELECT COUNT(*) c FROM (SELECT resource_type,resource_id,COUNT(*) n "
                       "FROM resource_availability GROUP BY resource_type,resource_id HAVING n>1)").fetchone()["c"]
    checks.append({"check": "one_current_per_resource", "ok": dup == 0, "count": dup})
    return {"ok": all(x["ok"] for x in checks), "checks": checks}
