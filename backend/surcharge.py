"""Dynamic Surcharge Engine — governed, effective-dated surcharge rules over the existing pricing.

Freight economics shift with fuel, peak days, holidays, congested lanes and demand. This module lets an
operator express those as GOVERNED rules and have them applied TRANSPARENTLY inside the existing
`marketplace_matching.price_booking` — without forking the pricing engine and without silently changing
any price:

  * `surcharge_rules` — effective-dated, versioned rules. Each has a type (FUEL / PEAK / HOLIDAY / ZONE /
    CONGESTION / DEMAND / SPECIAL), a basis (PERCENT of subtotal / FLAT / PER_KM), a rate, an
    `applies_when` condition set (zones / categories / cargo / weekdays / date range / distance band), a
    priority, and a `stackable` flag. Superseding a rule closes the prior one (audit trail).
  * `surcharge_applications` — an immutable record of exactly which rules were applied to which pricing
    snapshot and for how much (full transparency + auditability).

**Config-gated, fail-safe by default.** The pricing hook only fires when `marketplace.surcharge_enabled`
is `true` (it defaults to `false`), so existing prices and pricing-snapshot checksums are UNCHANGED
until an operator deliberately switches surcharging on — the same fail-closed discipline used for LTFRB
enforcement and live funds. Rates are operator-set, never fabricated from a live feed (there is no live
provider). `evaluate` is a pure, deterministic function.
"""
from __future__ import annotations

import datetime

import core
import tenant
import admin_platform


TYPES = ("FUEL", "PEAK", "HOLIDAY", "ZONE", "CONGESTION", "DEMAND", "SPECIAL")
BASES = ("PERCENT", "FLAT", "PER_KM")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _today():
    return datetime.date.today().isoformat()


def _j(v):
    import json
    return json.dumps(v) if v is not None else None


def _pj(v, d=None):
    import json
    if not v:
        return d
    try:
        return json.loads(v)
    except Exception:
        return d


SCHEMA = """
CREATE TABLE IF NOT EXISTS surcharge_rules(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, code TEXT NOT NULL, name TEXT,
  surcharge_type TEXT NOT NULL, basis TEXT NOT NULL, rate REAL NOT NULL,
  applies_when TEXT, priority INTEGER NOT NULL DEFAULT 0, stackable INTEGER NOT NULL DEFAULT 1,
  version INTEGER NOT NULL DEFAULT 1, effective_from TEXT, effective_to TEXT, active INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS surcharge_applications(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, booking_id INTEGER, snapshot_id INTEGER,
  rule_code TEXT, surcharge_type TEXT, basis TEXT, rate REAL, base_amount REAL, amount REAL,
  created_at TEXT);
"""


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    return


def enabled(conn, tenant_id=None):
    """The pricing hook only applies surcharges when this is true. Default false -> pricing unchanged."""
    try:
        v, _ = admin_platform.resolve_config(conn, "marketplace.surcharge_enabled", tenant=(tenant_id or ""))
        return str(v).lower() == "true"
    except Exception:
        return False


# --------------------------------------------------------------------------- #
def _row(conn, table, id):
    r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (id,)).fetchone()
    if not r:
        raise core.NotFoundError(f"{table} row {id} not found")
    return dict(r)


# --------------------------------------------------------------------------- #
# Governed rule catalog (effective-dated + versioned)
# --------------------------------------------------------------------------- #
def set_rule(conn, actor, code, name, surcharge_type, basis, rate, *, applies_when=None, priority=0,
             stackable=True, effective_from=None):
    core.require(actor, "marketplace.surcharge.manage")
    if surcharge_type not in TYPES:
        raise core.ValidationError(f"invalid surcharge_type '{surcharge_type}'")
    if basis not in BASES:
        raise core.ValidationError(f"invalid basis '{basis}'")
    if rate is None or float(rate) < 0:
        raise core.ValidationError("rate must be >= 0")
    at = tenant.actor_tenant(actor)
    prior = conn.execute("SELECT * FROM surcharge_rules WHERE code=? AND active=1 AND (tenant_id=? OR "
                         "tenant_id IS NULL) ORDER BY id DESC LIMIT 1", (code, at)).fetchone()
    version = (prior["version"] + 1) if prior else 1
    if prior:
        conn.execute("UPDATE surcharge_rules SET active=0, effective_to=? WHERE id=?",
                     (effective_from or _today(), prior["id"]))
    cur = conn.execute(
        "INSERT INTO surcharge_rules(code,name,surcharge_type,basis,rate,applies_when,priority,stackable,"
        "version,effective_from,active,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?)",
        (code, name, surcharge_type, basis, float(rate), _j(applies_when), int(priority),
         1 if stackable else 0, version, effective_from or _today(), actor["id"], _now()))
    rid = cur.lastrowid
    tenant.stamp(conn, actor, "surcharge_rules", rid)
    core.audit(conn, actor, "SURCHARGE_RULE_SET", "surcharge_rules", rid, None,
               {"code": code, "type": surcharge_type, "basis": basis, "rate": rate, "version": version})
    conn.commit()
    return {"rule_id": rid, "code": code, "version": version}


def deactivate_rule(conn, actor, rule_id):
    core.require(actor, "marketplace.surcharge.manage")
    r = _row(conn, "surcharge_rules", rule_id)
    tenant.guard(actor, r)
    conn.execute("UPDATE surcharge_rules SET active=0, effective_to=? WHERE id=?", (_today(), rule_id))
    core.audit(conn, actor, "SURCHARGE_RULE_DEACTIVATED", "surcharge_rules", rule_id, None, {})
    conn.commit()
    return {"status": "INACTIVE"}


def list_rules(conn, actor, include_history=False):
    core.require(actor, "marketplace.surcharge.view")
    frag, params = tenant.predicate(actor)
    q = "SELECT * FROM surcharge_rules WHERE 1=1" + frag
    if not include_history:
        q += " AND active=1"
    q += " ORDER BY priority DESC, id"
    out = []
    for r in conn.execute(q, params).fetchall():
        d = dict(r); d["applies_when"] = _pj(d["applies_when"], {})
        out.append(d)
    return out


def resolve_rules(conn, tenant_id=None, as_of=None):
    as_of = as_of or _today()
    rows = conn.execute(
        "SELECT * FROM surcharge_rules WHERE active=1 AND (tenant_id=? OR tenant_id IS NULL) "
        "AND (effective_from IS NULL OR effective_from<=?) AND (effective_to IS NULL OR effective_to>=?) "
        "ORDER BY priority DESC, id", (tenant_id, as_of, as_of)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Deterministic evaluation
# --------------------------------------------------------------------------- #
def _as_of_from_ctx(ctx, as_of):
    if as_of:
        return as_of
    win = ctx.get("pickup_window") or ctx.get("date")
    if win:
        return str(win)[:10]
    return _today()


def _matches(rule, ctx, as_of_date, distance_km):
    cond = _pj(rule["applies_when"], {}) or {}
    def in_list(key, val):
        allowed = cond.get(key)
        return (not allowed) or (val in allowed)
    if not in_list("origin_zones", ctx.get("origin_zone")):
        return False
    if not in_list("dest_zones", ctx.get("dest_zone")):
        return False
    if not in_list("vehicle_categories", ctx.get("vehicle_category")):
        return False
    if not in_list("cargo_codes", ctx.get("cargo_code")):
        return False
    if cond.get("weekdays"):
        try:
            wd = datetime.date.fromisoformat(as_of_date[:10]).weekday()
        except Exception:
            wd = None
        if wd is None or wd not in cond["weekdays"]:
            return False
    if cond.get("date_from") and as_of_date < cond["date_from"]:
        return False
    if cond.get("date_to") and as_of_date > cond["date_to"]:
        return False
    if cond.get("min_distance_km") is not None and (distance_km or 0) < cond["min_distance_km"]:
        return False
    if cond.get("max_distance_km") is not None and (distance_km or 0) > cond["max_distance_km"]:
        return False
    return True


def _amount(rule, subtotal, distance_km):
    basis, rate = rule["basis"], float(rule["rate"])
    if basis == "PERCENT":
        return round(subtotal * rate / 100, 2)
    if basis == "PER_KM":
        return round(rate * float(distance_km or 0), 2)
    return round(rate, 2)   # FLAT


def evaluate(conn, ctx, subtotal, *, distance_km=None, tenant_id=None, as_of=None):
    """Return the surcharges applicable to a pricing context. Pure + deterministic. Stackable rules all
    apply; among non-stackable matched rules only the single highest-priority one applies."""
    as_of_date = _as_of_from_ctx(ctx, as_of)
    matched = [r for r in resolve_rules(conn, tenant_id, as_of_date) if _matches(r, ctx, as_of_date, distance_km)]
    applied, non_stack_taken = [], False
    for r in matched:   # already priority DESC
        if not r["stackable"]:
            if non_stack_taken:
                continue
            non_stack_taken = True
        amt = _amount(r, subtotal, distance_km)
        if amt <= 0:
            continue
        applied.append({"code": r["code"], "surcharge_type": r["surcharge_type"], "basis": r["basis"],
                        "rate": float(r["rate"]), "amount": amt, "base_amount": round(subtotal, 2)})
    return {"surcharges": applied, "total": round(sum(a["amount"] for a in applied), 2)}


def record_applications(conn, actor, booking_id, snapshot_id, surcharges):
    """Persist an immutable audit of exactly what was applied to a pricing snapshot."""
    for s in surcharges:
        cur = conn.execute(
            "INSERT INTO surcharge_applications(booking_id,snapshot_id,rule_code,surcharge_type,basis,"
            "rate,base_amount,amount,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (booking_id, snapshot_id, s["code"], s["surcharge_type"], s["basis"], s["rate"],
             s.get("base_amount"), s["amount"], _now()))
        tenant.stamp(conn, actor, "surcharge_applications", cur.lastrowid)
    conn.commit()
    return {"recorded": len(surcharges)}


def quote(conn, actor, subtotal, *, origin_zone=None, dest_zone=None, vehicle_category=None,
          cargo_code=None, distance_km=None, as_of=None):
    """Operator 'what surcharges would apply' preview (does not persist)."""
    core.require(actor, "marketplace.surcharge.view")
    ctx = {"origin_zone": origin_zone, "dest_zone": dest_zone, "vehicle_category": vehicle_category,
           "cargo_code": cargo_code}
    return evaluate(conn, ctx, float(subtotal), distance_km=distance_km,
                    tenant_id=tenant.actor_tenant(actor), as_of=as_of)


def applications_for(conn, actor, booking_id):
    core.require(actor, "marketplace.surcharge.view")
    return [dict(r) for r in conn.execute(
        "SELECT * FROM surcharge_applications WHERE booking_id=? ORDER BY id", (booking_id,)).fetchall()]


# --------------------------------------------------------------------------- #
def queues(conn, actor):
    core.require(actor, "marketplace.surcharge.view")
    frag, params = tenant.predicate(actor)
    active = conn.execute("SELECT COUNT(*) c FROM surcharge_rules WHERE active=1" + frag, params).fetchone()["c"]
    apps = conn.execute("SELECT COUNT(*) c FROM surcharge_applications WHERE 1=1" + frag, params).fetchone()["c"]
    return {"active_rules": active, "applications": apps, "enabled": enabled(conn, tenant.actor_tenant(actor))}


def run_integrity(conn, actor):
    core.require(actor, "marketplace.surcharge.view")
    checks = []
    neg = conn.execute("SELECT COUNT(*) c FROM surcharge_rules WHERE rate<0").fetchone()["c"]
    checks.append({"check": "no_negative_rate", "ok": neg == 0, "count": neg})
    badbasis = conn.execute("SELECT COUNT(*) c FROM surcharge_rules WHERE basis NOT IN('PERCENT','FLAT','PER_KM')").fetchone()["c"]
    checks.append({"check": "valid_basis", "ok": badbasis == 0, "count": badbasis})
    return {"ok": all(x["ok"] for x in checks), "checks": checks}
