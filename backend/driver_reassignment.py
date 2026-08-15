"""Driver Reassignment / Re-matching — governed orchestration over the EXISTING matching primitives.

When an assigned resource falls through (driver sick / no-show, vehicle breakdown, licence expiry,
compliance lapse, carrier suspension, shipper request, ops-forced), the work must move to another
eligible resource WITHOUT losing tenant/RBAC/audit governance and WITHOUT ever moving or fabricating
protected funds. This module does NOT re-implement carriers, vehicles, drivers, matching, offers,
assignments or protected payment — it composes them:

  * INTRA-CARRIER SUBSTITUTION — swap in another driver/vehicle from the SAME carrier via the existing
    `marketplace_matching.request_substitution`, which deterministically re-runs every eligibility gate
    (carrier active + compliant, vehicle ACTIVE + eligible, driver assignable). Fail-closed.
  * INTER-CARRIER RE-MATCH — when the carrier cannot substitute (or is itself blocked), release the
    current carrier, return the booking to MATCHING and re-open the broadcast to other eligible carriers
    via the existing `generate_candidates` + `create_broadcast`. Ops authority required.

The ONLY new table is `mkt_reassignments` — a case/ledger recording why a reassignment happened, what
it moved from/to, and its outcome. Protected Payment is never touched here: a reassignment asserts
`funds_moved: False`, refuses once release/settlement is under way, and leaves the protected transaction
intact for the existing release gate to govern at settlement.
"""
from __future__ import annotations

import datetime
import json

import core
import tenant
import marketplace_matching as mm
import marketplace_onboarding as ob
import marketplace_trust_closure as tc


REASONS = (
    "DRIVER_UNAVAILABLE", "DRIVER_NO_SHOW", "VEHICLE_BREAKDOWN", "LICENSE_EXPIRED",
    "COMPLIANCE_LAPSED", "CARRIER_SUSPENDED", "SHIPPER_REQUESTED", "OPS_FORCED",
)
STATES = ("OPEN", "SUBSTITUTED", "REMATCH_INITIATED", "CANCELLED")
SCOPES = ("INTRA_CARRIER", "INTER_CARRIER")
SEVERITIES = ("NORMAL", "HIGH")

# Assignment states that can still be reassigned (before the trip has fully completed).
_REASSIGNABLE_ASSIGNMENT = ("PENDING_CONFIRMATION", "CONFIRMED", "PAYMENT_REQUIRED", "PAYMENT_PENDING",
                            "READY_FOR_TRIP_ACTIVATION", "REASSIGNMENT_REQUIRED")
# Protected-payment states where funds are already releasing/settled — reassignment is refused.
_FUNDS_LOCKED = ("RELEASE_APPROVED", "RELEASE_REQUESTED", "RELEASE_CONFIRMED", "SETTLED")
# Trip states that mean the driver is actively executing — reassignment becomes HIGH severity.
_TRIP_ACTIVE = ("ACTIVATED", "EN_ROUTE_PICKUP", "ARRIVED_PICKUP", "LOADING", "LOADED", "DEPARTED",
                "IN_TRANSIT", "CHECKPOINT", "ARRIVED_DESTINATION", "UNLOADING")
_TRIP_DONE = ("DELIVERED", "POD_SUBMITTED", "CLIENT_ACCEPTED", "COMPLETED")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS mkt_reassignments(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  assignment_id INTEGER NOT NULL,
  booking_id INTEGER,
  carrier_id INTEGER,
  from_driver_id INTEGER, from_vehicle_id INTEGER,
  to_driver_id INTEGER, to_vehicle_id INTEGER,
  to_carrier_id INTEGER,
  reason TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'INTRA_CARRIER',
  severity TEXT NOT NULL DEFAULT 'NORMAL',
  status TEXT NOT NULL DEFAULT 'OPEN',
  evidence TEXT,
  funds_moved INTEGER NOT NULL DEFAULT 0,
  opened_by INTEGER, opened_at TEXT,
  closed_by INTEGER, closed_at TEXT,
  correlation_id TEXT);
"""


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    return


# --------------------------------------------------------------------------- #
def _row(conn, table, id):
    r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (id,)).fetchone()
    if not r:
        raise core.NotFoundError(f"{table} row {id} not found")
    return dict(r)


def _case(conn, actor, reassignment_id):
    r = _row(conn, "mkt_reassignments", reassignment_id)
    tenant.guard(actor, r)
    return r


def _active_trip(conn, assignment_id):
    return conn.execute("SELECT * FROM mkt_trips WHERE assignment_id=? ORDER BY id DESC LIMIT 1",
                        (assignment_id,)).fetchone()


def _protected_tx(conn, booking_id):
    return conn.execute("SELECT * FROM mkt_protected_tx WHERE booking_id=? ORDER BY id DESC LIMIT 1",
                        (booking_id,)).fetchone()


def _assert_funds_safe(conn, booking_id):
    """A reassignment must never occur once funds are releasing/settled, and must never move funds."""
    tx = _protected_tx(conn, booking_id)
    if tx and tx["state"] in _FUNDS_LOCKED:
        raise core.ConflictError(
            f"reassignment refused: protected payment is {tx['state']} (release/settlement under way). "
            f"Funds are never moved by a reassignment.")
    return tx


# --------------------------------------------------------------------------- #
# Open a reassignment case
# --------------------------------------------------------------------------- #
def open_reassignment(conn, actor, assignment_id, reason, evidence=None, scope="INTRA_CARRIER"):
    core.require(actor, "marketplace.reassignment.open")
    if reason not in REASONS:
        raise core.ValidationError(f"invalid reason '{reason}' (expected one of {REASONS})")
    if scope not in SCOPES:
        raise core.ValidationError(f"invalid scope '{scope}'")
    a = _row(conn, "mkt_assignments", assignment_id)
    tenant.guard(actor, a)
    if a["status"] not in _REASSIGNABLE_ASSIGNMENT:
        raise core.ConflictError(f"assignment {assignment_id} is {a['status']} and cannot be reassigned")

    _assert_funds_safe(conn, a["booking_id"])

    # mid-trip reassignment is higher severity and needs evidence
    trip = _active_trip(conn, assignment_id)
    severity = "NORMAL"
    if trip and trip["status"] in _TRIP_ACTIVE:
        severity = "HIGH"
        if not evidence:
            raise core.ValidationError("mid-trip reassignment requires evidence (trip is active)")
    if trip and trip["status"] in _TRIP_DONE:
        raise core.ConflictError("trip already delivered/completed — reassignment not applicable")

    cur = conn.execute(
        "INSERT INTO mkt_reassignments(assignment_id,booking_id,carrier_id,from_driver_id,from_vehicle_id,"
        "reason,scope,severity,status,evidence,funds_moved,opened_by,opened_at,correlation_id) "
        "VALUES(?,?,?,?,?,?,?,?, 'OPEN', ?, 0, ?,?,?)",
        (assignment_id, a["booking_id"], a["carrier_id"], a["driver_id"], a["vehicle_id"],
         reason, scope, severity, evidence, actor["id"], _now(), core.correlation_id()))
    rid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_reassignments", rid)
    core.audit(conn, actor, "REASSIGNMENT_OPENED", "mkt_reassignments", rid, None,
               {"assignment": assignment_id, "reason": reason, "scope": scope, "severity": severity})
    conn.commit()
    return {"reassignment_id": rid, "status": "OPEN", "severity": severity, "scope": scope}


# --------------------------------------------------------------------------- #
# Intra-carrier substitution (reuses the deterministic eligibility re-check)
# --------------------------------------------------------------------------- #
def propose_substitute(conn, actor, reassignment_id, new_driver_id=None, new_vehicle_id=None):
    core.require(actor, "marketplace.reassignment.substitute")
    r = _case(conn, actor, reassignment_id)
    if r["status"] != "OPEN":
        raise core.ConflictError(f"reassignment {reassignment_id} is {r['status']}")
    if new_driver_id is None and new_vehicle_id is None:
        raise core.ValidationError("a new driver and/or vehicle is required")
    _assert_funds_safe(conn, r["booking_id"])

    # the substitute must belong to the SAME carrier (intra-carrier substitution)
    for tbl, _id in (("mkt_drivers", new_driver_id), ("mkt_vehicles", new_vehicle_id)):
        if _id is not None:
            owner = conn.execute(f"SELECT carrier_id FROM {tbl} WHERE id=?", (_id,)).fetchone()
            if not owner or owner["carrier_id"] != r["carrier_id"]:
                raise core.ForbiddenError("substitute resource must belong to the same carrier — "
                                          "use inter-carrier re-match instead")

    # reuse the governed substitution: it re-runs every eligibility gate, fail-closed
    res = mm.request_substitution(conn, actor, r["assignment_id"],
                                  new_vehicle_id=new_vehicle_id, new_driver_id=new_driver_id)
    if not res.get("ok"):
        core.audit(conn, actor, "REASSIGNMENT_SUBSTITUTE_REJECTED", "mkt_reassignments", reassignment_id,
                   None, {"reasons": res.get("reasons")})
        conn.commit()
        return {"ok": False, "reasons": res.get("reasons"), "status": "OPEN"}

    conn.execute("UPDATE mkt_reassignments SET status='SUBSTITUTED',to_driver_id=?,to_vehicle_id=?,"
                 "closed_by=?,closed_at=? WHERE id=?",
                 (res.get("driver_id") or new_driver_id, res.get("vehicle_id") or new_vehicle_id,
                  actor["id"], _now(), reassignment_id))
    core.audit(conn, actor, "REASSIGNMENT_SUBSTITUTED", "mkt_reassignments", reassignment_id, None,
               {"to_driver": res.get("driver_id"), "to_vehicle": res.get("vehicle_id"), "funds_moved": False})
    conn.commit()
    return {"ok": True, "status": "SUBSTITUTED", "vehicle_id": res.get("vehicle_id"),
            "driver_id": res.get("driver_id"), "funds_moved": False}


# --------------------------------------------------------------------------- #
# Inter-carrier re-match (ops authority; releases the current carrier)
# --------------------------------------------------------------------------- #
def escalate_to_rematch(conn, actor, reassignment_id, response_minutes=120):
    core.require(actor, "marketplace.reassignment.rematch")
    r = _case(conn, actor, reassignment_id)
    if r["status"] != "OPEN":
        raise core.ConflictError(f"reassignment {reassignment_id} is {r['status']}")
    tx = _assert_funds_safe(conn, r["booking_id"])

    a = _row(conn, "mkt_assignments", r["assignment_id"])
    # release the current carrier's assignment and return the booking to matching
    conn.execute("UPDATE mkt_assignments SET status='REASSIGNMENT_REQUIRED',updated_by=?,updated_at=? WHERE id=?",
                 (actor["id"], _now(), r["assignment_id"]))
    conn.execute("UPDATE mkt_bookings SET status='MATCHING',assignment_status='REASSIGNMENT_REQUIRED',"
                 "updated_by=?,updated_at=? WHERE id=?", (actor["id"], _now(), r["booking_id"]))

    # re-open matching to OTHER eligible carriers (reuses candidate generation + broadcast; the released
    # carrier is recorded so ranking/selection can avoid it). Never fabricates a carrier.
    rematch = None
    try:
        mm.generate_candidates(conn, actor, r["booking_id"])
        rematch = mm.create_broadcast(conn, actor, r["booking_id"], response_minutes=response_minutes)
    except Exception as e:
        rematch = {"broadcast": None, "note": f"candidates/broadcast deferred: {e}"}

    conn.execute("UPDATE mkt_reassignments SET status='REMATCH_INITIATED',scope='INTER_CARRIER',"
                 "closed_by=?,closed_at=? WHERE id=?", (actor["id"], _now(), reassignment_id))
    core.audit(conn, actor, "REASSIGNMENT_REMATCH_INITIATED", "mkt_reassignments", reassignment_id, None,
               {"released_carrier": r["carrier_id"], "booking": r["booking_id"],
                "protected_state": (tx["state"] if tx else None), "funds_moved": False})
    conn.commit()
    return {"status": "REMATCH_INITIATED", "released_carrier": r["carrier_id"],
            "booking_id": r["booking_id"], "rematch": rematch, "funds_moved": False}


def cancel_reassignment(conn, actor, reassignment_id, reason):
    core.require(actor, "marketplace.reassignment.open")
    r = _case(conn, actor, reassignment_id)
    if r["status"] != "OPEN":
        raise core.ConflictError(f"reassignment {reassignment_id} is {r['status']}")
    conn.execute("UPDATE mkt_reassignments SET status='CANCELLED',closed_by=?,closed_at=? WHERE id=?",
                 (actor["id"], _now(), reassignment_id))
    core.audit(conn, actor, "REASSIGNMENT_CANCELLED", "mkt_reassignments", reassignment_id, None,
               {"reason": reason})
    conn.commit()
    return {"status": "CANCELLED"}


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
def list_reassignments(conn, actor, status=None, carrier_id=None):
    core.require(actor, "marketplace.reassignment.view")
    frag, params = tenant.predicate(actor)
    q = "SELECT * FROM mkt_reassignments WHERE 1=1" + frag
    a = list(params)
    if status:
        q += " AND status=?"; a.append(status)
    if carrier_id:
        q += " AND carrier_id=?"; a.append(carrier_id)
    q += " ORDER BY id DESC"
    return [dict(x) for x in conn.execute(q, a).fetchall()]


def get_reassignment(conn, actor, reassignment_id):
    core.require(actor, "marketplace.reassignment.view")
    return _case(conn, actor, reassignment_id)


def reassignment_timeline(conn, actor, reassignment_id):
    core.require(actor, "marketplace.reassignment.view")
    _case(conn, actor, reassignment_id)   # tenant guard
    events = core.list_audit(conn, "mkt_reassignments", reassignment_id)
    return {"reassignment_id": reassignment_id, "events": events}


def queues(conn, actor):
    core.require(actor, "marketplace.reassignment.view")
    frag, params = tenant.predicate(actor)

    def cnt(extra):
        return conn.execute("SELECT COUNT(*) c FROM mkt_reassignments WHERE 1=1" + frag + extra, params).fetchone()["c"]
    return {
        "open": cnt(" AND status='OPEN'"),
        "substituted": cnt(" AND status='SUBSTITUTED'"),
        "rematch_initiated": cnt(" AND status='REMATCH_INITIATED'"),
        "high_severity_open": cnt(" AND status='OPEN' AND severity='HIGH'"),
    }


def run_integrity(conn, actor):
    core.require(actor, "marketplace.reassignment.view")
    checks = []
    orphan = conn.execute("SELECT COUNT(*) c FROM mkt_reassignments r LEFT JOIN mkt_assignments a "
                          "ON a.id=r.assignment_id WHERE a.id IS NULL").fetchone()["c"]
    checks.append({"check": "no_orphan_reassignment", "ok": orphan == 0, "count": orphan})
    funds = conn.execute("SELECT COUNT(*) c FROM mkt_reassignments WHERE funds_moved<>0").fetchone()["c"]
    checks.append({"check": "no_reassignment_moved_funds", "ok": funds == 0, "count": funds})
    return {"ok": all(x["ok"] for x in checks), "checks": checks}
