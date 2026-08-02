"""LiftHaul OS — Governed business-policy evaluators (Phase 2).

Approval, tax, and downpayment policies resolved from the effective-configuration cascade
(tenant/org-aware), returning both the applied result and an immutable policy SNAPSHOT for
historical reproducibility. Defaults equal the pre-Phase-2 constants, so totals are unchanged.

    Config → Effective Resolution → Policy Evaluation → Decision → Snapshot → Audit → History
"""
from __future__ import annotations

import datetime

import admin_platform as ap


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _resolve(conn, key, ctx):
    try:
        return ap.resolve_config_chain(conn, key, [
            ("user", ctx.get("user")), ("team", ctx.get("team")), ("department", ctx.get("department")),
            ("branch", ctx.get("branch")), ("business_unit", ctx.get("business_unit")),
            ("tenant", ctx.get("tenant"))])
    except Exception:
        return {"value": None, "scope": None, "scope_ref": None, "fallback_path": []}  # no rollback (would undo pending work)


def _num(conn, key, ctx, default):
    r = _resolve(conn, key, ctx)
    try:
        return float(r["value"]), r
    except (TypeError, ValueError):
        return float(default), r


def _apply_rounding(value, mode):
    if mode == "floor":
        import math; return math.floor(value)
    if mode == "ceil":
        import math; return math.ceil(value)
    return round(value)


def evaluate_tax(conn, taxable, ctx):
    rate, r = _num(conn, "tax.default.rate", ctx, 12)
    code = (_resolve(conn, "tax.default.code", ctx)["value"] or "VAT")
    mode = (_resolve(conn, "tax.rounding_mode", ctx)["value"] or "round")
    tax = _apply_rounding(taxable * rate / 100, mode)
    snap = {"consumer": "tax", "config_key": "tax.default.rate", "tax_code": code,
            "rate_applied": rate, "taxable_base": taxable, "tax_amount": tax, "rounding_mode": mode,
            "source_scope": r["scope"], "source_ref": r["scope_ref"], "calculated_at": _now(),
            "definition_version": 1}
    return {"rate": rate, "code": code, "tax": tax, "snapshot": snap}


def evaluate_downpayment(conn, total, ctx, requested_rate=None):
    default_rate, r = _num(conn, "payment.downpayment.default_rate", ctx, 30)
    min_rate, _ = _num(conn, "payment.downpayment.minimum_rate", ctx, 0)
    rate = float(requested_rate) if requested_rate is not None else default_rate
    if rate < min_rate:
        rate = min_rate
    required = (_resolve(conn, "payment.downpayment.required", ctx)["value"] or "true").lower() == "true"
    amount = round(total * rate / 100)
    snap = {"consumer": "downpayment", "config_key": "payment.downpayment.default_rate",
            "required": required, "rate_applied": rate, "minimum_rate": min_rate, "amount": amount,
            "total_basis": total, "source_scope": r["scope"], "source_ref": r["scope_ref"],
            "calculated_at": _now(), "definition_version": 1}
    return {"required": required, "rate": rate, "amount": amount, "snapshot": snap}


def evaluate_approval(conn, total, discount_pct, ctx):
    threshold, r = _num(conn, "quotation.approval.threshold_amount", ctx, 500000)
    disc_thr, _ = _num(conn, "quotation.approval.discount_threshold_pct", ctx, 10)
    reasons = []
    if total >= threshold:
        reasons.append(f"total {total} >= threshold {threshold}")
    if (discount_pct or 0) > disc_thr:
        reasons.append(f"discount {discount_pct}% > {disc_thr}%")
    required = bool(reasons)
    snap = {"consumer": "approval", "policy_key": "quotation.approval.threshold_amount",
            "required": required, "threshold_applied": threshold, "discount_threshold": disc_thr,
            "required_approver_role": "approver", "source_scope": r["scope"],
            "source_ref": r["scope_ref"], "reasons": reasons, "evaluated_at": _now(),
            "definition_version": 1}
    return {"required": required, "threshold": threshold, "reasons": reasons,
            "required_approver_role": "approver", "snapshot": snap}


def migrate_legacy_snapshots(conn):
    """Idempotently set a LEGACY_DERIVED policy snapshot on quotations lacking one, computed
    from the row's ALREADY-STORED values. Never writes a financial column (tax/total/dp_amount)."""
    import json
    n = 0
    for row in conn.execute(
            "SELECT id, tax, subtotal, discount, dp_pct FROM quotations WHERE tax_snapshot IS NULL").fetchall():
        taxable = (row["subtotal"] or 0) - (row["discount"] or 0)
        rate = round((row["tax"] or 0) / taxable * 100, 4) if taxable else 0
        tsnap = {"consumer": "tax", "origin": "LEGACY_DERIVED", "rate_applied": rate, "tax_amount": row["tax"]}
        dsnap = {"consumer": "downpayment", "origin": "LEGACY_DERIVED", "rate_applied": row["dp_pct"]}
        conn.execute("UPDATE quotations SET tax_snapshot=?, dp_snapshot=? WHERE id=?",
                     (json.dumps(tsnap), json.dumps(dsnap), row["id"]))
        n += 1
    conn.commit()
    return n


def policy_context(conn, actor, booking_row=None):
    """Derive the tenant/org context for policy resolution from the authenticated actor
    (and booking where relevant). Never trusts client-supplied scope."""
    tenant_id = (actor or {}).get("tenant_id")
    if tenant_id is None and booking_row is not None:
        try:
            tenant_id = booking_row["tenant_id"]
        except (KeyError, IndexError):
            tenant_id = None
    return {"tenant": str(tenant_id) if tenant_id is not None else None}
