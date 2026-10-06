"""LiftHaul service-provider earnings wallet and governed payout scheduling.

This module deliberately separates four events that competitor marketing often describes as one:
earned, available, requested, and paid.  It never treats a released booking, an HTTP response, or a
wallet screen as proof that money reached a driver or fleet owner.  Actual payout remains delegated
to the configured regulated payment provider and is fail-closed when that provider is unavailable.

Supported operating models:
* independent driver / owner-operator -> verified personal bank or e-wallet account;
* fleet operator -> verified business/owner payout account;
* contractual split -> only when a provider contract explicitly supports split disbursement.

The default LiftHaul policy offers on-demand business-day processing and an optional weekly Friday
schedule.  These are LiftHaul policies, not copied promises from another platform.
"""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import core
import tenant

MANILA = ZoneInfo("Asia/Manila")
MODES = ("ON_DEMAND", "WEEKLY_AUTOMATIC")
CHANNELS = ("GCASH", "MAYA", "BANK")
BENEFICIARIES = ("INDEPENDENT_DRIVER", "OWNER_OPERATOR", "FLEET_OWNER")
ALLOCATIONS = ("DRIVER_ONLY", "OWNER_ONLY", "CONTRACTUAL_SPLIT")
REQUEST_STATES = ("PENDING_REVIEW", "APPROVED", "SUBMITTED", "PROCESSING", "PAID",
                  "FAILED", "REJECTED", "HELD", "CANCELLED")

SCHEMA = """
CREATE TABLE IF NOT EXISTS mkt_provider_payout_policies(
  id INTEGER PRIMARY KEY, code TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  payout_mode TEXT NOT NULL, schedule_day INTEGER, cutoff_local TEXT,
  minimum_amount REAL NOT NULL DEFAULT 100, currency TEXT NOT NULL DEFAULT 'PHP',
  active INTEGER NOT NULL DEFAULT 1, effective_from TEXT, created_at TEXT,
  UNIQUE(code, version));

CREATE TABLE IF NOT EXISTS mkt_provider_payout_profiles(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, carrier_id INTEGER NOT NULL,
  beneficiary_type TEXT NOT NULL, allocation_mode TEXT NOT NULL,
  driver_share_bps INTEGER NOT NULL DEFAULT 0, owner_share_bps INTEGER NOT NULL DEFAULT 10000,
  payout_mode TEXT NOT NULL, policy_code TEXT NOT NULL,
  payout_account_id INTEGER NOT NULL, destination_channel TEXT NOT NULL,
  provider_name TEXT NOT NULL DEFAULT 'MOCK',
  status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL', created_by INTEGER,
  approved_by INTEGER, created_at TEXT, updated_at TEXT,
  UNIQUE(tenant_id, carrier_id));

CREATE TABLE IF NOT EXISTS mkt_provider_earnings(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, carrier_id INTEGER NOT NULL, driver_id INTEGER,
  source_payout_id INTEGER NOT NULL, booking_id INTEGER, assignment_id INTEGER,
  gross_amount REAL NOT NULL, platform_fee REAL NOT NULL DEFAULT 0,
  payment_fee REAL NOT NULL DEFAULT 0, net_amount REAL NOT NULL,
  paid_amount REAL NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'PHP',
  status TEXT NOT NULL DEFAULT 'AVAILABLE', hold_reason TEXT, available_at TEXT,
  created_at TEXT, UNIQUE(tenant_id, source_payout_id));

CREATE TABLE IF NOT EXISTS mkt_provider_payout_requests(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, carrier_id INTEGER NOT NULL,
  payout_profile_id INTEGER NOT NULL, payout_account_id INTEGER NOT NULL,
  amount REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'PHP', channel TEXT NOT NULL,
  payout_mode TEXT NOT NULL, policy_code TEXT NOT NULL, provider_name TEXT NOT NULL DEFAULT 'MOCK',
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING_REVIEW', requested_at TEXT,
  expected_processing_at TEXT, submitted_at TEXT, paid_at TEXT,
  provider_reference TEXT, failure_code TEXT, created_by INTEGER,
  UNIQUE(tenant_id, idempotency_key));

CREATE TABLE IF NOT EXISTS mkt_provider_payout_allocations(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, payout_request_id INTEGER NOT NULL,
  earning_id INTEGER NOT NULL, amount REAL NOT NULL, created_at TEXT,
  UNIQUE(payout_request_id, earning_id));
"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn):
    policies = (
        ("LH_ON_DEMAND_BD", 1, "ON_DEMAND", None, "12:00", 100.0),
        ("LH_WEEKLY_TUE", 1, "WEEKLY_AUTOMATIC", 1, "12:00", 100.0),
        ("LH_WEEKLY_FRI", 1, "WEEKLY_AUTOMATIC", 4, "12:00", 100.0),
    )
    for code, version, mode, day, cutoff, minimum in policies:
        if not conn.execute("SELECT 1 FROM mkt_provider_payout_policies WHERE code=? AND version=?",
                            (code, version)).fetchone():
            conn.execute(
                "INSERT INTO mkt_provider_payout_policies(code,version,payout_mode,schedule_day,"
                "cutoff_local,minimum_amount,effective_from,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (code, version, mode, day, cutoff, minimum, _now(), _now()))
    conn.commit()


def _business_day(dt):
    while dt.weekday() >= 5:
        dt += datetime.timedelta(days=1)
    return dt


def expected_processing_at(mode, requested_at=None, cutoff="12:00", schedule_day=4):
    """Return a transparent estimate; never a guarantee of bank/e-wallet receipt."""
    now = requested_at or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    local = now.astimezone(MANILA)
    hour, minute = (int(x) for x in cutoff.split(":", 1))
    if mode == "WEEKLY_AUTOMATIC":
        days = (int(schedule_day) - local.weekday()) % 7
        if days == 0 and local.time() >= datetime.time(hour, minute):
            days = 7
        target = local + datetime.timedelta(days=days)
    else:
        target = local
        if target.weekday() >= 5 or target.time() >= datetime.time(hour, minute):
            target += datetime.timedelta(days=1)
    target = _business_day(target)
    target = target.replace(hour=17, minute=0, second=0, microsecond=0)
    return target.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")


def configure_profile(conn, actor, carrier_id, *, beneficiary_type, allocation_mode,
                      payout_mode, payout_account_id, destination_channel,
                      driver_share_bps=0, owner_share_bps=10000,
                      policy_code=None, split_supported=False, provider_name="MOCK"):
    core.require(actor, "marketplace.payout.manage")
    if beneficiary_type not in BENEFICIARIES or allocation_mode not in ALLOCATIONS:
        raise ValueError("invalid beneficiary or allocation model")
    if payout_mode not in MODES or destination_channel not in CHANNELS:
        raise ValueError("invalid payout mode or destination channel")
    provider_name = str(provider_name or "MOCK").upper()
    if int(driver_share_bps) + int(owner_share_bps) != 10000:
        raise ValueError("driver and owner allocation must total 10000 basis points")
    if allocation_mode == "CONTRACTUAL_SPLIT" and not split_supported:
        raise ValueError("contractual split requires written provider split-disbursement support")
    if beneficiary_type == "FLEET_OWNER" and allocation_mode == "DRIVER_ONLY":
        raise ValueError("fleet-owner profile cannot silently route all earnings to one driver")
    account = conn.execute(
        "SELECT * FROM mkt_payout_accounts WHERE id=? AND carrier_id=?",
        (int(payout_account_id), int(carrier_id))).fetchone()
    if not account:
        raise core.NotFoundError("payout account does not belong to carrier")
    if account["status"] != "ACTIVE" or account["verification_status"] != "VERIFIED" or not account["holder_verified"]:
        raise ValueError("verified active beneficiary account required")
    policy_code = policy_code or ("LH_WEEKLY_FRI" if payout_mode == "WEEKLY_AUTOMATIC" else "LH_ON_DEMAND_BD")
    policy = conn.execute(
        "SELECT * FROM mkt_provider_payout_policies WHERE code=? AND payout_mode=? AND active=1 "
        "ORDER BY version DESC LIMIT 1", (policy_code, payout_mode)).fetchone()
    if not policy:
        raise ValueError("active payout policy not found")
    existing = conn.execute(
        "SELECT id FROM mkt_provider_payout_profiles WHERE tenant_id=? AND carrier_id=?",
        (actor.get("tenant_id"), carrier_id)).fetchone()
    now = _now()
    if existing:
        conn.execute(
            "UPDATE mkt_provider_payout_profiles SET beneficiary_type=?,allocation_mode=?,"
            "driver_share_bps=?,owner_share_bps=?,payout_mode=?,policy_code=?,payout_account_id=?,"
            "destination_channel=?,provider_name=?,status='ACTIVE',approved_by=?,updated_at=? WHERE id=?",
            (beneficiary_type, allocation_mode, int(driver_share_bps), int(owner_share_bps),
             payout_mode, policy_code, int(payout_account_id), destination_channel, provider_name,
             actor["id"], now, existing["id"]))
        pid = existing["id"]
    else:
        cur = conn.execute(
            "INSERT INTO mkt_provider_payout_profiles(tenant_id,carrier_id,beneficiary_type,"
            "allocation_mode,driver_share_bps,owner_share_bps,payout_mode,policy_code,payout_account_id,"
            "destination_channel,provider_name,status,created_by,approved_by,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',?,?,?,?)",
            (actor.get("tenant_id"), int(carrier_id), beneficiary_type, allocation_mode,
             int(driver_share_bps), int(owner_share_bps), payout_mode, policy_code,
             int(payout_account_id), destination_channel, provider_name,
             actor["id"], actor["id"], now, now))
        pid = cur.lastrowid
        tenant.stamp(conn, actor, "mkt_provider_payout_profiles", pid)
    core.audit(conn, actor, "MKT_PROVIDER_PAYOUT_PROFILE_CONFIGURED", "mkt_provider_payout_profiles", pid,
               new={"carrier_id": carrier_id, "beneficiary_type": beneficiary_type,
                    "allocation_mode": allocation_mode, "payout_mode": payout_mode,
                    "channel": destination_channel, "policy": policy_code,
                    "provider": provider_name})
    conn.commit()
    return profile(conn, actor, carrier_id)


def profile(conn, actor, carrier_id):
    row = conn.execute(
        "SELECT * FROM mkt_provider_payout_profiles WHERE tenant_id=? AND carrier_id=?",
        (actor.get("tenant_id"), int(carrier_id))).fetchone()
    return dict(row) if row else None


def credit_release(conn, actor, payout_id):
    """Create one immutable available-earning record from an approved release snapshot."""
    po = conn.execute("SELECT * FROM mkt_payouts WHERE id=?", (int(payout_id),)).fetchone()
    if not po:
        raise core.NotFoundError("payout snapshot not found")
    previous = conn.execute(
        "SELECT id FROM mkt_provider_earnings WHERE tenant_id=? AND source_payout_id=?",
        (po["tenant_id"], po["id"])).fetchone()
    if previous:
        return previous["id"]
    pr = conn.execute("SELECT booking_id,assignment_id FROM mkt_payment_requirements WHERE id=?",
                      (po["payment_requirement_id"],)).fetchone()
    driver_id = None
    if pr and pr["assignment_id"]:
        assignment = conn.execute("SELECT driver_id FROM mkt_assignments WHERE id=?",
                                  (pr["assignment_id"],)).fetchone()
        driver_id = assignment["driver_id"] if assignment else None
    cur = conn.execute(
        "INSERT INTO mkt_provider_earnings(tenant_id,carrier_id,driver_id,source_payout_id,booking_id,"
        "assignment_id,gross_amount,platform_fee,payment_fee,net_amount,currency,status,available_at,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,'AVAILABLE',?,?)",
        (po["tenant_id"], po["carrier_id"], driver_id, po["id"],
         pr["booking_id"] if pr else None, pr["assignment_id"] if pr else None,
         po["gross_value"], po["platform_commission"] or 0, po["payment_fee"] or 0,
         po["net_payout"], po["currency"], _now(), _now()))
    eid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_provider_earnings", eid)
    core.audit(conn, actor, "MKT_PROVIDER_EARNING_AVAILABLE", "mkt_provider_earnings", eid,
               new={"carrier_id": po["carrier_id"], "source_payout_id": po["id"],
                    "amount": po["net_payout"]})
    conn.commit()
    return eid


def wallet(conn, actor, carrier_id):
    rows = conn.execute(
        "SELECT * FROM mkt_provider_earnings WHERE tenant_id=? AND carrier_id=? ORDER BY id",
        (actor.get("tenant_id"), int(carrier_id))).fetchall()
    available = sum(max(0, (r["net_amount"] or 0) - (r["paid_amount"] or 0))
                    for r in rows if r["status"] in ("AVAILABLE", "PARTIALLY_PAID"))
    held = sum(max(0, (r["net_amount"] or 0) - (r["paid_amount"] or 0))
               for r in rows if r["status"] == "HELD")
    pending = conn.execute(
        "SELECT COALESCE(SUM(amount),0) v FROM mkt_provider_payout_requests WHERE tenant_id=? "
        "AND carrier_id=? AND status IN('PENDING_REVIEW','APPROVED','SUBMITTED','PROCESSING')",
        (actor.get("tenant_id"), int(carrier_id))).fetchone()["v"] or 0
    return {"carrier_id": int(carrier_id), "currency": "PHP",
            "available": round(available, 2), "reserved": round(pending, 2),
            "withdrawable": round(max(0, available - pending), 2), "held": round(held, 2),
            "earnings": [dict(r) for r in rows]}


def request_payout(conn, actor, carrier_id, amount, idempotency_key, requested_at=None):
    core.require(actor, "marketplace.payout.manage")
    prof = profile(conn, actor, carrier_id)
    if not prof or prof["status"] != "ACTIVE":
        raise ValueError("active payout profile required")
    prior = conn.execute(
        "SELECT * FROM mkt_provider_payout_requests WHERE tenant_id=? AND idempotency_key=?",
        (actor.get("tenant_id"), str(idempotency_key))).fetchone()
    if prior:
        if int(prior["carrier_id"]) != int(carrier_id) or round(prior["amount"], 2) != round(float(amount), 2):
            raise ValueError("idempotency key reused with different payout request")
        return dict(prior)
    policy = conn.execute(
        "SELECT * FROM mkt_provider_payout_policies WHERE code=? AND payout_mode=? AND active=1 "
        "ORDER BY version DESC LIMIT 1", (prof["policy_code"], prof["payout_mode"])).fetchone()
    amount = round(float(amount), 2)
    if amount < float(policy["minimum_amount"]):
        raise ValueError("amount is below the payout-policy minimum")
    bal = wallet(conn, actor, carrier_id)
    if amount > bal["withdrawable"]:
        raise ValueError("amount exceeds withdrawable earnings")
    import marketplace_trust_closure as tc
    guard = tc.payout_allowed(conn, prof["payout_account_id"], amount)
    if not guard["ok"]:
        raise ValueError("payout blocked: " + ",".join(guard["reasons"]))
    when = requested_at or datetime.datetime.now(datetime.timezone.utc)
    expected = expected_processing_at(prof["payout_mode"], when, policy["cutoff_local"],
                                      policy["schedule_day"] if policy["schedule_day"] is not None else 4)
    cur = conn.execute(
        "INSERT INTO mkt_provider_payout_requests(tenant_id,carrier_id,payout_profile_id,payout_account_id,"
        "amount,currency,channel,payout_mode,policy_code,provider_name,idempotency_key,status,requested_at,"
        "expected_processing_at,created_by) VALUES(?,?,?,?,?,'PHP',?,?,?,?,?,'PENDING_REVIEW',?,?,?)",
        (actor.get("tenant_id"), int(carrier_id), prof["id"], prof["payout_account_id"], amount,
         prof["destination_channel"], prof["payout_mode"], prof["policy_code"], prof["provider_name"],
         str(idempotency_key),
         when.isoformat(timespec="seconds"), expected, actor["id"]))
    rid = cur.lastrowid
    tenant.stamp(conn, actor, "mkt_provider_payout_requests", rid)
    core.audit(conn, actor, "MKT_PROVIDER_PAYOUT_REQUESTED", "mkt_provider_payout_requests", rid,
               new={"carrier_id": carrier_id, "amount": amount, "expected_processing_at": expected})
    conn.commit()
    return dict(conn.execute("SELECT * FROM mkt_provider_payout_requests WHERE id=?", (rid,)).fetchone())


def submit_payout(conn, actor, request_id, scenario=None):
    """Independent finance action. Provider success with a reference is required before PAID."""
    core.require(actor, "marketplace.payout.approve")
    req = conn.execute("SELECT * FROM mkt_provider_payout_requests WHERE id=?", (int(request_id),)).fetchone()
    if not req:
        raise core.NotFoundError("payout request not found")
    if req["status"] == "PAID":
        return dict(req)
    if req["status"] not in ("PENDING_REVIEW", "APPROVED", "FAILED"):
        raise ValueError("payout request is not submit-ready")
    account = conn.execute("SELECT * FROM mkt_payout_accounts WHERE id=?", (req["payout_account_id"],)).fetchone()
    import marketplace_trust_closure as tc
    guard = tc.payout_allowed(conn, account["id"], req["amount"])
    if not guard["ok"]:
        conn.execute("UPDATE mkt_provider_payout_requests SET status='HELD',failure_code=? WHERE id=?",
                     (",".join(guard["reasons"]), req["id"]))
        conn.commit()
        return dict(conn.execute("SELECT * FROM mkt_provider_payout_requests WHERE id=?", (req["id"],)).fetchone())
    import marketplace_payments as mp
    payload = dict(req)
    payload["provider_beneficiary_reference"] = account["provider_reference"]
    payload["_scenario"] = scenario
    if req["provider_name"] == "MOCK":
        import public_provider as environment
        if environment.env_posture()["treated_as_production"]:
            raise core.ForbiddenError("MOCK payout provider is forbidden in production")
    mp._assert_live_allowed(conn, req["provider_name"])
    conn.execute("UPDATE mkt_provider_payout_requests SET status='SUBMITTED',submitted_at=? WHERE id=?",
                 (_now(), req["id"]))
    result = mp.provider(req["provider_name"]).submit_payout(payload)
    if not result.get("ok") or not result.get("reference"):
        conn.execute("UPDATE mkt_provider_payout_requests SET status='FAILED',failure_code='provider_failed' WHERE id=?",
                     (req["id"],))
        conn.commit()
        return dict(conn.execute("SELECT * FROM mkt_provider_payout_requests WHERE id=?", (req["id"],)).fetchone())
    remaining = float(req["amount"])
    earnings = conn.execute(
        "SELECT * FROM mkt_provider_earnings WHERE tenant_id=? AND carrier_id=? "
        "AND status IN('AVAILABLE','PARTIALLY_PAID') ORDER BY available_at,id",
        (req["tenant_id"], req["carrier_id"])).fetchall()
    for earning in earnings:
        if remaining <= 0:
            break
        open_amount = round((earning["net_amount"] or 0) - (earning["paid_amount"] or 0), 2)
        take = min(open_amount, remaining)
        conn.execute(
            "INSERT INTO mkt_provider_payout_allocations(tenant_id,payout_request_id,earning_id,amount,created_at) "
            "VALUES(?,?,?,?,?)", (req["tenant_id"], req["id"], earning["id"], take, _now()))
        new_paid = round((earning["paid_amount"] or 0) + take, 2)
        state = "PAID" if new_paid >= round(earning["net_amount"], 2) else "PARTIALLY_PAID"
        conn.execute("UPDATE mkt_provider_earnings SET paid_amount=?,status=? WHERE id=?",
                     (new_paid, state, earning["id"]))
        if state == "PAID":
            conn.execute("UPDATE mkt_payouts SET status='PAID',provider_beneficiary_reference=?,payout_date=? "
                         "WHERE id=?", (result["reference"], _now(), earning["source_payout_id"]))
        remaining = round(remaining - take, 2)
    if remaining > 0.001:
        raise RuntimeError("payout allocation invariant failed")
    conn.execute("UPDATE mkt_provider_payout_requests SET status='PAID',provider_reference=?,paid_at=? WHERE id=?",
                 (result["reference"], _now(), req["id"]))
    core.audit(conn, actor, "MKT_PROVIDER_PAYOUT_PAID", "mkt_provider_payout_requests", req["id"],
               new={"provider_reference": result["reference"], "amount": req["amount"]})
    conn.commit()
    return dict(conn.execute("SELECT * FROM mkt_provider_payout_requests WHERE id=?", (req["id"],)).fetchone())


def list_requests(conn, actor, carrier_id=None):
    core.require(actor, "marketplace.payout.view")
    sql = "SELECT * FROM mkt_provider_payout_requests WHERE tenant_id=?"
    args = [actor.get("tenant_id")]
    if carrier_id is not None:
        sql += " AND carrier_id=?"; args.append(int(carrier_id))
    sql += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]
