"""LiftHaul Enterprise — Cargo Insurance / Goods Protection (orchestration only).

LiftHaul is NOT an insurer. This module orchestrates coverage selection, eligibility, quoting, binding
evidence, premium ledger separation and claims linkage while the actual policy sits with a licensed
insurer/broker. It EXTENDS existing domains — coverage lives on the canonical `mkt_bookings`; claims
reuse the canonical `mkt_claims` (+ `marketplace_trust_closure.open_claim`); the provider abstraction
follows the same pattern as `protected_payment`. No parallel booking/claims/insurance subsystem.

Honesty: no insurer is connected by default, so quotes return MANUAL_INSURANCE_REVIEW_REQUIRED.
High-value / engineered / heavy / excluded cargo -> MANUAL_UNDERWRITING_REQUIRED (never a fake instant
premium). Binding requires real provider/manual approval evidence. Product terms: "Goods Protection".
"""
from __future__ import annotations

import datetime
import json

import core
import tenant

CATEGORIES = ("GENERAL", "MACHINERY", "ELECTRONICS", "PERISHABLE", "FRAGILE", "VEHICLE", "PROJECT_CARGO",
              "DANGEROUS", "PROHIBITED")
GP_COLUMNS = [
    ("declared_value", "REAL"), ("cargo_category", "TEXT"), ("gp_requested", "INTEGER"),
    ("gp_status", "TEXT"), ("gp_coverage_limit", "REAL"), ("gp_premium", "REAL"),
    ("gp_deductible", "REAL"), ("gp_provider", "TEXT"), ("gp_policy_ref", "TEXT"),
    ("gp_effective_from", "TEXT"), ("gp_effective_to", "TEXT"), ("gp_evidence", "TEXT"),
    ("gp_bound_by", "INTEGER"), ("gp_bound_at", "TEXT"),
]
# GP-specific claim lifecycle (driven on the canonical mkt_claims row; LiftHaul never fabricates
# insurer decisions — approval/denial/settlement require a recorded adjuster/insurer reference).
GP_CLAIM_STATES = ("REPORTED", "EVIDENCE_COLLECTION", "SUBMITTED_TO_INSURER", "UNDER_REVIEW",
                   "MORE_INFORMATION_REQUIRED", "APPROVED", "PARTIALLY_APPROVED", "DENIED",
                   "SETTLED", "CLOSED")
_INSURER_DECISION = {"APPROVED", "PARTIALLY_APPROVED", "DENIED", "SETTLED"}

_MANAGE = "marketplace.insurance.manage"
_VIEW = "marketplace.insurance.view"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def init(conn):
    for col, typ in GP_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE mkt_bookings ADD COLUMN {col} {typ}")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass


def seed(conn):
    return 0


def _cfg(conn, key, default=None):
    try:
        import admin_platform as ap
        v, _ = ap.resolve_config(conn, key)
        return v if v is not None else default
    except Exception:
        return default


# --------------------------------------------------------------------------- #
# Provider abstraction (provider-neutral; licensed insurer/broker plugs in here)
# --------------------------------------------------------------------------- #
class GoodsProtectionProvider:
    def declare_capabilities(self):
        raise NotImplementedError

    def quote_coverage(self, declared_value, cargo_category, heavy, inter_island, max_auto):
        raise NotImplementedError

    def bind_coverage(self, quote):
        raise NotImplementedError


class MockGoodsProtectionProvider(GoodsProtectionProvider):
    """Sandbox provider. NOT a licensed insurer — quotes are clearly indicative and it can never bind
    real coverage. Used only when insurance.provider_active is switched on for testing."""
    code = "MOCK_GP"

    def declare_capabilities(self):
        return {"provider": self.code, "regulated_status": "NOT_A_LICENSED_INSURER", "live": False,
                "binds_real_policy": False}

    def quote_coverage(self, declared_value, cargo_category, heavy, inter_island, max_auto):
        if heavy or float(declared_value or 0) > float(max_auto or 0):
            return {"result": "MANUAL_UNDERWRITING_REQUIRED"}
        rate = 0.012 + (0.006 if inter_island else 0.0)   # indicative sandbox rate
        premium = round(float(declared_value) * rate)
        return {"result": "ELIGIBLE", "coverage_limit": float(declared_value),
                "premium": premium, "deductible": round(float(declared_value) * 0.02),
                "provider": self.code, "validity_days": 30,
                "exclusions": "Consequential loss, inherent vice, improper packing, war/nuclear.",
                "sandbox": True}


def active_provider(conn):
    """Returns a provider only when one is configured + activated; else None (honest: no insurer)."""
    if str(_cfg(conn, "insurance.provider_active", "false")).lower() != "true":
        return None
    return MockGoodsProtectionProvider()


# --------------------------------------------------------------------------- #
# Coverage request + eligibility (server-authoritative)
# --------------------------------------------------------------------------- #
def _booking(conn, booking_id):
    b = conn.execute("SELECT id,requested_vehicle_category,service_class,inter_island,declared_value,"
                     "cargo_category,gp_status,gp_coverage_limit,gp_premium,gp_deductible,gp_provider,"
                     "gp_policy_ref FROM mkt_bookings WHERE id=?", (booking_id,)).fetchone()
    if not b:
        raise core.NotFoundError("booking not found")
    return dict(b)


def request_coverage(conn, actor, booking_id, declared_value, cargo_category="GENERAL"):
    core.require(actor, _MANAGE) if core.can(actor, _MANAGE) else core.require(actor, "marketplace.booking.manage")
    cat = (cargo_category or "GENERAL").upper()
    conn.execute("UPDATE mkt_bookings SET declared_value=?, cargo_category=?, gp_requested=1, "
                 "gp_status='REQUESTED', updated_at=? WHERE id=?",
                 (float(declared_value or 0), cat, _now(), booking_id))
    core.audit(conn, actor, "GP_COVERAGE_REQUESTED", "mkt_bookings", booking_id, None,
               {"declared_value": declared_value, "cargo": cat})
    conn.commit()
    return {"booking_id": booking_id, "gp_status": "REQUESTED"}


def _heavy(b):
    return (b.get("service_class") == "ENGINEERED"
            or (b.get("requested_vehicle_category") or "") in ("LOWBED", "CRANE_RIGGING", "WING_VAN_10W"))


def eligibility(conn, booking_id):
    b = _booking(conn, booking_id)
    cat = (b.get("cargo_category") or "GENERAL").upper()
    excluded = [c.strip().upper() for c in str(_cfg(conn, "insurance.excluded_cargo", "PROHIBITED,DANGEROUS")).split(",") if c.strip()]
    dv = float(b.get("declared_value") or 0)
    manual_threshold = float(_cfg(conn, "insurance.manual_underwriting_threshold", "1000000") or 0)
    if cat in excluded:
        return {"result": "NOT_ELIGIBLE", "reason": f"{cat} cargo is excluded"}
    if dv <= 0:
        return {"result": "NOT_ELIGIBLE", "reason": "a positive declared value is required"}
    if active_provider(conn) is None:
        return {"result": "PROVIDER_UNAVAILABLE", "reason": "no licensed insurer connected"}
    if _heavy(b) or dv > manual_threshold:
        return {"result": "MANUAL_UNDERWRITING_REQUIRED",
                "reason": "engineered/heavy or high-value cargo requires underwriting"}
    return {"result": "ELIGIBLE"}


def quote_coverage(conn, actor, booking_id):
    """Never fabricates coverage. Returns a real (sandbox) provider quote only for eligible standard
    risks; otherwise the honest status (MANUAL_UNDERWRITING_REQUIRED / MANUAL_INSURANCE_REVIEW_REQUIRED
    / NOT_ELIGIBLE)."""
    core.require(actor, _MANAGE) if core.can(actor, _MANAGE) else core.require(actor, "marketplace.booking.manage")
    b = _booking(conn, booking_id)
    elig = eligibility(conn, booking_id)
    res = elig["result"]
    if res == "PROVIDER_UNAVAILABLE":
        _set_status(conn, booking_id, "MANUAL_INSURANCE_REVIEW_REQUIRED")
        return {"booking_id": booking_id, "result": "MANUAL_INSURANCE_REVIEW_REQUIRED", "reason": elig.get("reason")}
    if res == "NOT_ELIGIBLE":
        _set_status(conn, booking_id, "NOT_ELIGIBLE")
        return {"booking_id": booking_id, "result": "NOT_ELIGIBLE", "reason": elig.get("reason")}
    if res == "MANUAL_UNDERWRITING_REQUIRED":
        _set_status(conn, booking_id, "MANUAL_UNDERWRITING_REQUIRED")
        _emit(conn, b, "insurance.quote_ready", {"result": "MANUAL_UNDERWRITING_REQUIRED"})
        return {"booking_id": booking_id, "result": "MANUAL_UNDERWRITING_REQUIRED", "reason": elig.get("reason")}
    prov = active_provider(conn)
    q = prov.quote_coverage(b["declared_value"], b.get("cargo_category"), _heavy(b),
                            bool(b.get("inter_island")), float(_cfg(conn, "insurance.max_auto_quote_amount", "500000") or 0))
    if q.get("result") != "ELIGIBLE":
        _set_status(conn, booking_id, "MANUAL_UNDERWRITING_REQUIRED")
        return {"booking_id": booking_id, "result": "MANUAL_UNDERWRITING_REQUIRED"}
    conn.execute("UPDATE mkt_bookings SET gp_status='QUOTED', gp_coverage_limit=?, gp_premium=?, "
                 "gp_deductible=?, gp_provider=?, updated_at=? WHERE id=?",
                 (q["coverage_limit"], q["premium"], q["deductible"], q["provider"], _now(), booking_id))
    core.audit(conn, actor, "GP_COVERAGE_QUOTED", "mkt_bookings", booking_id, None,
               {"premium": q["premium"], "provider": q["provider"]})
    conn.commit()
    _emit(conn, b, "insurance.quote_ready", {"result": "ELIGIBLE", "premium": q["premium"]})
    return {"booking_id": booking_id, "result": "ELIGIBLE", "coverage_limit": q["coverage_limit"],
            "premium": q["premium"], "deductible": q["deductible"], "provider": q["provider"],
            "validity_days": q["validity_days"], "exclusions": q["exclusions"], "sandbox": q.get("sandbox", False)}


def _set_status(conn, booking_id, status):
    conn.execute("UPDATE mkt_bookings SET gp_status=?, updated_at=? WHERE id=?", (status, _now(), booking_id))
    conn.commit()


# --------------------------------------------------------------------------- #
# Binding gate — only with real provider/manual approval evidence
# --------------------------------------------------------------------------- #
def bind(conn, actor, booking_id, insurer, policy_ref, coverage_amount, premium, deductible,
         effective_from, effective_to, evidence):
    core.require(actor, _MANAGE) if core.can(actor, _MANAGE) else core.require(actor, "marketplace.booking.manage")
    if not policy_ref or not evidence:
        raise core.ValidationError("binding requires an insurer policy reference AND approval evidence")
    _booking(conn, booking_id)
    conn.execute(
        "UPDATE mkt_bookings SET gp_status='BOUND', gp_provider=?, gp_policy_ref=?, gp_coverage_limit=?, "
        "gp_premium=?, gp_deductible=?, gp_effective_from=?, gp_effective_to=?, gp_evidence=?, "
        "gp_bound_by=?, gp_bound_at=?, updated_at=? WHERE id=?",
        (insurer, policy_ref, float(coverage_amount or 0), float(premium or 0), float(deductible or 0),
         effective_from, effective_to, json.dumps(evidence) if not isinstance(evidence, str) else evidence,
         actor.get("id"), _now(), _now(), booking_id))
    core.audit(conn, actor, "GP_COVERAGE_BOUND", "mkt_bookings", booking_id, None,
               {"insurer": insurer, "policy_ref": policy_ref, "coverage": coverage_amount})
    conn.commit()
    b = _booking(conn, booking_id)
    _emit(conn, b, "insurance.bound", {"policy_ref": policy_ref, "insurer": insurer})
    return {"booking_id": booking_id, "gp_status": "BOUND", "policy_ref": policy_ref}


# --------------------------------------------------------------------------- #
# Premium ledger separation — premium is a pass-through to the insurer, never platform revenue
# --------------------------------------------------------------------------- #
def breakdown(conn, booking_id, platform_fee=0.0, provider_fee=0.0, carrier_payable=0.0, tax=0.0):
    b = _booking(conn, booking_id)
    premium = float(b.get("gp_premium") or 0)
    contract = float(b.get("gp_coverage_limit") or 0)   # informational
    customer_funding = float(carrier_payable) + float(platform_fee) + float(provider_fee) + premium + float(tax)
    return {
        "customer_funding": round(customer_funding, 2),
        "protected_funds": round(float(carrier_payable), 2),
        "carrier_payable": round(float(carrier_payable), 2),
        "platform_fee": round(float(platform_fee), 2),
        "provider_fee": round(float(provider_fee), 2),
        "insurance_premium": round(premium, 2),          # separate line — flows to insurer
        "insurance_premium_is_platform_revenue": False,  # invariant
        "tax": round(float(tax), 2),
        "declared_coverage_limit": contract,
    }


# --------------------------------------------------------------------------- #
# Claims — reuse the canonical mkt_claims via marketplace_trust_closure.open_claim
# --------------------------------------------------------------------------- #
def link_claim(conn, actor, booking_id, incident_ref, claimed_amount, claim_type="CARGO_DAMAGE",
               claimant="customer", evidence=None):
    core.require(actor, "marketplace.claim.manage")
    b = _booking(conn, booking_id)
    if b.get("gp_status") != "BOUND":
        raise core.ConflictError("cargo is not insured (Goods Protection not BOUND) — cannot open an insured claim")
    import marketplace_trust_closure as tc
    trip = conn.execute("SELECT t.id FROM mkt_trips t JOIN mkt_assignments a ON a.id=t.assignment_id "
                        "WHERE a.booking_id=? ORDER BY t.id DESC LIMIT 1", (booking_id,)).fetchone()
    cid = tc.open_claim(conn, actor, claim_type, claimant,
                        trip_id=(trip["id"] if trip else None), incident_ref=incident_ref,
                        claimed_amount=claimed_amount, insurer=b.get("gp_provider"),
                        policy_reference=b.get("gp_policy_ref"), evidence=evidence)
    conn.execute("UPDATE mkt_claims SET insured_amount=? WHERE id=?", (b.get("gp_coverage_limit"), cid))
    conn.commit()
    _emit(conn, b, "claim.created", {"claim_id": cid, "booking": booking_id})
    return {"claim_id": cid, "status": "REPORTED", "policy_ref": b.get("gp_policy_ref"),
            "insured_amount": b.get("gp_coverage_limit")}


def advance_gp_claim(conn, actor, claim_id, to_status, adjuster_reference=None, approved_amount=None):
    core.require(actor, "marketplace.claim.manage")
    to_status = (to_status or "").upper()
    if to_status not in GP_CLAIM_STATES:
        raise core.ValidationError(f"invalid Goods Protection claim status '{to_status}'")
    if to_status in _INSURER_DECISION and not adjuster_reference:
        raise core.ValidationError(f"{to_status} requires a recorded insurer/adjuster reference — LiftHaul never fabricates insurer decisions")
    row = conn.execute("SELECT id FROM mkt_claims WHERE id=?", (claim_id,)).fetchone()
    if not row:
        raise core.NotFoundError("claim not found")
    conn.execute("UPDATE mkt_claims SET status=?, adjuster_reference=COALESCE(?,adjuster_reference), "
                 "approved_amount=COALESCE(?,approved_amount), updated_at=? WHERE id=?",
                 (to_status, adjuster_reference, approved_amount, _now(), claim_id))
    core.audit(conn, actor, "GP_CLAIM_ADVANCED", "mkt_claims", claim_id, None,
               {"status": to_status, "adjuster": adjuster_reference})
    conn.commit()
    ev = {"APPROVED": "claim.approved", "PARTIALLY_APPROVED": "claim.approved", "DENIED": "claim.denied",
          "SETTLED": "claim.settled", "SUBMITTED_TO_INSURER": "claim.submitted"}.get(to_status)
    if ev:
        _emit(conn, {"tenant_id": None}, ev, {"claim_id": claim_id, "status": to_status})
    return {"claim_id": claim_id, "status": to_status}


# --------------------------------------------------------------------------- #
# Admin views
# --------------------------------------------------------------------------- #
def coverage_requests(conn, actor):
    core.require(actor, _VIEW) if core.can(actor, _VIEW) else core.require(actor, "marketplace.booking.view")
    frag, params = tenant.predicate(actor)
    rows = conn.execute("SELECT id,tracking_token,declared_value,cargo_category,gp_status,gp_premium,"
                        "gp_coverage_limit,gp_provider,gp_policy_ref FROM mkt_bookings WHERE gp_requested=1"
                        + frag + " ORDER BY id DESC", params).fetchall()
    out = [dict(r) for r in rows]
    return {"requests": out,
            "manual_underwriting_queue": [r for r in out if r["gp_status"] in ("MANUAL_UNDERWRITING_REQUIRED", "MANUAL_INSURANCE_REVIEW_REQUIRED")],
            "policies": [r for r in out if r["gp_status"] == "BOUND"],
            "provider_active": str(_cfg(conn, "insurance.provider_active", "false")).lower() == "true"}


def get_coverage(conn, booking_id):
    b = _booking(conn, booking_id)
    return {"booking_id": booking_id, "gp_status": b.get("gp_status"), "provider": b.get("gp_provider"),
            "policy_ref": b.get("gp_policy_ref"), "coverage_limit": b.get("gp_coverage_limit"),
            "premium": b.get("gp_premium"), "deductible": b.get("gp_deductible")}


def _emit(conn, booking, event_type, data):
    try:
        import api_platform as ap
        ap.emit_event(conn, (booking.get("tenant_id") if isinstance(booking, dict) else None), event_type, data)
    except Exception:
        pass
