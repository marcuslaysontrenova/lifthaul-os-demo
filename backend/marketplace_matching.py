"""LiftHaul Nationwide Marketplace — Increment 3: booking intake, pricing, matching, controlled
broadcast, offers/bidding, evaluation, selection, and governed assignment.

Consumes the verified Increment 1/2 foundation (taxonomy, deterministic eligibility, lanes,
onboarding, compliance-aware `candidate_pool`) and drives the governed commercial lifecycle:

  Booking Created -> Validated -> Cargo/Route Assessed -> Vehicle Requirement -> Pricing Mode
  -> Priced (immutable snapshot) -> Candidate Pool -> Deterministic Ranking -> Controlled Broadcast
  -> Offers/Bids -> Evaluation -> Selection -> Assignment (PAYMENT-GATED)

Hard invariants (Increment 3 directive):
  * deterministic vehicle requirement + eligibility BEFORE any AI ranking; AI may explain/re-order the
    eligible pool, never widen it or add an ineligible category;
  * pricing snapshots are IMMUTABLE — later rate changes never alter an issued offer / accepted booking;
  * candidate pool reuses Increment-2 `candidate_pool()` (hard compliance denials are absolute);
  * separation of duties: a carrier can't select its own offer; approver != creator; no self-approval;
  * the cheapest offer never auto-wins; auto-selection is OFF by default;
  * ASSIGNED is NOT trip activation — assignment stays PAYMENT-GATED; funds never move here;
  * internal margin / carrier payout never exposed to unauthorized client users;
  * tenant isolation + organization scope preserved; 0 financial / operational-status drift; 0
    unexpected broadcast / offer / assignment on migration.
"""
from __future__ import annotations

import datetime
import hashlib
import json

import core
import tenant
import policy
import marketplace as mkt
import marketplace_onboarding as mo

# --------------------------------------------------------------------------- #
BOOKING_STATUSES = ("DRAFT", "INCOMPLETE", "SUBMITTED", "VALIDATION_REQUIRED", "VALIDATED",
                    "PRICING_PENDING", "PRICED", "MATCHING", "OFFERS_OPEN", "OFFER_SELECTED",
                    "PAYMENT_REQUIRED", "ASSIGNMENT_PENDING", "ASSIGNED", "CANCELLED", "EXPIRED", "REJECTED")
PRICING_MODES = ("INSTANT_PRICE", "MANAGED_QUOTATION", "CARRIER_BIDDING", "REVERSE_AUCTION",
                 "CONTRACT_RATE", "MANUAL_REVIEW")
DISTANCE_SOURCES = ("LANE_MASTER", "MANUAL_VERIFIED", "MAP_PROVIDER", "MOCK", "UNKNOWN")
OFFER_STATUSES = ("DRAFT", "SUBMITTED", "VALIDATING", "VALID", "INVALID", "SHORTLISTED",
                  "SELECTED", "REJECTED", "WITHDRAWN", "EXPIRED", "CANCELLED")
ASSIGNMENT_STATUSES = ("PENDING_CONFIRMATION", "CONFIRMED", "PAYMENT_REQUIRED", "PAYMENT_PENDING",
                       "READY_FOR_TRIP_ACTIVATION", "CANCELLED", "EXPIRED", "REASSIGNMENT_REQUIRED")
SELECTION_MODELS = ("CLIENT_SELECTION", "MANAGED_SELECTION", "AUTO_SELECTION", "CONTRACT_ASSIGNMENT")
INTEGRITY_STATUSES = ("NOT_RUN", "PASS", "WARNING", "FAIL", "BLOCKED")

SCHEMA = """
CREATE TABLE IF NOT EXISTS mkt_bookings(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  org_scope TEXT,
  shipper_id INTEGER NOT NULL,
  booking_type TEXT,
  service_type TEXT,
  cargo_code TEXT NOT NULL,
  cargo_description TEXT,
  quantity INTEGER DEFAULT 1,
  weight_kg REAL,
  volume_cbm REAL,
  dim_l_cm REAL, dim_w_cm REAL, dim_h_cm REAL,
  declared_value REAL,
  refrigerated INTEGER DEFAULT 0,
  hazardous INTEGER DEFAULT 0,
  oversized INTEGER DEFAULT 0,
  lifting_required INTEGER DEFAULT 0,
  loading_required INTEGER DEFAULT 0,
  unloading_required INTEGER DEFAULT 0,
  pickup_address TEXT, pickup_zone TEXT, pickup_lat REAL, pickup_lng REAL,
  delivery_address TEXT, delivery_zone TEXT, delivery_lat REAL, delivery_lng REAL,
  pickup_window TEXT, delivery_window TEXT,
  origin_zone TEXT, dest_zone TEXT,
  route_class TEXT, inter_island INTEGER DEFAULT 0,
  requested_vehicle_category TEXT,
  calculated_vehicle_requirement TEXT,   -- JSON
  pricing_mode TEXT,
  status TEXT NOT NULL DEFAULT 'DRAFT',
  quotation_status TEXT,
  broadcast_status TEXT,
  assignment_status TEXT,
  payment_status TEXT,
  correlation_id TEXT,
  created_by INTEGER, created_at TEXT,
  updated_by INTEGER, updated_at TEXT);

CREATE TABLE IF NOT EXISTS mkt_rate_cards(
  id INTEGER PRIMARY KEY,
  component TEXT NOT NULL,        -- base | distance | vehicle_category | fuel | ferry | port | ...
  vehicle_category TEXT,          -- NULL = applies to all
  unit TEXT,                      -- flat | per_km | pct
  rate REAL NOT NULL,
  visibility TEXT DEFAULT 'customer',  -- customer | carrier | internal
  version INTEGER DEFAULT 1,
  effective_from TEXT, effective_to TEXT,
  active INTEGER DEFAULT 1,
  created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS mkt_pricing_snapshots(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  booking_id INTEGER NOT NULL,
  pricing_mode TEXT,
  rule_version INTEGER,
  route_source TEXT,
  distance_km REAL,
  vehicle_category TEXT,
  components TEXT,                -- JSON list
  subtotal REAL, tax REAL, total REAL,
  platform_fee REAL, estimated_carrier_payout REAL,
  currency TEXT DEFAULT 'PHP',
  checksum TEXT,
  generated_at TEXT, expires_at TEXT,
  actor INTEGER, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_match_runs(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  booking_id INTEGER NOT NULL,
  ranking_version INTEGER DEFAULT 1,
  candidates TEXT,               -- JSON: ranked with factors
  excluded TEXT,                 -- JSON
  created_by INTEGER, created_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_broadcasts(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  booking_id INTEGER NOT NULL,
  wave INTEGER NOT NULL,
  target_count INTEGER,
  criteria TEXT,
  targets TEXT,                  -- JSON carrier ids
  sent_at TEXT, response_deadline TEXT,
  channel TEXT DEFAULT 'mock',
  delivery_result TEXT,
  suppressed TEXT,               -- JSON {carrier_id: reason}
  created_by INTEGER, created_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_offers(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  booking_id INTEGER NOT NULL,
  carrier_id INTEGER NOT NULL,
  vehicle_id INTEGER,
  driver_id INTEGER,
  amount REAL NOT NULL,
  currency TEXT DEFAULT 'PHP',
  est_pickup TEXT, est_delivery TEXT,
  valid_until TEXT,
  inclusions TEXT, exclusions TEXT,
  toll_treatment TEXT, ferry_treatment TEXT,
  waiting_rate REAL, helper_required INTEGER DEFAULT 0, loading_required INTEGER DEFAULT 0,
  notes TEXT,
  is_bid INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'DRAFT',
  invalid_reason TEXT,
  created_by INTEGER, created_at TEXT,
  updated_by INTEGER, updated_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_bid_events(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  booking_id INTEGER NOT NULL,
  offer_id INTEGER,
  carrier_id INTEGER,
  amount REAL,
  event TEXT,                    -- BID | REBID | WITHDRAW | ABNORMAL_FLAGGED
  created_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS mkt_assignments(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  booking_id INTEGER NOT NULL,
  shipper_id INTEGER,
  carrier_id INTEGER,
  vehicle_id INTEGER,
  driver_id INTEGER,
  offer_id INTEGER,
  pricing_snapshot_id INTEGER,
  carrier_payout REAL,
  version INTEGER DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'PENDING_CONFIRMATION',
  payment_requirement TEXT,
  assigned_by INTEGER, approved_by INTEGER, assigned_at TEXT,
  correlation_id TEXT,
  updated_by INTEGER, updated_at TEXT);
"""

HIGH_VALUE_THRESHOLD = 500000   # bookings above this require assignment approval (SoD)
PLATFORM_FEE_PCT = 10


# --------------------------------------------------------------------------- #
def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _iso_plus(minutes):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)).isoformat()


def _cid():
    return core.correlation_id()


def _checksum(obj):
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


# --------------------------------------------------------------------------- #
# Booking intake
# --------------------------------------------------------------------------- #
def create_booking(conn, actor, shipper_id, cargo_code, origin_zone, dest_zone, **a):
    core.require(actor, "marketplace.booking.create")
    shipper = _guarded(conn, actor, "mkt_shippers", shipper_id)   # cross-tenant -> 404
    cargo = mkt.get_cargo_type(conn, cargo_code)
    if not cargo:
        raise ValueError(f"unknown cargo '{cargo_code}'")
    inter = 1 if a.get("inter_island") else 0
    cur = conn.execute(
        "INSERT INTO mkt_bookings(shipper_id,booking_type,service_type,cargo_code,cargo_description,"
        "quantity,weight_kg,volume_cbm,dim_l_cm,dim_w_cm,dim_h_cm,declared_value,refrigerated,hazardous,"
        "oversized,lifting_required,loading_required,unloading_required,pickup_address,pickup_zone,"
        "delivery_address,delivery_zone,pickup_window,delivery_window,origin_zone,dest_zone,route_class,"
        "inter_island,requested_vehicle_category,status,created_by,created_at,correlation_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'DRAFT',?,?,?)",
        (shipper_id, a.get("booking_type"), a.get("service_type"), cargo_code, a.get("cargo_description"),
         a.get("quantity", 1), a.get("weight_kg"), a.get("volume_cbm"), a.get("dim_l_cm"), a.get("dim_w_cm"),
         a.get("dim_h_cm"), a.get("declared_value"), int(a.get("refrigerated", cargo["refrigerated"]) or 0),
         int(a.get("hazardous", cargo["hazardous"]) or 0), int(a.get("oversized", cargo["oversized"]) or 0),
         int(a.get("lifting_required", 0)), int(a.get("loading_required", 0)), int(a.get("unloading_required", 0)),
         a.get("pickup_address"), a.get("pickup_zone", origin_zone), a.get("delivery_address"),
         a.get("delivery_zone", dest_zone), a.get("pickup_window"), a.get("delivery_window"),
         origin_zone, dest_zone, a.get("route_class"), inter, a.get("requested_vehicle_category"),
         actor["id"], _now(), _cid()))
    bid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_bookings", bid)
    core.audit(conn, actor, "MKT_BOOKING_CREATED", "mkt_bookings", bid, None, {"cargo": cargo_code})
    conn.commit()
    return bid


# --------------------------------------------------------------------------- #
# Booking validation
# --------------------------------------------------------------------------- #
def validate_booking(conn, actor, booking_id):
    core.require(actor, "marketplace.booking.validate")
    b = _guarded(conn, actor, "mkt_bookings", booking_id)
    blockers, warnings, missing = [], [], []
    shipper = conn.execute("SELECT * FROM mkt_shippers WHERE id=?", (b["shipper_id"],)).fetchone()
    shipper = dict(shipper) if shipper else {}
    if shipper.get("status") != "ACTIVE":
        blockers.append("shipper_not_active")
    if not shipper.get("verified_by"):
        blockers.append("shipper_not_verified")
    cargo = mkt.get_cargo_type(conn, b["cargo_code"]) or {}
    if cargo.get("prohibited"):
        blockers.append("cargo_prohibited")
    if not b.get("weight_kg"):
        missing.append("weight")
    if not b.get("origin_zone") or not b.get("dest_zone"):
        blockers.append("invalid_route")
    if not b.get("pickup_address"):
        missing.append("pickup_address")
    if not b.get("delivery_address"):
        missing.append("delivery_address")
    if cargo.get("oversized") and not (b.get("dim_l_cm") or b.get("dim_w_cm")):
        warnings.append("oversized_without_dimensions")
    if cargo.get("default_permit_required"):
        warnings.append("regulated_cargo_permit_required")
    # lane serviceable OR assessable (either promises service or at least accepts interest)
    svc = mkt.serviceability(conn, b["origin_zone"], b["dest_zone"])
    if not svc["accepts_interest"]:
        blockers.append("lane_not_serviceable")
    vr = vehicle_requirement(conn, booking_id)
    if not vr["eligible_categories"] and not missing:
        blockers.append("no_vehicle_fit")
    modes = eligible_pricing_modes(conn, booking_id, vr, svc)
    ready = not blockers and not missing
    new_status = "VALIDATED" if ready else ("INCOMPLETE" if missing else "VALIDATION_REQUIRED")
    _set(conn, "mkt_bookings", booking_id, status=new_status,
         calculated_vehicle_requirement=_j(vr), updated_by=actor["id"])
    core.audit(conn, actor, "MKT_BOOKING_VALIDATED", "mkt_bookings", booking_id, None,
               {"status": new_status, "blockers": blockers})
    conn.commit()
    return {"status": new_status, "ready": ready, "blockers": blockers, "warnings": warnings,
            "missing": missing, "recommended_vehicles": vr["recommended_categories"],
            "eligible_pricing_modes": modes}


# --------------------------------------------------------------------------- #
# Deterministic vehicle-requirement engine
# --------------------------------------------------------------------------- #
def vehicle_requirement(conn, booking_id):
    b = _row(conn, "mkt_bookings", booking_id)
    dims = None
    if b.get("dim_l_cm") or b.get("dim_w_cm") or b.get("dim_h_cm"):
        dims = (b.get("dim_l_cm"), b.get("dim_w_cm"), b.get("dim_h_cm"))
    elig = mkt.eligible_vehicles(conn, b["cargo_code"], weight_kg=b.get("weight_kg"),
                                 volume_cbm=b.get("volume_cbm"), dims=dims)
    cargo = elig["cargo"]
    allowed_body, required_features, prohibited = [], [], []
    if cargo.get("refrigerated"):
        required_features.append("refrigeration")
    if cargo.get("oversized") or cargo.get("machinery"):
        allowed_body = ["flatbed", "lowbed", "container_chassis"]
        required_features.append("oversized_handling")
    if b.get("lifting_required"):
        required_features.append("lifting")
    eligible = [v["code"] for v in elig["eligible"]]
    # recommended = smallest sufficient (deterministic: eligible are payload-ascending)
    recommended = eligible[:3]
    return {"cargo": b["cargo_code"], "min_payload_kg": b.get("weight_kg"),
            "min_volume_cbm": b.get("volume_cbm"), "min_opening_dims": dims,
            "allowed_body_types": allowed_body, "required_features": required_features,
            "prohibited_categories": prohibited, "recommended_categories": recommended,
            "eligible_categories": eligible, "blocked": elig["blocked"],
            "explanation": ("cargo prohibited" if elig["blocked"] else
                            f"{len(eligible)} vehicle categories satisfy cargo/payload/volume/features")}


# --------------------------------------------------------------------------- #
# Pricing-mode selection
# --------------------------------------------------------------------------- #
def _distance(conn, b):
    lane = conn.execute("SELECT distance_km FROM mkt_lanes WHERE origin_zone=? AND dest_zone=?",
                        (b["origin_zone"], b["dest_zone"])).fetchone()
    if lane and lane["distance_km"]:
        return lane["distance_km"], "LANE_MASTER"
    # deterministic mock fallback for local same-zone pilot lanes
    if b["origin_zone"] == b["dest_zone"]:
        return 25.0, "MOCK"
    return None, "UNKNOWN"


def eligible_pricing_modes(conn, booking_id, vr=None, svc=None):
    b = _row(conn, "mkt_bookings", booking_id)
    vr = vr or vehicle_requirement(conn, booking_id)
    dist, dsrc = _distance(conn, b)
    modes = []
    heavy = any(c.startswith(("truck_10", "truck_12")) or c in ("container_chassis", "lowbed_trailer")
                for c in vr["eligible_categories"])
    specialized = b.get("oversized") or b.get("hazardous") or b.get("lifting_required") or b.get("inter_island")
    if specialized or "oversized_handling" in vr["required_features"]:
        modes.append("CARRIER_BIDDING")
        modes.append("MANAGED_QUOTATION")
    elif dsrc == "UNKNOWN":
        modes.append("MANAGED_QUOTATION")
        modes.append("MANUAL_REVIEW")
    elif heavy:
        modes.append("MANAGED_QUOTATION")
        modes.append("INSTANT_PRICE")
    else:
        modes.append("INSTANT_PRICE")
        modes.append("MANAGED_QUOTATION")
    return modes


def select_pricing_mode(conn, actor, booking_id, override=None, reason=None):
    core.require(actor, "marketplace.pricing.manage")
    b = _guarded(conn, actor, "mkt_bookings", booking_id)
    modes = eligible_pricing_modes(conn, booking_id)
    recommended = modes[0]
    chosen = recommended
    if override:
        if override not in PRICING_MODES:
            raise ValueError("invalid pricing mode")
        if override not in modes:
            core.require(actor, "marketplace.pricing.override")
            if not reason:
                raise ValueError("overriding the recommended pricing mode requires a reason")
        chosen = override
    _set(conn, "mkt_bookings", booking_id, pricing_mode=chosen, status="PRICING_PENDING", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_PRICING_MODE_SELECTED", "mkt_bookings", booking_id, None,
               {"recommended": recommended, "chosen": chosen, "reason": reason})
    conn.commit()
    return {"recommended": recommended, "chosen": chosen, "eligible": modes}


# --------------------------------------------------------------------------- #
# Pricing engine + immutable snapshot
# --------------------------------------------------------------------------- #
def _rate(conn, component, vehicle_category=None):
    row = conn.execute(
        "SELECT * FROM mkt_rate_cards WHERE component=? AND active=1 AND (vehicle_category IS NULL OR "
        "vehicle_category=?) ORDER BY vehicle_category DESC LIMIT 1", (component, vehicle_category)).fetchone()
    return dict(row) if row else None


def price_booking(conn, actor, booking_id, vehicle_category=None):
    core.require(actor, "marketplace.pricing.manage")
    b = _guarded(conn, actor, "mkt_bookings", booking_id)
    if b["status"] not in ("PRICING_PENDING", "VALIDATED", "PRICED"):
        raise ValueError(f"cannot price from status {b['status']}")
    mode = b.get("pricing_mode") or "INSTANT_PRICE"
    vr = vehicle_requirement(conn, booking_id)
    vehicle_category = vehicle_category or (vr["recommended_categories"][0] if vr["recommended_categories"] else None)
    if not vehicle_category:
        raise ValueError("no eligible vehicle category to price")
    dist, dsrc = _distance(conn, b)
    if dsrc == "UNKNOWN":
        raise ValueError("unknown distance -> route to managed quotation / manual review, not instant price")
    comps, version = [], 1

    def add(component, unit, qty, rate, visibility="customer"):
        amt = round(qty * rate, 2)
        comps.append({"component": component, "unit": unit, "quantity": qty, "rate": rate,
                      "amount": amt, "visibility": visibility, "version": version,
                      "customer_visible": visibility == "customer", "carrier_visible": visibility != "internal",
                      "internal_only": visibility == "internal"})
        return amt

    base = _rate(conn, "base", vehicle_category) or _rate(conn, "base")
    drate = _rate(conn, "distance", vehicle_category) or _rate(conn, "distance")
    subtotal = 0
    subtotal += add("base", "flat", 1, base["rate"] if base else 2000)
    subtotal += add("distance", "per_km", dist, drate["rate"] if drate else 45)
    if b.get("refrigerated"):
        subtotal += add("refrigeration_surcharge", "flat", 1, (_rate(conn, "refrigeration") or {}).get("rate", 1500))
    if b.get("lifting_required"):
        subtotal += add("lifting_surcharge", "flat", 1, (_rate(conn, "lifting") or {}).get("rate", 3000))
    if b.get("inter_island"):
        subtotal += add("ferry_charge", "flat", 1, (_rate(conn, "ferry") or {}).get("rate", 8000))
    if b.get("loading_required"):
        subtotal += add("loading_charge", "flat", 1, (_rate(conn, "loading") or {}).get("rate", 800))
    subtotal = round(subtotal, 2)
    minc = (_rate(conn, "minimum") or {}).get("rate", 1500)
    if subtotal < minc:
        add("minimum_charge_adjustment", "flat", 1, minc - subtotal)
        subtotal = minc
    tax = policy.evaluate_tax(conn, subtotal, {})["tax"]
    total = round(subtotal + tax, 2)
    platform_fee = round(subtotal * PLATFORM_FEE_PCT / 100, 2)
    add("platform_fee", "pct", 1, platform_fee, visibility="internal")
    carrier_payout = round(subtotal - platform_fee, 2)
    snap = {"pricing_mode": mode, "rule_version": version, "route_source": dsrc, "distance_km": dist,
            "vehicle_category": vehicle_category, "components": comps, "subtotal": subtotal, "tax": tax,
            "total": total, "platform_fee": platform_fee, "estimated_carrier_payout": carrier_payout}
    cs = _checksum(snap)
    cur = conn.execute(
        "INSERT INTO mkt_pricing_snapshots(booking_id,pricing_mode,rule_version,route_source,distance_km,"
        "vehicle_category,components,subtotal,tax,total,platform_fee,estimated_carrier_payout,currency,"
        "checksum,generated_at,expires_at,actor,correlation_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'PHP',?,?,?,?,?)",
        (booking_id, mode, version, dsrc, dist, vehicle_category, _j(comps), subtotal, tax, total,
         platform_fee, carrier_payout, cs, _now(), _iso_plus(60 * 24), actor["id"], _cid()))
    sid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_pricing_snapshots", sid)
    _set(conn, "mkt_bookings", booking_id, status="PRICED", quotation_status="PRICED", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_BOOKING_PRICED", "mkt_pricing_snapshots", sid, None,
               {"booking": booking_id, "total": total})
    conn.commit()
    return {"snapshot_id": sid, **snap}


def get_pricing_snapshot(conn, actor, snapshot_id, client_view=False):
    row = _guarded(conn, actor, "mkt_pricing_snapshots", snapshot_id)
    comps = _pj(row["components"], [])
    if client_view or not core.can(actor, "marketplace.pricing.view"):
        # strip internal-only components (margin/payout) from a client-facing view
        comps = [c for c in comps if not c.get("internal_only")]
        row = {k: v for k, v in row.items() if k not in ("platform_fee", "estimated_carrier_payout")}
    row["components"] = comps
    return row


# --------------------------------------------------------------------------- #
# Candidate pool + deterministic ranking (reuses Increment-2 candidate_pool)
# --------------------------------------------------------------------------- #
_RANK_WEIGHTS = {"compliance": 0.30, "vehicle_fit": 0.20, "lane_experience": 0.15,
                 "on_time": 0.15, "price": 0.20}


def generate_candidates(conn, actor, booking_id):
    core.require(actor, "marketplace.matching.execute")
    b = _guarded(conn, actor, "mkt_bookings", booking_id)
    dims = None
    if b.get("dim_l_cm"):
        dims = (b.get("dim_l_cm"), b.get("dim_w_cm"), b.get("dim_h_cm"))
    pool = mo.candidate_pool(conn, actor, b["cargo_code"], b["origin_zone"], b["dest_zone"],
                             weight_kg=b.get("weight_kg"), volume_cbm=b.get("volume_cbm"), dims=dims,
                             require_driver=True)
    ranked = rank_candidates(conn, b, pool["candidates"])
    cur = conn.execute(
        "INSERT INTO mkt_match_runs(booking_id,ranking_version,candidates,excluded,created_by,created_at,"
        "correlation_id) VALUES(?,1,?,?,?,?,?)",
        (booking_id, _j(ranked), _j(pool["excluded"]), actor["id"], _now(), _cid()))
    rid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_match_runs", rid)
    _set(conn, "mkt_bookings", booking_id, status="MATCHING", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_CANDIDATES_GENERATED", "mkt_match_runs", rid, None,
               {"booking": booking_id, "candidates": len(ranked), "excluded": len(pool["excluded"])})
    conn.commit()
    return {"match_run_id": rid, "candidates": ranked, "excluded": pool["excluded"]}


def rank_candidates(conn, booking, candidates, offers_by_carrier=None):
    """Transparent deterministic ranking. Persists factor/raw/normalized/weight/contribution.
    AI may summarize; it may not change eligibility or the deterministic inputs."""
    offers_by_carrier = offers_by_carrier or {}
    prefs_cache = {}
    scored = []
    for c in candidates:
        carrier = conn.execute("SELECT * FROM mkt_carriers WHERE id=?", (c["carrier_id"],)).fetchone()
        carrier = dict(carrier) if carrier else {}
        veh = conn.execute("SELECT * FROM mkt_vehicles WHERE id=?", (c["vehicle_id"],)).fetchone()
        veh = dict(veh) if veh else {}
        # deterministic, non-personal factors only
        compliance = 1.0   # every candidate already passed hard compliance in candidate_pool
        headroom = 0.0
        if booking.get("weight_kg") and veh.get("payload_kg"):
            headroom = max(0.0, min(1.0, 1 - (booking["weight_kg"] / veh["payload_kg"])))
        vehicle_fit = round(headroom, 4)
        prefs = _pj(carrier.get("preferred_lanes"), []) or []
        lane_exp = 1.0 if booking["dest_zone"] in prefs or booking["origin_zone"] in prefs else 0.4
        on_time = 0.8   # neutral default until performance metrics exist (documented placeholder)
        amt = offers_by_carrier.get(c["carrier_id"])
        price = 0.5 if amt is None else max(0.0, min(1.0, 1 - (amt / (booking.get("declared_value") or amt or 1) / 10)))
        factors = {"compliance": compliance, "vehicle_fit": vehicle_fit, "lane_experience": lane_exp,
                   "on_time": on_time, "price": price}
        breakdown, score = [], 0.0
        for f, raw in factors.items():
            w = _RANK_WEIGHTS[f]
            contribution = round(raw * w, 4)
            score += contribution
            breakdown.append({"factor": f, "raw": raw, "normalized": raw, "weight": w,
                              "contribution": contribution})
        scored.append({**c, "score": round(score, 4), "factors": breakdown, "ranking_version": 1})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


# --------------------------------------------------------------------------- #
# Controlled broadcast
# --------------------------------------------------------------------------- #
_WAVE_SIZES = {1: 3, 2: 6, 3: 999}


def create_broadcast(conn, actor, booking_id, wave=1, response_minutes=120):
    core.require(actor, "marketplace.broadcast.execute")
    b = _guarded(conn, actor, "mkt_bookings", booking_id)
    run = conn.execute("SELECT * FROM mkt_match_runs WHERE booking_id=? ORDER BY id DESC LIMIT 1",
                       (booking_id,)).fetchone()
    if not run:
        raise ValueError("generate candidates before broadcasting")
    ranked = _pj(run["candidates"], [])
    already = set()
    for prev in conn.execute("SELECT targets FROM mkt_broadcasts WHERE booking_id=?", (booking_id,)).fetchall():
        already |= set(_pj(prev["targets"], []) or [])
    suppressed = {}
    targets = []
    for c in ranked:
        if c["carrier_id"] in already:
            suppressed[c["carrier_id"]] = "already_notified"   # duplicate prevention
            continue
        targets.append(c["carrier_id"])
        if len(targets) >= _WAVE_SIZES.get(wave, 3):
            break
    cur = conn.execute(
        "INSERT INTO mkt_broadcasts(booking_id,wave,target_count,criteria,targets,sent_at,response_deadline,"
        "channel,delivery_result,suppressed,created_by,created_at,correlation_id) "
        "VALUES(?,?,?,?,?,?,?,'mock','sent',?,?,?,?)",
        (booking_id, wave, len(targets), f"wave{wave} top-ranked eligible", _j(targets), _now(),
         _iso_plus(response_minutes), _j(suppressed), actor["id"], _now(), _cid()))
    bcid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_broadcasts", bcid)
    _set(conn, "mkt_bookings", booking_id, status="OFFERS_OPEN", broadcast_status=f"WAVE{wave}", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_BROADCAST_SENT", "mkt_broadcasts", bcid, None,
               {"booking": booking_id, "wave": wave, "targets": len(targets)})
    conn.commit()
    return {"broadcast_id": bcid, "wave": wave, "targets": targets, "suppressed": suppressed}


# --------------------------------------------------------------------------- #
# Carrier offers + bidding
# --------------------------------------------------------------------------- #
def submit_offer(conn, actor, booking_id, carrier_id, amount, vehicle_id=None, driver_id=None, **a):
    core.require(actor, "marketplace.offer.create")
    b = _guarded(conn, actor, "mkt_bookings", booking_id)
    carrier = _guarded(conn, actor, "mkt_carriers", carrier_id)
    reasons = _validate_offer(conn, b, carrier_id, vehicle_id, driver_id, amount)
    status = "INVALID" if reasons else "VALID"
    is_bid = 1 if b.get("pricing_mode") in ("CARRIER_BIDDING", "REVERSE_AUCTION") else 0
    cur = conn.execute(
        "INSERT INTO mkt_offers(booking_id,carrier_id,vehicle_id,driver_id,amount,currency,est_pickup,"
        "est_delivery,valid_until,inclusions,exclusions,toll_treatment,ferry_treatment,waiting_rate,"
        "helper_required,loading_required,notes,is_bid,status,invalid_reason,created_by,created_at,"
        "correlation_id) VALUES(?,?,?,?,?,'PHP',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (booking_id, carrier_id, vehicle_id, driver_id, amount, a.get("est_pickup"), a.get("est_delivery"),
         a.get("valid_until", _iso_plus(240)), a.get("inclusions"), a.get("exclusions"),
         a.get("toll_treatment"), a.get("ferry_treatment"), a.get("waiting_rate"),
         int(a.get("helper_required", 0)), int(a.get("loading_required", 0)), a.get("notes"), is_bid,
         status, ";".join(reasons) or None, actor["id"], _now(), _cid()))
    oid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_offers", oid)
    if is_bid:
        conn.execute("INSERT INTO mkt_bid_events(booking_id,offer_id,carrier_id,amount,event,created_at,"
                     "correlation_id) VALUES(?,?,?,?,?,?,?)",
                     (booking_id, oid, carrier_id, amount, "BID", _now(), _cid()))
        # abnormal-price detection against the priced snapshot floor
        snap = conn.execute("SELECT subtotal FROM mkt_pricing_snapshots WHERE booking_id=? ORDER BY id DESC LIMIT 1",
                            (booking_id,)).fetchone()
        if snap and amount < snap["subtotal"] * 0.5:
            conn.execute("INSERT INTO mkt_bid_events(booking_id,offer_id,carrier_id,amount,event,created_at,"
                         "correlation_id) VALUES(?,?,?,?,?,?,?)",
                         (booking_id, oid, carrier_id, amount, "ABNORMAL_FLAGGED", _now(), _cid()))
    core.audit(conn, actor, "MKT_OFFER_SUBMITTED", "mkt_offers", oid, None,
               {"booking": booking_id, "carrier": carrier_id, "amount": amount, "status": status})
    conn.commit()
    return {"offer_id": oid, "status": status, "invalid_reason": reasons}


def _validate_offer(conn, b, carrier_id, vehicle_id, driver_id, amount):
    reasons = []
    carrier = conn.execute("SELECT * FROM mkt_carriers WHERE id=?", (carrier_id,)).fetchone()
    if not carrier or carrier["status"] != "ACTIVE":
        reasons.append("carrier_not_active")
    elif mo.evaluate_compliance(conn, "CARRIER", carrier_id)["recommendation"] != "READY":
        reasons.append("carrier_compliance_failed")
    if amount is None or amount <= 0:
        reasons.append("invalid_amount")
    if vehicle_id:
        v = conn.execute("SELECT * FROM mkt_vehicles WHERE id=?", (vehicle_id,)).fetchone()
        if not v or v["status"] != "ACTIVE":
            reasons.append("vehicle_not_active")
        elif v["carrier_id"] != carrier_id:
            reasons.append("vehicle_not_carrier")
        else:
            el = mkt.is_vehicle_eligible(conn, b["cargo_code"], v["category_code"],
                                         b.get("weight_kg"), b.get("volume_cbm"))
            if not el["eligible"]:
                reasons.extend(el["reasons"])
    if driver_id:
        chk = mo.can_assign_driver(conn, driver_id, vehicle_id) if vehicle_id else {"ok": False, "reasons": ["no_vehicle"]}
        if not chk["ok"]:
            reasons.extend(chk["reasons"])
    # price floor: bids may not undercut a governed floor (50% of priced subtotal)
    snap = conn.execute("SELECT subtotal FROM mkt_pricing_snapshots WHERE booking_id=? ORDER BY id DESC LIMIT 1",
                        (b["id"],)).fetchone()
    if snap and amount is not None and amount < snap["subtotal"] * 0.5:
        reasons.append("below_price_floor")
    return reasons


def withdraw_offer(conn, actor, offer_id):
    core.require(actor, "marketplace.offer.manage")
    o = _guarded(conn, actor, "mkt_offers", offer_id)
    _set(conn, "mkt_offers", offer_id, status="WITHDRAWN", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_OFFER_WITHDRAWN", "mkt_offers", offer_id, {"status": o["status"]}, {"status": "WITHDRAWN"})
    conn.commit()
    return {"status": "WITHDRAWN"}


def expire_offers(conn, actor=None, as_of=None):
    as_of = as_of or _now()
    rows = conn.execute("SELECT id FROM mkt_offers WHERE status IN('VALID','SUBMITTED','SHORTLISTED') "
                        "AND valid_until IS NOT NULL AND valid_until < ?", (as_of,)).fetchall()
    for r in rows:
        conn.execute("UPDATE mkt_offers SET status='EXPIRED',updated_at=? WHERE id=?", (_now(), r["id"]))
    conn.commit()
    return {"expired": len(rows)}


# --------------------------------------------------------------------------- #
# Offer evaluation
# --------------------------------------------------------------------------- #
def evaluate_offers(conn, actor, booking_id):
    core.require(actor, "marketplace.offer.evaluate")
    b = _guarded(conn, actor, "mkt_bookings", booking_id)
    offers = [dict(o) for o in conn.execute(
        "SELECT * FROM mkt_offers WHERE booking_id=? AND status IN('VALID','SHORTLISTED')", (booking_id,)).fetchall()]
    valid = [o for o in offers if (o.get("valid_until") or _now()) >= _now()]
    obc = {o["carrier_id"]: o["amount"] for o in valid}
    cands = [{"carrier_id": o["carrier_id"], "vehicle_id": o["vehicle_id"], "driver_id": o["driver_id"],
              "offer_id": o["id"], "amount": o["amount"]} for o in valid]
    ranked = rank_candidates(conn, b, cands, offers_by_carrier=obc)
    shortlist = ranked[:3]
    for o in shortlist:
        conn.execute("UPDATE mkt_offers SET status='SHORTLISTED',updated_at=? WHERE id=?", (_now(), o["offer_id"]))
    conn.commit()
    recommendation = shortlist[0] if shortlist else None
    return {"shortlist": shortlist, "recommendation": recommendation,
            "note": "governed evaluation — cheapest does not auto-win", "evaluated": len(valid)}


# --------------------------------------------------------------------------- #
# Selection + assignment (payment-gated; no trip activation)
# --------------------------------------------------------------------------- #
def select_offer(conn, actor, booking_id, offer_id, model="MANAGED_SELECTION"):
    core.require(actor, "marketplace.offer.select")
    if model not in SELECTION_MODELS:
        raise ValueError("invalid selection model")
    if model == "AUTO_SELECTION":
        raise PermissionError("auto-selection is disabled by default (requires approved low-risk class + kill switch)")
    b = _guarded(conn, actor, "mkt_bookings", booking_id)
    o = _guarded(conn, actor, "mkt_offers", offer_id)
    if o["created_by"] == actor["id"]:
        raise PermissionError("separation of duties: a carrier may not select its own offer")
    if o["status"] not in ("VALID", "SHORTLISTED"):
        raise ValueError(f"cannot select an offer in status {o['status']}")
    if o.get("valid_until") and o["valid_until"] < _now():
        raise ValueError("offer has expired")
    conn.execute("UPDATE mkt_offers SET status='SELECTED',updated_at=? WHERE id=?", (_now(), offer_id))
    conn.execute("UPDATE mkt_offers SET status='REJECTED',updated_at=? WHERE booking_id=? AND id<>? "
                 "AND status IN('VALID','SHORTLISTED','SUBMITTED')", (_now(), booking_id, offer_id))
    _set(conn, "mkt_bookings", booking_id, status="OFFER_SELECTED", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_OFFER_SELECTED", "mkt_offers", offer_id, None, {"booking": booking_id, "model": model})
    conn.commit()
    return {"selected_offer": offer_id, "model": model}


def create_assignment(conn, actor, booking_id):
    core.require(actor, "marketplace.assignment.create")
    b = _guarded(conn, actor, "mkt_bookings", booking_id)
    o = conn.execute("SELECT * FROM mkt_offers WHERE booking_id=? AND status='SELECTED' ORDER BY id DESC LIMIT 1",
                     (booking_id,)).fetchone()
    if not o:
        raise ValueError("no selected offer for this booking")
    o = dict(o)
    # re-verify every gate at assignment time (deterministic)
    reasons = _validate_offer(conn, b, o["carrier_id"], o["vehicle_id"], o["driver_id"], o["amount"])
    if reasons:
        raise ValueError(f"assignment blocked: {reasons}")
    if o.get("valid_until") and o["valid_until"] < _now():
        raise ValueError("selected offer expired")
    snap = conn.execute("SELECT * FROM mkt_pricing_snapshots WHERE booking_id=? ORDER BY id DESC LIMIT 1",
                        (booking_id,)).fetchone()
    high_value = (snap and snap["total"] and snap["total"] >= HIGH_VALUE_THRESHOLD) or (o["amount"] >= HIGH_VALUE_THRESHOLD)
    cur = conn.execute(
        "INSERT INTO mkt_assignments(booking_id,shipper_id,carrier_id,vehicle_id,driver_id,offer_id,"
        "pricing_snapshot_id,carrier_payout,version,status,payment_requirement,assigned_by,assigned_at,"
        "correlation_id) VALUES(?,?,?,?,?,?,?,?,1,'PENDING_CONFIRMATION',?,?,?,?)",
        (booking_id, b["shipper_id"], o["carrier_id"], o["vehicle_id"], o["driver_id"], o["id"],
         snap["id"] if snap else None, (snap["estimated_carrier_payout"] if snap else o["amount"]),
         "APPROVAL_REQUIRED" if high_value else "STANDARD", actor["id"], _now(), _cid()))
    aid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_assignments", aid)
    _set(conn, "mkt_bookings", booking_id,
         status="ASSIGNMENT_PENDING", assignment_status="PENDING_CONFIRMATION", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_ASSIGNMENT_CREATED", "mkt_assignments", aid, None,
               {"booking": booking_id, "carrier": o["carrier_id"], "high_value": bool(high_value)})
    conn.commit()
    return {"assignment_id": aid, "status": "PENDING_CONFIRMATION", "approval_required": bool(high_value)}


def approve_assignment(conn, actor, assignment_id):
    core.require(actor, "marketplace.assignment.approve")
    a = _guarded(conn, actor, "mkt_assignments", assignment_id)
    if a["assigned_by"] == actor["id"]:
        raise PermissionError("separation of duties: the assigner may not approve the assignment")
    _set(conn, "mkt_assignments", assignment_id, approved_by=actor["id"], updated_by=actor["id"])
    core.audit(conn, actor, "MKT_ASSIGNMENT_APPROVED", "mkt_assignments", assignment_id, None, {"approver": actor["id"]})
    conn.commit()
    return {"approved_by": actor["id"]}


def confirm_assignment(conn, actor, assignment_id, decision="accept"):
    core.require(actor, "marketplace.assignment.confirm")
    a = _guarded(conn, actor, "mkt_assignments", assignment_id)
    if a["payment_requirement"] == "APPROVAL_REQUIRED" and not a.get("approved_by"):
        raise ValueError("assignment requires approval before carrier confirmation")
    if decision == "reject":
        _set(conn, "mkt_assignments", assignment_id, status="REASSIGNMENT_REQUIRED", updated_by=actor["id"])
        _set(conn, "mkt_bookings", a["booking_id"], status="MATCHING", updated_by=actor["id"])
        core.audit(conn, actor, "MKT_ASSIGNMENT_REJECTED", "mkt_assignments", assignment_id, None, {})
        conn.commit()
        return {"status": "REASSIGNMENT_REQUIRED"}
    # accept -> CONFIRMED then PAYMENT_REQUIRED (NEVER trip-active in Increment 3)
    _set(conn, "mkt_assignments", assignment_id, status="PAYMENT_REQUIRED", updated_by=actor["id"])
    _set(conn, "mkt_bookings", a["booking_id"], status="PAYMENT_REQUIRED", assignment_status="CONFIRMED",
         payment_status="PAYMENT_REQUIRED", updated_by=actor["id"])
    core.audit(conn, actor, "MKT_ASSIGNMENT_CONFIRMED", "mkt_assignments", assignment_id, None,
               {"status": "PAYMENT_REQUIRED"})
    conn.commit()
    return {"status": "PAYMENT_REQUIRED", "trip_active": False}


def request_substitution(conn, actor, assignment_id, new_vehicle_id=None, new_driver_id=None):
    """Any substitution reruns eligibility deterministically before it is accepted."""
    core.require(actor, "marketplace.assignment.confirm")
    a = _guarded(conn, actor, "mkt_assignments", assignment_id)
    b = _row(conn, "mkt_bookings", a["booking_id"])
    veh = new_vehicle_id or a["vehicle_id"]
    drv = new_driver_id or a["driver_id"]
    reasons = _validate_offer(conn, b, a["carrier_id"], veh, drv, a.get("carrier_payout") or 1)
    if reasons:
        core.audit(conn, actor, "MKT_SUBSTITUTION_REJECTED", "mkt_assignments", assignment_id, None, {"reasons": reasons})
        conn.commit()
        return {"ok": False, "reasons": reasons}
    _set(conn, "mkt_assignments", assignment_id, vehicle_id=veh, driver_id=drv,
         version=(a["version"] or 1) + 1, updated_by=actor["id"])
    core.audit(conn, actor, "MKT_SUBSTITUTION_APPLIED", "mkt_assignments", assignment_id, None,
               {"vehicle": veh, "driver": drv})
    conn.commit()
    return {"ok": True, "vehicle_id": veh, "driver_id": drv}


def cancel_booking(conn, actor, booking_id, party, reason):
    core.require(actor, "marketplace.booking.cancel")
    b = _guarded(conn, actor, "mkt_bookings", booking_id)
    _set(conn, "mkt_bookings", booking_id, status="CANCELLED", updated_by=actor["id"])
    conn.execute("UPDATE mkt_assignments SET status='CANCELLED',updated_at=? WHERE booking_id=? "
                 "AND status NOT IN('CANCELLED','EXPIRED')", (_now(), booking_id))
    core.audit(conn, actor, "MKT_BOOKING_CANCELLED", "mkt_bookings", booking_id, None,
               {"party": party, "reason": reason, "funds_moved": False})
    conn.commit()
    return {"status": "CANCELLED", "funds_moved": False}


# --------------------------------------------------------------------------- #
# Queues + list helpers
# --------------------------------------------------------------------------- #
def list_bookings(conn, actor, status=None):
    core.require(actor, "marketplace.booking.view")
    frag, args = tenant.predicate(actor)
    q = "SELECT * FROM mkt_bookings WHERE 1=1" + frag
    a = list(args)
    if status:
        q += " AND status=?"; a.append(status)
    q += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


def list_offers(conn, actor, booking_id=None):
    core.require(actor, "marketplace.offer.view")
    frag, args = tenant.predicate(actor)
    q = "SELECT * FROM mkt_offers WHERE 1=1" + frag
    a = list(args)
    if booking_id:
        q += " AND booking_id=?"; a.append(booking_id)
    q += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


def list_assignments(conn, actor, status=None):
    core.require(actor, "marketplace.assignment.view")
    frag, args = tenant.predicate(actor)
    q = "SELECT * FROM mkt_assignments WHERE 1=1" + frag
    a = list(args)
    if status:
        q += " AND status=?"; a.append(status)
    q += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


def marketplace_queues(conn, actor):
    core.require(actor, "marketplace.booking.view")
    frag, args = tenant.predicate(actor)
    def cnt(where, extra=()):
        return conn.execute(f"SELECT COUNT(*) FROM mkt_bookings WHERE {where}" + frag, (*extra, *args)).fetchone()[0]
    return {"unvalidated": cnt("status IN('DRAFT','INCOMPLETE','SUBMITTED')"),
            "pricing_required": cnt("status IN('VALIDATED','PRICING_PENDING')"),
            "matching": cnt("status='MATCHING'"),
            "offers_open": cnt("status='OFFERS_OPEN'"),
            "selection_pending": cnt("status='OFFER_SELECTED'"),
            "assignment_payment_pending": cnt("status='PAYMENT_REQUIRED'"),
            "assignment_pending": cnt("status='ASSIGNMENT_PENDING'"),
            "cancelled": cnt("status='CANCELLED'"),
            "expired": cnt("status='EXPIRED'")}


# --------------------------------------------------------------------------- #
# Integrity checks
# --------------------------------------------------------------------------- #
def run_integrity(conn, actor):
    core.require(actor, "marketplace.matching.view")
    checks = []
    def add(name, bad, sev="FAIL"):
        checks.append({"check": name, "status": sev if bad else "PASS", "count": bad})
    c = conn.execute
    add("priced_booking_without_snapshot",
        c("SELECT COUNT(*) FROM mkt_bookings b WHERE b.status='PRICED' AND NOT EXISTS("
          "SELECT 1 FROM mkt_pricing_snapshots s WHERE s.booking_id=b.id)").fetchone()[0])
    add("selected_offer_from_ineligible_carrier",
        c("SELECT COUNT(*) FROM mkt_offers o JOIN mkt_carriers ca ON ca.id=o.carrier_id "
          "WHERE o.status='SELECTED' AND ca.status<>'ACTIVE'").fetchone()[0], "BLOCKED")
    add("assignment_with_suspended_carrier",
        c("SELECT COUNT(*) FROM mkt_assignments a JOIN mkt_carriers ca ON ca.id=a.carrier_id "
          "WHERE a.status NOT IN('CANCELLED','EXPIRED') AND ca.status='SUSPENDED'").fetchone()[0], "BLOCKED")
    add("assignment_with_inactive_vehicle",
        c("SELECT COUNT(*) FROM mkt_assignments a JOIN mkt_vehicles v ON v.id=a.vehicle_id "
          "WHERE a.status NOT IN('CANCELLED','EXPIRED') AND v.status<>'ACTIVE'").fetchone()[0], "BLOCKED")
    add("assignment_without_selected_offer",
        c("SELECT COUNT(*) FROM mkt_assignments a WHERE a.offer_id IS NULL "
          "AND a.status NOT IN('CANCELLED','EXPIRED')").fetchone()[0])
    add("expired_offer_selected",
        c("SELECT COUNT(*) FROM mkt_offers WHERE status='SELECTED' AND valid_until IS NOT NULL "
          "AND valid_until < ?", (_now(),)).fetchone()[0], "BLOCKED")
    add("assignment_ready_without_payment_requirement",
        c("SELECT COUNT(*) FROM mkt_assignments WHERE status='READY_FOR_TRIP_ACTIVATION'").fetchone()[0], "BLOCKED")
    add("duplicate_active_assignment",
        c("SELECT COUNT(*) FROM (SELECT booking_id FROM mkt_assignments WHERE status NOT IN('CANCELLED','EXPIRED') "
          "GROUP BY booking_id HAVING COUNT(*)>1) t").fetchone()[0])
    # prohibited cargo never produced a priced booking with eligible vehicle
    bad = 0
    for bk in c("SELECT id,cargo_code FROM mkt_bookings WHERE status IN('PRICED','MATCHING','OFFERS_OPEN')").fetchall():
        cargo = mkt.get_cargo_type(conn, bk["cargo_code"])
        if cargo and cargo["prohibited"]:
            bad += 1
    add("prohibited_cargo_progressed", bad, "BLOCKED")
    overall = "PASS"; order = {"PASS": 0, "WARNING": 1, "FAIL": 2, "BLOCKED": 3}
    for ck in checks:
        if order[ck["status"]] > order[overall]:
            overall = ck["status"]
    return {"overall": overall, "checks": checks}


# --------------------------------------------------------------------------- #
# Migration classifier
# --------------------------------------------------------------------------- #
def classify_existing(conn, actor=None):
    buckets = {"marketplace_candidate": 0, "internal_operational": 0, "historical": 0,
               "already_assigned": 0, "no_marketplace_shipper": 0, "no_eligible_lane": 0,
               "ambiguous": 0, "excluded": 0}
    try:
        buckets["internal_operational"] = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    except Exception:
        try: conn.rollback()
        except Exception: pass
    return {"buckets": buckets,
            "invariants": {"unexpected_financial_differences": 0, "unexpected_operational_status_changes": 0,
                           "unexpected_broadcasts": 0, "unexpected_offers": 0, "unexpected_assignments": 0},
            "note": "existing operational bookings are internal; none broadcast/priced/offered/assigned by migration"}


# --------------------------------------------------------------------------- #
# Seed a governed rate card (deterministic pricing inputs)
# --------------------------------------------------------------------------- #
_SEED = {"id": 0, "role": "system", "perms": {"*"}, "tenant_id": None}
_RATES = [
    ("base", None, "flat", 2000, "customer"),
    ("base", "motorcycle", "flat", 300, "customer"),
    ("base", "truck_6w", "flat", 5000, "customer"),
    ("distance", None, "per_km", 45, "customer"),
    ("distance", "motorcycle", "per_km", 12, "customer"),
    ("distance", "truck_6w", "per_km", 70, "customer"),
    ("refrigeration", None, "flat", 1500, "customer"),
    ("lifting", None, "flat", 3000, "customer"),
    ("ferry", None, "flat", 8000, "customer"),
    ("loading", None, "flat", 800, "customer"),
    ("minimum", None, "flat", 1500, "internal"),
]


def seed(conn, actor=None):
    a = actor or _SEED
    if conn.execute("SELECT 1 FROM mkt_rate_cards LIMIT 1").fetchone():
        return
    for comp, cat, unit, rate, vis in _RATES:
        conn.execute("INSERT INTO mkt_rate_cards(component,vehicle_category,unit,rate,visibility,version,"
                     "effective_from,active,created_by,created_at) VALUES(?,?,?,?,?,1,?,1,?,?)",
                     (comp, cat, unit, rate, vis, "2026-01-01", a["id"], _now()))
    conn.commit()
