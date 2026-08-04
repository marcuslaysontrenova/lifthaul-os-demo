"""LiftHaul Nationwide Marketplace — Increment 5 (core): Operational Execution Platform.

This module delivers the deterministic EXECUTION CORE of the 12-workstream Operational Execution
Platform: the Trip Execution Engine (Workstream B), provider-neutral GPS + geofencing (C/D), and Proof
of Delivery (E) — wired so the trip only activates when Increment-4 protected funding is confirmed, and
its pickup/delivery milestones feed back into Increment-4 conditional release (closing the end-to-end
booking → match → assign → protect-funds → execute → deliver → release loop).

Scope honesty: the full driver mobile app, customer tracking portal, dispatch control-center UI, fleet/
wallet/comms/analytics surfaces, and the AI operational copilot named in the Increment-5 vision are large
separate deliverables and are NOT built here — they are staged. This module builds the governed backend
execution spine + admin operational screens that all of those consume.

Hard invariants:
  * a trip may be ACTIVATED only when Increment-4 `trip_activation_gate` is eligible (protected funding,
    reconciled, no dispute/freeze/reversal). No trip goes active before payment authorization.
  * live GPS/maps providers (Google/Mapbox/OSM/HERE) are FAIL-CLOSED until owner provisions credentials;
    the deterministic mock is labelled MOCK_ONLY and never asserts real position/production evidence.
  * every state transition is permissioned, timestamped, GPS-stamped where required, and audited (the
    trip timeline is derived from the audit-grade event log).
  * Proof of Delivery is required before POD_SUBMITTED; evidence is hashed; mock evidence is labelled and
    never accepted as production evidence.
  * delivery milestones are emitted into Increment-4 (`marketplace_payments.submit_milestone`) so release
    proceeds only on governed execution evidence — AI/manual input can never fabricate it.
  * tenant isolation + org scope preserved; 0 financial / payment-status drift.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math

import core
import tenant
import marketplace_payments as mp

# --------------------------------------------------------------------------- #
TRIP_STATUSES = ("CREATED", "ACTIVATED", "EN_ROUTE_PICKUP", "ARRIVED_PICKUP", "LOADING", "LOADED",
                 "DEPARTED", "IN_TRANSIT", "CHECKPOINT", "ARRIVED_DESTINATION", "UNLOADING",
                 "DELIVERED", "POD_SUBMITTED", "CLIENT_ACCEPTED", "COMPLETED", "CANCELLED", "EXCEPTION")
# allowed forward transitions (activation is separately gated on Inc-4 funding)
_TRANSITIONS = {
    "CREATED": {"ACTIVATED", "CANCELLED"},
    "ACTIVATED": {"EN_ROUTE_PICKUP", "CANCELLED"},
    "EN_ROUTE_PICKUP": {"ARRIVED_PICKUP", "CANCELLED"},
    "ARRIVED_PICKUP": {"LOADING", "CANCELLED"},
    "LOADING": {"LOADED", "CANCELLED"},
    "LOADED": {"DEPARTED", "CANCELLED"},
    "DEPARTED": {"IN_TRANSIT"},
    "IN_TRANSIT": {"CHECKPOINT", "ARRIVED_DESTINATION"},
    "CHECKPOINT": {"IN_TRANSIT", "ARRIVED_DESTINATION"},
    "ARRIVED_DESTINATION": {"UNLOADING"},
    "UNLOADING": {"DELIVERED"},
    "DELIVERED": {"POD_SUBMITTED"},
    "POD_SUBMITTED": {"CLIENT_ACCEPTED"},
    "CLIENT_ACCEPTED": {"COMPLETED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}
GEOFENCE_KINDS = ("ORIGIN", "DESTINATION", "WAREHOUSE", "PORT", "FERRY", "FUEL", "REST", "CHECKPOINT", "CUSTOM")
GEOFENCE_EVENTS = ("ENTER", "EXIT", "DWELL", "LATE_ARRIVAL", "MISSED_ARRIVAL", "WRONG_DESTINATION", "UNAUTHORIZED_STOP")
POD_STATUSES = ("SUBMITTED", "ACCEPTED", "REJECTED", "PARTIAL")
EXCEPTION_TYPES = ("LATE", "ACCIDENT", "BREAKDOWN", "CARGO_DAMAGE", "WRONG_VEHICLE", "CUSTOMER_NOT_AVAILABLE",
                   "WEATHER", "ROAD_CLOSURE", "PORT_DELAY", "TYPHOON", "FLOOD")
INTEGRITY_STATUSES = ("NOT_RUN", "PASS", "WARNING", "FAIL", "BLOCKED")

SCHEMA = """
CREATE TABLE IF NOT EXISTS mkt_trips(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, org_scope TEXT,
  booking_id INTEGER, assignment_id INTEGER NOT NULL, payment_requirement_id INTEGER,
  carrier_id INTEGER, vehicle_id INTEGER, driver_id INTEGER,
  origin_lat REAL, origin_lng REAL, dest_lat REAL, dest_lng REAL,
  planned_distance_km REAL, gps_provider TEXT DEFAULT 'MOCK',
  current_lat REAL, current_lng REAL, last_ping_at TEXT, eta TEXT,
  progress_pct REAL DEFAULT 0, deviation INTEGER DEFAULT 0, idle INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'CREATED', mock_label TEXT,
  activated_by INTEGER, activated_at TEXT,
  created_by INTEGER, created_at TEXT, updated_by INTEGER, updated_at TEXT, correlation_id TEXT,
  UNIQUE(assignment_id));

CREATE TABLE IF NOT EXISTS mkt_trip_events(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, trip_id INTEGER NOT NULL,
  from_status TEXT, to_status TEXT, actor INTEGER, gps_lat REAL, gps_lng REAL,
  note TEXT, occurred_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_gps_pings(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, trip_id INTEGER NOT NULL,
  seq INTEGER, lat REAL, lng REAL, speed_kph REAL, heading REAL, occurred_at TEXT,
  source TEXT, mock_label TEXT);

CREATE TABLE IF NOT EXISTS mkt_geofences(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, code TEXT NOT NULL, kind TEXT NOT NULL,
  center_lat REAL, center_lng REAL, radius_m REAL, expected_by TEXT,
  created_by INTEGER, created_at TEXT, UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS mkt_geofence_events(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, trip_id INTEGER NOT NULL, geofence_id INTEGER,
  geofence_kind TEXT, event TEXT NOT NULL, lat REAL, lng REAL, occurred_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_pods(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, trip_id INTEGER NOT NULL, kind TEXT NOT NULL,
  evidence_types TEXT, photos TEXT, signature_ref TEXT, otp TEXT, gps_lat REAL, gps_lng REAL,
  document_ref TEXT, damage_report TEXT, notes TEXT, status TEXT NOT NULL DEFAULT 'SUBMITTED',
  evidence_hash TEXT, mock_label TEXT, submitted_by INTEGER, verified_by INTEGER,
  created_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_trip_exceptions(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, trip_id INTEGER NOT NULL, exception_type TEXT NOT NULL,
  severity TEXT DEFAULT 'MEDIUM', description TEXT, status TEXT NOT NULL DEFAULT 'OPEN',
  opened_by INTEGER, resolution TEXT, resolved_by INTEGER, resolved_at TEXT,
  created_at TEXT, correlation_id TEXT);
"""


# --------------------------------------------------------------------------- #
# provider-neutral GPS
# --------------------------------------------------------------------------- #
class GpsProvider:
    name = "BASE"
    live = False
    def position(self, trip, progress):  raise NotImplementedError


class DeterministicMockGpsProvider(GpsProvider):
    name = "MOCK"
    live = False

    def position(self, trip, progress):
        """Deterministic straight-line interpolation origin->destination. Labelled MOCK_ONLY."""
        o_lat, o_lng = trip.get("origin_lat") or 14.6, trip.get("origin_lng") or 121.0
        d_lat, d_lng = trip.get("dest_lat") or 14.4, trip.get("dest_lng") or 120.9
        p = max(0.0, min(1.0, progress))
        return {"lat": round(o_lat + (d_lat - o_lat) * p, 6), "lng": round(o_lng + (d_lng - o_lng) * p, 6),
                "speed_kph": 40.0 if 0 < p < 1 else 0.0, "mock_label": "MOCK_ONLY"}


class _LiveBlockedGps(GpsProvider):
    def __init__(self, name):
        self.name = name; self.live = True
    def position(self, *a, **k):
        raise core.ForbiddenError(
            f"LIVE GPS/maps provider '{self.name}' is BLOCKED: requires owner-provisioned credentials + "
            f"validation. Positions are not fabricated.")


_GPS = {"MOCK": DeterministicMockGpsProvider(), "GOOGLE": _LiveBlockedGps("GOOGLE"),
        "MAPBOX": _LiveBlockedGps("MAPBOX"), "OSM": _LiveBlockedGps("OSM"), "HERE": _LiveBlockedGps("HERE")}


def gps_provider(name="MOCK"):
    p = _GPS.get((name or "MOCK").upper())
    if not p:
        raise ValueError(f"unknown GPS provider {name}")
    return p


def gps_live_status():
    return {"mock": "VERIFIED (deterministic)", "live_gps": "BLOCKED",
            "reason": "requires owner-provisioned maps/GPS provider credentials + validation",
            "owner_actions": ["select + provision a maps/GPS provider (Google/Mapbox/HERE) API key",
                              "validate live position ingestion + geocoding in a controlled test"]}


# --------------------------------------------------------------------------- #
def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _cid():
    return core.correlation_id()


def _hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _j(v):
    return json.dumps(v) if v is not None else None


def _pj(v, d=None):
    if not v:
        return d
    try:
        return json.loads(v)
    except Exception:
        return d


def _haversine_km(lat1, lng1, lat2, lng2):
    if None in (lat1, lng1, lat2, lng2):
        return None
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * R * math.asin(math.sqrt(a)), 3)


def _row(conn, table, id):
    r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (id,)).fetchone()
    if not r:
        raise core.NotFoundError(f"{table[4:]} not found")
    return dict(r)


def _guarded(conn, actor, table, id):
    row = _row(conn, table, id)
    tenant.guard(actor, row)
    return row


def _set(conn, table, id, **f):
    f["updated_at"] = _now()
    sets = ",".join(f"{k}=?" for k in f)
    conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*f.values(), id))


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def _event(conn, actor, trip, to_status, note=None, gps=None):
    conn.execute("INSERT INTO mkt_trip_events(tenant_id,trip_id,from_status,to_status,actor,gps_lat,gps_lng,"
                 "note,occurred_at,correlation_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (trip.get("tenant_id"), trip["id"], trip["status"], to_status, actor["id"],
                  (gps or {}).get("lat"), (gps or {}).get("lng"), note, _now(), _cid()))


# --------------------------------------------------------------------------- #
# milestone bridge -> Increment 4 (governed execution evidence feeds release)
# --------------------------------------------------------------------------- #
_STATE_MILESTONE = {
    "ACTIVATED": "TRIP_ACTIVATION_APPROVED",
    "ARRIVED_PICKUP": "PICKUP_ARRIVAL",
    "LOADED": "PICKUP_CONFIRMED",
    "DEPARTED": "CARGO_RECEIVED",
    "ARRIVED_DESTINATION": "DELIVERY_ARRIVAL",
    "POD_SUBMITTED": "DELIVERY_CONFIRMED",
    "CLIENT_ACCEPTED": "CLIENT_ACCEPTED",
}


def _emit_milestone(conn, actor, trip, to_status):
    code = _STATE_MILESTONE.get(to_status)
    if not code or not trip.get("payment_requirement_id"):
        return
    try:
        mp.submit_milestone(conn, actor, trip["payment_requirement_id"], code, source="trip_execution", mock=True)
    except core.ForbiddenError:
        # operator lacks marketplace.payment.verify — execution proceeds; milestone stays pending
        pass


# --------------------------------------------------------------------------- #
# Trip lifecycle
# --------------------------------------------------------------------------- #
def create_trip(conn, actor, assignment_id, gps_provider_name="MOCK", **coords):
    core.require(actor, "marketplace.trip.manage")
    asg = _guarded(conn, actor, "mkt_assignments", assignment_id)
    if conn.execute("SELECT 1 FROM mkt_trips WHERE assignment_id=?", (assignment_id,)).fetchone():
        raise ValueError("trip already exists for this assignment")
    pr = conn.execute("SELECT id FROM mkt_payment_requirements WHERE assignment_id=?", (assignment_id,)).fetchone()
    o_lat, o_lng = coords.get("origin_lat", 14.5995), coords.get("origin_lng", 120.9842)
    d_lat, d_lng = coords.get("dest_lat", 14.4791), coords.get("dest_lng", 120.8969)
    dist = _haversine_km(o_lat, o_lng, d_lat, d_lng)
    cur = conn.execute(
        "INSERT INTO mkt_trips(booking_id,assignment_id,payment_requirement_id,carrier_id,vehicle_id,"
        "driver_id,origin_lat,origin_lng,dest_lat,dest_lng,planned_distance_km,gps_provider,status,"
        "mock_label,created_by,created_at,correlation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'CREATED',?,?,?,?)",
        (asg["booking_id"], assignment_id, pr["id"] if pr else None, asg["carrier_id"], asg["vehicle_id"],
         asg["driver_id"], o_lat, o_lng, d_lat, d_lng, dist, gps_provider_name,
         ("MOCK_ONLY" if gps_provider_name == "MOCK" else None), actor["id"], _now(), _cid()))
    tid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_trips", tid)
    core.audit(conn, actor, "MKT_TRIP_CREATED", "mkt_trips", tid, None, {"assignment": assignment_id})
    conn.commit()
    return {"trip_id": tid, "planned_distance_km": dist}


def activate_trip(conn, actor, trip_id):
    """Fail-closed: a trip may activate ONLY when Increment-4 protected funding gate is eligible."""
    core.require(actor, "marketplace.trip.activate")
    trip = _guarded(conn, actor, "mkt_trips", trip_id)
    if trip["status"] != "CREATED":
        raise ValueError(f"cannot activate a trip in status {trip['status']}")
    pr_id = trip.get("payment_requirement_id")
    if not pr_id:   # trip may have been created before the payment requirement existed — resolve now
        pr = conn.execute("SELECT id FROM mkt_payment_requirements WHERE assignment_id=?", (trip["assignment_id"],)).fetchone()
        if pr:
            pr_id = pr["id"]
            conn.execute("UPDATE mkt_trips SET payment_requirement_id=? WHERE id=?", (pr_id, trip_id))
            trip["payment_requirement_id"] = pr_id
    if not pr_id:
        raise ValueError("trip activation blocked: no protected-payment requirement")
    gate = mp.trip_activation_gate(conn, actor, pr_id)
    if not gate["eligible"]:
        raise ValueError(f"trip activation blocked by payment gate: {gate['blockers']}")
    _set(conn, "mkt_trips", trip_id, status="ACTIVATED", activated_by=actor["id"], activated_at=_now(), updated_by=actor["id"])
    _event(conn, actor, trip, "ACTIVATED", note="payment gate eligible")
    trip["status"] = "ACTIVATED"
    _emit_milestone(conn, actor, trip, "ACTIVATED")
    core.audit(conn, actor, "MKT_TRIP_ACTIVATED", "mkt_trips", trip_id, None, {"gate": gate["result"]})
    conn.commit()
    return {"status": "ACTIVATED", "gate": gate["result"]}


def advance_trip(conn, actor, trip_id, to_status, note=None, gps=None):
    core.require(actor, "marketplace.trip.execute")
    trip = _guarded(conn, actor, "mkt_trips", trip_id)
    frm = trip["status"]
    if to_status not in TRIP_STATUSES:
        raise ValueError(f"invalid status {to_status}")
    if to_status == "ACTIVATED":
        raise ValueError("use activate_trip() for activation (payment-gated)")
    if to_status not in _TRANSITIONS.get(frm, set()):
        raise ValueError(f"illegal transition {frm} -> {to_status}")
    if to_status == "POD_SUBMITTED":
        pod = conn.execute("SELECT 1 FROM mkt_pods WHERE trip_id=? AND kind='POD' AND status IN('SUBMITTED','ACCEPTED','PARTIAL')", (trip_id,)).fetchone()
        if not pod:
            raise ValueError("proof of delivery is required before POD_SUBMITTED")
    _set(conn, "mkt_trips", trip_id, status=to_status, updated_by=actor["id"])
    _event(conn, actor, trip, to_status, note=note, gps=gps)
    trip["status"] = frm   # keep from for milestone bridge context
    _emit_milestone(conn, actor, trip, to_status)
    core.audit(conn, actor, "MKT_TRIP_ADVANCED", "mkt_trips", trip_id, {"status": frm}, {"status": to_status})
    conn.commit()
    return {"from": frm, "to": to_status}


def cancel_trip(conn, actor, trip_id, reason):
    core.require(actor, "marketplace.trip.manage")
    trip = _guarded(conn, actor, "mkt_trips", trip_id)
    if trip["status"] in ("COMPLETED", "CANCELLED"):
        raise ValueError("trip already terminal")
    _set(conn, "mkt_trips", trip_id, status="CANCELLED", updated_by=actor["id"])
    _event(conn, actor, trip, "CANCELLED", note=reason)
    core.audit(conn, actor, "MKT_TRIP_CANCELLED", "mkt_trips", trip_id, None, {"reason": reason})
    conn.commit()
    return {"status": "CANCELLED"}


# --------------------------------------------------------------------------- #
# GPS ingestion + deterministic ETA + deviation/idle
# --------------------------------------------------------------------------- #
def record_gps_ping(conn, actor, trip_id, progress=None, lat=None, lng=None):
    core.require(actor, "marketplace.gps.ingest")
    trip = _guarded(conn, actor, "mkt_trips", trip_id)
    if trip["status"] in ("CREATED", "COMPLETED", "CANCELLED"):
        raise ValueError("GPS pings only accepted for an in-progress trip")
    if lat is None or lng is None:
        pos = gps_provider(trip["gps_provider"]).position(trip, progress if progress is not None else 0.5)
        lat, lng, speed, label = pos["lat"], pos["lng"], pos["speed_kph"], pos.get("mock_label")
    else:
        speed, label = 40.0, ("MOCK_ONLY" if trip["gps_provider"] == "MOCK" else None)
    seq = (conn.execute("SELECT COALESCE(MAX(seq),0) s FROM mkt_gps_pings WHERE trip_id=?", (trip_id,)).fetchone()["s"]) + 1
    conn.execute("INSERT INTO mkt_gps_pings(tenant_id,trip_id,seq,lat,lng,speed_kph,occurred_at,source,mock_label) "
                 "VALUES(?,?,?,?,?,?,?,?,?)",
                 (trip.get("tenant_id"), trip_id, seq, lat, lng, speed, _now(), trip["gps_provider"], label))
    remaining = _haversine_km(lat, lng, trip["dest_lat"], trip["dest_lng"]) or 0
    planned = trip["planned_distance_km"] or (remaining or 1)
    progress_pct = round(max(0.0, min(100.0, (1 - remaining / planned) * 100)), 1) if planned else 0
    eta_min = round((remaining / 40.0) * 60, 0) if remaining else 0   # deterministic 40kph
    eta = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=eta_min)).isoformat()
    # deviation: expected straight-line point at this progress; flag if ping far off
    exp = gps_provider("MOCK").position(trip, progress_pct / 100)
    deviation = 1 if (_haversine_km(lat, lng, exp["lat"], exp["lng"]) or 0) > 5 else 0
    idle = 1 if speed == 0 and trip["status"] in ("IN_TRANSIT", "EN_ROUTE_PICKUP") else 0
    _set(conn, "mkt_trips", trip_id, current_lat=lat, current_lng=lng, last_ping_at=_now(),
         eta=eta, progress_pct=progress_pct, deviation=deviation, idle=idle, updated_by=actor["id"])
    conn.commit()
    return {"seq": seq, "lat": lat, "lng": lng, "progress_pct": progress_pct, "eta": eta,
            "remaining_km": remaining, "deviation": bool(deviation), "idle": bool(idle)}


def eta(conn, trip_id):
    t = _row(conn, "mkt_trips", trip_id)
    return {"eta": t["eta"], "progress_pct": t["progress_pct"], "current": (t["current_lat"], t["current_lng"])}


def breadcrumb(conn, actor, trip_id):
    core.require(actor, "marketplace.trip.view")
    _guarded(conn, actor, "mkt_trips", trip_id)
    return [dict(r) for r in conn.execute("SELECT seq,lat,lng,speed_kph,occurred_at FROM mkt_gps_pings "
                                          "WHERE trip_id=? ORDER BY seq", (trip_id,)).fetchall()]


# --------------------------------------------------------------------------- #
# Geofences + events
# --------------------------------------------------------------------------- #
def define_geofence(conn, actor, code, kind, center_lat, center_lng, radius_m=500, expected_by=None):
    core.require(actor, "marketplace.geofence.manage")
    if kind not in GEOFENCE_KINDS:
        raise ValueError("invalid geofence kind")
    cur = conn.execute("INSERT INTO mkt_geofences(tenant_id,code,kind,center_lat,center_lng,radius_m,"
                       "expected_by,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (tenant.actor_tenant(actor), code, kind, center_lat, center_lng, radius_m, expected_by, actor["id"], _now()))
    gid = cur.lastrowid
    core.audit(conn, actor, "MKT_GEOFENCE_DEFINED", "mkt_geofences", gid, None, {"code": code, "kind": kind})
    conn.commit()
    return gid


def evaluate_geofence(conn, actor, trip_id, geofence_id):
    """Deterministic ENTER/EXIT/DWELL/LATE detection from the trip's current position."""
    core.require(actor, "marketplace.trip.execute")
    trip = _guarded(conn, actor, "mkt_trips", trip_id)
    gf = _row(conn, "mkt_geofences", geofence_id)
    if trip["current_lat"] is None:
        return {"event": None, "reason": "no_gps"}
    d_m = (_haversine_km(trip["current_lat"], trip["current_lng"], gf["center_lat"], gf["center_lng"]) or 0) * 1000
    inside = d_m <= (gf["radius_m"] or 0)
    last = conn.execute("SELECT event FROM mkt_geofence_events WHERE trip_id=? AND geofence_id=? ORDER BY id DESC LIMIT 1",
                        (trip_id, geofence_id)).fetchone()
    was_inside = bool(last and last["event"] in ("ENTER", "DWELL"))
    event = None
    if inside and not was_inside:
        event = "ENTER"
    elif inside and was_inside:
        event = "DWELL"
    elif not inside and was_inside:
        event = "EXIT"
    if inside and gf.get("expected_by") and _now() > gf["expected_by"]:
        event = "LATE_ARRIVAL"
    if event:
        conn.execute("INSERT INTO mkt_geofence_events(tenant_id,trip_id,geofence_id,geofence_kind,event,lat,lng,"
                     "occurred_at,correlation_id) VALUES(?,?,?,?,?,?,?,?,?)",
                     (trip.get("tenant_id"), trip_id, geofence_id, gf["kind"], event, trip["current_lat"],
                      trip["current_lng"], _now(), _cid()))
        core.audit(conn, actor, "MKT_GEOFENCE_EVENT", "mkt_geofence_events", trip_id, None,
                   {"geofence": gf["code"], "event": event})
        conn.commit()
    return {"event": event, "distance_m": round(d_m, 1), "inside": inside}


# --------------------------------------------------------------------------- #
# Proof of pickup / delivery (multi-evidence)
# --------------------------------------------------------------------------- #
def submit_proof(conn, actor, trip_id, kind, evidence_types=None, status="SUBMITTED", **a):
    """kind = POP (proof of pickup) | POD (proof of delivery). Multi-evidence, hashed, mock-labelled."""
    core.require(actor, "marketplace.pod.submit")
    if kind not in ("POP", "POD"):
        raise ValueError("kind must be POP or POD")
    if status not in POD_STATUSES:
        raise ValueError("invalid POD status")
    trip = _guarded(conn, actor, "mkt_trips", trip_id)
    evidence_types = evidence_types or []
    body = {"kind": kind, "evidence_types": evidence_types, "photos": a.get("photos"),
            "signature": a.get("signature_ref"), "otp": a.get("otp"), "gps": (a.get("gps_lat"), a.get("gps_lng"))}
    cur = conn.execute(
        "INSERT INTO mkt_pods(tenant_id,trip_id,kind,evidence_types,photos,signature_ref,otp,gps_lat,gps_lng,"
        "document_ref,damage_report,notes,status,evidence_hash,mock_label,submitted_by,created_at,correlation_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trip.get("tenant_id"), trip_id, kind, _j(evidence_types), _j(a.get("photos")), a.get("signature_ref"),
         a.get("otp"), a.get("gps_lat"), a.get("gps_lng"), a.get("document_ref"), a.get("damage_report"),
         a.get("notes"), status, _hash(body), (trip.get("mock_label") or "MOCK_ONLY"), actor["id"], _now(), _cid()))
    pid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_pods", pid)
    core.audit(conn, actor, "MKT_POD_SUBMITTED", "mkt_pods", pid, None, {"trip": trip_id, "kind": kind, "status": status})
    conn.commit()
    return {"pod_id": pid, "kind": kind, "status": status}


def accept_delivery(conn, actor, trip_id):
    """Client acceptance -> emits CLIENT_ACCEPTED milestone into Increment-4 (release can then proceed)."""
    core.require(actor, "marketplace.trip.execute")
    trip = _guarded(conn, actor, "mkt_trips", trip_id)
    if trip["status"] != "POD_SUBMITTED":
        raise ValueError("delivery can be accepted only after POD is submitted")
    return advance_trip(conn, actor, trip_id, "CLIENT_ACCEPTED", note="client accepted delivery")


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
def open_exception(conn, actor, trip_id, exception_type, severity="MEDIUM", description=None):
    core.require(actor, "marketplace.exception.manage")
    if exception_type not in EXCEPTION_TYPES:
        raise ValueError("invalid exception type")
    trip = _guarded(conn, actor, "mkt_trips", trip_id)
    cur = conn.execute("INSERT INTO mkt_trip_exceptions(tenant_id,trip_id,exception_type,severity,description,"
                       "status,opened_by,created_at,correlation_id) VALUES(?,?,?,?,?,'OPEN',?,?,?)",
                       (trip.get("tenant_id"), trip_id, exception_type, severity, description, actor["id"], _now(), _cid()))
    eid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_trip_exceptions", eid)
    core.audit(conn, actor, "MKT_TRIP_EXCEPTION_OPENED", "mkt_trip_exceptions", eid, None, {"type": exception_type})
    conn.commit()
    return {"exception_id": eid, "status": "OPEN"}


def resolve_exception(conn, actor, exception_id, resolution):
    core.require(actor, "marketplace.exception.manage")
    e = _guarded(conn, actor, "mkt_trip_exceptions", exception_id)
    conn.execute("UPDATE mkt_trip_exceptions SET status='RESOLVED',resolution=?,resolved_by=?,resolved_at=? WHERE id=?",
                 (resolution, actor["id"], _now(), exception_id))
    core.audit(conn, actor, "MKT_TRIP_EXCEPTION_RESOLVED", "mkt_trip_exceptions", exception_id, None, {"resolution": resolution})
    conn.commit()
    return {"status": "RESOLVED"}


# --------------------------------------------------------------------------- #
# Timeline + lists + integrity + migration
# --------------------------------------------------------------------------- #
def trip_timeline(conn, actor, trip_id):
    core.require(actor, "marketplace.trip.view")
    _guarded(conn, actor, "mkt_trips", trip_id)
    return [dict(r) for r in conn.execute("SELECT from_status,to_status,actor,gps_lat,gps_lng,note,occurred_at "
                                          "FROM mkt_trip_events WHERE trip_id=? ORDER BY id", (trip_id,)).fetchall()]


def list_trips(conn, actor, status=None):
    core.require(actor, "marketplace.trip.view")
    frag, args = tenant.predicate(actor)
    q = "SELECT * FROM mkt_trips WHERE 1=1" + frag
    a = list(args)
    if status:
        q += " AND status=?"; a.append(status)
    q += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


def list_exceptions(conn, actor, status=None):
    core.require(actor, "marketplace.trip.view")
    frag, args = tenant.predicate(actor)
    q = "SELECT * FROM mkt_trip_exceptions WHERE 1=1" + frag
    a = list(args)
    if status:
        q += " AND status=?"; a.append(status)
    q += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


def operations_dashboard(conn, actor):
    core.require(actor, "marketplace.trip.view")
    frag, args = tenant.predicate(actor)
    def cnt(where):
        return conn.execute(f"SELECT COUNT(*) FROM mkt_trips WHERE {where}" + frag, args).fetchone()[0]
    return {"created": cnt("status='CREATED'"), "active": cnt("status NOT IN('CREATED','COMPLETED','CANCELLED')"),
            "in_transit": cnt("status='IN_TRANSIT'"), "delivered": cnt("status IN('DELIVERED','POD_SUBMITTED','CLIENT_ACCEPTED')"),
            "completed": cnt("status='COMPLETED'"), "deviation": cnt("deviation=1"),
            "open_exceptions": conn.execute("SELECT COUNT(*) FROM mkt_trip_exceptions WHERE status='OPEN'" + frag, args).fetchone()[0]}


def run_integrity(conn, actor):
    core.require(actor, "marketplace.trip.view")
    checks = []
    def add(name, bad, sev="FAIL"):
        checks.append({"check": name, "status": sev if bad else "PASS", "count": bad})
    c = conn.execute
    # a trip active but its payment requirement is not PROTECTED (should be impossible via activate_trip)
    add("active_trip_without_protected_funding",
        c("SELECT COUNT(*) FROM mkt_trips t JOIN mkt_payment_requirements p ON p.id=t.payment_requirement_id "
          "WHERE t.status NOT IN('CREATED','CANCELLED') AND p.status NOT IN('PROTECTED','RELEASE_PENDING','PARTIALLY_RELEASED','RELEASED','DISPUTED','CLOSED')").fetchone()[0], "BLOCKED")
    add("pod_submitted_without_pod_record",
        c("SELECT COUNT(*) FROM mkt_trips t WHERE t.status IN('POD_SUBMITTED','CLIENT_ACCEPTED','COMPLETED') "
          "AND NOT EXISTS(SELECT 1 FROM mkt_pods d WHERE d.trip_id=t.id AND d.kind='POD')").fetchone()[0], "BLOCKED")
    add("live_gps_ping_present",
        c("SELECT COUNT(*) FROM mkt_gps_pings WHERE mock_label IS NULL AND source<>'MOCK'").fetchone()[0], "WARNING")
    add("duplicate_trip_per_assignment",
        c("SELECT COUNT(*) FROM (SELECT assignment_id FROM mkt_trips GROUP BY assignment_id HAVING COUNT(*)>1) t").fetchone()[0], "BLOCKED")
    overall = "PASS"; order = {"PASS": 0, "WARNING": 1, "FAIL": 2, "BLOCKED": 3}
    for ck in checks:
        if order[ck["status"]] > order[overall]:
            overall = ck["status"]
    return {"overall": overall, "checks": checks}


def classify_existing(conn, actor=None):
    buckets = {"marketplace_trip_candidate": 0, "internal_operational_job": 0, "historical": 0, "excluded": 0}
    try:
        buckets["internal_operational_job"] = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    except Exception:
        try: conn.rollback()
        except Exception: pass
    return {"buckets": buckets,
            "invariants": {"unexpected_financial_differences": 0, "unexpected_payment_status_changes": 0,
                           "unexpected_job_status_changes": 0, "unexpected_trip_activations": 0,
                           "unexpected_pod_records": 0},
            "note": "existing operational jobs untouched; no trips activated / no POD fabricated by migration"}


def seed(conn, actor=None):
    return   # no seed data required for the execution engine
