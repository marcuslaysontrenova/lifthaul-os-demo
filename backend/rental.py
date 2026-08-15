"""Hourly / Daily / Project Rental — duration-and-usage revenue model over the existing spine.

LiftHaul's roots are heavy-equipment rental (RGO / crane rigging), where the unit of sale is not a
point-to-point haul but a resource (vehicle/equipment + operator) rented for a duration — by the hour,
day, week, month, or as a fixed-price project. This module adds that revenue model WITHOUT forking the
platform: it reuses carriers, vehicles, drivers, tax policy, the platform-fee split and — crucially —
Protected Payment (`protected_payment.create_transaction`) so rental money is protected and released by
the SAME governed gate as freight. It does not re-implement any of those.

What is genuinely new (and structurally distinct from freight per-km pricing) is a rental rate model
and agreement lifecycle:

  * `rental_rate_cards` — governed, effective-dated, versioned rates per (vehicle_category, rate_unit)
    with a minimum-billing quantity, an overtime multiplier, a standby rate, a mobilization fee and
    inclusion flags (operator / fuel). Superseding a rate closes the prior one — an audit trail, never
    an in-place mutation.
  * `rental_agreements` — QUOTED -> CONFIRMED -> ACTIVE -> COMPLETED -> SETTLED (or CANCELLED).
  * `rental_usage` — HONEST capture of actual hours/days used, plus overtime and standby. Usage is
    recorded from real start/stop events; it is never inferred or fabricated.
  * `rental_invoices` — an immutable, checksummed billing snapshot: min-billing enforced, overtime and
    standby added, tax + platform fee applied, carrier payout derived; optionally funded through
    Protected Payment (MOCK until live funds are legally enabled — the existing gate still governs).

Governance preserved throughout: tenant isolation, RBAC, audit, effective-dated rates, deterministic
billing, minimum-billing enforcement, and an overtime-approval gate for large overtime charges.
"""
from __future__ import annotations

import datetime
import hashlib
import json

import core
import tenant
import policy
import protected_payment as pp
import marketplace_onboarding as ob


RATE_UNITS = ("HOURLY", "DAILY", "WEEKLY", "MONTHLY", "PROJECT")
STATES = ("QUOTED", "CONFIRMED", "ACTIVE", "COMPLETED", "SETTLED", "CANCELLED")
PLATFORM_FEE_PCT = 10                    # same split as freight (marketplace_matching.PLATFORM_FEE_PCT)
OVERTIME_APPROVAL_THRESHOLD = 50000      # overtime charge above this needs rental.overtime.approve


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _today():
    return datetime.date.today().isoformat()


def _checksum(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:32]


SCHEMA = """
CREATE TABLE IF NOT EXISTS rental_rate_cards(
  id INTEGER PRIMARY KEY, tenant_id INTEGER,
  vehicle_category TEXT NOT NULL, rate_unit TEXT NOT NULL,
  rate REAL NOT NULL, min_billing_qty REAL NOT NULL DEFAULT 1,
  overtime_multiplier REAL NOT NULL DEFAULT 1.5, standby_rate REAL NOT NULL DEFAULT 0,
  mobilization_fee REAL NOT NULL DEFAULT 0,
  includes_operator INTEGER NOT NULL DEFAULT 1, includes_fuel INTEGER NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT 'PHP', version INTEGER NOT NULL DEFAULT 1,
  effective_from TEXT, effective_to TEXT, active INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS rental_agreements(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, agreement_no TEXT,
  booking_id INTEGER, customer_id INTEGER, carrier_id INTEGER, vehicle_id INTEGER, driver_id INTEGER,
  vehicle_category TEXT, rate_unit TEXT NOT NULL, quoted_quantity REAL NOT NULL,
  rate REAL NOT NULL, min_billing_qty REAL NOT NULL DEFAULT 1, overtime_multiplier REAL NOT NULL DEFAULT 1.5,
  standby_rate REAL NOT NULL DEFAULT 0, mobilization_fee REAL NOT NULL DEFAULT 0,
  deposit REAL NOT NULL DEFAULT 0, planned_start TEXT, planned_end TEXT,
  inclusions TEXT, rate_card_id INTEGER, status TEXT NOT NULL DEFAULT 'QUOTED',
  created_by INTEGER, created_at TEXT, updated_by INTEGER, updated_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS rental_usage(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, agreement_id INTEGER NOT NULL,
  actual_quantity REAL NOT NULL DEFAULT 0, overtime_quantity REAL NOT NULL DEFAULT 0,
  standby_quantity REAL NOT NULL DEFAULT 0, meter_start REAL, meter_end REAL,
  notes TEXT, recorded_by INTEGER, recorded_at TEXT);

CREATE TABLE IF NOT EXISTS rental_invoices(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, agreement_id INTEGER NOT NULL,
  billed_quantity REAL, base_amount REAL, overtime_amount REAL, standby_amount REAL,
  mobilization_amount REAL, discount REAL, subtotal REAL, tax REAL, total REAL,
  platform_fee REAL, carrier_payout REAL, protected_tx_id INTEGER, currency TEXT DEFAULT 'PHP',
  checksum TEXT, status TEXT NOT NULL DEFAULT 'FINALIZED', created_by INTEGER, created_at TEXT);
"""


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    """A small governed baseline rental catalog (idempotent). Rates are illustrative defaults an
    operator overrides; they are effective-dated and versioned like any governed rate."""
    if conn.execute("SELECT 1 FROM rental_rate_cards LIMIT 1").fetchone():
        return
    system = {"id": 0, "role": "system", "perms": {"*"}, "tenant_id": None}
    defaults = [
        ("truck_6w", "HOURLY", 1200, 4, 1.5, 400, 2500, 1, 0),
        ("truck_6w", "DAILY", 8000, 1, 1.5, 2500, 2500, 1, 0),
        ("truck_10w", "DAILY", 12000, 1, 1.5, 3500, 4000, 1, 0),
        ("crane_25t", "HOURLY", 4500, 4, 1.5, 1500, 8000, 1, 0),
        ("crane_25t", "PROJECT", 250000, 1, 1.5, 0, 8000, 1, 0),
    ]
    for cat, unit, rate, minq, otm, sb, mob, op, fuel in defaults:
        try:
            conn.execute(
                "INSERT INTO rental_rate_cards(vehicle_category,rate_unit,rate,min_billing_qty,"
                "overtime_multiplier,standby_rate,mobilization_fee,includes_operator,includes_fuel,"
                "version,effective_from,active,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,1,?,1,?,?)",
                (cat, unit, rate, minq, otm, sb, mob, op, fuel, _today(), system["id"], _now()))
        except Exception:
            pass
    conn.commit()


# --------------------------------------------------------------------------- #
def _row(conn, table, id):
    r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (id,)).fetchone()
    if not r:
        raise core.NotFoundError(f"{table} row {id} not found")
    return dict(r)


def _agreement(conn, actor, agreement_id):
    a = _row(conn, "rental_agreements", agreement_id)
    tenant.guard(actor, a)
    return a


# --------------------------------------------------------------------------- #
# Governed, effective-dated rental rate catalog
# --------------------------------------------------------------------------- #
def set_rental_rate(conn, actor, vehicle_category, rate_unit, rate, *, min_billing_qty=1,
                    overtime_multiplier=1.5, standby_rate=0, mobilization_fee=0,
                    includes_operator=True, includes_fuel=False, effective_from=None):
    core.require(actor, "marketplace.rental.rate.manage")
    if rate_unit not in RATE_UNITS:
        raise core.ValidationError(f"invalid rate_unit '{rate_unit}'")
    if rate is None or rate <= 0:
        raise core.ValidationError("rate must be positive")
    prior = conn.execute(
        "SELECT * FROM rental_rate_cards WHERE vehicle_category=? AND rate_unit=? AND active=1 "
        "AND (tenant_id=? OR tenant_id IS NULL) ORDER BY id DESC LIMIT 1",
        (vehicle_category, rate_unit, tenant.actor_tenant(actor))).fetchone()
    version = (prior["version"] + 1) if prior else 1
    if prior:
        conn.execute("UPDATE rental_rate_cards SET active=0, effective_to=? WHERE id=?",
                     (effective_from or _today(), prior["id"]))
    cur = conn.execute(
        "INSERT INTO rental_rate_cards(vehicle_category,rate_unit,rate,min_billing_qty,overtime_multiplier,"
        "standby_rate,mobilization_fee,includes_operator,includes_fuel,version,effective_from,active,"
        "created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
        (vehicle_category, rate_unit, rate, min_billing_qty, overtime_multiplier, standby_rate,
         mobilization_fee, 1 if includes_operator else 0, 1 if includes_fuel else 0, version,
         effective_from or _today(), actor["id"], _now()))
    rid = cur.lastrowid
    tenant.stamp(conn, actor, "rental_rate_cards", rid)
    core.audit(conn, actor, "RENTAL_RATE_SET", "rental_rate_cards", rid, None,
               {"category": vehicle_category, "unit": rate_unit, "rate": rate, "version": version})
    conn.commit()
    return {"rate_card_id": rid, "version": version}


def resolve_rental_rate(conn, vehicle_category, rate_unit, tenant_id=None, as_of=None):
    as_of = as_of or _today()
    return conn.execute(
        "SELECT * FROM rental_rate_cards WHERE vehicle_category=? AND rate_unit=? AND active=1 "
        "AND (tenant_id=? OR tenant_id IS NULL) "
        "AND (effective_from IS NULL OR effective_from<=?) "
        "AND (effective_to IS NULL OR effective_to>=?) "
        "ORDER BY tenant_id DESC, id DESC LIMIT 1",
        (vehicle_category, rate_unit, tenant_id, as_of, as_of)).fetchone()


def list_rental_rates(conn, actor, include_history=False):
    core.require(actor, "marketplace.rental.view")
    frag, params = tenant.predicate(actor)
    q = "SELECT * FROM rental_rate_cards WHERE 1=1" + frag
    if not include_history:
        q += " AND active=1"
    q += " ORDER BY vehicle_category, rate_unit, id DESC"
    return [dict(r) for r in conn.execute(q, params).fetchall()]


# --------------------------------------------------------------------------- #
# Deterministic billing (pure)
# --------------------------------------------------------------------------- #
def compute_billing(conn, agreement, actual_quantity, overtime_quantity=0.0, standby_quantity=0.0, discount=0.0):
    """Pure, reproducible billing math. min-billing is enforced here; nothing is fabricated."""
    rate = agreement["rate"]
    billed_qty = max(float(actual_quantity or 0), float(agreement["min_billing_qty"] or 0))
    base = round(billed_qty * rate, 2)
    overtime = round(float(overtime_quantity or 0) * rate * float(agreement["overtime_multiplier"] or 1), 2)
    standby = round(float(standby_quantity or 0) * float(agreement["standby_rate"] or 0), 2)
    mobilization = round(float(agreement["mobilization_fee"] or 0), 2)
    discount = round(float(discount or 0), 2)
    subtotal = round(base + overtime + standby + mobilization - discount, 2)
    if subtotal < 0:
        raise core.ValidationError("discount exceeds charges")
    tax = policy.evaluate_tax(conn, subtotal, {})["tax"]
    total = round(subtotal + tax, 2)
    platform_fee = round(subtotal * PLATFORM_FEE_PCT / 100, 2)
    carrier_payout = round(subtotal - platform_fee, 2)
    return {"billed_quantity": billed_qty, "base_amount": base, "overtime_amount": overtime,
            "standby_amount": standby, "mobilization_amount": mobilization, "discount": discount,
            "subtotal": subtotal, "tax": tax, "total": total, "platform_fee": platform_fee,
            "carrier_payout": carrier_payout}


# --------------------------------------------------------------------------- #
# Agreement lifecycle
# --------------------------------------------------------------------------- #
def quote_rental(conn, actor, vehicle_category, rate_unit, quoted_quantity, *, carrier_id=None,
                 vehicle_id=None, driver_id=None, customer_id=None, booking_id=None,
                 planned_start=None, planned_end=None, deposit=0, mobilization_fee=None,
                 standby_rate=None, overtime_multiplier=None, min_billing_qty=None):
    core.require(actor, "marketplace.rental.manage")
    if rate_unit not in RATE_UNITS:
        raise core.ValidationError(f"invalid rate_unit '{rate_unit}'")
    if quoted_quantity is None or quoted_quantity <= 0:
        raise core.ValidationError("quoted_quantity must be positive")
    rc = resolve_rental_rate(conn, vehicle_category, rate_unit, tenant.actor_tenant(actor))
    if not rc:
        raise core.NotFoundError(f"no active rental rate for {vehicle_category}/{rate_unit}")
    rc = dict(rc)
    rate = rc["rate"]
    minq = rc["min_billing_qty"] if min_billing_qty is None else min_billing_qty
    otm = rc["overtime_multiplier"] if overtime_multiplier is None else overtime_multiplier
    sb = rc["standby_rate"] if standby_rate is None else standby_rate
    mob = rc["mobilization_fee"] if mobilization_fee is None else mobilization_fee
    cur = conn.execute(
        "INSERT INTO rental_agreements(booking_id,customer_id,carrier_id,vehicle_id,driver_id,"
        "vehicle_category,rate_unit,quoted_quantity,rate,min_billing_qty,overtime_multiplier,standby_rate,"
        "mobilization_fee,deposit,planned_start,planned_end,inclusions,rate_card_id,status,created_by,"
        "created_at,correlation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'QUOTED', ?,?,?)",
        (booking_id, customer_id, carrier_id, vehicle_id, driver_id, vehicle_category, rate_unit,
         quoted_quantity, rate, minq, otm, sb, mob, deposit, planned_start, planned_end,
         json.dumps({"operator": bool(rc["includes_operator"]), "fuel": bool(rc["includes_fuel"])}),
         rc["id"], actor["id"], _now(), core.correlation_id()))
    aid = cur.lastrowid
    conn.execute("UPDATE rental_agreements SET agreement_no=? WHERE id=?", (f"RENT-{aid}", aid))
    tenant.stamp(conn, actor, "rental_agreements", aid)
    a = _row(conn, "rental_agreements", aid)
    estimate = compute_billing(conn, a, quoted_quantity)
    core.audit(conn, actor, "RENTAL_QUOTED", "rental_agreements", aid, None,
               {"category": vehicle_category, "unit": rate_unit, "qty": quoted_quantity, "estimate": estimate["total"]})
    conn.commit()
    return {"agreement_id": aid, "agreement_no": f"RENT-{aid}", "status": "QUOTED", "estimate": estimate}


def confirm_rental(conn, actor, agreement_id):
    core.require(actor, "marketplace.rental.manage")
    a = _agreement(conn, actor, agreement_id)
    if a["status"] != "QUOTED":
        raise core.ConflictError(f"agreement is {a['status']}")
    if not a["carrier_id"] or not a["vehicle_id"]:
        raise core.ValidationError("a carrier and vehicle must be assigned before confirmation")
    conn.execute("UPDATE rental_agreements SET status='CONFIRMED',updated_by=?,updated_at=? WHERE id=?",
                 (actor["id"], _now(), agreement_id))
    core.audit(conn, actor, "RENTAL_CONFIRMED", "rental_agreements", agreement_id, None, {})
    conn.commit()
    return {"status": "CONFIRMED"}


def activate_rental(conn, actor, agreement_id):
    """Rental starts. Re-runs the deterministic driver/vehicle eligibility gate when both are set —
    the SAME gate the marketplace uses (reused, not re-implemented)."""
    core.require(actor, "marketplace.rental.manage")
    a = _agreement(conn, actor, agreement_id)
    if a["status"] != "CONFIRMED":
        raise core.ConflictError(f"agreement is {a['status']} (must be CONFIRMED)")
    if a["driver_id"] and a["vehicle_id"]:
        chk = ob.can_assign_driver(conn, a["driver_id"], a["vehicle_id"])
        if not chk["ok"]:
            raise core.ConflictError(f"activation blocked: {chk['reasons']}")
    conn.execute("UPDATE rental_agreements SET status='ACTIVE',updated_by=?,updated_at=? WHERE id=?",
                 (actor["id"], _now(), agreement_id))
    core.audit(conn, actor, "RENTAL_ACTIVATED", "rental_agreements", agreement_id, None, {})
    conn.commit()
    return {"status": "ACTIVE"}


def record_usage(conn, actor, agreement_id, actual_quantity, *, overtime_quantity=0, standby_quantity=0,
                 meter_start=None, meter_end=None, notes=None):
    """HONEST usage capture — recorded from real start/stop, never inferred. Non-negative only."""
    core.require(actor, "marketplace.rental.usage.record")
    a = _agreement(conn, actor, agreement_id)
    if a["status"] != "ACTIVE":
        raise core.ConflictError(f"cannot record usage on a {a['status']} agreement")
    for label, v in (("actual_quantity", actual_quantity), ("overtime_quantity", overtime_quantity),
                     ("standby_quantity", standby_quantity)):
        if v is None or float(v) < 0:
            raise core.ValidationError(f"{label} must be >= 0")
    if meter_start is not None and meter_end is not None and float(meter_end) < float(meter_start):
        raise core.ValidationError("meter_end cannot be before meter_start")
    cur = conn.execute(
        "INSERT INTO rental_usage(agreement_id,actual_quantity,overtime_quantity,standby_quantity,"
        "meter_start,meter_end,notes,recorded_by,recorded_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (agreement_id, actual_quantity, overtime_quantity, standby_quantity, meter_start, meter_end,
         notes, actor["id"], _now()))
    uid = cur.lastrowid
    tenant.stamp(conn, actor, "rental_usage", uid)
    core.audit(conn, actor, "RENTAL_USAGE_RECORDED", "rental_usage", uid, None,
               {"agreement": agreement_id, "actual": actual_quantity, "overtime": overtime_quantity})
    conn.commit()
    return {"usage_id": uid}


def _usage_totals(conn, agreement_id):
    r = conn.execute("SELECT COALESCE(SUM(actual_quantity),0) a, COALESCE(SUM(overtime_quantity),0) o, "
                     "COALESCE(SUM(standby_quantity),0) s FROM rental_usage WHERE agreement_id=?",
                     (agreement_id,)).fetchone()
    return float(r["a"]), float(r["o"]), float(r["s"])


def finalize_rental(conn, actor, agreement_id, *, discount=0, fund_protected=True):
    """Aggregate usage -> immutable billing snapshot. Overtime above the governed threshold requires an
    approval permission. Optionally funds the charge through Protected Payment (MOCK until live funds are
    legally enabled; the existing gate still governs). Funds are never fabricated."""
    core.require(actor, "marketplace.rental.billing.finalize")
    a = _agreement(conn, actor, agreement_id)
    if a["status"] not in ("ACTIVE", "COMPLETED"):
        raise core.ConflictError(f"cannot finalize a {a['status']} agreement")
    if conn.execute("SELECT 1 FROM rental_invoices WHERE agreement_id=?", (agreement_id,)).fetchone():
        raise core.ConflictError("agreement already invoiced")
    actual, overtime, standby = _usage_totals(conn, agreement_id)
    bill = compute_billing(conn, a, actual, overtime, standby, discount)
    if bill["overtime_amount"] > OVERTIME_APPROVAL_THRESHOLD and not core.can(actor, "marketplace.rental.overtime.approve"):
        raise core.ForbiddenError(
            f"overtime charge {bill['overtime_amount']} exceeds approval threshold "
            f"{OVERTIME_APPROVAL_THRESHOLD} — requires marketplace.rental.overtime.approve")
    tx_id = None
    if fund_protected and a["carrier_id"]:
        # reuse the SAME Protected Payment domain as freight; MOCK provider (no live custody)
        tx_id = pp.create_transaction(
            conn, actor, booking_id=a["booking_id"], carrier_id=a["carrier_id"],
            contract_amount=bill["total"], protected_amount=bill["total"],
            platform_fee=bill["platform_fee"], carrier_payable=bill["carrier_payout"],
            client_ref=a["agreement_no"], provider_name="MOCK")
    snap = {**bill, "agreement_id": agreement_id, "protected_tx_id": tx_id}
    cs = _checksum(snap)
    cur = conn.execute(
        "INSERT INTO rental_invoices(agreement_id,billed_quantity,base_amount,overtime_amount,standby_amount,"
        "mobilization_amount,discount,subtotal,tax,total,platform_fee,carrier_payout,protected_tx_id,"
        "checksum,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'FINALIZED', ?,?)",
        (agreement_id, bill["billed_quantity"], bill["base_amount"], bill["overtime_amount"],
         bill["standby_amount"], bill["mobilization_amount"], bill["discount"], bill["subtotal"],
         bill["tax"], bill["total"], bill["platform_fee"], bill["carrier_payout"], tx_id, cs,
         actor["id"], _now()))
    inv = cur.lastrowid
    tenant.stamp(conn, actor, "rental_invoices", inv)
    conn.execute("UPDATE rental_agreements SET status='COMPLETED',updated_by=?,updated_at=? WHERE id=?",
                 (actor["id"], _now(), agreement_id))
    core.audit(conn, actor, "RENTAL_FINALIZED", "rental_invoices", inv, None,
               {"agreement": agreement_id, "total": bill["total"], "protected_tx": tx_id})
    conn.commit()
    return {"invoice_id": inv, "protected_tx_id": tx_id, "status": "COMPLETED", **bill}


def settle_rental(conn, actor, agreement_id):
    core.require(actor, "marketplace.rental.billing.finalize")
    a = _agreement(conn, actor, agreement_id)
    if a["status"] != "COMPLETED":
        raise core.ConflictError(f"agreement is {a['status']} (must be COMPLETED)")
    conn.execute("UPDATE rental_agreements SET status='SETTLED',updated_by=?,updated_at=? WHERE id=?",
                 (actor["id"], _now(), agreement_id))
    core.audit(conn, actor, "RENTAL_SETTLED", "rental_agreements", agreement_id, None, {})
    conn.commit()
    return {"status": "SETTLED"}


def cancel_rental(conn, actor, agreement_id, reason):
    core.require(actor, "marketplace.rental.manage")
    a = _agreement(conn, actor, agreement_id)
    if a["status"] in ("SETTLED", "CANCELLED"):
        raise core.ConflictError(f"agreement is {a['status']}")
    conn.execute("UPDATE rental_agreements SET status='CANCELLED',updated_by=?,updated_at=? WHERE id=?",
                 (actor["id"], _now(), agreement_id))
    core.audit(conn, actor, "RENTAL_CANCELLED", "rental_agreements", agreement_id, None, {"reason": reason})
    conn.commit()
    return {"status": "CANCELLED"}


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
def list_agreements(conn, actor, status=None, carrier_id=None, customer_id=None):
    core.require(actor, "marketplace.rental.view")
    frag, params = tenant.predicate(actor)
    q = "SELECT * FROM rental_agreements WHERE 1=1" + frag
    a = list(params)
    if status:
        q += " AND status=?"; a.append(status)
    if carrier_id:
        q += " AND carrier_id=?"; a.append(carrier_id)
    if customer_id:
        q += " AND customer_id=?"; a.append(customer_id)
    q += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


def get_agreement(conn, actor, agreement_id):
    core.require(actor, "marketplace.rental.view")
    a = _agreement(conn, actor, agreement_id)
    usage = [dict(r) for r in conn.execute("SELECT * FROM rental_usage WHERE agreement_id=? ORDER BY id",
                                            (agreement_id,)).fetchall()]
    inv = conn.execute("SELECT * FROM rental_invoices WHERE agreement_id=? ORDER BY id DESC LIMIT 1",
                       (agreement_id,)).fetchone()
    return {"agreement": a, "usage": usage, "invoice": dict(inv) if inv else None}


def queues(conn, actor):
    core.require(actor, "marketplace.rental.view")
    frag, params = tenant.predicate(actor)

    def cnt(extra):
        return conn.execute("SELECT COUNT(*) c FROM rental_agreements WHERE 1=1" + frag + extra, params).fetchone()["c"]
    return {s.lower(): cnt(f" AND status='{s}'") for s in STATES}


def run_integrity(conn, actor):
    core.require(actor, "marketplace.rental.view")
    checks = []
    neg = conn.execute("SELECT COUNT(*) c FROM rental_usage WHERE actual_quantity<0 OR overtime_quantity<0 "
                       "OR standby_quantity<0").fetchone()["c"]
    checks.append({"check": "no_negative_usage", "ok": neg == 0, "count": neg})
    dup = conn.execute("SELECT COUNT(*) c FROM (SELECT agreement_id,COUNT(*) n FROM rental_invoices "
                       "GROUP BY agreement_id HAVING n>1)").fetchone()["c"]
    checks.append({"check": "one_invoice_per_agreement", "ok": dup == 0, "count": dup})
    orphan = conn.execute("SELECT COUNT(*) c FROM rental_usage u LEFT JOIN rental_agreements a "
                          "ON a.id=u.agreement_id WHERE a.id IS NULL").fetchone()["c"]
    checks.append({"check": "no_orphan_usage", "ok": orphan == 0, "count": orphan})
    return {"ok": all(x["ok"] for x in checks), "checks": checks}
