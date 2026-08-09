"""LiftHaul Enterprise — Public Nationwide Booking Intake (National Marketplace demand side).

Turns the public `book.html` experience into a REAL governed booking. It does NOT create a parallel
booking system: every public request becomes a canonical `mkt_bookings` row (via the existing
`marketplace_matching.create_booking`) tagged `source=PUBLIC_MARKETPLACE`, and flows into the existing
quotation / matching / Protected Payment / dispatch / tracking domains.

Server owns the truth: geography + inter-island, service classification, quote (real rate engine where
eligible; ENGINEERED jobs go to the estimator queue — never a fabricated "instant price" for a crane
lift), routing candidate, and a safe tracking token. Frontend totals are ignored. Live protected-fund
custody stays OFF; public bookings settle via operator-verified / Wise until the three-flag gate opens.
"""
from __future__ import annotations

import datetime
import json
import secrets

import core
import tenant

ISLAND_GROUPS = ("LUZON", "VISAYAS", "MINDANAO")

# Public vehicle class -> (governed requested_vehicle_category, service_class, indicative base ₱, per-km ₱, inter-island sea ₱)
VEHICLE_MAP = {
    "moto":   ("MOTORCYCLE",  "STANDARD",   60,   8,   250),
    "sedan":  ("SEDAN",       "STANDARD",   120,  14,  900),
    "mpv":    ("MPV_SUV",     "STANDARD",   180,  18,  1400),
    "pickup": ("PICKUP",      "STANDARD",   220,  22,  1800),
    "van":    ("VAN_L300",    "STANDARD",   300,  28,  2600),
    "6w":     ("TRUCK_6W",    "STANDARD",   1200, 55,  7500),
    "10w":    ("WING_VAN_10W","STANDARD",   2500, 80,  14000),
    "lowbed": ("LOWBED",      "ENGINEERED", None, None, None),
    "crane":  ("CRANE_RIGGING","ENGINEERED",None, None, None),
}
ENGINEERED_NOTE = "Engineering estimate required — load charts, permits, route survey and ground-bearing checks price this, not a distance formula."


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


SCHEMA_COLUMNS = [
    ("source", "TEXT"), ("contact_name", "TEXT"), ("contact_email", "TEXT"), ("contact_phone", "TEXT"),
    ("tracking_token", "TEXT"), ("service_class", "TEXT"), ("routing_candidate", "TEXT"),
    ("quote_amount", "REAL"), ("quote_status", "TEXT"), ("intended_payment", "TEXT"),
    ("idempotency_key", "TEXT"), ("special_instructions", "TEXT"),
]


def init(conn):
    """Extend the canonical mkt_bookings with nullable public-intake columns (backward-compatible).
    Idempotent + dialect-agnostic: attempt each ADD COLUMN; a duplicate is harmless and rolled back."""
    for col, typ in SCHEMA_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE mkt_bookings ADD COLUMN {col} {typ}")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass


def seed(conn):
    _guest_shipper(conn)
    return 0


def _platform_tenant():
    import admin_platform as ap
    return getattr(ap, "PLATFORM_TENANT", 0)


def _service_actor():
    """Internal system actor for public intake — never derived from public input; no tenant chosen by user."""
    return {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": _platform_tenant()}


def _guest_shipper(conn):
    """A single platform-scoped guest shipper that public requests attach to until staff assign a real
    customer. Keeps one canonical booking identity; no per-request duplicate company records."""
    row = conn.execute("SELECT id FROM mkt_shippers WHERE legal_name=? LIMIT 1",
                        ("Public Marketplace (Guest Intake)",)).fetchone()
    if row:
        return row["id"] if not isinstance(row, tuple) else row[0]
    cur = conn.execute(
        "INSERT INTO mkt_shippers(tenant_id,applicant_type,legal_name,status,created_at) VALUES(?,?,?,?,?)",
        (_platform_tenant(), "CORPORATION", "Public Marketplace (Guest Intake)", "ACTIVE", _now()))
    conn.commit()
    return cur.lastrowid


# --------------------------------------------------------------------------- #
# Server-authoritative classification
# --------------------------------------------------------------------------- #
def classify_route(o_island, d_island):
    o = (o_island or "").upper(); d = (d_island or "").upper()
    if o not in ISLAND_GROUPS or d not in ISLAND_GROUPS:
        raise core.ValidationError("origin/destination island group must be Luzon, Visayas or Mindanao")
    inter = o != d
    return {"route_class": ("INTER_ISLAND" if inter else f"DOMESTIC_{o}"),
            "inter_island": inter, "multi_leg": inter}


def classify_service(vehicle):
    m = VEHICLE_MAP.get(vehicle)
    if not m:
        raise core.ValidationError(f"unknown vehicle class '{vehicle}'")
    return {"requested_vehicle_category": m[0], "service_class": m[1]}


def quote(conn, vehicle, km, inter_island):
    """Server-side estimate. Real rate card when one exists for the equipment; else a server-owned
    indicative rate for STANDARD classes. ENGINEERED classes NEVER get a fabricated instant price."""
    veh, cls, base, perkm, sea = VEHICLE_MAP[vehicle]
    if cls == "ENGINEERED":
        return {"amount": None, "status": "ESTIMATE_REQUIRED", "note": ENGINEERED_NOTE}
    km = float(km or 0)
    if km <= 0:
        return {"amount": None, "status": "ESTIMATE_REQUIRED", "note": "distance required for a standard quote"}
    # Prefer the governed rate engine if a card is configured for this equipment.
    try:
        import rates
        rc = rates.resolve_rate(conn, veh)
        if rc and rc.get("standard_rate"):
            line = rates.price_line(rc["standard_rate"], 1, max(1, round(km / 200)), 0.0, rc.get("internal_cost", 0.0))
            amt = round(line["total"] + (sea if inter_island else 0))
            return {"amount": amt, "status": "QUOTED", "source": "rate_card"}
    except Exception:
        pass
    amt = round(base + perkm * km + (sea if inter_island else 0))
    return {"amount": amt, "status": "QUOTED_INDICATIVE", "source": "server_tariff"}


def routing_candidate(service_class):
    return "ENGINEERED_REVIEW" if service_class == "ENGINEERED" else "MARKETPLACE_CANDIDATE"


# --------------------------------------------------------------------------- #
# Intake
# --------------------------------------------------------------------------- #
_REQUIRED = ("contact_name", "origin_island", "dest_island", "vehicle")


def submit(conn, payload):
    if not isinstance(payload, dict):
        raise core.ValidationError("invalid payload")
    for k in _REQUIRED:
        if not str(payload.get(k, "")).strip():
            raise core.ValidationError(f"missing required field: {k}")
    if not (str(payload.get("contact_phone", "")).strip() or str(payload.get("contact_email", "")).strip()):
        raise core.ValidationError("a contact phone or email is required")
    # length / safety caps (defence-in-depth; server layer also caps body size)
    for k, v in list(payload.items()):
        if isinstance(v, str) and len(v) > 2000:
            raise core.ValidationError(f"field too long: {k}")

    idem = str(payload.get("idempotency_key", "")).strip() or None
    if idem:
        prev = conn.execute("SELECT tracking_token FROM mkt_bookings WHERE idempotency_key=? LIMIT 1", (idem,)).fetchone()
        if prev:
            tok = prev["tracking_token"] if not isinstance(prev, tuple) else prev[0]
            return track(conn, tok)  # idempotent replay

    route = classify_route(payload.get("origin_island"), payload.get("dest_island"))
    svc = classify_service(payload.get("vehicle"))
    q = quote(conn, payload["vehicle"], payload.get("km"), route["inter_island"])
    routing = routing_candidate(svc["service_class"])
    intended = "protected" if str(payload.get("payment", "protected")).lower().startswith("prot") else "operator"

    actor = _service_actor()
    shipper_id = _guest_shipper(conn)
    import marketplace_matching as mm
    bid = mm.create_booking(
        conn, actor, shipper_id, "general",
        (payload.get("origin_island") or "").upper(), (payload.get("dest_island") or "").upper(),
        service_type=svc["service_class"], requested_vehicle_category=svc["requested_vehicle_category"],
        inter_island=1 if route["inter_island"] else 0, route_class=route["route_class"],
        pickup_address=str(payload.get("origin_city", ""))[:400],
        delivery_address=str(payload.get("dest_city", ""))[:400],
        weight_kg=_num(payload.get("weight_kg")), cargo_description=str(payload.get("cargo", ""))[:400])

    token = "pbk_" + secrets.token_urlsafe(18)
    ref = "LH-" + ("II" if route["inter_island"] else "") + token[-6:].upper()
    conn.execute(
        "UPDATE mkt_bookings SET source='PUBLIC_MARKETPLACE', status='REQUEST_RECEIVED', "
        "contact_name=?, contact_email=?, contact_phone=?, tracking_token=?, service_class=?, "
        "routing_candidate=?, quote_amount=?, quote_status=?, intended_payment=?, idempotency_key=?, "
        "special_instructions=?, payment_status='PROTECTED_PENDING', quotation_status=?, updated_at=? WHERE id=?",
        (str(payload.get("contact_name"))[:200], str(payload.get("contact_email", ""))[:200],
         str(payload.get("contact_phone", ""))[:60], token, svc["service_class"], routing,
         q.get("amount"), q["status"], intended, idem, str(payload.get("notes", ""))[:2000],
         ("ESTIMATE_REQUIRED" if q["status"] == "ESTIMATE_REQUIRED" else "AUTO_QUOTED"), _now(), bid))
    # Protected Payment: eligibility recorded; NO live transaction (no carrier yet, funds gate OFF).
    import marketplace_payments as pay
    live = pay.live_funds_enabled(conn)
    core.audit(conn, actor, "PUBLIC_BOOKING_CREATED", "mkt_bookings", bid, None,
               {"ref": ref, "route": route["route_class"], "service_class": svc["service_class"],
                "routing": routing, "quote_status": q["status"], "live_funds": live})
    conn.commit()

    return {
        "ref": ref, "tracking_token": token, "booking_id": bid, "status": "REQUEST_RECEIVED",
        "service": ("Inter-Island" if route["inter_island"] else "Domestic"),
        "inter_island": route["inter_island"], "service_class": svc["service_class"],
        "routing_candidate": routing,
        "estimate": q.get("amount"), "estimate_status": q["status"], "estimate_note": q.get("note"),
        "protected_payment": {"eligible": True, "live_funds_enabled": live, "intended_method": intended},
        "next_step": ("Engineering estimate — our team returns a priced lift plan"
                      if q["status"] == "ESTIMATE_REQUIRED"
                      else "Quotation review → carrier matching"),
    }


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Public tracking (by safe token only — never a sequential id, never a tenant leak)
# --------------------------------------------------------------------------- #
_STAGES = ["Request Received", "Quotation", "Payment", "Matching", "Assigned", "Pickup", "In Transit",
           "Destination", "Out for Delivery", "Delivered", "POD", "Settled"]
_STAGES_II = ["Request Received", "Quotation", "Payment", "Matching", "Assigned", "Pickup", "In Transit",
              "At Port", "Sea Leg", "Destination Port", "Out for Delivery", "Delivered", "POD", "Settled"]
_STATUS_STAGE = {"REQUEST_RECEIVED": 0, "QUOTED": 1, "PAYMENT_PENDING": 2, "MATCHING": 3,
                 "ASSIGNMENT_PENDING": 4, "ASSIGNED": 4, "IN_TRANSIT": 6, "DELIVERED": 9, "SETTLED": 11}


def track(conn, token):
    if not token or not str(token).startswith("pbk_"):
        raise core.NotFoundError("booking not found")
    r = conn.execute(
        "SELECT id,tracking_token,status,service_class,inter_island,route_class,quote_amount,quote_status,"
        "quotation_status,payment_status,assignment_status,contact_name FROM mkt_bookings WHERE tracking_token=?",
        (token,)).fetchone()
    if not r:
        raise core.NotFoundError("booking not found")
    d = dict(r)
    ii = bool(d.get("inter_island"))
    stages = _STAGES_II if ii else _STAGES
    cur = _STATUS_STAGE.get(d.get("status") or "REQUEST_RECEIVED", 0)
    ref = "LH-" + ("II" if ii else "") + token[-6:].upper()
    return {
        "ref": ref, "status": d.get("status"), "service": ("Inter-Island" if ii else "Domestic"),
        "service_class": d.get("service_class"), "inter_island": ii,
        "estimate": d.get("quote_amount"), "estimate_status": d.get("quote_status"),
        "stages": [{"name": s, "state": ("done" if i < cur else "current" if i == cur else "upcoming")}
                   for i, s in enumerate(stages)],
    }


# --------------------------------------------------------------------------- #
# Admin — public requests appear in the existing booking queue
# --------------------------------------------------------------------------- #
def admin_queue(conn, actor, limit=100):
    core.require(actor, "marketplace.booking.view")
    frag, params = tenant.predicate(actor)
    rows = conn.execute(
        "SELECT id,tracking_token,status,service_type,service_class,routing_candidate,inter_island,"
        "route_class,requested_vehicle_category,quote_amount,quote_status,contact_name,contact_phone,"
        "contact_email,created_at FROM mkt_bookings WHERE source='PUBLIC_MARKETPLACE'" + frag +
        " ORDER BY id DESC LIMIT ?", list(params) + [limit]).fetchall()
    return {"source": "PUBLIC_MARKETPLACE", "count": len(rows), "requests": [dict(r) for r in rows]}
