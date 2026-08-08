"""LiftHaul OS — quotation pricing subsystem.

Two concerns, deliberately separated:

  * the **governed master rate catalog** (`rate_cards`) — effective-dated, versioned,
    never overwritten; edits create a new version and supersede the prior row; and
  * the **authoritative line-pricing engine** — the single source of truth for every
    computed money value on a quotation line (subtotal, gross profit, margin). The
    frontend may pre-compute for responsiveness, but the values persisted and returned
    are always the ones this module calculates. A tampered client total is ignored.

`standard_rate` is the master selling rate. `quoted_rate` is the per-quotation selling
rate; it defaults to `standard_rate` and may be overridden by authorized users WITHOUT
ever mutating the master. `internal_cost` is the vendor/standard cost and is never
exposed to unauthorized viewers (redaction lives in `core.get_quotation`).
"""
from __future__ import annotations

import core

# Permissions this subsystem enforces (also declared in admin_platform.CATALOG).
PERM_RATE_OVERRIDE = "quotation.rate.override"
PERM_DISCOUNT_OVERRIDE = "quotation.discount.override"
PERM_COST_EDIT = "quotation.carrier_cost.edit"
PERM_PRICE_EDIT = "quotation.customer_price.edit"

# Governed defaults (overridable through the config cascade — see policy.evaluate_rate_variance).
DEFAULT_REASON_THRESHOLD_PCT = 5.0     # |variance| at/above this REQUIRES an override reason
DEFAULT_APPROVAL_VARIANCE_PCT = 15.0   # |variance| at/above this REQUIRES additional approval


# --------------------------------------------------------------------------- #
# Master rate catalog (effective-dated, versioned)
# --------------------------------------------------------------------------- #
def resolve_rate(conn, equipment_code, on_date=None, customer_id=None, branch=None):
    """Resolve the governing rate card for an equipment code.

    Preference order (most specific wins): customer-specific override → branch → general.
    Within a tier the latest ACTIVE, non-superseded version whose effective window covers
    ``on_date`` is chosen. Returns a dict or None.
    """
    on_date = on_date or core.now()
    rows = conn.execute(
        "SELECT * FROM rate_cards WHERE equipment_code=? AND status='ACTIVE' AND superseded=0"
        " AND (effective_from IS NULL OR effective_from<=?)"
        " AND (effective_to IS NULL OR effective_to>=?)",
        (equipment_code, on_date, on_date),
    ).fetchall()

    def tier(r):
        if customer_id is not None and r["customer_id"] == customer_id:
            return 0
        if r["customer_id"] is not None:
            return 9  # a different customer's override never applies
        if branch is not None and r["branch"] == branch:
            return 1
        if r["branch"] is not None:
            return 8
        return 2

    best = None
    for r in rows:
        t = tier(r)
        if t >= 8:
            continue
        if best is None or (t, r["version"]) > (best[0], best[1]["version"]):
            best = (t, r)
    return dict(best[1]) if best else None


def create_rate_card(conn, actor, equipment_code, equipment_name, standard_rate, *,
                     service_type=None, billing_unit="day", min_rate=None, internal_cost=None,
                     currency="PHP", branch=None, region=None, customer_id=None,
                     effective_from=None, effective_to=None):
    core.require(actor, "crm.admin.pricing.manage")
    if standard_rate is None or standard_rate < 0:
        raise core.ValidationError("standard_rate is required and must be non-negative")
    if min_rate is not None and standard_rate < min_rate:
        raise core.ValidationError("standard_rate must be >= min_rate")
    tid = (actor or {}).get("tenant_id")
    cur = conn.execute(
        "INSERT INTO rate_cards(tenant_id,equipment_code,equipment_name,service_type,billing_unit,"
        "standard_rate,min_rate,internal_cost,currency,branch,region,customer_id,version,"
        "effective_from,effective_to,status,created_by,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?,?, 'ACTIVE',?,?)",
        (tid, equipment_code, equipment_name, service_type, billing_unit, standard_rate, min_rate,
         internal_cost, currency, branch, region, customer_id, effective_from or core.now(),
         effective_to, actor["id"], core.now()))
    rid = cur.lastrowid
    core.audit(conn, actor, "rate_card.create", "rate_card", rid,
               new={"equipment_code": equipment_code, "standard_rate": standard_rate})
    conn.commit()
    return rid


def update_rate_card(conn, actor, rate_card_id, **changes):
    """Effective-dated edit: supersede the prior row and insert a new version. The historical
    row is preserved (never overwritten) so quotations referencing older versions stay intact."""
    core.require(actor, "crm.admin.pricing.manage")
    old = conn.execute("SELECT * FROM rate_cards WHERE id=?", (rate_card_id,)).fetchone()
    if not old:
        raise core.NotFoundError("rate card not found")
    if old["status"] == "ARCHIVED":
        raise core.ConflictError("archived rate card cannot be edited; create a new one")
    merged = dict(old)
    for k in ("equipment_name", "service_type", "billing_unit", "standard_rate", "min_rate",
              "internal_cost", "currency", "branch", "region", "customer_id",
              "effective_from", "effective_to"):
        if k in changes and changes[k] is not None:
            merged[k] = changes[k]
    if merged["min_rate"] is not None and merged["standard_rate"] < merged["min_rate"]:
        raise core.ValidationError("standard_rate must be >= min_rate")
    conn.execute("UPDATE rate_cards SET superseded=1, effective_to=? WHERE id=?", (core.now(), rate_card_id))
    cur = conn.execute(
        "INSERT INTO rate_cards(tenant_id,equipment_code,equipment_name,service_type,billing_unit,"
        "standard_rate,min_rate,internal_cost,currency,branch,region,customer_id,version,"
        "effective_from,effective_to,status,created_by,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'ACTIVE',?,?)",
        (merged["tenant_id"], merged["equipment_code"], merged["equipment_name"], merged["service_type"],
         merged["billing_unit"], merged["standard_rate"], merged["min_rate"], merged["internal_cost"],
         merged["currency"], merged["branch"], merged["region"], merged["customer_id"],
         old["version"] + 1, merged.get("effective_from") or core.now(), merged.get("effective_to"),
         actor["id"], core.now()))
    rid = cur.lastrowid
    core.audit(conn, actor, "rate_card.new_version", "rate_card", rid,
               old={"version": old["version"], "standard_rate": old["standard_rate"]},
               new={"version": old["version"] + 1, "standard_rate": merged["standard_rate"]})
    conn.commit()
    return rid


def archive_rate_card(conn, actor, rate_card_id):
    core.require(actor, "crm.admin.pricing.manage")
    row = conn.execute("SELECT * FROM rate_cards WHERE id=?", (rate_card_id,)).fetchone()
    if not row:
        raise core.NotFoundError("rate card not found")
    conn.execute("UPDATE rate_cards SET status='ARCHIVED', effective_to=? WHERE id=?",
                 (core.now(), rate_card_id))
    core.audit(conn, actor, "rate_card.archive", "rate_card", rate_card_id)
    conn.commit()


def list_rate_cards(conn, actor, include_history=False):
    core.require(actor, "crm.admin.pricing.view")
    sql = "SELECT * FROM rate_cards"
    if not include_history:
        sql += " WHERE superseded=0 AND status='ACTIVE'"
    sql += " ORDER BY equipment_code, version DESC"
    return [dict(r) for r in conn.execute(sql).fetchall()]


# --------------------------------------------------------------------------- #
# Authoritative line pricing (server-side is the source of truth)
# --------------------------------------------------------------------------- #
def price_line(quoted_rate, qty, days, discount_pct=0.0, internal_cost=0.0):
    """Compute the money values for a single line. Pure function → deterministic and testable.

    Returns pre-tax figures; governed tax is applied at the quotation level. ``subtotal`` is
    the net (post-discount, pre-tax) selling value; ``gross_profit`` and ``margin_percent`` are
    against internal cost.
    """
    qty = qty or 1
    days = days or 1
    quoted_rate = quoted_rate or 0
    discount_pct = discount_pct or 0
    internal_cost = internal_cost or 0
    base = round(quoted_rate * qty * days, 2)
    discount = round(base * discount_pct / 100.0, 2)
    subtotal = round(base - discount, 2)                 # net selling value, pre-tax
    internal_total = round(internal_cost * qty * days, 2)
    gross_profit = round(subtotal - internal_total, 2)
    margin_percent = round(gross_profit / subtotal * 100.0, 2) if subtotal else None
    return {"base": base, "discount": discount, "subtotal": subtotal,
            "internal_total": internal_total, "gross_profit": gross_profit,
            "margin_percent": margin_percent}


def variance(standard_rate, quoted_rate):
    """Rate override variance of the quoted rate against the master standard rate."""
    standard_rate = standard_rate or 0
    quoted_rate = quoted_rate or 0
    amount = round(quoted_rate - standard_rate, 2)
    pct = round(amount / standard_rate * 100.0, 2) if standard_rate else (0.0 if amount == 0 else 100.0)
    return {"amount": amount, "pct": pct, "abs_pct": abs(pct)}


def seed_default_rate_cards(conn):
    """Seed a governed baseline catalog if the table is empty (dev/demo + tests)."""
    if conn.execute("SELECT 1 FROM rate_cards LIMIT 1").fetchone():
        return 0
    seeds = [
        ("CRANE-250T", "250t Crawler Crane", "crane", 85000, 60000),
        ("CRANE-350T", "350t All-Terrain Crane", "crane", 110000, 78000),
        ("CRANE-100T", "100t All-Terrain Crane", "crane", 55000, 39000),
        ("CRANE-50T", "50t Rough-Terrain Crane", "crane", 28000, 19000),
        ("TRAILER-LOWBED", "Lowbed Trailer", "transport", 12000, 8000),
        ("TRAILER-EXT", "Extendable Trailer", "transport", 15000, 10500),
        ("CREW-RIGGING", "Rigging Crew", "labor", 18000, 12000),
        ("MOBILIZATION", "Mobilization (flat)", "logistics", 25000, 18000),
    ]
    n = 0
    for code, name, svc, std, cost in seeds:
        unit = "flat" if code == "MOBILIZATION" else "day"
        conn.execute(
            "INSERT INTO rate_cards(equipment_code,equipment_name,service_type,billing_unit,"
            "standard_rate,min_rate,internal_cost,currency,version,effective_from,status,created_at)"
            " VALUES(?,?,?,?,?,?,?, 'PHP',1,?, 'ACTIVE',?)",
            (code, name, svc, unit, std, round(std * 0.85), cost, core.now(), core.now()))
        n += 1
    conn.commit()
    return n
