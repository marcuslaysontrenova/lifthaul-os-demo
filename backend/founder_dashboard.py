"""Founder-only financial and operating command-center read model.

The dashboard deliberately separates booking volume, recognized LiftHaul revenue,
provider/customer liabilities and cash-like records. It never treats protected
funds or refundable provider deposits as revenue. Unsupported accounting lines are
returned as ``None`` with an explicit data-quality blocker instead of fabricated
numbers.
"""
from __future__ import annotations

import csv
import datetime
import io

import admin_platform
import core


FOUNDER_ROLE = "founder"
FOUNDER_PERMISSIONS = {"founder.dashboard.view", "founder.dashboard.export"}
core.PERMISSIONS.setdefault(FOUNDER_ROLE, set()).update(FOUNDER_PERMISSIONS)

PERIODS = ("daily", "weekly", "monthly", "quarterly", "annual")


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(value):
    return value.isoformat(timespec="seconds")


def period_bounds(period="monthly", anchor=None):
    period = str(period or "monthly").lower()
    if period not in PERIODS:
        raise core.ValidationError("period must be daily, weekly, monthly, quarterly or annual")
    anchor = anchor or _now()
    if isinstance(anchor, str):
        try:
            anchor = datetime.datetime.fromisoformat(anchor.replace("Z", "+00:00"))
        except ValueError as exc:
            raise core.ValidationError("anchor must be an ISO-8601 timestamp") from exc
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=datetime.timezone.utc)
    anchor = anchor.astimezone(datetime.timezone.utc)
    day = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "daily":
        start, end = day, day + datetime.timedelta(days=1)
    elif period == "weekly":
        start = day - datetime.timedelta(days=day.weekday())
        end = start + datetime.timedelta(days=7)
    elif period == "monthly":
        start = day.replace(day=1)
        end = (start.replace(year=start.year + 1, month=1)
               if start.month == 12 else start.replace(month=start.month + 1))
    elif period == "quarterly":
        month = ((day.month - 1) // 3) * 3 + 1
        start = day.replace(month=month, day=1)
        end = (start.replace(year=start.year + 1, month=1)
               if month == 10 else start.replace(month=month + 3))
    else:
        start = day.replace(month=1, day=1)
        end = start.replace(year=start.year + 1)
    return period, _iso(start), _iso(end)


def _founder_assigned(conn, actor):
    if not actor:
        return False
    if actor.get("role") == FOUNDER_ROLE:
        return True
    return bool(conn.execute(
        "SELECT 1 FROM admin_user_roles ur JOIN admin_roles r ON r.id=ur.role_id "
        "WHERE ur.user_id=? AND r.code=? LIMIT 1",
        (actor.get("id"), FOUNDER_ROLE),
    ).fetchone())


def require_founder(conn, actor, permission="founder.dashboard.view"):
    """Enforce an exclusive Founder identity; wildcard admins are not accepted."""
    if not _founder_assigned(conn, actor):
        raise core.ForbiddenError("Founder command-center access is restricted to the Founder role")
    if not admin_platform.mfa_enrolled(conn, actor["id"]):
        raise core.ForbiddenError("Founder command-center access requires confirmed MFA")
    if permission not in FOUNDER_PERMISSIONS:
        raise core.ForbiddenError("unsupported Founder permission")


def _sum(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    value = row[0] if row else 0
    return round(float(value or 0), 2)


def _count(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return int((row[0] if row else 0) or 0)


def financial_summary(conn, actor, period="monthly", anchor=None):
    require_founder(conn, actor)
    period, start, end = period_bounds(period, anchor)
    window = (start, end)

    gbv = _sum(
        conn,
        "SELECT COALESCE(SUM(gross_value),0) FROM mkt_payment_requirements "
        "WHERE status NOT IN ('DRAFT','CANCELLED','FAILED') AND created_at>=? AND created_at<?",
        window,
    )
    transaction_revenue = _sum(
        conn,
        "SELECT COALESCE(SUM(fee_amount-refunded_amount),0) FROM platform_fee_settlements "
        "WHERE status='COMPLETED' AND completed_at>=? AND completed_at<?",
        window,
    )
    membership_revenue = _sum(
        conn,
        "SELECT COALESCE(SUM(subtotal),0) FROM billing_evidence "
        "WHERE LOWER(payment_status)='paid' AND generated_at>=? AND generated_at<?",
        window,
    )
    recognized_revenue = round(transaction_revenue + membership_revenue, 2)

    payment_processing_cost = _sum(
        conn,
        "SELECT COALESCE(SUM(payment_fee),0) FROM mkt_payment_requirements "
        "WHERE status IN ('RELEASE_PENDING','PARTIALLY_RELEASED','RELEASED','CLOSED') "
        "AND created_at>=? AND created_at<?",
        window,
    )
    approved_operating_expenses = _sum(
        conn,
        "SELECT COALESCE(SUM(amount),0) FROM expenses "
        "WHERE status='APPROVED' AND created_at>=? AND created_at<?",
        window,
    )
    gross_profit = round(recognized_revenue - payment_processing_cost, 2)
    operating_profit = round(gross_profit - approved_operating_expenses, 2)

    protected_funds_liability = _sum(
        conn,
        "SELECT COALESCE(SUM(CASE WHEN funded_amount-released_amount-refunded_amount>0 "
        "THEN funded_amount-released_amount-refunded_amount ELSE 0 END),0) "
        "FROM mkt_payment_requirements",
    )
    provider_earnings_liability = _sum(
        conn,
        "SELECT COALESCE(SUM(CASE WHEN net_amount-paid_amount>0 THEN net_amount-paid_amount ELSE 0 END),0) "
        "FROM mkt_provider_earnings WHERE status NOT IN ('CANCELLED','REVERSED')",
    )
    deposit_liability = _sum(
        conn,
        "SELECT COALESCE(SUM(CASE WHEN funded_amount-applied_amount-refunded_amount>0 "
        "THEN funded_amount-applied_amount-refunded_amount ELSE 0 END),0) "
        "FROM mkt_provider_security_deposits",
    )
    accounts_receivable = _sum(
        conn,
        "SELECT COALESCE(SUM(CASE WHEN kind='CHARGE' THEN total ELSE -total END),0) "
        "FROM billing_items",
    )
    refunds = _sum(
        conn,
        "SELECT COALESCE(SUM(amount),0) FROM mkt_refunds "
        "WHERE status IN ('COMPLETED','PARTIALLY_COMPLETED') AND created_at>=? AND created_at<?",
        window,
    ) + _sum(
        conn,
        "SELECT COALESCE(SUM(refundable),0) FROM refunds "
        "WHERE status='PAID' AND created_at>=? AND created_at<?",
        window,
    )
    chargebacks = _sum(
        conn,
        "SELECT COALESCE(SUM(amount),0) FROM mkt_funding_events "
        "WHERE reconciliation_status='CHARGEBACK' AND created_at>=? AND created_at<?",
        window,
    )
    paid_provider_settlements = _sum(
        conn,
        "SELECT COALESCE(SUM(net_payout),0) FROM mkt_payouts "
        "WHERE status='PAID' AND payout_date>=? AND payout_date<?",
        window,
    )
    active_bookings = _count(
        conn,
        "SELECT COUNT(*) FROM mkt_bookings WHERE status NOT IN ('DRAFT','CANCELLED','EXPIRED','REJECTED') "
        "AND created_at>=? AND created_at<?",
        window,
    )
    payment_failures = _count(
        conn,
        "SELECT COUNT(*) FROM gateway_payment_transactions WHERE status='FAILED' "
        "AND created_at>=? AND created_at<?",
        window,
    )

    protected_in = _sum(conn, "SELECT COALESCE(SUM(funded_amount),0) FROM mkt_payment_requirements")
    protected_out = _sum(conn, "SELECT COALESCE(SUM(released_amount+refunded_amount),0) FROM mkt_payment_requirements")
    reconciliation_difference = round(protected_in - protected_out - protected_funds_liability, 2)

    by_vehicle = [dict(row) for row in conn.execute(
        "SELECT COALESCE(NULLIF(b.requested_vehicle_category,''),'UNCLASSIFIED') category, "
        "ROUND(COALESCE(SUM(s.fee_amount-s.refunded_amount),0),2) revenue "
        "FROM platform_fee_settlements s LEFT JOIN mkt_bookings b ON b.id=s.booking_id "
        "WHERE s.status='COMPLETED' AND s.completed_at>=? AND s.completed_at<? "
        "GROUP BY COALESCE(NULLIF(b.requested_vehicle_category,''),'UNCLASSIFIED') ORDER BY revenue DESC",
        window,
    ).fetchall()]
    by_region = [dict(row) for row in conn.execute(
        "SELECT COALESCE(NULLIF(b.origin_zone,''),'UNCLASSIFIED') region, "
        "ROUND(COALESCE(SUM(s.fee_amount-s.refunded_amount),0),2) revenue "
        "FROM platform_fee_settlements s LEFT JOIN mkt_bookings b ON b.id=s.booking_id "
        "WHERE s.status='COMPLETED' AND s.completed_at>=? AND s.completed_at<? "
        "GROUP BY COALESCE(NULLIF(b.origin_zone,''),'UNCLASSIFIED') ORDER BY revenue DESC",
        window,
    ).fetchall()]

    quality = [
        {"code": "FOUNDER_EXCLUSIVE_MFA", "status": "PASS",
         "message": "Founder role and confirmed MFA were enforced for this response."},
        {"code": "GBV_NOT_REVENUE", "status": "PASS",
         "message": "Gross booking value is reported separately from recognized platform revenue."},
        {"code": "PROTECTED_FUNDS_NOT_REVENUE", "status": "PASS",
         "message": "Protected customer funds and refundable deposits are classified as liabilities."},
        {"code": "ENTERPRISE_REVENUE_MAPPING", "status": "BLOCKED",
         "message": "Enterprise-contract and other-revenue recognition mappings require Finance approval."},
        {"code": "EBITDA_NET_PROFIT", "status": "BLOCKED",
         "message": "Depreciation, amortization, interest and income-tax ledgers are not yet governed; EBITDA and net profit are not fabricated."},
        {"code": "CUSTOMER_SEGMENT", "status": "BLOCKED",
         "message": "A governed customer-segment dimension is not yet attached to recognized revenue."},
    ]
    result = {
        "as_of": _iso(_now()),
        "period": {"grain": period, "start": start, "end": end},
        "currency": "PHP",
        "financials": {
            "gross_booking_value": gbv,
            "recognized_platform_revenue": recognized_revenue,
            "membership_subscription_revenue": membership_revenue,
            "transaction_fee_revenue": transaction_revenue,
            "enterprise_contract_revenue": None,
            "other_approved_revenue": None,
            "cost_of_revenue": payment_processing_cost,
            "gross_profit": gross_profit,
            "operating_expenses": approved_operating_expenses,
            "operating_profit": operating_profit,
            "ebitda": None,
            "net_profit": None,
            "refunds": round(refunds, 2),
            "chargebacks": chargebacks,
            "paid_provider_settlements": paid_provider_settlements,
        },
        "balances": {
            "accounts_receivable": accounts_receivable,
            "accounts_payable_provider_earnings": provider_earnings_liability,
            "protected_customer_funds_liability": protected_funds_liability,
            "refundable_security_deposit_liability": deposit_liability,
            "cash_and_settlement_reconciliation_difference": reconciliation_difference,
        },
        "operations": {
            "active_bookings_in_period": active_bookings,
            "payment_failures_in_period": payment_failures,
        },
        "revenue_by_vehicle_category": by_vehicle,
        "revenue_by_region": by_region,
        "revenue_by_customer_segment": [],
        "data_quality": quality,
        "definitions": {
            "gross_booking_value": "Confirmed marketplace payment requirements, not LiftHaul revenue.",
            "recognized_platform_revenue": "Completed platform-fee settlements net of recorded fee refunds plus paid membership billing evidence.",
            "protected_customer_funds_liability": "Funded amounts not yet released or refunded.",
        },
    }
    core.audit(conn, actor, "FOUNDER_DASHBOARD_VIEWED", "founder_dashboard", 0,
               new={"period": result["period"], "quality_blockers": 3})
    conn.commit()
    return result


def export_csv(conn, actor, period="monthly", anchor=None):
    require_founder(conn, actor, "founder.dashboard.export")
    report = financial_summary(conn, actor, period, anchor)
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("section", "metric", "value", "currency", "period_start", "period_end"))
    for section in ("financials", "balances", "operations"):
        for metric, value in report[section].items():
            writer.writerow((section, metric, "NOT_AVAILABLE" if value is None else value,
                             report["currency"], report["period"]["start"], report["period"]["end"]))
    core.audit(conn, actor, "FOUNDER_DASHBOARD_EXPORTED", "founder_dashboard", 0,
               new={"period": report["period"], "format": "CSV"})
    conn.commit()
    return {"filename": f"lifthaul-founder-{period}.csv", "content": output.getvalue(),
            "data_quality": report["data_quality"]}

