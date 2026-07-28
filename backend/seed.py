"""RGO OS — DEMONSTRATION seed data (kept SEPARATE from production migrations).

`migrate.py` applies schema only. This script inserts demo users + one worked
lifecycle so a fresh non-production instance has something to show. Never run in
production (guarded by APP_ENV).
"""
from __future__ import annotations

import os
import sys

import core
import ops
import db


DEMO_USERS = [
    ("admin@rgo.demo", "admin", "Admin"),
    ("est@rgo.demo", "estimator", "Estimator"),
    ("appr@rgo.demo", "approver", "Approver"),
    ("fin@rgo.demo", "finance", "Finance"),
    ("ops@rgo.demo", "operations_manager", "Ops Manager"),
]
DEMO_PW = "Demo1234Pass"


def seed(conn):
    def _u(email, role, name, **kw):
        try:
            return core.create_user(conn, email, DEMO_PW, role, name, **kw)
        except core.ConflictError:
            row = conn.execute("SELECT id FROM users WHERE email=?", (email.lower(),)).fetchone()
            return row["id"]

    for e, r, n in DEMO_USERS:
        _u(e, r, n)
    A = lambda e: core.actor_for(conn, core.login(conn, e, DEMO_PW))
    admin, est, appr, fin = A("admin@rgo.demo"), A("est@rgo.demo"), A("appr@rgo.demo"), A("fin@rgo.demo")

    if conn.execute("SELECT 1 FROM jobs LIMIT 1").fetchone():
        return {"seeded": False, "reason": "already has data"}

    cid = core.create_customer(conn, admin, "Demo Rigging Client", "J. Roe", "jroe@client.demo")
    _u("client@rgo.demo", "customer", "J. Roe", customer_id=cid)
    bid = core.create_booking(conn, est, cid, "Crane Rental & Lifting", "Transformer relocation", 42)
    core.review_booking(conn, est, bid)
    core.ready_for_quotation(conn, est, bid)
    qid = core.create_quotation(conn, est, bid,
                                [{"kind": "crane", "description": "350t all-terrain", "qty": 1, "days": 3, "rate": 160000},
                                 {"kind": "logi", "description": "Mobilization & crew", "qty": 1, "days": 1, "rate": 140000}],
                                est_cost=380000)
    core.submit_quotation(conn, est, qid)
    core.approve_quotation(conn, appr, qid)
    core.send_quotation(conn, est, qid)
    cust = A("client@rgo.demo")
    core.accept_quotation(conn, cust, qid, "J. Roe", "CFO")
    prid = core.create_payment_request(conn, fin, bid, ops.MockWiseProvider() if hasattr(ops, "MockWiseProvider") else core.MockWiseProvider())
    due = conn.execute("SELECT amount_due FROM payment_requests WHERE id=?", (prid,)).fetchone()["amount_due"]
    core.verify_payment(conn, fin, prid, due, "WISE-DEMO", fees=round(due * 0.007))
    job = core.confirm_job(conn, admin, bid)
    return {"seeded": True, "customer": cid, "booking": bid, "job": job, "users": len(DEMO_USERS) + 1}


def main():
    if os.environ.get("APP_ENV") == "production":
        print("[seed] refusing to seed demo data in production", file=sys.stderr)
        sys.exit(4)
    conn = db.connect(os.environ.get("DATABASE_URL"))
    print("[seed]", seed(conn))


if __name__ == "__main__":
    main()
