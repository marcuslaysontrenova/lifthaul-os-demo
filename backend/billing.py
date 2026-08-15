"""Corporate Billing & Statements — consolidated A/R over the existing revenue streams.

A corporate customer that runs both freight and rental with LiftHaul wants ONE account, ONE running
balance and periodic statements — not a scattering of per-transaction invoices. This module provides
that WITHOUT a parallel payment domain and WITHOUT coupling to each revenue source's schema:

  * `billing_accounts` — one corporate account per customer, carrying credit limit + payment terms +
    billing cycle. Credit governance reuses `crm_admin.evaluate_credit` (evidence-only by default),
    never a second credit engine.
  * `billing_items` — a normalized, source-agnostic charge/credit LEDGER. Any revenue source posts a
    charge here via `post_charge(source_type, source_id, ...)` (idempotent per source), so statements
    read one ledger and never need to understand rental_invoices / protected-payment internals. Rental
    finalize posts here automatically; freight/ERP can post via the same call.
  * `billing_payments` — A/R payment RECORDS. This is accounts-receivable bookkeeping, not a payment
    rail: recording a payment posts a CREDIT to the ledger and moves the balance — it never moves real
    money (live custody stays behind the Protected Payment funds gate).
  * `billing_statements` — an immutable, checksummed period snapshot: opening balance, charges,
    payments, closing balance, aging buckets (current / 1-30 / 31-60 / 61-90 / 90+), due date, and a
    credit-limit evaluation. Items in the period are stamped with the statement id.

Governance preserved: tenant isolation, RBAC, audit, deterministic rollups, idempotent charge posting,
immutable statements, and honest money semantics (A/R only; no fabricated fund movement).
"""
from __future__ import annotations

import datetime
import hashlib
import json

import core
import tenant
import crm_admin


ITEM_KINDS = ("CHARGE", "CREDIT")
ITEM_STATUSES = ("OPEN", "STATEMENTED", "PAID")
STATEMENT_STATUSES = ("ISSUED", "PAID", "VOID")
CYCLES = ("WEEKLY", "MONTHLY", "QUARTERLY", "PROJECT")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _today():
    return datetime.date.today().isoformat()


def _d(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _checksum(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:32]


SCHEMA = """
CREATE TABLE IF NOT EXISTS billing_accounts(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, customer_id INTEGER NOT NULL, account_no TEXT,
  credit_limit REAL, payment_terms_days INTEGER NOT NULL DEFAULT 30, billing_cycle TEXT NOT NULL DEFAULT 'MONTHLY',
  currency TEXT NOT NULL DEFAULT 'PHP', credit_status TEXT NOT NULL DEFAULT 'GOOD',
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_by INTEGER, created_at TEXT, updated_by INTEGER, updated_at TEXT,
  UNIQUE(tenant_id, customer_id));

CREATE TABLE IF NOT EXISTS billing_items(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, account_id INTEGER NOT NULL,
  kind TEXT NOT NULL DEFAULT 'CHARGE', source_type TEXT, source_id INTEGER,
  description TEXT, item_date TEXT, amount REAL NOT NULL DEFAULT 0, tax REAL NOT NULL DEFAULT 0,
  total REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'OPEN', statement_id INTEGER,
  created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS billing_payments(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, account_id INTEGER NOT NULL,
  amount REAL NOT NULL, method TEXT, reference TEXT, received_at TEXT,
  recorded_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS billing_statements(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, account_id INTEGER NOT NULL, statement_no TEXT,
  period_start TEXT, period_end TEXT, opening_balance REAL, charges_total REAL, payments_total REAL,
  closing_balance REAL, aging TEXT, due_date TEXT, credit_limit REAL, over_limit INTEGER NOT NULL DEFAULT 0,
  currency TEXT DEFAULT 'PHP', checksum TEXT, status TEXT NOT NULL DEFAULT 'ISSUED',
  created_by INTEGER, created_at TEXT);
"""


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    return


# --------------------------------------------------------------------------- #
def _row(conn, table, id):
    r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (id,)).fetchone()
    if not r:
        raise core.NotFoundError(f"{table} row {id} not found")
    return dict(r)


def _account(conn, actor, account_id):
    a = _row(conn, "billing_accounts", account_id)
    tenant.guard(actor, a)
    return a


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
def open_account(conn, actor, customer_id, *, credit_limit=None, payment_terms_days=30,
                 billing_cycle="MONTHLY"):
    core.require(actor, "marketplace.billing.manage")
    if billing_cycle not in CYCLES:
        raise core.ValidationError(f"invalid billing_cycle '{billing_cycle}'")
    if not conn.execute("SELECT 1 FROM customers WHERE id=?", (customer_id,)).fetchone():
        raise core.NotFoundError(f"customer {customer_id} not found")
    at = tenant.actor_tenant(actor)
    dup = conn.execute("SELECT id FROM billing_accounts WHERE customer_id=? AND (tenant_id=? OR tenant_id IS NULL)",
                       (customer_id, at)).fetchone()
    if dup:
        raise core.ConflictError(f"customer {customer_id} already has a billing account ({dup['id']})")
    cur = conn.execute(
        "INSERT INTO billing_accounts(customer_id,credit_limit,payment_terms_days,billing_cycle,status,"
        "created_by,created_at) VALUES(?,?,?,?, 'ACTIVE', ?,?)",
        (customer_id, credit_limit, int(payment_terms_days), billing_cycle, actor["id"], _now()))
    aid = cur.lastrowid
    conn.execute("UPDATE billing_accounts SET account_no=? WHERE id=?", (f"BILL-{aid}", aid))
    tenant.stamp(conn, actor, "billing_accounts", aid)
    core.audit(conn, actor, "BILLING_ACCOUNT_OPENED", "billing_accounts", aid, None,
               {"customer_id": customer_id, "credit_limit": credit_limit, "terms": payment_terms_days})
    conn.commit()
    return {"account_id": aid, "account_no": f"BILL-{aid}"}


def set_account_terms(conn, actor, account_id, *, credit_limit=None, payment_terms_days=None,
                      billing_cycle=None, status=None):
    core.require(actor, "marketplace.billing.manage")
    a = _account(conn, actor, account_id)
    fields, params = [], []
    for k, v in (("credit_limit", credit_limit), ("payment_terms_days", payment_terms_days),
                 ("billing_cycle", billing_cycle), ("status", status)):
        if v is not None:
            fields.append(f"{k}=?"); params.append(v)
    if not fields:
        return {"unchanged": True}
    params += [actor["id"], _now(), account_id]
    conn.execute(f"UPDATE billing_accounts SET {','.join(fields)},updated_by=?,updated_at=? WHERE id=?", params)
    core.audit(conn, actor, "BILLING_ACCOUNT_TERMS", "billing_accounts", account_id, a,
               {"credit_limit": credit_limit, "terms": payment_terms_days, "cycle": billing_cycle, "status": status})
    conn.commit()
    return {"account_id": account_id}


def account_for_customer(conn, actor, customer_id):
    at = tenant.actor_tenant(actor)
    r = conn.execute("SELECT * FROM billing_accounts WHERE customer_id=? AND (tenant_id=? OR tenant_id IS NULL)",
                     (customer_id, at)).fetchone()
    return dict(r) if r else None


def list_accounts(conn, actor, status=None):
    core.require(actor, "marketplace.billing.view")
    frag, params = tenant.predicate(actor)
    q = "SELECT * FROM billing_accounts WHERE 1=1" + frag
    a = list(params)
    if status:
        q += " AND status=?"; a.append(status)
    q += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


# --------------------------------------------------------------------------- #
# Ledger — charges (idempotent per source) + A/R payments (never move funds)
# --------------------------------------------------------------------------- #
def post_charge(conn, actor, account_id, source_type, source_id, description, amount, *, tax=0,
                item_date=None):
    """Append a CHARGE to the ledger. Idempotent per (account, source_type, source_id) so a revenue
    source can post safely without creating duplicates."""
    core.require(actor, "marketplace.billing.manage")
    a = _account(conn, actor, account_id)
    if amount is None or float(amount) < 0:
        raise core.ValidationError("charge amount must be >= 0")
    existing = conn.execute(
        "SELECT id FROM billing_items WHERE account_id=? AND kind='CHARGE' AND source_type=? AND source_id=?",
        (account_id, source_type, source_id)).fetchone()
    if existing:
        return {"item_id": existing["id"], "idempotent": True}
    total = round(float(amount) + float(tax or 0), 2)
    cur = conn.execute(
        "INSERT INTO billing_items(account_id,kind,source_type,source_id,description,item_date,amount,"
        "tax,total,status,created_by,created_at) VALUES(?, 'CHARGE', ?,?,?,?,?,?,?, 'OPEN', ?,?)",
        (account_id, source_type, source_id, description, item_date or _today(), round(float(amount), 2),
         round(float(tax or 0), 2), total, actor["id"], _now()))
    iid = cur.lastrowid
    tenant.stamp(conn, actor, "billing_items", iid)
    core.audit(conn, actor, "BILLING_CHARGE_POSTED", "billing_items", iid, None,
               {"account": account_id, "source": f"{source_type}:{source_id}", "total": total})
    conn.commit()
    return {"item_id": iid, "total": total}


def record_payment(conn, actor, account_id, amount, *, method="BANK_TRANSFER", reference=None,
                   received_at=None):
    """Record an A/R payment against the account. This is bookkeeping — it posts a CREDIT to the ledger
    and moves the running balance. It NEVER moves real money (live custody stays behind the Protected
    Payment funds gate)."""
    core.require(actor, "marketplace.billing.payment")
    a = _account(conn, actor, account_id)
    if amount is None or float(amount) <= 0:
        raise core.ValidationError("payment amount must be positive")
    cur = conn.execute(
        "INSERT INTO billing_payments(account_id,amount,method,reference,received_at,recorded_by,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (account_id, round(float(amount), 2), method, reference, received_at or _today(), actor["id"], _now()))
    pid = cur.lastrowid
    tenant.stamp(conn, actor, "billing_payments", pid)
    conn.execute(
        "INSERT INTO billing_items(account_id,kind,source_type,source_id,description,item_date,amount,"
        "tax,total,status,created_by,created_at) VALUES(?, 'CREDIT', 'PAYMENT', ?,?,?,?,0,?, 'OPEN', ?,?)",
        (account_id, pid, f"Payment received ({method}" + (f" ref {reference}" if reference else "") + ")",
         received_at or _today(), round(float(amount), 2), round(float(amount), 2), actor["id"], _now()))
    core.audit(conn, actor, "BILLING_PAYMENT_RECORDED", "billing_payments", pid, None,
               {"account": account_id, "amount": amount, "method": method, "funds_moved": False})
    conn.commit()
    return {"payment_id": pid, "funds_moved": False}


def _sum(conn, account_id, kind, upto=None, since=None):
    q = "SELECT COALESCE(SUM(total),0) s FROM billing_items WHERE account_id=? AND kind=?"
    p = [account_id, kind]
    if since:
        q += " AND item_date>=?"; p.append(since)
    if upto:
        q += " AND item_date<=?"; p.append(upto)
    return round(float(conn.execute(q, p).fetchone()["s"]), 2)


def account_balance(conn, actor, account_id):
    core.require(actor, "marketplace.billing.view")
    _account(conn, actor, account_id)
    charges = _sum(conn, account_id, "CHARGE")
    credits = _sum(conn, account_id, "CREDIT")
    return {"account_id": account_id, "charges": charges, "credits": credits,
            "balance": round(charges - credits, 2)}


# --------------------------------------------------------------------------- #
# Statements — immutable period rollup with aging
# --------------------------------------------------------------------------- #
def _aging(conn, account_id, as_of, terms_days):
    """Aging over still-open charges (not yet settled by a statement marked PAID), bucketed by how far
    past due each charge is relative to its own due date (item_date + terms)."""
    buckets = {"current": 0.0, "d1_30": 0.0, "d31_60": 0.0, "d61_90": 0.0, "d90_plus": 0.0}
    asof = _d(as_of)
    for r in conn.execute("SELECT item_date,total FROM billing_items WHERE account_id=? AND kind='CHARGE' "
                          "AND status!='PAID'", (account_id,)).fetchall():
        idate = _d(r["item_date"])
        if not idate or not asof:
            buckets["current"] += float(r["total"]); continue
        overdue = (asof - idate).days - int(terms_days or 0)
        if overdue <= 0:
            buckets["current"] += float(r["total"])
        elif overdue <= 30:
            buckets["d1_30"] += float(r["total"])
        elif overdue <= 60:
            buckets["d31_60"] += float(r["total"])
        elif overdue <= 90:
            buckets["d61_90"] += float(r["total"])
        else:
            buckets["d90_plus"] += float(r["total"])
    return {k: round(v, 2) for k, v in buckets.items()}


def generate_statement(conn, actor, account_id, period_start, period_end):
    core.require(actor, "marketplace.billing.statement")
    a = _account(conn, actor, account_id)
    if _d(period_end) and _d(period_start) and _d(period_end) < _d(period_start):
        raise core.ValidationError("period_end before period_start")
    prior = conn.execute("SELECT closing_balance FROM billing_statements WHERE account_id=? "
                         "ORDER BY id DESC LIMIT 1", (account_id,)).fetchone()
    opening = round(float(prior["closing_balance"]), 2) if prior else 0.0
    # Sweep ALL still-open (not-yet-statemented) items dated up to period_end — so an item posted late
    # (dated before this period but after the last statement) is never lost. Opening already reflects
    # everything previously statemented, so summing only OPEN items cannot double-count.
    def _open_sum(kind):
        return round(float(conn.execute(
            "SELECT COALESCE(SUM(total),0) s FROM billing_items WHERE account_id=? AND kind=? "
            "AND status='OPEN' AND item_date<=?", (account_id, kind, period_end)).fetchone()["s"]), 2)
    charges = _open_sum("CHARGE")
    payments = _open_sum("CREDIT")
    closing = round(opening + charges - payments, 2)
    terms = int(a["payment_terms_days"] or 0)
    aging = _aging(conn, account_id, period_end, terms)
    due = None
    if _d(period_end):
        due = (_d(period_end) + datetime.timedelta(days=terms)).isoformat()
    # credit governance reuses the CRM credit engine (evidence-only by default)
    over_limit = bool(a["credit_limit"] is not None and closing > float(a["credit_limit"]))
    try:
        crm_admin.evaluate_credit(conn, actor, a["customer_id"], "statement", amount=closing)
    except Exception:
        pass
    snap = {"account_id": account_id, "period_start": period_start, "period_end": period_end,
            "opening_balance": opening, "charges_total": charges, "payments_total": payments,
            "closing_balance": closing, "aging": aging, "due_date": due,
            "credit_limit": a["credit_limit"], "over_limit": over_limit}
    cs = _checksum(snap)
    cur = conn.execute(
        "INSERT INTO billing_statements(account_id,period_start,period_end,opening_balance,charges_total,"
        "payments_total,closing_balance,aging,due_date,credit_limit,over_limit,checksum,status,created_by,"
        "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'ISSUED', ?,?)",
        (account_id, period_start, period_end, opening, charges, payments, closing, json.dumps(aging),
         due, a["credit_limit"], 1 if over_limit else 0, cs, actor["id"], _now()))
    sid = cur.lastrowid
    conn.execute("UPDATE billing_statements SET statement_no=? WHERE id=?", (f"STMT-{sid}", sid))
    tenant.stamp(conn, actor, "billing_statements", sid)
    # stamp every swept OPEN item as STATEMENTED (immutably tied to this statement)
    conn.execute("UPDATE billing_items SET status='STATEMENTED', statement_id=? WHERE account_id=? "
                 "AND status='OPEN' AND item_date<=?", (sid, account_id, period_end))
    if over_limit:
        conn.execute("UPDATE billing_accounts SET credit_status='OVER_LIMIT',updated_at=? WHERE id=?",
                     (_now(), account_id))
    core.audit(conn, actor, "BILLING_STATEMENT_GENERATED", "billing_statements", sid, None,
               {"account": account_id, "closing": closing, "over_limit": over_limit})
    conn.commit()
    return {"statement_id": sid, "statement_no": f"STMT-{sid}", "opening_balance": opening,
            "charges_total": charges, "payments_total": payments, "closing_balance": closing,
            "aging": aging, "due_date": due, "over_limit": over_limit}


def get_statement(conn, actor, statement_id):
    core.require(actor, "marketplace.billing.view")
    s = _row(conn, "billing_statements", statement_id)
    tenant.guard(actor, s)
    s["aging"] = json.loads(s["aging"]) if s.get("aging") else {}
    lines = [dict(r) for r in conn.execute(
        "SELECT id,kind,source_type,source_id,description,item_date,amount,tax,total,status "
        "FROM billing_items WHERE statement_id=? ORDER BY item_date, id", (statement_id,)).fetchall()]
    return {"statement": s, "lines": lines}


def list_statements(conn, actor, account_id=None):
    core.require(actor, "marketplace.billing.view")
    frag, params = tenant.predicate(actor)
    q = "SELECT * FROM billing_statements WHERE 1=1" + frag
    a = list(params)
    if account_id:
        q += " AND account_id=?"; a.append(account_id)
    q += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(q, a).fetchall()]


def mark_statement_paid(conn, actor, statement_id):
    core.require(actor, "marketplace.billing.payment")
    s = _row(conn, "billing_statements", statement_id)
    tenant.guard(actor, s)
    conn.execute("UPDATE billing_statements SET status='PAID' WHERE id=?", (statement_id,))
    conn.execute("UPDATE billing_items SET status='PAID' WHERE statement_id=? AND kind='CHARGE'", (statement_id,))
    core.audit(conn, actor, "BILLING_STATEMENT_PAID", "billing_statements", statement_id, None, {})
    conn.commit()
    return {"status": "PAID"}


# --------------------------------------------------------------------------- #
# Source integration — safe, best-effort charge posting (called by revenue modules)
# --------------------------------------------------------------------------- #
def post_charge_for_customer(conn, actor, customer_id, source_type, source_id, description, amount, *, tax=0):
    """Post a charge to a customer's billing account IF one exists. Used by revenue modules (rental,
    freight) so a corporate customer's activity accrues to one statement. No account -> no-op (the
    charge simply isn't consolidated; the source invoice still stands)."""
    acct = account_for_customer(conn, actor, customer_id)
    if not acct:
        return {"posted": False, "reason": "no_billing_account"}
    r = post_charge(conn, actor, acct["id"], source_type, source_id, description, amount, tax=tax)
    return {"posted": True, **r}


# --------------------------------------------------------------------------- #
def queues(conn, actor):
    core.require(actor, "marketplace.billing.view")
    frag, params = tenant.predicate(actor)
    accts = conn.execute("SELECT COUNT(*) c FROM billing_accounts WHERE 1=1" + frag, params).fetchone()["c"]
    over = conn.execute("SELECT COUNT(*) c FROM billing_accounts WHERE credit_status='OVER_LIMIT'" + frag,
                        params).fetchone()["c"]
    stmts = conn.execute("SELECT COUNT(*) c FROM billing_statements WHERE status='ISSUED'" + frag,
                         params).fetchone()["c"]
    return {"accounts": accts, "over_limit_accounts": over, "issued_statements": stmts}


def run_integrity(conn, actor):
    core.require(actor, "marketplace.billing.view")
    checks = []
    orphan = conn.execute("SELECT COUNT(*) c FROM billing_items i LEFT JOIN billing_accounts a "
                          "ON a.id=i.account_id WHERE a.id IS NULL").fetchone()["c"]
    checks.append({"check": "no_orphan_items", "ok": orphan == 0, "count": orphan})
    dupacct = conn.execute("SELECT COUNT(*) c FROM (SELECT customer_id,COALESCE(tenant_id,-1) t,COUNT(*) n "
                           "FROM billing_accounts GROUP BY customer_id,t HAVING n>1)").fetchone()["c"]
    checks.append({"check": "one_account_per_customer", "ok": dupacct == 0, "count": dupacct})
    neg = conn.execute("SELECT COUNT(*) c FROM billing_items WHERE total<0").fetchone()["c"]
    checks.append({"check": "no_negative_items", "ok": neg == 0, "count": neg})
    return {"ok": all(x["ok"] for x in checks), "checks": checks}
