"""Samantha — LiftHaul's AI Business Development Manager (native module).

Samantha runs governed business development for the LiftHaul marketplace across BOTH sides:
  * DEMAND — enterprise shippers / cargo owners who need heavy-haul & logistics done;
  * SUPPLY — carriers / fleet / crane-rigging operators to onboard as providers.

Pipeline (per prospect): ADD -> QUALIFY (deterministic score + coded reasons) -> DRAFT outreach
(sector/side playbook) -> human APPROVE (maker/checker: never approve your own draft) -> SEND.

Governance held, matching the rest of LiftHaul:
  * Sends are HUMAN-APPROVED, never autonomous. A draft is inert until a *different* user approves it.
  * Delivery is HONEST: with no messaging provider connected the copy is marked "ready for manual send",
    never faked as delivered. `SENT` is only set when a provider actually accepts.
  * Everything is tenant-scoped, RBAC-governed and audited.

Reuse-only: builds on core (users/audit/RBAC), tenant, and the existing notifications provider seam.
No cross-repo runtime dependency on TrenovaTech — this is Samantha's operating model brought natively
into LiftHaul.
"""
from __future__ import annotations

import json
import core
import tenant

SIDES = ("DEMAND", "SUPPLY")
PROSPECT_STATUSES = ("NEW", "QUALIFIED", "CONTACTED", "REPLIED", "CONVERTED", "DISQUALIFIED")
OUTREACH_STATUSES = ("PENDING_APPROVAL", "APPROVED", "SENT", "REJECTED")

# Title -> authority weight (ported from Samantha's BDM heuristic; higher = stronger buying authority).
_TITLE_AUTHORITY = {
    "owner": 30, "founder": 30, "president": 30, "ceo": 30, "chief executive": 30,
    "coo": 26, "cfo": 24, "chief": 24, "vp": 22, "vice president": 22, "avp": 18,
    "director": 18, "head": 16, "general manager": 16, "gm": 16, "manager": 10,
    "supervisor": 6, "officer": 5, "coordinator": 4,
}
_URGENCY = ("urgent", "immediately", "asap", "this quarter", "q1", "q2", "q3", "q4",
            "deadline", "before year-end", "expansion", "new plant", "new route", "tender", "bid")

# Sector fit for heavy-haul / logistics relevance (0-30).
_DEMAND_SECTORS = {
    "mining": 30, "construction": 28, "infrastructure": 28, "energy": 26, "power": 26,
    "manufacturing": 24, "cement": 26, "steel": 26, "ports": 24, "oil_gas": 26,
    "agriculture": 18, "retail": 12, "fmcg": 14, "telco": 16, "real_estate": 20, "other": 8,
}
_SUPPLY_SECTORS = {
    "trucking": 30, "hauling": 30, "crane": 28, "rigging": 28, "heavy_equipment": 28,
    "logistics": 26, "freight_forwarding": 22, "equipment_rental": 24, "other": 8,
}

# Side + sector playbooks: the angle Samantha leads with and the opening template.
PLAYBOOKS = {
    "DEMAND": {
        "_default": {
            "angle": "Reliable, compliant heavy-haul capacity on demand — one network, verified carriers.",
            "opening": ("Hi {contact}, I'm reaching out from LiftHaul. We give {sector} shippers like "
                        "{company} access to a verified network of heavy-haul and lifting operators — "
                        "compliant, tracked, and with protected payments. Would a short call on your "
                        "current lane and equipment needs be useful?")},
        "mining": {"angle": "Move oversized ore-processing and plant equipment with verified heavy-haul + crane capacity.",
                   "opening": ("Hi {contact}, moving oversized equipment and plant modules is where LiftHaul's "
                               "verified heavy-haul and crane network fits {company}. Compliant operators, live "
                               "tracking, protected payments. Open to a quick conversation on your next haul?")},
        "construction": {"angle": "Project-based lowbed, boom-truck and crane capacity matched to your build schedule.",
                         "opening": ("Hi {contact}, for {company}'s projects LiftHaul matches lowbed, boom-truck and "
                                     "crane capacity to your build schedule — verified operators, no idle fleet to own. "
                                     "Worth a short call?")},
        "energy": {"angle": "Specialized transport for transformers, turbines and oversized energy cargo.",
                   "opening": ("Hi {contact}, LiftHaul moves transformers, turbines and oversized energy cargo for "
                               "operators like {company} with verified specialized-transport capacity. Can I share how?")},
    },
    "SUPPLY": {
        "_default": {
            "angle": "Steady, lane-matched work for your fleet — you set availability, we bring the jobs.",
            "opening": ("Hi {contact}, LiftHaul brings lane-matched hauling work to fleets like {company}. You "
                        "register once, set your availability, and receive jobs that fit your units — with "
                        "protected payments. Would you be open to onboarding as a verified carrier?")},
        "trucking": {"angle": "Fill empty backhauls and idle units with matched LiftHaul jobs.",
                     "opening": ("Hi {contact}, empty backhauls and idle units cost {company} money. LiftHaul "
                                 "matches your fleet to paying lane work with protected payments. Can I walk you "
                                 "through onboarding as a verified carrier?")},
        "crane": {"angle": "Get matched to lifting and rigging jobs that fit your equipment class.",
                  "opening": ("Hi {contact}, LiftHaul matches crane and rigging operators like {company} to lifting "
                              "jobs that fit your equipment class — verified, tracked, paid on completion. Interested "
                              "in onboarding your units?")},
    },
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS bd_prospects(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, side TEXT NOT NULL, company TEXT NOT NULL,
  contact_name TEXT, contact_title TEXT, contact_email TEXT, contact_mobile TEXT,
  sector TEXT, region TEXT, profile TEXT, source TEXT,
  score INTEGER DEFAULT 0, tier TEXT, status TEXT NOT NULL DEFAULT 'NEW',
  qualify_reasons TEXT, notes TEXT, created_by INTEGER, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS bd_outreach(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, prospect_id INTEGER NOT NULL, channel TEXT,
  angle TEXT, subject TEXT, body TEXT, status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL',
  drafted_by INTEGER, approved_by INTEGER, delivered INTEGER DEFAULT 0, send_note TEXT,
  sent_at TEXT, created_at TEXT, updated_at TEXT);
"""

# RBAC
P_VIEW, P_MANAGE, P_DRAFT, P_APPROVE, P_SEND = (
    "bd.prospect.view", "bd.prospect.manage", "bd.outreach.draft", "bd.outreach.approve", "bd.outreach.send")


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    return  # prospects are created by BD staff / imports


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _norm_side(side):
    s = str(side or "").strip().upper()
    if s not in SIDES:
        raise core.ValidationError(f"side must be one of {SIDES}")
    return s


def _sector_key(sector):
    return str(sector or "other").strip().lower().replace(" ", "_").replace("&", "").replace("/", "_")


def _row(conn, table, rid):
    return conn.execute(f"SELECT * FROM {table} WHERE id=?", (rid,)).fetchone()


def _playbook(side, sector):
    book = PLAYBOOKS[side]
    return book.get(_sector_key(sector), book["_default"])


# --------------------------------------------------------------------------- #
# prospects
# --------------------------------------------------------------------------- #
def add_prospect(conn, actor, payload):
    """Add a BD prospect (DEMAND enterprise shipper, or SUPPLY carrier/fleet)."""
    core.require(actor, P_MANAGE)
    if not isinstance(payload, dict):
        raise core.ValidationError("invalid payload")
    side = _norm_side(payload.get("side"))
    company = str(payload.get("company", "")).strip()
    if not company:
        raise core.ValidationError("company is required")
    now = core.now()
    cur = conn.execute(
        "INSERT INTO bd_prospects(side,company,contact_name,contact_title,contact_email,contact_mobile,"
        "sector,region,profile,source,status,notes,created_by,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?, 'NEW', ?,?,?,?)",
        (side, company[:200], str(payload.get("contact_name", ""))[:160] or None,
         str(payload.get("contact_title", ""))[:120] or None,
         str(payload.get("contact_email", ""))[:200] or None,
         str(payload.get("contact_mobile", ""))[:40] or None,
         str(payload.get("sector", ""))[:60] or None,
         str(payload.get("region", ""))[:60] or None,
         str(payload.get("profile", ""))[:600] or None,
         str(payload.get("source", "manual"))[:60],
         str(payload.get("notes", ""))[:600] or None, actor["id"], now, now))
    pid = cur.lastrowid
    tenant.stamp(conn, actor, "bd_prospects", pid)
    core.audit(conn, actor, "BD_PROSPECT_ADDED", "bd_prospects", pid, None,
               {"side": side, "company": company})
    conn.commit()
    return {"id": pid, "side": side, "company": company, "status": "NEW"}


def list_prospects(conn, actor, side=None, status=None):
    core.require(actor, P_VIEW)
    frag, params = tenant.predicate(actor)
    q = "SELECT * FROM bd_prospects WHERE 1=1" + frag
    args = list(params)
    if side:
        q += " AND side=?"; args.append(_norm_side(side))
    if status:
        q += " AND status=?"; args.append(str(status).upper())
    q += " ORDER BY score DESC, id DESC"
    return [dict(r) for r in conn.execute(q, args).fetchall()]


def qualify(conn, actor, prospect_id):
    """Deterministic qualification: score 0-100 from authority + sector fit + region + signal + urgency.
    Sets tier (HOT/WARM/COOL) and status QUALIFIED, with coded reasons (no LLM, fully explainable)."""
    core.require(actor, P_MANAGE)
    p = _row(conn, "bd_prospects", prospect_id)
    if not p:
        raise core.NotFoundError("prospect not found")
    tenant.guard(actor, p)
    side = p["side"]
    reasons = []
    score = 0

    # authority (title)
    title = (p["contact_title"] or "").lower()
    auth = max([w for k, w in _TITLE_AUTHORITY.items() if k in title] or [0])
    if auth:
        score += auth; reasons.append(f"authority:+{auth} ({title.strip()[:40] or 'title'})")
    else:
        reasons.append("authority:+0 (no decision-maker title)")

    # sector fit
    table = _DEMAND_SECTORS if side == "DEMAND" else _SUPPLY_SECTORS
    sfit = table.get(_sector_key(p["sector"]), table["other"])
    score += sfit; reasons.append(f"sector_fit:+{sfit} ({p['sector'] or 'unspecified'})")

    # region presence
    if (p["region"] or "").strip():
        score += 10; reasons.append("region:+10 (serviceable area named)")

    # profile signal (heavy/oversized cargo for demand; fleet size/units for supply)
    prof = (p["profile"] or "").lower()
    signal_kw = ("oversized", "heavy", "lowbed", "modular", "transformer", "plant", "project") \
        if side == "DEMAND" else ("fleet", "units", "trucks", "cranes", "wing van", "prime mover", "cpc")
    if any(k in prof for k in signal_kw):
        score += 15; reasons.append("profile_signal:+15 (relevant cargo/fleet signal)")

    # urgency
    blob = f"{prof} {(p['notes'] or '').lower()}"
    if any(k in blob for k in _URGENCY):
        score += 15; reasons.append("urgency:+15 (time-bound signal)")

    score = min(score, 100)
    tier = "HOT" if score >= 70 else "WARM" if score >= 45 else "COOL"
    conn.execute("UPDATE bd_prospects SET score=?,tier=?,status='QUALIFIED',qualify_reasons=?,updated_at=? "
                 "WHERE id=?", (score, tier, json.dumps(reasons), core.now(), prospect_id))
    core.audit(conn, actor, "BD_PROSPECT_QUALIFIED", "bd_prospects", prospect_id, None,
               {"score": score, "tier": tier})
    conn.commit()
    return {"id": prospect_id, "score": score, "tier": tier, "status": "QUALIFIED", "reasons": reasons}


# --------------------------------------------------------------------------- #
# outreach — draft -> approve (SoD) -> send (honest)
# --------------------------------------------------------------------------- #
def draft_outreach(conn, actor, prospect_id, channel="email"):
    """Draft a tailored outreach message from the side+sector playbook. Lands PENDING_APPROVAL —
    NEVER sent. A human (a different user) must approve before it can go out."""
    core.require(actor, P_DRAFT)
    p = _row(conn, "bd_prospects", prospect_id)
    if not p:
        raise core.NotFoundError("prospect not found")
    tenant.guard(actor, p)
    pb = _playbook(p["side"], p["sector"])
    contact = (p["contact_name"] or "there").split(" ")[0]
    fields = {"contact": contact, "company": p["company"], "sector": (p["sector"] or "your").strip()}
    body = pb["opening"].format(**fields)
    subject = (f"LiftHaul × {p['company']} — verified heavy-haul capacity" if p["side"] == "DEMAND"
               else f"LiftHaul × {p['company']} — lane-matched work for your fleet")
    now = core.now()
    cur = conn.execute(
        "INSERT INTO bd_outreach(prospect_id,channel,angle,subject,body,status,drafted_by,created_at,updated_at) "
        "VALUES(?,?,?,?,?, 'PENDING_APPROVAL', ?,?,?)",
        (prospect_id, channel[:20], pb["angle"], subject, body, actor["id"], now, now))
    oid = cur.lastrowid
    tenant.stamp(conn, actor, "bd_outreach", oid)
    core.audit(conn, actor, "BD_OUTREACH_DRAFTED", "bd_outreach", oid, None,
               {"prospect_id": prospect_id, "channel": channel})
    conn.commit()
    return {"id": oid, "prospect_id": prospect_id, "status": "PENDING_APPROVAL",
            "channel": channel, "angle": pb["angle"], "subject": subject, "body": body,
            "note": "Draft only — a different user must approve before it can be sent."}


def approve_outreach(conn, actor, outreach_id):
    """Human approval (maker/checker): the approver must NOT be the drafter (separation of duties)."""
    core.require(actor, P_APPROVE)
    o = _row(conn, "bd_outreach", outreach_id)
    if not o:
        raise core.NotFoundError("outreach not found")
    tenant.guard(actor, o)
    if o["status"] != "PENDING_APPROVAL":
        raise core.ConflictError(f"outreach is {o['status']}, not PENDING_APPROVAL")
    if o["drafted_by"] == actor["id"]:
        raise core.ForbiddenError("separation of duties: you cannot approve your own draft")
    conn.execute("UPDATE bd_outreach SET status='APPROVED',approved_by=?,updated_at=? WHERE id=?",
                 (actor["id"], core.now(), outreach_id))
    core.audit(conn, actor, "BD_OUTREACH_APPROVED", "bd_outreach", outreach_id, None, {"by": actor["id"]})
    conn.commit()
    return {"id": outreach_id, "status": "APPROVED"}


def reject_outreach(conn, actor, outreach_id, reason=None):
    core.require(actor, P_APPROVE)
    o = _row(conn, "bd_outreach", outreach_id)
    if not o:
        raise core.NotFoundError("outreach not found")
    tenant.guard(actor, o)
    conn.execute("UPDATE bd_outreach SET status='REJECTED',send_note=?,updated_at=? WHERE id=?",
                 (str(reason or "")[:300], core.now(), outreach_id))
    core.audit(conn, actor, "BD_OUTREACH_REJECTED", "bd_outreach", outreach_id, None, {"reason": reason})
    conn.commit()
    return {"id": outreach_id, "status": "REJECTED"}


def send_outreach(conn, actor, outreach_id):
    """Send an APPROVED outreach. HONEST: only marks SENT if a messaging provider actually accepts it.
    With no provider connected the approved copy is returned 'ready for manual send' — never faked."""
    core.require(actor, P_SEND)
    o = _row(conn, "bd_outreach", outreach_id)
    if not o:
        raise core.NotFoundError("outreach not found")
    tenant.guard(actor, o)
    if o["status"] != "APPROVED":
        raise core.ConflictError(f"outreach is {o['status']} — only APPROVED outreach can be sent")
    p = _row(conn, "bd_prospects", o["prospect_id"])
    channel = o["channel"] or "email"
    recipient = (p["contact_email"] if channel == "email" else p["contact_mobile"]) if p else None

    delivered, note = _deliver(conn, actor, channel, recipient, o)
    now = core.now()
    if delivered:
        conn.execute("UPDATE bd_outreach SET status='SENT',delivered=1,send_note=?,sent_at=?,updated_at=? "
                     "WHERE id=?", (note, now, now, outreach_id))
        conn.execute("UPDATE bd_prospects SET status='CONTACTED',updated_at=? WHERE id=?",
                     (now, o["prospect_id"]))
        core.audit(conn, actor, "BD_OUTREACH_SENT", "bd_outreach", outreach_id, None,
                   {"prospect_id": o["prospect_id"], "channel": channel})
    else:
        conn.execute("UPDATE bd_outreach SET send_note=?,updated_at=? WHERE id=?", (note, now, outreach_id))
        core.audit(conn, actor, "BD_OUTREACH_SEND_UNAVAILABLE", "bd_outreach", outreach_id, None,
                   {"reason": note})
    conn.commit()
    return {"id": outreach_id, "status": "SENT" if delivered else "APPROVED",
            "delivered": delivered, "note": note,
            "ready_to_send_copy": None if delivered else {"subject": o["subject"], "body": o["body"],
                                                          "recipient": recipient}}


def _deliver(conn, actor, channel, recipient, outreach):
    """Attempt real delivery via the notification provider seam (default OFF). Honest-failure semantics."""
    import notifications_engine as ne
    try:
        active = bool(ne.provider_active(conn, channel))
    except Exception:
        active = False
    if not active:
        return False, ("No messaging provider connected — approved copy is ready for manual human send. "
                       "Connect a provider to enable governed automated sends.")
    if not recipient:
        return False, "No recipient contact on the prospect for this channel."
    try:
        ne.notify(conn, actor.get("tenant_id"), "BD_OUTREACH", recipient,
                  data={"subject": outreach["subject"], "body": outreach["body"]})
        return True, f"Sent via connected {channel} provider."
    except Exception as e:  # honest: surface the failure, do not mark SENT
        return False, f"Provider send failed: {type(e).__name__}"


# --------------------------------------------------------------------------- #
# read models for the console
# --------------------------------------------------------------------------- #
def playbooks(actor=None):
    """The side/sector playbook catalog (angles + opening templates)."""
    out = {}
    for side, book in PLAYBOOKS.items():
        out[side] = [{"sector": k if k != "_default" else "(default)", "angle": v["angle"]}
                     for k, v in book.items()]
    return {"sides": list(SIDES), "playbooks": out,
            "demand_sectors": sorted(_DEMAND_SECTORS), "supply_sectors": sorted(_SUPPLY_SECTORS)}


def pipeline_summary(conn, actor):
    """Counts by side + status for the BD console board."""
    core.require(actor, P_VIEW)
    frag, params = tenant.predicate(actor)
    rows = conn.execute("SELECT side,status,COUNT(*) c FROM bd_prospects WHERE 1=1" + frag +
                        " GROUP BY side,status", list(params)).fetchall()
    board = {s: {st: 0 for st in PROSPECT_STATUSES} for s in SIDES}
    total = 0
    for r in rows:
        if r["side"] in board:
            board[r["side"]][r["status"]] = r["c"]; total += r["c"]
    oc = conn.execute("SELECT status,COUNT(*) c FROM bd_outreach WHERE 1=1" + frag +
                      " GROUP BY status", list(params)).fetchall()
    outreach = {st: 0 for st in OUTREACH_STATUSES}
    for r in oc:
        outreach[r["status"]] = r["c"]
    return {"total_prospects": total, "by_side": board, "outreach": outreach}
