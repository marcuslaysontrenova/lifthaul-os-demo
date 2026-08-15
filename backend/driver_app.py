"""Driver Mobile App — a driver-facing operating surface over the EXISTING trip / POD / OTP domains.

The final competitive-sequence increment. It gives a driver the screens they need on the road —
assigned trips, status advance, GPS pings, proof-of-delivery, and recipient OTP verification — WITHOUT
a parallel trip, POD, or verification domain. It mirrors the Carrier Portal governance pattern exactly:

  1. A driver login is bound to exactly one driver record (`driver_principals`) — identity-derived,
     never client-supplied. A principal can only ever see/act on its OWN trips.
  2. Reads and writes delegate to the canonical `marketplace_trips` and `delivery_verification`
     functions via a minimal, auditable elevation (`_svc`) that adds ONLY the one operational
     permission a given action needs.

Two hard safety rules, both preserved by construction:

  * **A driver never self-verifies compliance.** The `driver_principal` role holds no operational
    `marketplace.*` permission and no verify/activate/approve, so `/admin/*` is 403; the elevation
    allow-list `_SELF_SERVICE` can never include a verify/approve permission (`_FORBIDDEN_ELEVATION`,
    hard-asserted).
  * **A driver never sees a delivery OTP.** The app can only VERIFY a code the recipient provides
    (`delivery.verification.verify`) — it can never issue, resend, or read the plaintext (those are
    separate permissions the role does not hold, and the verify path never returns the code).
"""
from __future__ import annotations

import datetime

import core
import tenant
import marketplace_trips as tp
import delivery_verification as dv


PORTAL_PERMISSIONS = [
    "driver.app.view",       # read own trips + timeline + profile
    "driver.app.execute",    # advance status, GPS ping, POD, accept, verify recipient OTP, report exception
]

_SELF_SERVICE = {
    "start_trip":  ("marketplace.trip.activate",),
    "advance":     ("marketplace.trip.execute",),
    "ping":        ("marketplace.gps.ingest",),
    "pod":         ("marketplace.pod.submit",),
    "accept":      ("marketplace.trip.execute",),
    "verify_otp":  ("delivery.verification.verify",),
    "exception":   ("marketplace.exception.manage",),
    "view":        ("marketplace.trip.view",),
}

_FORBIDDEN_ELEVATION = {
    "delivery.verification.issue", "delivery.verification.resend", "delivery.verification.override",
    "marketplace.trip.manage", "marketplace.driver.verify", "marketplace.driver.activate",
    "marketplace.vehicle.verify", "marketplace.compliance.verify", "marketplace.payout.approve",
}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _svc(actor, *perms):
    """Minimal, auditable elevation: the driver's own actor + exactly the operational permission(s) for
    ONE action. Never grants a verify/approve/activate/override or an OTP issue/resend/override perm."""
    for p in perms:
        if p in _FORBIDDEN_ELEVATION:
            raise core.ForbiddenError(f"driver app may never elevate into '{p}'")
    base = set(actor.get("perms") or core.PERMISSIONS.get(actor.get("role"), set()))
    out = dict(actor)
    out["perms"] = base | set(perms)
    return out


SCHEMA = """
CREATE TABLE IF NOT EXISTS driver_principals(
  id INTEGER PRIMARY KEY, tenant_id INTEGER,
  user_id INTEGER NOT NULL, driver_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_by INTEGER, created_at TEXT, revoked_by INTEGER, revoked_at TEXT,
  UNIQUE(user_id, driver_id));
"""


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    return


# --------------------------------------------------------------------------- #
# Principal binding + resolver (identity-derived scoping)
# --------------------------------------------------------------------------- #
def bind_principal(conn, actor, user_id, driver_id):
    """Operator action: link a login to a driver so they can run the driver app. Requires the same
    authority as managing drivers."""
    core.require(actor, "marketplace.driver.manage")
    d = conn.execute("SELECT * FROM mkt_drivers WHERE id=?", (driver_id,)).fetchone()
    if not d:
        raise core.NotFoundError(f"driver {driver_id} not found")
    tenant.guard(actor, d)
    existing = conn.execute("SELECT id FROM driver_principals WHERE user_id=? AND driver_id=?",
                            (user_id, driver_id)).fetchone()
    if existing:
        conn.execute("UPDATE driver_principals SET status='ACTIVE',revoked_by=NULL,revoked_at=NULL WHERE id=?",
                     (existing["id"],))
        pid = existing["id"]
    else:
        cur = conn.execute("INSERT INTO driver_principals(user_id,driver_id,status,created_by,created_at) "
                           "VALUES(?,?, 'ACTIVE', ?,?)", (user_id, driver_id, actor["id"], _now()))
        pid = cur.lastrowid
        tenant.stamp(conn, actor, "driver_principals", pid)
    core.audit(conn, actor, "DRIVER_PRINCIPAL_BOUND", "driver_principals", pid, None,
               {"user_id": user_id, "driver_id": driver_id})
    conn.commit()
    return {"principal_id": pid}


def revoke_principal(conn, actor, principal_id, reason=None):
    core.require(actor, "marketplace.driver.manage")
    row = conn.execute("SELECT * FROM driver_principals WHERE id=?", (principal_id,)).fetchone()
    if not row:
        raise core.NotFoundError("principal not found")
    tenant.guard(actor, row)
    conn.execute("UPDATE driver_principals SET status='REVOKED',revoked_by=?,revoked_at=? WHERE id=?",
                 (actor["id"], _now(), principal_id))
    core.audit(conn, actor, "DRIVER_PRINCIPAL_REVOKED", "driver_principals", principal_id, None, {"reason": reason})
    conn.commit()
    return {"status": "REVOKED"}


def _binding(conn, actor):
    return conn.execute("SELECT * FROM driver_principals WHERE user_id=? AND status='ACTIVE' "
                        "ORDER BY id DESC LIMIT 1", (actor["id"],)).fetchone()


def resolve_driver(conn, actor):
    b = _binding(conn, actor)
    if not b:
        raise core.ForbiddenError("no active driver binding for this user")
    return b["driver_id"]


def _own_trip(conn, driver_id, trip_id):
    t = conn.execute("SELECT * FROM mkt_trips WHERE id=?", (trip_id,)).fetchone()
    if not t:
        raise core.NotFoundError("trip not found")
    if t["driver_id"] != driver_id:
        raise core.ForbiddenError("trip is not assigned to you")
    return dict(t)


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
def my_profile(conn, actor):
    core.require(actor, "driver.app.view")
    did = resolve_driver(conn, actor)
    d = conn.execute("SELECT id,full_name,status,licence_class,licence_expiry,carrier_id FROM mkt_drivers "
                     "WHERE id=?", (did,)).fetchone()
    return dict(d) if d else {"id": did}


def my_trips(conn, actor, status=None):
    core.require(actor, "driver.app.view")
    did = resolve_driver(conn, actor)
    q = "SELECT id,booking_id,assignment_id,status,progress_pct,eta,planned_distance_km,last_ping_at " \
        "FROM mkt_trips WHERE driver_id=?"
    p = [did]
    if status:
        q += " AND status=?"; p.append(status)
    q += " ORDER BY id DESC"
    return {"driver_id": did, "trips": [dict(r) for r in conn.execute(q, p).fetchall()]}


def trip_detail(conn, actor, trip_id):
    core.require(actor, "driver.app.view")
    did = resolve_driver(conn, actor)
    t = _own_trip(conn, did, trip_id)
    timeline = tp.trip_timeline(conn, _svc(actor, *_SELF_SERVICE["view"]), trip_id)
    # recipient-verification status is a read-only flag; the OTP code is NEVER exposed here
    dv_status = None
    if t.get("booking_id"):
        try:
            dv_status = dv.status(conn, t["booking_id"])
        except Exception:
            dv_status = None
    return {"trip": {k: t[k] for k in ("id", "booking_id", "assignment_id", "status", "progress_pct",
                                       "eta", "planned_distance_km", "current_lat", "current_lng")},
            "timeline": timeline, "delivery_verification": dv_status}


# --------------------------------------------------------------------------- #
# Writes (delegate to the canonical trip / OTP functions; own trip only)
# --------------------------------------------------------------------------- #
def start_trip(conn, actor, trip_id):
    core.require(actor, "driver.app.execute")
    did = resolve_driver(conn, actor)
    _own_trip(conn, did, trip_id)
    return tp.activate_trip(conn, _svc(actor, *_SELF_SERVICE["start_trip"]), trip_id)


def advance(conn, actor, trip_id, to_status, note=None, lat=None, lng=None):
    core.require(actor, "driver.app.execute")
    did = resolve_driver(conn, actor)
    _own_trip(conn, did, trip_id)
    gps = {"lat": lat, "lng": lng} if (lat is not None and lng is not None) else None
    return tp.advance_trip(conn, _svc(actor, *_SELF_SERVICE["advance"]), trip_id, to_status, note=note, gps=gps)


def ping(conn, actor, trip_id, progress=None, lat=None, lng=None):
    core.require(actor, "driver.app.execute")
    did = resolve_driver(conn, actor)
    _own_trip(conn, did, trip_id)
    return tp.record_gps_ping(conn, _svc(actor, *_SELF_SERVICE["ping"]), trip_id, progress=progress, lat=lat, lng=lng)


def submit_pod(conn, actor, trip_id, kind="POD", evidence_types=None, **attrs):
    core.require(actor, "driver.app.execute")
    did = resolve_driver(conn, actor)
    _own_trip(conn, did, trip_id)
    return tp.submit_proof(conn, _svc(actor, *_SELF_SERVICE["pod"]), trip_id, kind, evidence_types=evidence_types, **attrs)


def accept_delivery(conn, actor, trip_id):
    core.require(actor, "driver.app.execute")
    did = resolve_driver(conn, actor)
    _own_trip(conn, did, trip_id)
    return tp.accept_delivery(conn, _svc(actor, *_SELF_SERVICE["accept"]), trip_id)


def verify_recipient_otp(conn, actor, trip_id, code, stop_seq=None):
    """The driver enters the code the RECIPIENT reads to them. The app never issues, resends, or sees a
    code — it only verifies. The verify path returns a result, never the plaintext."""
    core.require(actor, "driver.app.execute")
    did = resolve_driver(conn, actor)
    t = _own_trip(conn, did, trip_id)
    if not t.get("booking_id"):
        raise core.ValidationError("trip has no booking to verify")
    return dv.verify_otp(conn, _svc(actor, *_SELF_SERVICE["verify_otp"]), t["booking_id"], code, stop_seq=stop_seq)


def report_exception(conn, actor, trip_id, exception_type, severity="MEDIUM", description=None):
    core.require(actor, "driver.app.execute")
    did = resolve_driver(conn, actor)
    _own_trip(conn, did, trip_id)
    return tp.open_exception(conn, _svc(actor, *_SELF_SERVICE["exception"]), trip_id, exception_type,
                             severity=severity, description=description)


# --------------------------------------------------------------------------- #
def run_integrity(conn, actor):
    core.require(actor, "driver.app.view")
    checks = []
    orphan = conn.execute("SELECT COUNT(*) c FROM driver_principals p LEFT JOIN mkt_drivers d "
                          "ON d.id=p.driver_id WHERE d.id IS NULL").fetchone()["c"]
    checks.append({"check": "no_orphan_driver_binding", "ok": orphan == 0, "count": orphan})
    dup = conn.execute("SELECT COUNT(*) c FROM (SELECT user_id,driver_id,COUNT(*) n FROM driver_principals "
                       "GROUP BY user_id,driver_id HAVING n>1)").fetchone()["c"]
    checks.append({"check": "no_duplicate_binding", "ok": dup == 0, "count": dup})
    return {"ok": all(x["ok"] for x in checks), "checks": checks}
