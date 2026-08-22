"""LiftHaul Enterprise — Provider Vehicle/Equipment Accreditation Fee Engine.

Company registration is FREE. This module prices the ONE-TIME "Vehicle Accreditation & Platform
Activation" that a provider pays per unit before commercial activation. Payment is NEVER approval —
a unit can be Fee: PAID while Compliance: PENDING and Marketplace: NOT ELIGIBLE. Marketplace activation
is decided by independent compliance (see fleet_registration.unit_eligibility), never by this module.

Design (reuses the effective-dated + versioned pattern proven in rates.py; no new pricing domain for job
rates — this is a distinct provider-accreditation concern):
  * `accreditation_schedule`   — master-data fee per canonical VARIANT or CATEGORY code; effective-dated,
                                 versioned (supersede on change), tenant-aware, tax-aware. Never hardcoded.
  * `accreditation_volume_tiers` — configurable fleet-size discount tiers (never punish large fleets).
  * `accreditation_assessments`  — the immutable historical price snapshot per unit (schedule version,
                                 components, discount, waiver, VAT, total, status, payment/refund refs).

The fee is ALWAYS computed server-side from the unit's canonical classification — any client-supplied
fee or variant is ignored. Unclassifiable / specialized units resolve to MANUAL_QUOTE, never a guess.
"""
from __future__ import annotations

import json

import core
import tenant

CURRENCY = "PHP"
STATUSES = ("ASSESSED", "PAID", "WAIVED", "REFUNDED", "MANUAL_QUOTE")

# RBAC
P_MANAGE = "commercial.fee.manage"    # platform admin: schedule / tiers / waiver
P_ASSESS = "marketplace.vehicle.manage"
P_PAY = "payment.confirm"             # finance confirms payment (never the carrier itself)
P_VIEW = "marketplace.fleet.view"

# Proposed launch fee schedule (§16) — seeded as DATA, fully configurable afterwards. Keyed by the
# canonical category_code, with variant_code overrides for units that must be quoted manually.
_SEED_CATEGORY_FEES = {
    "motorcycle": 299, "motorcycle_box": 299, "sedan": 399, "mpv": 399,
    "pickup": 499, "l300_van": 499, "ref_van_light": 499, "elf_4w": 599,
    "truck_6w": 799, "truck_6w_wing": 799, "truck_6w_ref": 799,
    "truck_10w": 999, "truck_10w_wing": 999,
    "truck_12w": 1199, "truck_14w": 1199, "prime_mover": 1199,
    "flatbed_10w": 1299, "container_chassis": 1299,
    "lowbed_trailer": 1499, "boom_truck": 1499,
    "forklift": 999, "reach_truck": 999, "telehandler": 999,
    "crane_truck": 1999,
    "tanker": 999, "cement_mixer": 999, "dump_truck": 799, "tow_truck": 999, "car_carrier": 999,
}
# variant-specific overrides — specialized cranes are a manual accreditation quote (§16).
_SEED_VARIANT_MANUAL = ("tower_crane", "crawler_crane")

_SEED_VOLUME_TIERS = [
    (1, 9, 0.0, "1–9 units"),
    (10, 24, 5.0, "10–24 units"),
    (25, 49, 10.0, "25–49 units"),
    (50, None, 15.0, "50+ units"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS accreditation_schedule(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, key_type TEXT NOT NULL, key_code TEXT NOT NULL,
  base_fee REAL, manual_quote INTEGER DEFAULT 0, components TEXT, currency TEXT DEFAULT 'PHP',
  version INTEGER DEFAULT 1, effective_from TEXT, effective_to TEXT, status TEXT DEFAULT 'ACTIVE',
  created_by INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS accreditation_volume_tiers(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, min_units INTEGER NOT NULL, max_units INTEGER,
  discount_pct REAL DEFAULT 0, fixed_discount REAL DEFAULT 0, label TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS accreditation_assessments(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, carrier_id INTEGER NOT NULL, vehicle_id INTEGER NOT NULL,
  schedule_version INTEGER, key_code TEXT, variant_code TEXT, category_code TEXT, class_label TEXT,
  components TEXT, subtotal REAL, discount REAL DEFAULT 0, discount_label TEXT, waived INTEGER DEFAULT 0,
  waiver_reason TEXT, tax_pct REAL DEFAULT 0, tax REAL DEFAULT 0, total REAL, currency TEXT DEFAULT 'PHP',
  status TEXT NOT NULL DEFAULT 'ASSESSED', payment_method TEXT, payment_ref TEXT, receipt_ref TEXT,
  refund_ref TEXT, refund_reason TEXT, assessed_by INTEGER, assessed_at TEXT, paid_by INTEGER, paid_at TEXT);
"""


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    system = {"id": 0, "role": "system", "perms": {"*"}, "tenant_id": None}
    now = core.now()
    for code, fee in _SEED_CATEGORY_FEES.items():
        if conn.execute("SELECT 1 FROM accreditation_schedule WHERE tenant_id IS NULL AND key_type='CATEGORY'"
                        " AND key_code=? AND status='ACTIVE'", (code,)).fetchone():
            continue
        conn.execute("INSERT INTO accreditation_schedule(tenant_id,key_type,key_code,base_fee,manual_quote,"
                     "components,currency,version,effective_from,status,created_by,created_at) "
                     "VALUES(NULL,'CATEGORY',?,?,0,?,?,1,?, 'ACTIVE',0,?)",
                     (code, float(fee), json.dumps(_split_components(fee)), CURRENCY, now, now))
    for vc in _SEED_VARIANT_MANUAL:
        if conn.execute("SELECT 1 FROM accreditation_schedule WHERE tenant_id IS NULL AND key_type='VARIANT'"
                        " AND key_code=? AND status='ACTIVE'", (vc,)).fetchone():
            continue
        conn.execute("INSERT INTO accreditation_schedule(tenant_id,key_type,key_code,base_fee,manual_quote,"
                     "components,currency,version,effective_from,status,created_by,created_at) "
                     "VALUES(NULL,'VARIANT',?,NULL,1,NULL,?,1,?, 'ACTIVE',0,?)", (vc, CURRENCY, now, now))
    for lo, hi, pct, label in _SEED_VOLUME_TIERS:
        if conn.execute("SELECT 1 FROM accreditation_volume_tiers WHERE tenant_id IS NULL AND min_units=?",
                        (lo,)).fetchone():
            continue
        conn.execute("INSERT INTO accreditation_volume_tiers(tenant_id,min_units,max_units,discount_pct,"
                     "fixed_discount,label,created_at) VALUES(NULL,?,?,?,0,?,?)", (lo, hi, pct, label, now))
    conn.commit()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cfg(conn, key, default=None):
    try:
        import admin_platform as ap
        v, _ = ap.resolve_config(conn, key)
        return v if v is not None else default
    except Exception:
        return default


def _vat_pct(conn):
    try:
        return float(_cfg(conn, "accreditation.vat_pct", "12") or 0)
    except Exception:
        return 12.0


def gate_enabled(conn):
    """Whether an unpaid accreditation fee blocks marketplace eligibility. Config-driven so the commercial
    gate can be activated per tenant/rollout without disturbing existing behaviour; OFF by default."""
    return str(_cfg(conn, "accreditation.gate_enabled", "false")).lower() == "true"


def _split_components(total):
    """Transparent default component split (§17) that always sums to the total and never goes negative:
    platform activation + compliance administration are capped, the remainder is vehicle accreditation."""
    total = float(total or 0)
    platform_activation = min(199.0, round(total * 0.2, 2))
    compliance_admin = min(200.0, round(total * 0.25, 2))
    accreditation_fee = round(total - platform_activation - compliance_admin, 2)
    return {"vehicle_accreditation": accreditation_fee, "compliance_administration": compliance_admin,
            "platform_activation": platform_activation}


def _tenant_of(actor):
    return actor.get("tenant_id") if isinstance(actor, dict) else None


def resolve_schedule(conn, variant_code, category_code, tenant_id=None, on_date=None):
    """Resolve the active fee row: a tenant-specific override wins over the platform default; a
    variant_code row wins over a category_code row. Returns the row dict or None."""
    on_date = on_date or core.now()

    def _q(key_type, key_code, tid_clause, params):
        return conn.execute(
            "SELECT * FROM accreditation_schedule WHERE status='ACTIVE' AND key_type=? AND key_code=?"
            + tid_clause +
            " AND (effective_from IS NULL OR effective_from<=?) AND (effective_to IS NULL OR effective_to>=?)"
            " ORDER BY version DESC LIMIT 1", (key_type, key_code, *params, on_date, on_date)).fetchone()

    for key_type, key_code in (("VARIANT", variant_code), ("CATEGORY", category_code)):
        if not key_code:
            continue
        if tenant_id is not None:
            r = _q(key_type, key_code, " AND tenant_id=?", (tenant_id,))
            if r:
                return dict(r)
        r = _q(key_type, key_code, " AND tenant_id IS NULL", ())
        if r:
            return dict(r)
    return None


def _carrier_unit_count(conn, carrier_id):
    row = conn.execute("SELECT COUNT(*) c FROM mkt_vehicles WHERE carrier_id=?", (carrier_id,)).fetchone()
    return int(row["c"]) if row else 0


def volume_discount(conn, tenant_id, unit_count):
    """Resolve the configured fleet-volume tier for this unit count -> (pct, fixed, label)."""
    rows = conn.execute("SELECT * FROM accreditation_volume_tiers WHERE (tenant_id=? OR tenant_id IS NULL) "
                        "ORDER BY tenant_id DESC, min_units ASC", (tenant_id,)).fetchall()
    seen_min = set()
    for r in rows:
        if r["min_units"] in seen_min:      # tenant override already taken for this band
            continue
        seen_min.add(r["min_units"])
        if unit_count >= r["min_units"] and (r["max_units"] is None or unit_count <= r["max_units"]):
            return float(r["discount_pct"] or 0), float(r["fixed_discount"] or 0), r["label"]
    return 0.0, 0.0, None


# --------------------------------------------------------------------------- #
# assessment — server-authoritative snapshot
# --------------------------------------------------------------------------- #
def _vehicle_spec(conn, vehicle_id):
    v = conn.execute("SELECT id,carrier_id,category_code FROM mkt_vehicles WHERE id=?", (vehicle_id,)).fetchone()
    if not v:
        raise core.NotFoundError("vehicle not found")
    s = conn.execute("SELECT variant_code,category_code,class_label FROM vehicle_specs WHERE vehicle_id=? "
                     "ORDER BY id DESC LIMIT 1", (vehicle_id,)).fetchone()
    return dict(v), (dict(s) if s else {})


def assess_fee(conn, actor, carrier_id, vehicle_id):
    """Assess (or re-assess) the one-time accreditation fee for a unit from its CANONICAL classification.
    Ignores any client-supplied fee/variant. Supersedes a prior unpaid ASSESSED snapshot; never disturbs
    a PAID/WAIVED/REFUNDED one. Specialized units with no schedule fee -> MANUAL_QUOTE."""
    core.require(actor, P_ASSESS)
    v, spec = _vehicle_spec(conn, vehicle_id)
    tid = _tenant_of(actor)
    variant_code = spec.get("variant_code")
    category_code = spec.get("category_code") or v.get("category_code")
    class_label = spec.get("class_label")

    existing = conn.execute("SELECT id,status FROM accreditation_assessments WHERE vehicle_id=? "
                            "ORDER BY id DESC LIMIT 1", (vehicle_id,)).fetchone()
    if existing and existing["status"] in ("PAID", "WAIVED", "REFUNDED"):
        return _assessment(conn, existing["id"])          # locked snapshot — do not re-price
    if existing and existing["status"] in ("ASSESSED", "MANUAL_QUOTE"):
        conn.execute("DELETE FROM accreditation_assessments WHERE id=?", (existing["id"],))

    sched = resolve_schedule(conn, variant_code, category_code, tenant_id=tid)
    now = core.now()
    if sched is None or sched.get("manual_quote"):
        cur = conn.execute(
            "INSERT INTO accreditation_assessments(carrier_id,vehicle_id,schedule_version,key_code,"
            "variant_code,category_code,class_label,components,subtotal,discount,tax,total,currency,"
            "status,assessed_by,assessed_at) VALUES(?,?,?,?,?,?,?,?,0,0,0,NULL,?, 'MANUAL_QUOTE',?,?)",
            (carrier_id, vehicle_id, (sched or {}).get("version"), (sched or {}).get("key_code"),
             variant_code, category_code, class_label, None, CURRENCY, actor.get("id"), now))
        aid = cur.lastrowid
        tenant.stamp(conn, actor, "accreditation_assessments", aid)
        core.audit(conn, actor, "ACCREDITATION_FEE_ASSESSED", "accreditation_assessments", aid, None,
                   {"vehicle_id": vehicle_id, "status": "MANUAL_QUOTE", "variant": variant_code})
        conn.commit()
        return _assessment(conn, aid)

    components = json.loads(sched["components"]) if sched.get("components") else _split_components(sched["base_fee"])
    subtotal = round(sum(float(x) for x in components.values()), 2)
    pct, fixed, dlabel = volume_discount(conn, tid, _carrier_unit_count(conn, carrier_id))
    discount = round(subtotal * pct / 100.0 + fixed, 2)
    net = max(round(subtotal - discount, 2), 0.0)
    vat_pct = _vat_pct(conn)
    tax = round(net * vat_pct / 100.0, 2)
    total = round(net + tax, 2)
    cur = conn.execute(
        "INSERT INTO accreditation_assessments(carrier_id,vehicle_id,schedule_version,key_code,variant_code,"
        "category_code,class_label,components,subtotal,discount,discount_label,tax_pct,tax,total,currency,"
        "status,assessed_by,assessed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'ASSESSED',?,?)",
        (carrier_id, vehicle_id, sched["version"], sched["key_code"], variant_code, category_code,
         class_label, json.dumps(components), subtotal, discount, dlabel, vat_pct, tax, total, CURRENCY,
         actor.get("id"), now))
    aid = cur.lastrowid
    tenant.stamp(conn, actor, "accreditation_assessments", aid)
    core.audit(conn, actor, "ACCREDITATION_FEE_ASSESSED", "accreditation_assessments", aid, None,
               {"vehicle_id": vehicle_id, "total": total, "version": sched["version"], "variant": variant_code})
    conn.commit()
    return _assessment(conn, aid)


def _assessment(conn, aid):
    r = conn.execute("SELECT * FROM accreditation_assessments WHERE id=?", (aid,)).fetchone()
    if not r:
        raise core.NotFoundError("assessment not found")
    d = dict(r)
    d["components"] = json.loads(d["components"]) if d.get("components") else None
    return d


def assessment_for(conn, vehicle_id):
    r = conn.execute("SELECT * FROM accreditation_assessments WHERE vehicle_id=? ORDER BY id DESC LIMIT 1",
                     (vehicle_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["components"] = json.loads(d["components"]) if d.get("components") else None
    return d


def fee_status(conn, vehicle_id):
    a = assessment_for(conn, vehicle_id)
    return a["status"] if a else None


def fee_paid(conn, vehicle_id):
    """A unit's accreditation obligation is satisfied only by PAID or an explicit WAIVED."""
    return fee_status(conn, vehicle_id) in ("PAID", "WAIVED")


# --------------------------------------------------------------------------- #
# payment / waiver / refund — payment is NEVER approval
# --------------------------------------------------------------------------- #
def record_payment(conn, actor, assessment_id, method, payment_ref, receipt_ref=None):
    """Finance/payment-provider confirms the accreditation fee is PAID. Deliberately touches NOTHING about
    compliance or marketplace eligibility — independent verification decides that."""
    core.require(actor, P_PAY)
    a = _assessment(conn, assessment_id)
    tenant.guard(actor, a)
    if a["status"] == "PAID":
        return a
    if a["status"] not in ("ASSESSED",):
        raise core.ConflictError(f"assessment is {a['status']} — only an ASSESSED fee can be paid")
    if not (str(method or "").strip() and str(payment_ref or "").strip()):
        raise core.ValidationError("payment method and provider reference are required")
    conn.execute("UPDATE accreditation_assessments SET status='PAID',payment_method=?,payment_ref=?,"
                 "receipt_ref=?,paid_by=?,paid_at=? WHERE id=?",
                 (str(method)[:40], str(payment_ref)[:120], (str(receipt_ref)[:120] if receipt_ref else None),
                  actor.get("id"), core.now(), assessment_id))
    core.audit(conn, actor, "ACCREDITATION_FEE_PAID", "accreditation_assessments", assessment_id, None,
               {"total": a["total"], "method": method, "payment_ref": payment_ref,
                "note": "payment recorded; marketplace eligibility still requires independent compliance"})
    conn.commit()
    return _assessment(conn, assessment_id)


def waive_fee(conn, actor, assessment_id, reason):
    core.require(actor, P_MANAGE)
    a = _assessment(conn, assessment_id)
    tenant.guard(actor, a)
    if not str(reason or "").strip():
        raise core.ValidationError("a waiver reason is required")
    conn.execute("UPDATE accreditation_assessments SET status='WAIVED',waiver_reason=?,waived=1 WHERE id=?",
                 (str(reason)[:300], assessment_id))
    core.audit(conn, actor, "ACCREDITATION_FEE_WAIVED", "accreditation_assessments", assessment_id, None,
               {"reason": reason})
    conn.commit()
    return _assessment(conn, assessment_id)


def refund(conn, actor, assessment_id, reason, refund_ref=None):
    """Refund a PAID accreditation fee per the published refund policy. Never silently alters the amount
    that was charged — records a REFUNDED status + reference against the immutable snapshot."""
    core.require(actor, P_PAY)
    a = _assessment(conn, assessment_id)
    tenant.guard(actor, a)
    if a["status"] != "PAID":
        raise core.ConflictError("only a PAID accreditation fee can be refunded")
    conn.execute("UPDATE accreditation_assessments SET status='REFUNDED',refund_reason=?,refund_ref=? WHERE id=?",
                 (str(reason or "")[:300], (str(refund_ref)[:120] if refund_ref else None), assessment_id))
    core.audit(conn, actor, "REFUND_APPROVED", "accreditation_assessments", assessment_id, None,
               {"reason": reason, "refund_ref": refund_ref})
    conn.commit()
    return _assessment(conn, assessment_id)


# --------------------------------------------------------------------------- #
# reads for portal / UX (§35 — never "PAY TO JOIN"; transparent breakdown)
# --------------------------------------------------------------------------- #
def fee_breakdown(conn, actor, vehicle_id):
    core.require(actor, P_VIEW)
    a = assessment_for(conn, vehicle_id)
    if a is None:
        return {"vehicle_id": vehicle_id, "assessed": False,
                "note": "No accreditation assessed yet. Company registration is free; accredit the unit to see its fee."}
    return {"vehicle_id": vehicle_id, "assessed": True, "class_label": a["class_label"],
            "status": a["status"], "components": a["components"], "subtotal": a["subtotal"],
            "discount": a["discount"], "discount_label": a["discount_label"], "vat_pct": a["tax_pct"],
            "vat": a["tax"], "total": a["total"], "currency": a["currency"],
            "manual_quote": a["status"] == "MANUAL_QUOTE",
            "disclaimer": ("Payment does not guarantee marketplace approval. Marketplace activation "
                           "requires successful, independent compliance verification.")}


# --------------------------------------------------------------------------- #
# admin: fee-schedule + volume-tier management (effective-dated, versioned)
# --------------------------------------------------------------------------- #
def set_fee(conn, actor, key_type, key_code, base_fee=None, *, components=None, manual_quote=False,
            effective_from=None, tenant_id=None):
    """Create/supersede a fee-schedule entry (Platform Administration). Never a code change."""
    core.require(actor, P_MANAGE)
    kt = str(key_type or "").upper()
    if kt not in ("VARIANT", "CATEGORY"):
        raise core.ValidationError("key_type must be VARIANT or CATEGORY")
    if not manual_quote and base_fee is None:
        raise core.ValidationError("base_fee is required unless manual_quote")
    tid_clause = " AND tenant_id=?" if tenant_id is not None else " AND tenant_id IS NULL"
    prev = conn.execute("SELECT * FROM accreditation_schedule WHERE status='ACTIVE' AND key_type=? AND "
                        "key_code=?" + tid_clause, (kt, key_code) + ((tenant_id,) if tenant_id is not None else ())).fetchone()
    ver = (prev["version"] + 1) if prev else 1
    now = core.now()
    if prev:
        conn.execute("UPDATE accreditation_schedule SET status='SUPERSEDED', effective_to=? WHERE id=?",
                     (effective_from or now, prev["id"]))
    comp = json.dumps(components or (_split_components(base_fee) if base_fee is not None else None)) if not manual_quote else None
    cur = conn.execute("INSERT INTO accreditation_schedule(tenant_id,key_type,key_code,base_fee,manual_quote,"
                       "components,currency,version,effective_from,status,created_by,created_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?, 'ACTIVE',?,?)",
                       (tenant_id, kt, key_code, (float(base_fee) if base_fee is not None else None),
                        1 if manual_quote else 0, comp, CURRENCY, ver, effective_from or now,
                        actor.get("id"), now))
    core.audit(conn, actor, "FEE_SCHEDULE_CHANGED", "accreditation_schedule", cur.lastrowid, None,
               {"key_type": kt, "key_code": key_code, "base_fee": base_fee, "version": ver})
    conn.commit()
    return {"id": cur.lastrowid, "key_type": kt, "key_code": key_code, "version": ver, "base_fee": base_fee}


def set_volume_tier(conn, actor, min_units, max_units, discount_pct=0.0, fixed_discount=0.0, label=None,
                    tenant_id=None):
    core.require(actor, P_MANAGE)
    conn.execute("INSERT INTO accreditation_volume_tiers(tenant_id,min_units,max_units,discount_pct,"
                 "fixed_discount,label,created_at) VALUES(?,?,?,?,?,?,?)",
                 (tenant_id, int(min_units), (int(max_units) if max_units is not None else None),
                  float(discount_pct or 0), float(fixed_discount or 0), label, core.now()))
    core.audit(conn, actor, "FEE_SCHEDULE_CHANGED", "accreditation_volume_tiers", None, None,
               {"tier": [min_units, max_units], "discount_pct": discount_pct})
    conn.commit()
    return {"ok": True}


def list_schedule(conn, actor, include_history=False):
    core.require(actor, P_VIEW)
    where = "" if include_history else " WHERE status='ACTIVE'"
    rows = conn.execute("SELECT * FROM accreditation_schedule" + where +
                        " ORDER BY key_type, key_code, version DESC").fetchall()
    return {"schedule": [dict(r) for r in rows],
            "volume_tiers": [dict(r) for r in conn.execute(
                "SELECT * FROM accreditation_volume_tiers ORDER BY min_units").fetchall()]}
