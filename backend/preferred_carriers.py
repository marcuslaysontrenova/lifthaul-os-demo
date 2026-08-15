"""Preferred Carriers / Dedicated Capacity — a shipper preference layer over the existing matching.

Large shippers want to steer work to carriers they trust, exclude ones they don't, and reserve
guaranteed capacity for peak periods. This module adds that WITHOUT forking matching, ranking, or the
carrier/vehicle domains:

  * `carrier_preferences` — a shipper's carrier list, each at a tier:
        DEDICATED  — a carrier the shipper has reserved capacity with (highest preference)
        EXCLUSIVE  — when ANY exclusive preference exists, the shipper's pool is RESTRICTED to its
                     preferred carriers only
        PREFERRED  — boosted in ranking, but non-preferred carriers still compete
        BLOCKED    — excluded from this shipper's matches (a business choice, NOT a compliance action)
  * `dedicated_capacity` — a commitment of N units of a vehicle category from a carrier to a shipper
    over a period. Usage is computed HONESTLY from real assignments, never asserted.

The preference layer is applied AFTER the deterministic `rank_candidates` step via `apply_preferences`,
which only REORDERS/FILTERS an already-eligible candidate list — it can never override a hard
eligibility or compliance gate (those ran in `candidate_pool`). The adjustment is transparent: every
candidate is annotated with its preference tier and the exact bonus applied. `generate_candidates`
consults this layer through a guarded hook, so preferences take effect without matching having to know
how they are stored.
"""
from __future__ import annotations

import datetime

import core
import tenant


TIERS = ("DEDICATED", "EXCLUSIVE", "PREFERRED", "BLOCKED")
CAP_STATUSES = ("ACTIVE", "EXPIRED", "CANCELLED")
# Ranking scores sum weighted factors in ~[0,1]; these bonuses guarantee tier ordering above
# non-preferred carriers while preserving the deterministic relative order WITHIN a tier.
_TIER_BONUS = {"DEDICATED": 3.0, "EXCLUSIVE": 2.0, "PREFERRED": 1.0}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _today():
    return datetime.date.today().isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS carrier_preferences(
  id INTEGER PRIMARY KEY, tenant_id INTEGER,
  shipper_id INTEGER NOT NULL, carrier_id INTEGER NOT NULL,
  tier TEXT NOT NULL DEFAULT 'PREFERRED', priority INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'ACTIVE', note TEXT,
  created_by INTEGER, created_at TEXT, updated_by INTEGER, updated_at TEXT,
  UNIQUE(tenant_id, shipper_id, carrier_id));

CREATE TABLE IF NOT EXISTS dedicated_capacity(
  id INTEGER PRIMARY KEY, tenant_id INTEGER,
  shipper_id INTEGER NOT NULL, carrier_id INTEGER NOT NULL,
  vehicle_category TEXT NOT NULL, committed_units INTEGER NOT NULL,
  period_start TEXT, period_end TEXT, rate_ref TEXT,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_by INTEGER, created_at TEXT, cancelled_by INTEGER, cancelled_at TEXT);
"""


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    return


def _row(conn, table, id):
    r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (id,)).fetchone()
    if not r:
        raise core.NotFoundError(f"{table} row {id} not found")
    return dict(r)


def _exists(conn, table, id):
    return conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (id,)).fetchone() is not None


# --------------------------------------------------------------------------- #
# Preferences
# --------------------------------------------------------------------------- #
def set_preference(conn, actor, shipper_id, carrier_id, tier, *, priority=0, note=None):
    core.require(actor, "marketplace.preference.manage")
    if tier not in TIERS:
        raise core.ValidationError(f"invalid tier '{tier}' (expected one of {TIERS})")
    if not _exists(conn, "mkt_shippers", shipper_id):
        raise core.NotFoundError(f"shipper {shipper_id} not found")
    if not _exists(conn, "mkt_carriers", carrier_id):
        raise core.NotFoundError(f"carrier {carrier_id} not found")
    at = tenant.actor_tenant(actor)
    existing = conn.execute("SELECT id FROM carrier_preferences WHERE shipper_id=? AND carrier_id=? "
                            "AND (tenant_id=? OR tenant_id IS NULL)", (shipper_id, carrier_id, at)).fetchone()
    if existing:
        conn.execute("UPDATE carrier_preferences SET tier=?,priority=?,note=?,status='ACTIVE',updated_by=?,"
                     "updated_at=? WHERE id=?", (tier, priority, note, actor["id"], _now(), existing["id"]))
        pid = existing["id"]
    else:
        cur = conn.execute("INSERT INTO carrier_preferences(shipper_id,carrier_id,tier,priority,status,note,"
                           "created_by,created_at) VALUES(?,?,?,?, 'ACTIVE', ?,?,?)",
                           (shipper_id, carrier_id, tier, priority, note, actor["id"], _now()))
        pid = cur.lastrowid
        tenant.stamp(conn, actor, "carrier_preferences", pid)
    core.audit(conn, actor, "CARRIER_PREFERENCE_SET", "carrier_preferences", pid, None,
               {"shipper": shipper_id, "carrier": carrier_id, "tier": tier, "priority": priority})
    conn.commit()
    return {"preference_id": pid, "tier": tier}


def remove_preference(conn, actor, preference_id):
    core.require(actor, "marketplace.preference.manage")
    p = _row(conn, "carrier_preferences", preference_id)
    tenant.guard(actor, p)
    conn.execute("UPDATE carrier_preferences SET status='REMOVED',updated_by=?,updated_at=? WHERE id=?",
                 (actor["id"], _now(), preference_id))
    core.audit(conn, actor, "CARRIER_PREFERENCE_REMOVED", "carrier_preferences", preference_id, None, {})
    conn.commit()
    return {"status": "REMOVED"}


def list_preferences(conn, actor, shipper_id=None):
    core.require(actor, "marketplace.preference.view")
    frag, params = tenant.predicate(actor)
    q = "SELECT * FROM carrier_preferences WHERE status='ACTIVE'" + frag
    a = list(params)
    if shipper_id:
        q += " AND shipper_id=?"; a.append(shipper_id)
    q += " ORDER BY tier, priority DESC, id"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


def preference_map(conn, shipper_id, tenant_id=None):
    """Read-only map carrier_id -> {tier, priority} for a shipper. Internal helper (no actor)."""
    if not shipper_id:
        return {}
    rows = conn.execute("SELECT carrier_id,tier,priority FROM carrier_preferences WHERE shipper_id=? "
                        "AND status='ACTIVE' AND (tenant_id=? OR tenant_id IS NULL)",
                        (shipper_id, tenant_id)).fetchall()
    return {r["carrier_id"]: {"tier": r["tier"], "priority": r["priority"]} for r in rows}


def apply_preferences(conn, shipper_id, ranked, tenant_id=None):
    """Reorder/filter an ALREADY-ELIGIBLE ranked candidate list by a shipper's preferences. Never
    changes eligibility (hard gates already passed). Transparent: annotates preference_tier +
    preference_bonus + adjusted_score on each surviving candidate."""
    prefs = preference_map(conn, shipper_id, tenant_id)
    if not prefs:
        return ranked
    exclusive_mode = any(v["tier"] == "EXCLUSIVE" for v in prefs.values())
    out = []
    for c in ranked:
        pref = prefs.get(c.get("carrier_id"))
        tier = pref["tier"] if pref else None
        if tier == "BLOCKED":
            continue   # shipper-excluded (business choice, not a compliance action)
        if exclusive_mode and tier not in ("EXCLUSIVE", "DEDICATED", "PREFERRED"):
            continue   # exclusive pool: only the shipper's preferred carriers compete
        bonus = _TIER_BONUS.get(tier, 0.0)
        prio = (pref["priority"] if pref else 0) * 0.001
        adjusted = round(float(c.get("score", 0)) + bonus + prio, 4)
        out.append({**c, "preference_tier": tier, "preference_bonus": bonus, "adjusted_score": adjusted})
    out.sort(key=lambda x: x["adjusted_score"], reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Dedicated capacity commitments
# --------------------------------------------------------------------------- #
def reserve_capacity(conn, actor, shipper_id, carrier_id, vehicle_category, committed_units,
                     *, period_start=None, period_end=None, rate_ref=None):
    core.require(actor, "marketplace.capacity.manage")
    if not _exists(conn, "mkt_shippers", shipper_id):
        raise core.NotFoundError(f"shipper {shipper_id} not found")
    carrier = conn.execute("SELECT status FROM mkt_carriers WHERE id=?", (carrier_id,)).fetchone()
    if not carrier:
        raise core.NotFoundError(f"carrier {carrier_id} not found")
    if carrier["status"] != "ACTIVE":
        raise core.ConflictError("carrier must be ACTIVE to commit dedicated capacity")
    if committed_units is None or int(committed_units) <= 0:
        raise core.ValidationError("committed_units must be positive")
    if period_start and period_end and period_end < period_start:
        raise core.ValidationError("period_end before period_start")
    cur = conn.execute(
        "INSERT INTO dedicated_capacity(shipper_id,carrier_id,vehicle_category,committed_units,period_start,"
        "period_end,rate_ref,status,created_by,created_at) VALUES(?,?,?,?,?,?,?, 'ACTIVE', ?,?)",
        (shipper_id, carrier_id, vehicle_category, int(committed_units), period_start, period_end,
         rate_ref, actor["id"], _now()))
    cid = cur.lastrowid
    tenant.stamp(conn, actor, "dedicated_capacity", cid)
    # a dedicated commitment implies a DEDICATED preference (idempotent upsert)
    try:
        set_preference(conn, actor, shipper_id, carrier_id, "DEDICATED", priority=10,
                       note=f"dedicated capacity #{cid}")
    except Exception:
        pass
    core.audit(conn, actor, "DEDICATED_CAPACITY_RESERVED", "dedicated_capacity", cid, None,
               {"shipper": shipper_id, "carrier": carrier_id, "category": vehicle_category, "units": committed_units})
    conn.commit()
    return {"capacity_id": cid, "status": "ACTIVE"}


def cancel_capacity(conn, actor, capacity_id, reason=None):
    core.require(actor, "marketplace.capacity.manage")
    c = _row(conn, "dedicated_capacity", capacity_id)
    tenant.guard(actor, c)
    conn.execute("UPDATE dedicated_capacity SET status='CANCELLED',cancelled_by=?,cancelled_at=? WHERE id=?",
                 (actor["id"], _now(), capacity_id))
    core.audit(conn, actor, "DEDICATED_CAPACITY_CANCELLED", "dedicated_capacity", capacity_id, None,
               {"reason": reason})
    conn.commit()
    return {"status": "CANCELLED"}


def capacity_status(conn, actor, capacity_id):
    """Committed vs USED vs available. Usage is counted HONESTLY from real assignments to the carrier
    whose vehicle matches the category within the commitment period — never asserted."""
    core.require(actor, "marketplace.capacity.view")
    c = _row(conn, "dedicated_capacity", capacity_id)
    tenant.guard(actor, c)
    q = ("SELECT COUNT(*) n FROM mkt_assignments a JOIN mkt_vehicles v ON v.id=a.vehicle_id "
         "WHERE a.carrier_id=? AND v.category_code=? AND a.status NOT IN('CANCELLED','EXPIRED','REASSIGNMENT_REQUIRED')")
    params = [c["carrier_id"], c["vehicle_category"]]
    # scope to the shipper's own bookings within the period
    q += " AND a.shipper_id=?"; params.append(c["shipper_id"])
    if c["period_start"]:
        q += " AND (a.assigned_at IS NULL OR a.assigned_at>=?)"; params.append(c["period_start"])
    if c["period_end"]:
        q += " AND (a.assigned_at IS NULL OR a.assigned_at<=?)"; params.append(c["period_end"] + "T23:59:59")
    used = conn.execute(q, params).fetchone()["n"]
    committed = int(c["committed_units"])
    return {"capacity_id": capacity_id, "committed_units": committed, "used_units": used,
            "available_units": max(0, committed - used), "status": c["status"],
            "vehicle_category": c["vehicle_category"], "carrier_id": c["carrier_id"]}


def list_capacity(conn, actor, shipper_id=None, status=None):
    core.require(actor, "marketplace.capacity.view")
    frag, params = tenant.predicate(actor)
    q = "SELECT * FROM dedicated_capacity WHERE 1=1" + frag
    a = list(params)
    if shipper_id:
        q += " AND shipper_id=?"; a.append(shipper_id)
    if status:
        q += " AND status=?"; a.append(status)
    q += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


# --------------------------------------------------------------------------- #
def queues(conn, actor):
    core.require(actor, "marketplace.preference.view")
    frag, params = tenant.predicate(actor)

    def cnt(table, extra=""):
        return conn.execute(f"SELECT COUNT(*) c FROM {table} WHERE 1=1" + frag + extra, params).fetchone()["c"]
    return {
        "preferred": cnt("carrier_preferences", " AND status='ACTIVE' AND tier='PREFERRED'"),
        "dedicated": cnt("carrier_preferences", " AND status='ACTIVE' AND tier='DEDICATED'"),
        "exclusive": cnt("carrier_preferences", " AND status='ACTIVE' AND tier='EXCLUSIVE'"),
        "blocked": cnt("carrier_preferences", " AND status='ACTIVE' AND tier='BLOCKED'"),
        "active_capacity": cnt("dedicated_capacity", " AND status='ACTIVE'"),
    }


def run_integrity(conn, actor):
    core.require(actor, "marketplace.preference.view")
    checks = []
    orphan = conn.execute("SELECT COUNT(*) c FROM carrier_preferences p LEFT JOIN mkt_carriers c "
                          "ON c.id=p.carrier_id WHERE c.id IS NULL").fetchone()["c"]
    checks.append({"check": "no_orphan_preference", "ok": orphan == 0, "count": orphan})
    dup = conn.execute("SELECT COUNT(*) c FROM (SELECT shipper_id,carrier_id,COALESCE(tenant_id,-1) t,"
                       "COUNT(*) n FROM carrier_preferences GROUP BY shipper_id,carrier_id,t HAVING n>1)").fetchone()["c"]
    checks.append({"check": "one_preference_per_pair", "ok": dup == 0, "count": dup})
    neg = conn.execute("SELECT COUNT(*) c FROM dedicated_capacity WHERE committed_units<=0").fetchone()["c"]
    checks.append({"check": "positive_committed_units", "ok": neg == 0, "count": neg})
    return {"ok": all(x["ok"] for x in checks), "checks": checks}
