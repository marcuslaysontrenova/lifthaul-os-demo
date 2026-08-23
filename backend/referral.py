"""LiftHaul Enterprise — Referral Rewards (single-level, direct referral only).

A legitimate SINGLE-LEVEL direct referral program. A refers B; when B completes a real qualifying
commercial event, A may earn a configured reward. **A never earns anything from B's referrals** — there
are no downlines, genealogy trees, override commissions, or multi-level payouts. A referral row stores
ONLY `referrer_ref` + `referred_ref` (§38) — no parent chain is ever created or traversed for compensation.

Hard rules enforced here:
  * REGISTERED != EARNED — a reward requires a verified qualifying commercial event (§3), checked against
    the real canonical domains (accreditation fee paid / marketplace-eligible unit / settled booking).
  * Reward is a LiftHaul acquisition expense computed on a clean basis — NEVER on VAT, insurance, Protected
    Payment funds, carrier settlement, or payment pass-through (§13–14, §44–46).
  * Governed lifecycle with a validation cooldown; Finance approves + pays; referrer can never
    qualify/approve/pay its own reward (§16, §39).
  * Fraud screening (self / duplicate-company / circular / duplicate-payout / farming) — HIGH/CRITICAL
    risk fails closed to REVIEW_REQUIRED; weak signals never auto-accuse (§19–24).
  * Campaigns with budget + per-user/monthly caps + terms version; earned rewards are immutable snapshots
    (§26–29, §42). The whole program is OFF by default behind `referral.program.enabled` (§30, §59).

Reuse-only: extends nothing's identity/payment/carrier model; it references them.
"""
from __future__ import annotations

import json
import secrets

import core
import tenant

REFERRER_TYPES = ("CARRIER", "SHIPPER", "PARTNER", "AFFILIATE", "EMPLOYEE", "BD_AGENT")
REFERRED_TYPES = ("CARRIER", "SHIPPER")
QUALIFYING_EVENTS = ("FIRST_ACCREDITED_VEHICLE", "FIRST_MARKETPLACE_ELIGIBLE_UNIT", "FIRST_COMPLETED_BOOKING",
                     "FIRST_SETTLED_MARKETPLACE_JOB", "ENTERPRISE_FLEET_ACTIVATION", "FIRST_PAID_SUBSCRIPTION",
                     "MANUAL_STRATEGIC_QUALIFICATION")
REWARD_TYPES = ("FIXED", "PERCENTAGE", "TIERED", "CREDIT", "MANUAL")
STATES = ("INVITED", "REGISTERED", "VERIFIED", "QUALIFIED", "REVIEW_REQUIRED", "EARNED", "APPROVED",
          "PAYABLE", "PAID", "REJECTED", "CANCELLED", "REVERSED")
# governed transitions (fraud/cancellation branches handled explicitly by the functions)
_TRANSITIONS = {
    "REGISTERED": {"VERIFIED", "REVIEW_REQUIRED", "CANCELLED", "REJECTED"},
    "VERIFIED": {"QUALIFIED", "REVIEW_REQUIRED", "CANCELLED", "REJECTED"},
    "QUALIFIED": {"EARNED", "REVIEW_REQUIRED", "REJECTED"},
    "REVIEW_REQUIRED": {"EARNED", "REJECTED", "CANCELLED", "REVERSED"},
    "EARNED": {"APPROVED", "REVIEW_REQUIRED", "REVERSED"},
    "APPROVED": {"PAYABLE", "REVERSED"},
    "PAYABLE": {"PAID", "REVERSED"},
    "PAID": {"REVERSED"},
}

# RBAC
P_VIEW = "referral.view"          # a referrer sees ONLY its own code/referrals/rewards
P_MANAGE = "referral.manage"      # platform admin: campaigns + codes
P_QUALIFY = "referral.qualify"    # ops/compliance: confirm qualification
P_FINANCE = "referral.finance"    # finance: approve / pay / reverse
P_FRAUD = "referral.fraud"        # risk: review / release

_HIGH_RISK = ("HIGH", "CRITICAL")

SCHEMA = """
CREATE TABLE IF NOT EXISTS referral_campaigns(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, code TEXT, name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'DRAFT', referrer_types TEXT, referred_types TEXT,
  qualifying_event TEXT, reward_type TEXT DEFAULT 'FIXED', reward_amount REAL, reward_pct REAL,
  reward_basis TEXT DEFAULT 'NET_ACCREDITATION_FEE', max_reward REAL, validation_days INTEGER DEFAULT 14,
  per_user_cap INTEGER, monthly_cap INTEGER, total_budget REAL, committed REAL DEFAULT 0,
  earned REAL DEFAULT 0, paid REAL DEFAULT 0, geography TEXT, equipment_class TEXT,
  fleet_bonus_units INTEGER, fleet_bonus_amount REAL, terms_version TEXT, version INTEGER DEFAULT 1,
  start_date TEXT, end_date TEXT, created_by INTEGER, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS referral_codes(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, code TEXT NOT NULL UNIQUE, referrer_type TEXT NOT NULL,
  referrer_ref TEXT NOT NULL, referrer_label TEXT, campaign_id INTEGER, status TEXT NOT NULL DEFAULT 'ACTIVE',
  expires_at TEXT, created_by INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS referrals(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, code TEXT, code_id INTEGER, campaign_id INTEGER,
  referrer_type TEXT, referrer_ref TEXT, referred_type TEXT, referred_ref TEXT, referred_label TEXT,
  source TEXT, status TEXT NOT NULL DEFAULT 'REGISTERED', qualifying_event TEXT, qualified_at TEXT,
  qualifying_txn TEXT, risk_status TEXT DEFAULT 'NONE', risk_reasons TEXT, attribution_conflict INTEGER DEFAULT 0,
  terms_version TEXT, campaign_version INTEGER, reward_type TEXT, reward_amount REAL, reward_basis_amount REAL,
  currency TEXT DEFAULT 'PHP', earned_at TEXT, validation_until TEXT, approved_by INTEGER, approved_at TEXT,
  payout_method TEXT, payout_ref TEXT, paid_by INTEGER, paid_at TEXT, reversed_reason TEXT,
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS referral_credits(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, referral_id INTEGER, beneficiary_type TEXT, beneficiary_ref TEXT,
  kind TEXT, amount REAL, currency TEXT DEFAULT 'PHP', status TEXT DEFAULT 'ISSUED', expiry TEXT,
  used_ref TEXT, created_at TEXT);
"""


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    return


# --------------------------------------------------------------------------- #
# config / program flag
# --------------------------------------------------------------------------- #
def _cfg(conn, key, default=None):
    try:
        import admin_platform as ap
        v, _ = ap.resolve_config(conn, key)
        return v if v is not None else default
    except Exception:
        return default


def program_enabled(conn):
    """Public payability gate — OFF by default; Legal/Finance must explicitly activate (§30/§59)."""
    return str(_cfg(conn, "referral.program.enabled", "false")).lower() == "true"


def _now():
    return core.now()


def _days_from(iso, days):
    import datetime
    try:
        base = datetime.datetime.fromisoformat(iso)
    except Exception:
        base = datetime.datetime.now(datetime.timezone.utc)
    return (base + datetime.timedelta(days=int(days))).isoformat(timespec="seconds")


def _passed(iso):
    import datetime
    try:
        t = datetime.datetime.fromisoformat(iso)
    except Exception:
        return True
    now = datetime.datetime.now(datetime.timezone.utc)
    if t.tzinfo is None:
        now = now.replace(tzinfo=None)
    return now >= t


def _tid(actor):
    return actor.get("tenant_id") if isinstance(actor, dict) else None


# --------------------------------------------------------------------------- #
# campaigns (Platform Control)
# --------------------------------------------------------------------------- #
def create_campaign(conn, actor, name, qualifying_event, **kw):
    core.require(actor, P_MANAGE)
    if qualifying_event not in QUALIFYING_EVENTS:
        raise core.ValidationError(f"qualifying_event must be one of {QUALIFYING_EVENTS}")
    rt = (kw.get("reward_type") or "FIXED").upper()
    if rt not in REWARD_TYPES:
        raise core.ValidationError(f"reward_type must be one of {REWARD_TYPES}")
    now = _now()
    cur = conn.execute(
        "INSERT INTO referral_campaigns(tenant_id,code,name,status,referrer_types,referred_types,"
        "qualifying_event,reward_type,reward_amount,reward_pct,reward_basis,max_reward,validation_days,"
        "per_user_cap,monthly_cap,total_budget,geography,equipment_class,fleet_bonus_units,"
        "fleet_bonus_amount,terms_version,version,start_date,end_date,created_by,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)",
        (_tid(actor), kw.get("code"), name[:200], (kw.get("status") or "DRAFT").upper(),
         json.dumps(kw.get("referrer_types") or list(REFERRER_TYPES)),
         json.dumps(kw.get("referred_types") or list(REFERRED_TYPES)), qualifying_event, rt,
         kw.get("reward_amount"), kw.get("reward_pct"),
         (kw.get("reward_basis") or "NET_ACCREDITATION_FEE"), kw.get("max_reward"),
         int(kw.get("validation_days", 14)), kw.get("per_user_cap"), kw.get("monthly_cap"),
         kw.get("total_budget"), kw.get("geography"), kw.get("equipment_class"),
         kw.get("fleet_bonus_units"), kw.get("fleet_bonus_amount"),
         (kw.get("terms_version") or "v1"), kw.get("start_date"), kw.get("end_date"),
         actor.get("id"), now, now))
    cid = cur.lastrowid
    tenant.stamp(conn, actor, "referral_campaigns", cid)
    core.audit(conn, actor, "CAMPAIGN_CREATED", "referral_campaigns", cid, None,
               {"name": name, "event": qualifying_event, "reward_type": rt})
    conn.commit()
    return _campaign(conn, cid)


def update_campaign(conn, actor, campaign_id, **changes):
    core.require(actor, P_MANAGE)
    c = _campaign(conn, campaign_id)
    tenant.guard(actor, c)
    allowed = {"name", "status", "reward_amount", "reward_pct", "max_reward", "validation_days",
               "per_user_cap", "monthly_cap", "geography", "equipment_class", "start_date", "end_date",
               "referrer_types", "referred_types"}
    sets, vals = [], []
    for k, v in changes.items():
        if k not in allowed:
            continue
        sets.append(f"{k}=?")
        vals.append(json.dumps(v) if k in ("referrer_types", "referred_types") else v)
    if not sets:
        return c
    conn.execute(f"UPDATE referral_campaigns SET {','.join(sets)},version=version+1,updated_at=? WHERE id=?",
                 (*vals, _now(), campaign_id))
    core.audit(conn, actor, "CAMPAIGN_CHANGED", "referral_campaigns", campaign_id, None,
               {"changes": list(changes.keys())})
    conn.commit()
    return _campaign(conn, campaign_id)


def set_campaign_budget(conn, actor, campaign_id, total_budget):
    core.require(actor, P_MANAGE)
    c = _campaign(conn, campaign_id)
    tenant.guard(actor, c)
    conn.execute("UPDATE referral_campaigns SET total_budget=?,updated_at=? WHERE id=?",
                 (float(total_budget), _now(), campaign_id))
    core.audit(conn, actor, "CAMPAIGN_BUDGET_CHANGED", "referral_campaigns", campaign_id, None,
               {"total_budget": total_budget})
    conn.commit()
    return _campaign(conn, campaign_id)


def _campaign(conn, cid):
    r = conn.execute("SELECT * FROM referral_campaigns WHERE id=?", (cid,)).fetchone()
    if not r:
        raise core.NotFoundError("campaign not found")
    d = dict(r)
    for k in ("referrer_types", "referred_types"):
        try:
            d[k] = json.loads(d[k]) if d.get(k) else []
        except Exception:
            d[k] = []
    d["remaining_budget"] = (None if d["total_budget"] is None
                             else round(float(d["total_budget"]) - float(d.get("committed") or 0), 2))
    return d


def list_campaigns(conn, actor):
    core.require(actor, P_MANAGE)
    frag, params = tenant.predicate(actor)
    rows = conn.execute("SELECT id FROM referral_campaigns WHERE 1=1" + frag + " ORDER BY id DESC",
                        list(params)).fetchall()
    return {"campaigns": [_campaign(conn, r["id"]) for r in rows]}


# --------------------------------------------------------------------------- #
# referral codes
# --------------------------------------------------------------------------- #
def _gen_code(label):
    slug = "".join(ch for ch in str(label or "").upper() if ch.isalnum())[:8] or "REF"
    return f"LH-{slug}-{secrets.token_hex(3).upper()}"


def issue_code(conn, actor, referrer_type, referrer_ref, *, campaign_id=None, referrer_label=None,
               expires_at=None):
    core.require(actor, P_MANAGE)
    rt = str(referrer_type or "").upper()
    if rt not in REFERRER_TYPES:
        raise core.ValidationError(f"referrer_type must be one of {REFERRER_TYPES}")
    for _ in range(5):
        code = _gen_code(referrer_label or referrer_ref)
        if not conn.execute("SELECT 1 FROM referral_codes WHERE code=?", (code,)).fetchone():
            break
    now = _now()
    cur = conn.execute("INSERT INTO referral_codes(tenant_id,code,referrer_type,referrer_ref,referrer_label,"
                       "campaign_id,status,expires_at,created_by,created_at) VALUES(?,?,?,?,?,?, 'ACTIVE',?,?,?)",
                       (_tid(actor), code, rt, str(referrer_ref), (str(referrer_label)[:120] if referrer_label else None),
                        campaign_id, expires_at, actor.get("id"), now))
    cid = cur.lastrowid
    tenant.stamp(conn, actor, "referral_codes", cid)
    core.audit(conn, actor, "REFERRAL_CODE_CREATED", "referral_codes", cid, None,
               {"code": code, "referrer_type": rt, "referrer_ref": str(referrer_ref)})
    conn.commit()
    return {"id": cid, "code": code, "referrer_type": rt, "status": "ACTIVE",
            "link": f"/register?ref={code}"}


def revoke_code(conn, actor, code):
    core.require(actor, P_MANAGE)
    r = conn.execute("SELECT * FROM referral_codes WHERE code=?", (code,)).fetchone()
    if not r:
        raise core.NotFoundError("code not found")
    tenant.guard(actor, r)
    conn.execute("UPDATE referral_codes SET status='REVOKED' WHERE id=?", (r["id"],))
    core.audit(conn, actor, "REFERRAL_CODE_REVOKED", "referral_codes", r["id"], None, {"code": code})
    conn.commit()
    return {"code": code, "status": "REVOKED"}


def validate_code(conn, code):
    """Public code check for the registration page. Never leaks the referrer's identity."""
    r = conn.execute("SELECT * FROM referral_codes WHERE code=?", (str(code or ""),)).fetchone()
    if not r or r["status"] != "ACTIVE":
        return {"valid": False, "reason": "Referral code not valid"}
    if r["expires_at"] and _passed(r["expires_at"]):
        return {"valid": False, "reason": "Referral code expired"}
    return {"valid": True, "message": "Referral applied"}


# --------------------------------------------------------------------------- #
# attribution (server = source of truth) + single-level + fraud screen
# --------------------------------------------------------------------------- #
def _carrier_ident(conn, carrier_ref):
    try:
        r = conn.execute("SELECT tax_id,registration_type,registration_number,contacts FROM mkt_carriers "
                         "WHERE id=?", (int(carrier_ref),)).fetchone()
        return dict(r) if r else {}
    except Exception:
        return {}


def _risk_screen(conn, actor, code_row, referred_type, referred_ref):
    """Return (risk_status, reasons[]) — supporting indicators only; never auto-accuse on one weak signal."""
    reasons = []
    rtype, rref = code_row["referrer_type"], str(code_row["referrer_ref"])
    # SELF_REFERRAL — same entity, or (carrier↔carrier) identical registration/tax identity
    if rtype == referred_type and rref == str(referred_ref):
        reasons.append("SELF_REFERRAL")
    if rtype == "CARRIER" and referred_type == "CARRIER":
        a, b = _carrier_ident(conn, rref), _carrier_ident(conn, referred_ref)
        if a and b and ((a.get("tax_id") and a.get("tax_id") == b.get("tax_id")) or
                        (a.get("registration_number") and a.get("registration_number") == b.get("registration_number")
                         and a.get("registration_type") == b.get("registration_type"))):
            reasons.append("SELF_REFERRAL" if "SELF_REFERRAL" not in reasons else "DUPLICATE_COMPANY")
    # CIRCULAR_REFERRAL — the referred entity has previously referred THIS referrer
    if conn.execute("SELECT 1 FROM referrals WHERE referrer_type=? AND referrer_ref=? AND referred_type=? "
                    "AND referred_ref=?", (referred_type, str(referred_ref), rtype, rref)).fetchone():
        reasons.append("CIRCULAR_REFERRAL")
    # DUPLICATE_COMPANY — the referred entity already has a live referral (no second reward per business)
    if conn.execute("SELECT 1 FROM referrals WHERE referred_type=? AND referred_ref=? AND status NOT IN "
                    "('REJECTED','CANCELLED','REVERSED')", (referred_type, str(referred_ref))).fetchone():
        reasons.append("DUPLICATE_REGISTRATION_ID")
    # ACCOUNT_FARMING / velocity — many recent referrals from the same referrer
    n = conn.execute("SELECT COUNT(*) c FROM referrals WHERE referrer_type=? AND referrer_ref=? AND "
                     "created_at>=?", (rtype, rref, _days_from(_now(), -1))).fetchone()["c"]
    if n >= 10:
        reasons.append("SUSPICIOUS_REFERRAL_VELOCITY")
    reasons = list(dict.fromkeys(reasons))
    if not reasons:
        return "NONE", []
    critical = {"SELF_REFERRAL", "CIRCULAR_REFERRAL"}
    status = "CRITICAL" if any(r in critical for r in reasons) else "HIGH" if len(reasons) >= 2 else "MEDIUM"
    return status, reasons


def attribute(conn, actor, code, referred_type, referred_ref, *, referred_label=None, source="REGISTRATION"):
    """Attach a valid referral code to a newly-registered business. Creates a REGISTERED referral with an
    immutable attribution + terms snapshot, then runs the fraud screen. REGISTERED earns nothing. A row
    stores only referrer_ref + referred_ref (single-level; no parent chain)."""
    core.require(actor, P_MANAGE) if core.can(actor, P_MANAGE) else core.require(actor, "marketplace.vehicle.manage")
    rtype = str(referred_type or "").upper()
    if rtype not in REFERRED_TYPES:
        raise core.ValidationError(f"referred_type must be one of {REFERRED_TYPES}")
    cr = conn.execute("SELECT * FROM referral_codes WHERE code=?", (str(code or ""),)).fetchone()
    if not cr or cr["status"] != "ACTIVE" or (cr["expires_at"] and _passed(cr["expires_at"])):
        raise core.ValidationError("referral code is not valid")
    cr = dict(cr)
    # prevent silent reassignment: one live attribution per referred business
    if conn.execute("SELECT 1 FROM referrals WHERE referred_type=? AND referred_ref=? AND status NOT IN "
                    "('REJECTED','CANCELLED','REVERSED')", (rtype, str(referred_ref))).fetchone():
        raise core.ConflictError("this business already has an active referral attribution")
    camp = _campaign(conn, cr["campaign_id"]) if cr["campaign_id"] else None
    terms_v = (camp["terms_version"] if camp else _cfg(conn, "referral.terms_version", "v1"))
    risk_status, risk_reasons = _risk_screen(conn, actor, cr, rtype, referred_ref)
    status = "REVIEW_REQUIRED" if risk_status in _HIGH_RISK else "REGISTERED"
    now = _now()
    cur = conn.execute(
        "INSERT INTO referrals(tenant_id,code,code_id,campaign_id,referrer_type,referrer_ref,referred_type,"
        "referred_ref,referred_label,source,status,risk_status,risk_reasons,terms_version,campaign_version,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (_tid(actor), cr["code"], cr["id"], cr["campaign_id"], cr["referrer_type"], cr["referrer_ref"],
         rtype, str(referred_ref), (str(referred_label)[:200] if referred_label else None), source, status,
         risk_status, json.dumps(risk_reasons), terms_v, (camp["version"] if camp else None), now, now))
    rid = cur.lastrowid
    tenant.stamp(conn, actor, "referrals", rid)
    core.audit(conn, actor, "REFERRAL_ATTRIBUTED", "referrals", rid, None,
               {"code": cr["code"], "referred_type": rtype, "referred_ref": str(referred_ref)})
    core.audit(conn, actor, "REFERRAL_REGISTERED", "referrals", rid, None, {"status": status})
    if risk_reasons:
        core.audit(conn, actor, "REFERRAL_REVIEW_REQUIRED", "referrals", rid, None,
                   {"risk": risk_status, "reasons": risk_reasons})
    conn.commit()
    _notify(conn, "referral.registered", rid)
    return _referral(conn, rid)


# --------------------------------------------------------------------------- #
# qualification (REGISTERED != EARNED — checked against real domains)
# --------------------------------------------------------------------------- #
def _event_satisfied(conn, ref, event):
    """Verify the qualifying commercial event actually occurred in the canonical domains."""
    rt, rref = ref["referred_type"], ref["referred_ref"]
    if event == "MANUAL_STRATEGIC_QUALIFICATION":
        return True
    if rt == "CARRIER":
        try:
            vids = [r["id"] for r in conn.execute("SELECT id FROM mkt_vehicles WHERE carrier_id=?",
                                                  (int(rref),)).fetchall()]
        except Exception:
            vids = []
        if event == "FIRST_ACCREDITED_VEHICLE":
            import accreditation as acc
            return any(acc.fee_paid(conn, v) for v in vids)
        if event in ("FIRST_MARKETPLACE_ELIGIBLE_UNIT", "ENTERPRISE_FLEET_ACTIVATION"):
            import fleet_registration as fr
            sysact = {"id": 0, "role": "system", "perms": {"*"}, "tenant_id": ref.get("tenant_id")}
            elig = [v for v in vids if fr.unit_eligibility(conn, sysact, int(rref), v)["eligible"]]
            need = 1
            if event == "ENTERPRISE_FLEET_ACTIVATION":
                camp = _campaign(conn, ref["campaign_id"]) if ref.get("campaign_id") else None
                need = int((camp or {}).get("fleet_bonus_units") or 10)
            return len(elig) >= need
    if rt == "SHIPPER":
        if event in ("FIRST_COMPLETED_BOOKING", "FIRST_SETTLED_MARKETPLACE_JOB"):
            try:
                done = conn.execute("SELECT COUNT(*) c FROM mkt_bookings WHERE shipper_id=? AND "
                                    "UPPER(COALESCE(status,'')) IN ('DELIVERED','COMPLETED','SETTLED')",
                                    (int(rref),)).fetchone()["c"]
                return done > 0
            except Exception:
                return False
    return False


def mark_verified(conn, actor, referral_id):
    core.require(actor, P_QUALIFY)
    ref = _referral(conn, referral_id); tenant.guard(actor, ref)
    _transition(conn, actor, referral_id, ref["status"], "VERIFIED", "REFERRAL_VERIFIED")
    return _referral(conn, referral_id)


def qualify(conn, actor, referral_id, *, event=None, txn_ref=None, force=False):
    """Confirm the qualifying commercial event and earn the reward (immutable snapshot). REGISTERED alone
    never earns. Fails closed when the program is disabled, the event is unmet, risk is HIGH/CRITICAL, or
    a campaign cap/budget is exhausted."""
    core.require(actor, P_QUALIFY)
    ref = _referral(conn, referral_id); tenant.guard(actor, ref)
    if ref["status"] in ("EARNED", "APPROVED", "PAYABLE", "PAID"):
        return ref
    if ref["risk_status"] in _HIGH_RISK and not force:
        _transition(conn, actor, referral_id, ref["status"], "REVIEW_REQUIRED", "REFERRAL_REVIEW_REQUIRED",
                    {"risk": ref["risk_status"]})
        raise core.ConflictError("referral is HIGH/CRITICAL risk — resolve fraud review before qualifying")
    camp = _campaign(conn, ref["campaign_id"]) if ref.get("campaign_id") else None
    ev = event or (camp or {}).get("qualifying_event") or "MANUAL_STRATEGIC_QUALIFICATION"
    if not force and not _event_satisfied(conn, ref, ev):
        raise core.ConflictError(f"qualifying event {ev} not met — registration alone does not earn a reward")
    now = _now()
    conn.execute("UPDATE referrals SET status='QUALIFIED',qualifying_event=?,qualifying_txn=?,qualified_at=?,"
                 "updated_at=? WHERE id=?", (ev, (str(txn_ref)[:120] if txn_ref else None), now, now, referral_id))
    core.audit(conn, actor, "REFERRAL_QUALIFIED", "referrals", referral_id, None, {"event": ev})
    _notify(conn, "referral.qualified", referral_id)
    return _earn(conn, actor, referral_id, camp)


def _earn(conn, actor, referral_id, camp):
    """Compute + snapshot the reward (immutable). Enforces program flag, caps, and budget; fails closed."""
    ref = _referral(conn, referral_id)
    if not program_enabled(conn):
        # qualified but the program is not activated — earnable value is held until Legal/Finance enable it
        raise core.ConflictError("referral program is not activated (referral.program.enabled=false)")
    reward_type = (camp or {}).get("reward_type") or (_cfg(conn, "referral.reward_type", "FIXED")).upper()
    currency = "PHP"
    basis_amount = None
    amount = 0.0
    if reward_type == "FIXED":
        amount = float((camp or {}).get("reward_amount") if camp else _cfg(conn, "referral.reward_amount", "150") or 0)
    elif reward_type == "PERCENTAGE":
        basis_amount = _reward_basis_amount(conn, ref, (camp or {}).get("reward_basis") or "NET_ACCREDITATION_FEE")
        pct = float((camp or {}).get("reward_pct") or 0)
        amount = round(basis_amount * pct / 100.0, 2)
        cap = (camp or {}).get("max_reward")
        if cap is not None:
            amount = min(amount, float(cap))
    elif reward_type == "CREDIT":
        amount = float((camp or {}).get("reward_amount") if camp else _cfg(conn, "referral.reward_amount", "150") or 0)
    else:  # MANUAL / TIERED -> manual amount if set, else 0 pending admin
        amount = float((camp or {}).get("reward_amount") or 0)

    # caps + budget (§27, §53)
    _enforce_caps(conn, ref, camp, amount)
    now = _now()
    vdays = int((camp or {}).get("validation_days") if camp else _cfg(conn, "referral.validation_days", "14") or 14)
    conn.execute("UPDATE referrals SET status='EARNED',reward_type=?,reward_amount=?,reward_basis_amount=?,"
                 "currency=?,earned_at=?,validation_until=?,updated_at=? WHERE id=?",
                 (reward_type, amount, basis_amount, currency, now, _days_from(now, vdays), now, referral_id))
    if camp:
        conn.execute("UPDATE referral_campaigns SET committed=COALESCE(committed,0)+?, earned=COALESCE(earned,0)+?,"
                     "updated_at=? WHERE id=?", (amount, amount, now, camp["id"]))
    core.audit(conn, actor, "REFERRAL_EARNED", "referrals", referral_id, None,
               {"reward_type": reward_type, "amount": amount, "validation_days": vdays,
                "terms_version": ref["terms_version"]})
    _notify(conn, "referral.earned", referral_id)
    conn.commit()
    return _referral(conn, referral_id)


def _reward_basis_amount(conn, ref, basis):
    """Clean basis only — NEVER VAT / insurance / protected funds / settlement / pass-through (§14)."""
    if ref["referred_type"] == "CARRIER" and basis in ("NET_ACCREDITATION_FEE", "ACCREDITATION_COMPONENT"):
        try:
            import accreditation as acc
            vids = [r["id"] for r in conn.execute("SELECT id FROM mkt_vehicles WHERE carrier_id=?",
                                                  (int(ref["referred_ref"]),)).fetchall()]
            for v in vids:
                a = acc.assessment_for(conn, v)
                if a and a["status"] in ("PAID", "WAIVED") and a.get("subtotal"):
                    return float(a["subtotal"])   # net-of-VAT accreditation subtotal
        except Exception:
            pass
    return 0.0


def _enforce_caps(conn, ref, camp, amount):
    if camp:
        if camp.get("total_budget") is not None:
            remaining = float(camp["total_budget"]) - float(camp.get("committed") or 0)
            if amount > remaining + 1e-6:
                raise core.ConflictError("campaign budget exhausted — reward cannot be earned")
        if camp.get("per_user_cap") is not None:
            n = conn.execute("SELECT COUNT(*) c FROM referrals WHERE campaign_id=? AND referrer_type=? AND "
                             "referrer_ref=? AND status IN ('EARNED','APPROVED','PAYABLE','PAID')",
                             (camp["id"], ref["referrer_type"], ref["referrer_ref"])).fetchone()["c"]
            if n >= int(camp["per_user_cap"]):
                raise core.ConflictError("per-user reward cap reached for this campaign")
        if camp.get("monthly_cap") is not None:
            n = conn.execute("SELECT COUNT(*) c FROM referrals WHERE campaign_id=? AND status IN "
                             "('EARNED','APPROVED','PAYABLE','PAID') AND earned_at>=?",
                             (camp["id"], _days_from(_now(), -30))).fetchone()["c"]
            if n >= int(camp["monthly_cap"]):
                raise core.ConflictError("monthly reward cap reached for this campaign")


# --------------------------------------------------------------------------- #
# finance: approve -> payable -> pay  (SoD; validation cooldown)
# --------------------------------------------------------------------------- #
def approve(conn, actor, referral_id, *, force=False):
    core.require(actor, P_FINANCE)
    ref = _referral(conn, referral_id); tenant.guard(actor, ref)
    if ref["status"] != "EARNED":
        raise core.ConflictError(f"only EARNED referrals can be approved (is {ref['status']})")
    if ref["risk_status"] in _HIGH_RISK:
        raise core.ConflictError("HIGH/CRITICAL risk referral cannot be approved — fraud review required")
    if ref["validation_until"] and not _passed(ref["validation_until"]) and not force:
        raise core.ConflictError("validation period has not elapsed")
    _transition(conn, actor, referral_id, "EARNED", "APPROVED", "REFERRAL_APPROVED", {"by": actor.get("id")})
    conn.execute("UPDATE referrals SET approved_by=?,approved_at=? WHERE id=?", (actor.get("id"), _now(), referral_id))
    _transition(conn, actor, referral_id, "APPROVED", "PAYABLE", "REFERRAL_PAYABLE")
    conn.commit()
    _notify(conn, "referral.payable", referral_id)
    return _referral(conn, referral_id)


def pay(conn, actor, referral_id, *, method="CASH", payout_ref=None):
    core.require(actor, P_FINANCE)
    ref = _referral(conn, referral_id); tenant.guard(actor, ref)
    if ref["status"] != "PAYABLE":
        raise core.ConflictError(f"only PAYABLE referrals can be paid (is {ref['status']})")
    if str(method).upper().endswith("CREDIT"):
        _issue_credit(conn, actor, ref, method.upper())
    conn.execute("UPDATE referrals SET status='PAID',payout_method=?,payout_ref=?,paid_by=?,paid_at=?,"
                 "updated_at=? WHERE id=?", (str(method)[:40], (str(payout_ref)[:120] if payout_ref else None),
                 actor.get("id"), _now(), _now(), referral_id))
    if ref.get("campaign_id"):
        conn.execute("UPDATE referral_campaigns SET paid=COALESCE(paid,0)+? WHERE id=?",
                     (float(ref["reward_amount"] or 0), ref["campaign_id"]))
    core.audit(conn, actor, "REFERRAL_PAID", "referrals", referral_id, None,
               {"method": method, "amount": ref["reward_amount"], "payout_ref": payout_ref})
    conn.commit()
    _notify(conn, "referral.paid", referral_id)
    return _referral(conn, referral_id)


def _issue_credit(conn, actor, ref, kind):
    conn.execute("INSERT INTO referral_credits(tenant_id,referral_id,beneficiary_type,beneficiary_ref,kind,"
                 "amount,currency,status,created_at) VALUES(?,?,?,?,?,?,?, 'ISSUED',?)",
                 (ref.get("tenant_id"), ref["id"], ref["referrer_type"], ref["referrer_ref"], kind,
                  float(ref["reward_amount"] or 0), ref["currency"], _now()))


def reject(conn, actor, referral_id, reason):
    core.require(actor, P_QUALIFY) if core.can(actor, P_QUALIFY) else core.require(actor, P_FRAUD)
    ref = _referral(conn, referral_id); tenant.guard(actor, ref)
    conn.execute("UPDATE referrals SET status='REJECTED',reversed_reason=?,updated_at=? WHERE id=?",
                 (str(reason or "")[:300], _now(), referral_id))
    core.audit(conn, actor, "REFERRAL_REJECTED", "referrals", referral_id, None, {"reason": reason})
    conn.commit()
    return _referral(conn, referral_id)


def reverse(conn, actor, referral_id, reason):
    """Reverse a reward when the underlying transaction is refunded/cancelled/fraudulent. Never deletes the
    original row — records a REVERSED status + reason as audit evidence (§25)."""
    core.require(actor, P_FINANCE)
    ref = _referral(conn, referral_id); tenant.guard(actor, ref)
    if ref["status"] not in ("EARNED", "APPROVED", "PAYABLE", "PAID", "REVIEW_REQUIRED"):
        raise core.ConflictError(f"cannot reverse a {ref['status']} referral")
    conn.execute("UPDATE referrals SET status='REVERSED',reversed_reason=?,updated_at=? WHERE id=?",
                 (str(reason or "")[:300], _now(), referral_id))
    if ref.get("campaign_id"):
        conn.execute("UPDATE referral_campaigns SET committed=MAX(COALESCE(committed,0)-?,0) WHERE id=?",
                     (float(ref["reward_amount"] or 0), ref["campaign_id"]))
    core.audit(conn, actor, "REFERRAL_REVERSED", "referrals", referral_id, None, {"reason": reason})
    conn.commit()
    _notify(conn, "referral.reversed", referral_id)
    return _referral(conn, referral_id)


def flag_review(conn, actor, referral_id, reason=None):
    core.require(actor, P_FRAUD)
    ref = _referral(conn, referral_id); tenant.guard(actor, ref)
    conn.execute("UPDATE referrals SET status='REVIEW_REQUIRED',risk_status='HIGH',updated_at=? WHERE id=?",
                 (_now(), referral_id))
    core.audit(conn, actor, "REFERRAL_REVIEW_REQUIRED", "referrals", referral_id, None, {"reason": reason})
    conn.commit()
    return _referral(conn, referral_id)


# --------------------------------------------------------------------------- #
# helpers + reads
# --------------------------------------------------------------------------- #
def _transition(conn, actor, rid, frm, to, action, extra=None):
    if to not in _TRANSITIONS.get(frm, set()):
        raise core.ConflictError(f"illegal transition {frm} -> {to}")
    conn.execute("UPDATE referrals SET status=?,updated_at=? WHERE id=?", (to, _now(), rid))
    core.audit(conn, actor, action, "referrals", rid, None, extra or {})


def _referral(conn, rid):
    r = conn.execute("SELECT * FROM referrals WHERE id=?", (rid,)).fetchone()
    if not r:
        raise core.NotFoundError("referral not found")
    d = dict(r)
    try:
        d["risk_reasons"] = json.loads(d["risk_reasons"]) if d.get("risk_reasons") else []
    except Exception:
        d["risk_reasons"] = []
    return d


def _notify(conn, event, rid):
    try:
        import notifications_engine as ne
        ref = _referral(conn, rid)
        ne.notify(conn, ref.get("tenant_id"), event.upper().replace(".", "_"),
                  str(ref.get("referrer_ref")), data={"referral_id": rid, "status": ref["status"]})
    except Exception:
        pass


def referrer_dashboard(conn, actor, referrer_type, referrer_ref):
    """A referrer's own view — counts + reward totals only; never the referred company's confidential data."""
    core.require(actor, P_VIEW)
    return _dashboard(conn, str(referrer_type).upper(), str(referrer_ref))


def dashboard_by_code(conn, code):
    """Privacy-safe referrer self-service dashboard authorised by the referral code itself (a bearer
    handle the referrer holds, like a booking tracking token). Lets shippers/customers WITHOUT an account
    see their own referral rewards. Returns only the referrer's own aggregates + privacy-safe labels."""
    cr = conn.execute("SELECT referrer_type,referrer_ref,status FROM referral_codes WHERE code=?",
                      (str(code or ""),)).fetchone()
    if not cr or cr["status"] != "ACTIVE":
        return {"valid": False, "reason": "Referral code not valid"}
    d = _dashboard(conn, cr["referrer_type"], str(cr["referrer_ref"]))
    d["valid"] = True
    return d


def _dashboard(conn, rt, rr):
    codes = [dict(r) for r in conn.execute("SELECT code,status,campaign_id FROM referral_codes WHERE "
             "referrer_type=? AND referrer_ref=? AND status='ACTIVE'", (rt, rr)).fetchall()]
    rows = conn.execute("SELECT status,referred_label,reward_amount,currency FROM referrals WHERE "
                        "referrer_type=? AND referrer_ref=? ORDER BY id DESC", (rt, rr)).fetchall()
    def total(states):
        return round(sum(float(r["reward_amount"] or 0) for r in rows if r["status"] in states), 2)
    qualified = sum(1 for r in rows if r["status"] in ("QUALIFIED", "EARNED", "APPROVED", "PAYABLE", "PAID"))
    return {
        "codes": codes, "primary_code": (codes[0]["code"] if codes else None),
        "share_link": (f"/register?ref={codes[0]['code']}" if codes else None),
        "referred_businesses": len(rows), "qualified": qualified,
        "pending_review": sum(1 for r in rows if r["status"] == "REVIEW_REQUIRED"),
        "total_earned": total(("EARNED", "APPROVED", "PAYABLE", "PAID")),
        "payable": total(("PAYABLE",)), "paid": total(("PAID",)),
        "reversed": total(("REVERSED",)),
        # privacy-safe list — label + status only (§52)
        "referrals": [{"business": r["referred_label"] or "Referred business", "status": r["status"]} for r in rows],
    }


def admin_list(conn, actor, status=None, campaign_id=None):
    core.require(actor, P_MANAGE) if core.can(actor, P_MANAGE) else core.require(actor, P_FINANCE)
    frag, params = tenant.predicate(actor)
    q = "SELECT * FROM referrals WHERE 1=1" + frag
    args = list(params)
    if status:
        q += " AND status=?"; args.append(str(status).upper())
    if campaign_id:
        q += " AND campaign_id=?"; args.append(campaign_id)
    q += " ORDER BY id DESC"
    return {"referrals": [_referral(conn, r["id"]) for r in conn.execute(q, args).fetchall()]}


def leaderboard(conn, actor, limit=10):
    """Top referrers by QUALIFIED count. No genealogy/downline/level is ever exposed (§37)."""
    core.require(actor, P_VIEW)
    frag, params = tenant.predicate(actor)
    rows = conn.execute("SELECT referrer_type,referrer_ref,COUNT(*) qualified FROM referrals WHERE status IN "
                        "('QUALIFIED','EARNED','APPROVED','PAYABLE','PAID')" + frag +
                        " GROUP BY referrer_type,referrer_ref ORDER BY qualified DESC LIMIT ?",
                        list(params) + [int(limit)]).fetchall()
    return {"leaderboard": [dict(r) for r in rows]}
