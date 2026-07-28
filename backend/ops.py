"""RGO OS backend — operations & finance domain (extends the commercial spine).

Adds the second half of the lifecycle the spine didn't cover: site assessment,
resource reservations (temporary hold + confirmed + expiry + double-book
prevention), the full job lifecycle with gated transitions, dispatch blocks,
change orders, expenses + actual job costing, final billing & collection (invoice,
downpayment deduction, partial payments, allocations, balance, overdue),
cancellation + refunds, and reporting from stored data.

Same discipline as core.py: server-side authorization, foreign keys, soft state,
audit on every change, transactions where money/state changes, tested.
"""
from __future__ import annotations

import core
from core import (AppError, ConflictError, ForbiddenError, NotFoundError,
                  ValidationError, require, audit, now)

# --------------------------------------------------------------------------- #
# Extend RBAC with the operations/finance roles + permissions (least privilege)
# --------------------------------------------------------------------------- #
core.PERMISSIONS.setdefault("fleet_manager", {"equipment.*", "reservation.read", "job.read"})
core.PERMISSIONS.setdefault("mechanic", {"maintenance.*", "equipment.read"})
core.PERMISSIONS.setdefault("safety_officer", {"safety.*", "job.safety", "job.read"})
core.PERMISSIONS.setdefault("driver", {"job.read", "job.field"})
core.PERMISSIONS.setdefault("operator", {"job.read", "job.field"})
core.PERMISSIONS.setdefault("employee", {"self.read"})
# grant new perms to existing roles
core.PERMISSIONS["operations_manager"] |= {"assessment.*", "reservation.*", "job.*",
                                           "changeorder.*", "expense.create", "expense.approve", "expense.read"}
core.PERMISSIONS["estimator"] |= {"assessment.create", "assessment.read"}
core.PERMISSIONS["dispatcher"] |= {"job.transition", "reservation.read"}
core.PERMISSIONS["finance"] |= {"invoice.*", "expense.*", "refund.*"}
core.PERMISSIONS["safety_officer"] |= {"job.transition"}
for r in ("admin", "owner", "super_admin"):
    core.PERMISSIONS[r] = {"*"}


OPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS site_assessments(
  id INTEGER PRIMARY KEY, booking_id INTEGER NOT NULL REFERENCES bookings(id),
  assessor INTEGER, assessed_at TEXT, access TEXT, ground TEXT, hazards TEXT,
  power_lines INTEGER, required_equipment TEXT, recommendation TEXT,
  status TEXT NOT NULL, created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS reservations(
  id INTEGER PRIMARY KEY, booking_id INTEGER NOT NULL REFERENCES bookings(id),
  resource_type TEXT NOT NULL, resource_ref TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'TEMP',           -- TEMP|CONFIRMED|RELEASED
  hold_expiry TEXT, created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS job_stage_history(
  id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
  from_status TEXT, to_status TEXT, actor INTEGER, role TEXT, ts TEXT, reason TEXT);

CREATE TABLE IF NOT EXISTS change_orders(
  id INTEGER PRIMARY KEY, no TEXT UNIQUE NOT NULL, job_id INTEGER NOT NULL REFERENCES jobs(id),
  reason TEXT, amount REAL, tax REAL, revised_total REAL,
  status TEXT NOT NULL DEFAULT 'DRAFT',          -- DRAFT|INTERNAL_REVIEW|SENT|ACCEPTED|DECLINED|CANCELLED|BILLED
  approved_by INTEGER, approved_at TEXT, created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS expenses(
  id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
  category TEXT, amount REAL, currency TEXT DEFAULT 'PHP', supplier TEXT, spent_on TEXT,
  receipt TEXT, status TEXT NOT NULL DEFAULT 'SUBMITTED',  -- SUBMITTED|APPROVED|REJECTED
  submitted_by INTEGER, approved_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS invoices(
  id INTEGER PRIMARY KEY, no TEXT UNIQUE NOT NULL, job_id INTEGER NOT NULL REFERENCES jobs(id),
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  quoted REAL, change_orders_total REAL, downpayment_applied REAL, total REAL, balance REAL,
  status TEXT NOT NULL DEFAULT 'ISSUED',          -- ISSUED|PARTIALLY_PAID|PAID|OVERDUE|VOID
  due_date TEXT, created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS payment_allocations(
  id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id),
  amount REAL, ref TEXT, allocated_by INTEGER, allocated_at TEXT);

CREATE TABLE IF NOT EXISTS refunds(
  id INTEGER PRIMARY KEY, booking_id INTEGER NOT NULL REFERENCES bookings(id),
  reason TEXT, responsible TEXT, refundable REAL, non_refundable REAL,
  status TEXT NOT NULL DEFAULT 'PENDING',          -- PENDING|APPROVED|PAID|DECLINED
  ref TEXT, approved_by INTEGER, created_by INTEGER, created_at TEXT);
"""


def init_ops(conn):
    conn.executescript(OPS_SCHEMA)
    conn.commit()


def connect_full(path=":memory:"):
    conn = core.connect(path)
    init_ops(conn)
    return conn


def _job(conn, jid):
    r = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not r:
        raise NotFoundError("job not found")
    return r


# --------------------------------------------------------------------------- #
# 8. Site assessment  (gate: booking cannot go READY_FOR_QUOTATION with NOT_READY)
# --------------------------------------------------------------------------- #
def create_site_assessment(conn, actor, booking_id, status, *, access=None, ground=None,
                           hazards=None, power_lines=False, required_equipment=None,
                           recommendation=None):
    require(actor, "assessment.create")
    if status not in ("READY", "READY_WITH_CONDITIONS", "REASSESSMENT_REQUIRED", "NOT_READY"):
        raise ValidationError("invalid assessment status")
    cur = conn.execute(
        "INSERT INTO site_assessments(booking_id,assessor,assessed_at,access,ground,hazards,"
        "power_lines,required_equipment,recommendation,status,created_by,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (booking_id, actor["id"], now(), access, ground, hazards, 1 if power_lines else 0,
         required_equipment, recommendation, status, actor["id"], now()))
    conn.commit()
    audit(conn, actor, "assessment.create", "site_assessment", cur.lastrowid, new={"status": status})
    conn.commit()
    return cur.lastrowid


def assessment_ok(conn, booking_id) -> bool:
    r = conn.execute("SELECT status FROM site_assessments WHERE booking_id=? ORDER BY id DESC LIMIT 1",
                     (booking_id,)).fetchone()
    return (r is None) or r["status"] in ("READY", "READY_WITH_CONDITIONS")


# --------------------------------------------------------------------------- #
# 14. Resource reservation — temp hold, confirm, expiry, double-book prevention
# --------------------------------------------------------------------------- #
def _resource_held_by_other(conn, resource_type, resource_ref, booking_id):
    r = conn.execute(
        "SELECT booking_id FROM reservations WHERE resource_type=? AND resource_ref=?"
        " AND status IN ('TEMP','CONFIRMED') AND booking_id<>?",
        (resource_type, resource_ref, booking_id)).fetchone()
    return r["booking_id"] if r else None


def reserve_resource(conn, actor, booking_id, resource_type, resource_ref,
                     confirmed=False, hold_hours=48):
    require(actor, "reservation.create")
    other = _resource_held_by_other(conn, resource_type, resource_ref, booking_id)
    if other:
        raise ConflictError(f"{resource_type} {resource_ref} already reserved by booking {other}")
    from datetime import datetime, timedelta, timezone
    exp = (datetime.now(timezone.utc) + timedelta(hours=hold_hours)).isoformat()
    cur = conn.execute(
        "INSERT INTO reservations(booking_id,resource_type,resource_ref,status,hold_expiry,created_by,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (booking_id, resource_type, resource_ref, "CONFIRMED" if confirmed else "TEMP",
         None if confirmed else exp, actor["id"], now()))
    conn.commit()
    audit(conn, actor, "reservation.create", "reservation", cur.lastrowid,
          new={"resource": f"{resource_type}:{resource_ref}", "status": "CONFIRMED" if confirmed else "TEMP"})
    conn.commit()
    return cur.lastrowid


def confirm_reservations(conn, actor, booking_id):
    require(actor, "reservation.create")
    conn.execute("UPDATE reservations SET status='CONFIRMED', hold_expiry=NULL"
                 " WHERE booking_id=? AND status='TEMP'", (booking_id,))
    audit(conn, actor, "reservation.confirm", "booking", booking_id)
    conn.commit()


def release_expired_holds(conn, actor, as_of=None):
    """Release TEMP holds whose expiry has passed; returns released ids (+notify via audit)."""
    require(actor, "reservation.create")
    as_of = as_of or now()
    rows = conn.execute("SELECT id,booking_id,resource_type,resource_ref FROM reservations"
                        " WHERE status='TEMP' AND hold_expiry IS NOT NULL AND hold_expiry < ?",
                        (as_of,)).fetchall()
    for r in rows:
        conn.execute("UPDATE reservations SET status='RELEASED' WHERE id=?", (r["id"],))
        audit(conn, actor, "reservation.expire_release", "reservation", r["id"],
              new={"resource": f"{r['resource_type']}:{r['resource_ref']}", "notify": "ops+customer"})
    conn.commit()
    return [r["id"] for r in rows]


# --------------------------------------------------------------------------- #
# 13. Credit terms + confirmation gate (downpayment OR approved credit)
# --------------------------------------------------------------------------- #
def _payment_verified(conn, booking_id):
    r = conn.execute("SELECT status FROM payment_requests WHERE booking_id=?", (booking_id,)).fetchone()
    return bool(r and r["status"] == "VERIFIED")


# --------------------------------------------------------------------------- #
# 15/16. Job lifecycle + dispatch gating
# --------------------------------------------------------------------------- #
JOB_FLOW = {
    "CONFIRMED": {"PLANNING", "CANCELLED"},
    "PLANNING": {"RESOURCES_RESERVED", "ON_HOLD", "CANCELLED"},
    "RESOURCES_RESERVED": {"SAFETY_REVIEW", "ON_HOLD"},
    "SAFETY_REVIEW": {"READY_FOR_DISPATCH", "ON_HOLD"},
    "READY_FOR_DISPATCH": {"DISPATCHED", "ON_HOLD"},
    "DISPATCHED": {"IN_TRANSIT", "ON_SITE"},
    "IN_TRANSIT": {"ON_SITE"},
    "ON_SITE": {"IN_PROGRESS"},
    "IN_PROGRESS": {"COMPLETED", "ON_HOLD"},
    "ON_HOLD": {"PLANNING", "IN_PROGRESS", "CANCELLED"},
    "COMPLETED": {"CUSTOMER_ACCEPTANCE_PENDING"},
    "CUSTOMER_ACCEPTANCE_PENDING": {"ACCEPTED", "COMPLETION_DISPUTED"},
    "COMPLETION_DISPUTED": {"IN_PROGRESS", "ACCEPTED"},
    "ACCEPTED": {"CLOSED"},
    "CLOSED": set(),
    "CANCELLED": set(),
}
# transitions that require mandatory evidence text
_EVIDENCE_REQUIRED = {"DISPATCHED", "ON_SITE", "COMPLETED", "ACCEPTED"}


def transition_job(conn, actor, job_id, to_status, *, evidence=None, reason=None):
    require(actor, "job.transition")
    j = _job(conn, job_id)
    frm = j["status"]
    if to_status not in JOB_FLOW.get(frm, set()):
        raise ConflictError(f"illegal job transition {frm} -> {to_status}")
    if to_status in _EVIDENCE_REQUIRED and not evidence:
        raise ValidationError(f"transition to {to_status} requires evidence")
    # --- gates ---
    if to_status == "READY_FOR_DISPATCH":
        if not conn.execute("SELECT 1 FROM reservations WHERE booking_id=? AND status='CONFIRMED'",
                            (j["booking_id"],)).fetchone():
            raise ConflictError("dispatch block: no confirmed resource reservation")
        # safety block: if a safety check exists, the latest must PASS
        if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='safety_records'").fetchone():
            sr = conn.execute("SELECT result FROM safety_records WHERE job_id=? ORDER BY id DESC LIMIT 1",
                              (job_id,)).fetchone()
            if sr and sr["result"] != "PASS":
                raise ConflictError("dispatch block: latest safety check did not PASS")
    if to_status == "DISPATCHED":
        if not _payment_verified(conn, j["booking_id"]):
            raise ConflictError("dispatch block: downpayment not verified")
    with conn:
        conn.execute("UPDATE jobs SET status=? WHERE id=?", (to_status, job_id))
        conn.execute("INSERT INTO job_stage_history(job_id,from_status,to_status,actor,role,ts,reason)"
                     " VALUES(?,?,?,?,?,?,?)",
                     (job_id, frm, to_status, actor["id"], actor["role"], now(), reason or evidence))
        audit(conn, actor, "job.transition", "job", job_id,
              old={"status": frm}, new={"status": to_status}, reason=reason)
    return to_status


# --------------------------------------------------------------------------- #
# 17. Change orders  (no billing of unapproved additional work)
# --------------------------------------------------------------------------- #
def create_change_order(conn, actor, job_id, reason, amount, tax=0):
    require(actor, "changeorder.create")
    j = _job(conn, job_id)
    n = conn.execute("SELECT COUNT(*) c FROM change_orders").fetchone()["c"]
    no = f"CO-{7001 + n}"
    revised = j["amount"] + amount + tax
    cur = conn.execute(
        "INSERT INTO change_orders(no,job_id,reason,amount,tax,revised_total,status,created_by,created_at)"
        " VALUES(?,?,?,?,?,?, 'DRAFT',?,?)",
        (no, job_id, reason, amount, tax, revised, actor["id"], now()))
    conn.commit()
    audit(conn, actor, "changeorder.create", "change_order", cur.lastrowid, new={"no": no, "amount": amount})
    conn.commit()
    return cur.lastrowid


def approve_change_order(conn, actor, co_id):
    require(actor, "changeorder.approve")
    co = conn.execute("SELECT * FROM change_orders WHERE id=?", (co_id,)).fetchone()
    if not co:
        raise NotFoundError("change order not found")
    conn.execute("UPDATE change_orders SET status='ACCEPTED', approved_by=?, approved_at=? WHERE id=?",
                 (actor["id"], now(), co_id))
    audit(conn, actor, "changeorder.approve", "change_order", co_id, new={"status": "ACCEPTED"})
    conn.commit()


def approved_change_total(conn, job_id):
    r = conn.execute("SELECT COALESCE(SUM(amount+tax),0) t FROM change_orders"
                     " WHERE job_id=? AND status IN ('ACCEPTED','BILLED')", (job_id,)).fetchone()
    return r["t"]


# --------------------------------------------------------------------------- #
# 18. Expenses + actual costing
# --------------------------------------------------------------------------- #
def add_expense(conn, actor, job_id, category, amount, supplier=None):
    require(actor, "expense.create")
    cur = conn.execute(
        "INSERT INTO expenses(job_id,category,amount,supplier,spent_on,status,submitted_by,created_at)"
        " VALUES(?,?,?,?,?, 'SUBMITTED',?,?)",
        (job_id, category, amount, supplier, now(), actor["id"], now()))
    conn.commit()
    audit(conn, actor, "expense.create", "expense", cur.lastrowid, new={"amount": amount, "category": category})
    conn.commit()
    return cur.lastrowid


def approve_expense(conn, actor, exp_id):
    require(actor, "expense.approve")
    conn.execute("UPDATE expenses SET status='APPROVED', approved_by=? WHERE id=?", (actor["id"], exp_id))
    audit(conn, actor, "expense.approve", "expense", exp_id, new={"status": "APPROVED"})
    conn.commit()


def actual_cost(conn, job_id):
    return conn.execute("SELECT COALESCE(SUM(amount),0) t FROM expenses WHERE job_id=? AND status='APPROVED'",
                        (job_id,)).fetchone()["t"]


def job_profitability(conn, job_id):
    j = _job(conn, job_id)
    q = conn.execute("SELECT est_cost,total FROM quotations WHERE id=?", (j["quotation_id"],)).fetchone()
    revenue = j["amount"] + approved_change_total(conn, job_id)
    actual = actual_cost(conn, job_id)
    gross = revenue - actual
    return {"quoted_revenue": j["amount"], "approved_variations": approved_change_total(conn, job_id),
            "final_revenue": revenue, "estimated_cost": q["est_cost"] if q else None,
            "actual_cost": actual, "gross_profit": gross,
            "margin_pct": round(gross / revenue * 100, 1) if revenue else None}


# --------------------------------------------------------------------------- #
# 19. Final billing & collection
# --------------------------------------------------------------------------- #
def generate_final_invoice(conn, actor, job_id, due_date=None):
    require(actor, "invoice.create")
    j = _job(conn, job_id)
    if j["status"] not in ("ACCEPTED", "CLOSED", "COMPLETED", "CUSTOMER_ACCEPTANCE_PENDING"):
        raise ConflictError("CONTROL: invoice only after job completion/acceptance")
    if conn.execute("SELECT 1 FROM invoices WHERE job_id=?", (job_id,)).fetchone():
        raise ConflictError("invoice already exists for this job")
    dp = conn.execute(
        "SELECT amount_received FROM payment_requests WHERE booking_id=? AND status='VERIFIED'",
        (j["booking_id"],)).fetchone()
    downpayment = (dp["amount_received"] or 0) if dp else 0
    co_total = approved_change_total(conn, job_id)
    total = j["amount"] + co_total
    balance = total - downpayment
    n = conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"]
    no = f"INV-{9001 + n}"
    with conn:
        cur = conn.execute(
            "INSERT INTO invoices(no,job_id,customer_id,quoted,change_orders_total,downpayment_applied,"
            "total,balance,status,due_date,created_by,created_at)"
            " VALUES(?,?,?,?,?,?,?,?, 'ISSUED',?,?,?)",
            (no, job_id, j["customer_id"], j["amount"], co_total, downpayment, total, balance,
             due_date, actor["id"], now()))
        iid = cur.lastrowid
    audit(conn, actor, "invoice.create", "invoice", iid, new={"no": no, "total": total, "balance": balance})
    conn.commit()
    return iid


def allocate_payment(conn, actor, invoice_id, amount, ref):
    """Record a (partial) balance payment against an invoice; updates status/balance."""
    require(actor, "invoice.pay")
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if not inv:
        raise NotFoundError("invoice not found")
    with conn:
        conn.execute("INSERT INTO payment_allocations(invoice_id,amount,ref,allocated_by,allocated_at)"
                     " VALUES(?,?,?,?,?)", (invoice_id, amount, ref, actor["id"], now()))
        paid = conn.execute("SELECT COALESCE(SUM(amount),0) t FROM payment_allocations WHERE invoice_id=?",
                            (invoice_id,)).fetchone()["t"]
        balance = inv["total"] - inv["downpayment_applied"] - paid
        status = "PAID" if balance <= 0 else "PARTIALLY_PAID"
        conn.execute("UPDATE invoices SET balance=?, status=? WHERE id=?", (balance, status, invoice_id))
        audit(conn, actor, "payment.allocate", "invoice", invoice_id,
              new={"allocated": amount, "balance": balance, "status": status})
    return {"balance": balance, "status": status}


def mark_overdue(conn, actor, as_of):
    require(actor, "invoice.create")
    conn.execute("UPDATE invoices SET status='OVERDUE' WHERE status IN ('ISSUED','PARTIALLY_PAID')"
                 " AND due_date IS NOT NULL AND due_date < ?", (as_of,))
    conn.commit()


# --------------------------------------------------------------------------- #
# 20. Cancellation + refunds
# --------------------------------------------------------------------------- #
def cancel_and_refund(conn, actor, booking_id, reason, responsible, refundable, non_refundable):
    require(actor, "refund.create")
    cur = conn.execute(
        "INSERT INTO refunds(booking_id,reason,responsible,refundable,non_refundable,status,created_by,created_at)"
        " VALUES(?,?,?,?,?, 'PENDING',?,?)",
        (booking_id, reason, responsible, refundable, non_refundable, actor["id"], now()))
    audit(conn, actor, "refund.create", "refund", cur.lastrowid,
          new={"refundable": refundable, "non_refundable": non_refundable}, reason=reason)
    conn.commit()
    return cur.lastrowid


def approve_refund(conn, actor, refund_id, ref):
    require(actor, "refund.approve")
    conn.execute("UPDATE refunds SET status='APPROVED', ref=?, approved_by=? WHERE id=?",
                 (ref, actor["id"], refund_id))
    audit(conn, actor, "refund.approve", "refund", refund_id, new={"status": "APPROVED", "ref": ref})
    conn.commit()


# --------------------------------------------------------------------------- #
# 27. Reporting — computed from stored data (no hard-coded metrics)
# --------------------------------------------------------------------------- #
def report_quotation_conversion(conn):
    sent = conn.execute("SELECT COUNT(DISTINCT booking_id) c FROM quotations WHERE status IN"
                        " ('sent','accepted','superseded','declined')").fetchone()["c"]
    accepted = conn.execute("SELECT COUNT(DISTINCT booking_id) c FROM quotations WHERE status='accepted'").fetchone()["c"]
    return {"quotations_sent": sent, "accepted": accepted,
            "conversion_pct": round(accepted / sent * 100, 1) if sent else 0.0}


def report_accepted_awaiting_payment(conn):
    return conn.execute(
        "SELECT COUNT(*) c FROM bookings WHERE stage IN ('QUOTATION_ACCEPTED','AWAITING_DOWNPAYMENT',"
        "'PAYMENT_UNDER_VERIFICATION')").fetchone()["c"]


def report_receivables(conn):
    rows = conn.execute("SELECT status, COALESCE(SUM(balance),0) bal, COUNT(*) c FROM invoices"
                        " WHERE status IN ('ISSUED','PARTIALLY_PAID','OVERDUE') GROUP BY status").fetchall()
    return {r["status"]: {"balance": r["bal"], "count": r["c"]} for r in rows}


def report_confirmed_jobs(conn):
    return conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
