"""LiftHaul OS — Items 1-4 PostgreSQL runtime validation (CI only).

Runs against a REAL PostgreSQL database (DATABASE_URL) inside GitHub Actions:
tenant isolation, tenant stamping, cross-tenant relationship denial, a financial
sanity check, and restart persistence (reconnect). Exits non-zero on any failure so
the workflow fails loudly. This is the runtime proof the local sandbox cannot produce.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import db          # noqa: E402
import core        # noqa: E402
import admin_platform as ap   # noqa: E402
import tenant      # noqa: E402

FAILED = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        FAILED.append(msg)


def main():
    url = os.environ["DATABASE_URL"]
    assert url.startswith(("postgres://", "postgresql://")), "must run against PostgreSQL"
    conn = db.connect(url)                     # applies PG DDL + seeds platform
    print("connected to PostgreSQL; schema_version =", db.current_version(conn), flush=True)

    tA = ap.create_tenant(conn, "HAULA", "Synthetic Hauling A")
    tB = ap.create_tenant(conn, "HAULB", "Synthetic Hauling B")
    uA = core.create_user(conn, "a@haula", "Demo1234Xy", "operations_manager", "A")
    uB = core.create_user(conn, "b@haulb", "Demo1234Xy", "operations_manager", "B")
    tenant.bind_user_tenant(conn, None, uA, tA)
    tenant.bind_user_tenant(conn, None, uB, tB)

    def actor(email):
        a = core.actor_for(conn, core.login(conn, email, "Demo1234Xy"))
        ap.apply_rbac(conn, a)
        return a
    aA, aB = actor("a@haula"), actor("b@haulb")
    check(aA["tenant_id"] == tA, "actor A carries authoritative tenant A")

    custA = core.create_customer(conn, aA, "Acme Hauling")
    bkA = core.create_booking(conn, aA, custA, "Crane", "Transformer", 40)
    custB = core.create_customer(conn, aB, "Acme Hauling")
    bkB = core.create_booking(conn, aB, custB, "Crane", "Transformer", 40)

    stamped = conn.execute("SELECT tenant_id FROM bookings WHERE id=?", (bkA,)).fetchone()["tenant_id"]
    check(stamped == tA, "booking A stamped with tenant A (server-derived)")

    try:
        core.get_booking(conn, aA, bkB); check(False, "cross-tenant read must be denied")
    except core.NotFoundError:
        check(True, "cross-tenant read -> 404 NotFound (no leak) on PostgreSQL")

    try:
        core.create_booking(conn, aA, custB, "Crane", "x", 1); check(False, "cross-tenant relationship must be denied")
    except core.ForbiddenError:
        check(True, "cross-tenant relationship -> Forbidden on PostgreSQL")

    # financial sanity on PG: subtotal 300000*2=600000, +12% VAT = 672000
    core.review_booking(conn, aA, bkA); core.ready_for_quotation(conn, aA, bkA)
    est = actor_role(conn, tA, "estimator", "est@haula")
    q = core.create_quotation(conn, est, bkA, [{"kind": "crane", "description": "350t", "qty": 2, "days": 1, "rate": 300000}], est_cost=200000)
    qrow = conn.execute("SELECT subtotal,tax,total FROM quotations WHERE id=?", (q,)).fetchone()
    check(qrow["subtotal"] == 600000 and qrow["total"] == 672000, "quotation financial math correct on PostgreSQL")

    # restart persistence: reconnect to the same PostgreSQL database
    conn2 = db.connect(url)
    check(core.get_booking(conn2, aA, bkA)["id"] == bkA, "booking survives reconnect (persistence)")
    try:
        core.get_booking(conn2, aA, bkB); check(False, "isolation must hold after reconnect")
    except core.NotFoundError:
        check(True, "tenant isolation holds after reconnect")

    # Item 5 — expiring platform cross-access on PostgreSQL
    plat = actor_role(conn, tA, "admin", "plat@ci")
    plat["perms"] = {"*"}
    g = tenant.activate_cross_access(conn, plat, "HAULB", "CI audit", ttl=60)
    check(tenant.active_cross_grant(conn, plat["id"]) is not None, "cross-access grant active on PostgreSQL")
    conn.execute("UPDATE cross_access_grants SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (g["grant_id"],))
    conn.commit()
    check(tenant.active_cross_grant(conn, plat["id"]) is None, "cross-access denied after expiry on PostgreSQL")

    # Item 6 — admin guardrail: self-elevation blocked on PostgreSQL
    weak = {"id": plat["id"], "role": "x", "perms": {"customer.view"}}
    appr = ap.role_by_code(conn, "RGO", "approver")
    try:
        ap.assign_role(conn, uB, appr["id"], actor=weak); check(False, "self-elevation must be blocked")
    except core.ForbiddenError:
        check(True, "self-elevation blocked on PostgreSQL")

    # Item 1/4 — report counts are tenant-scoped on PostgreSQL (no cross-tenant aggregate)
    import ops
    conn.execute("UPDATE bookings SET stage='QUOTATION_ACCEPTED' WHERE id IN (?,?)", (bkA, bkB))
    conn.commit()
    check(ops.report_accepted_awaiting_payment(conn, aA) == 1, "report count scoped to tenant A on PostgreSQL")
    check(ops.report_accepted_awaiting_payment(conn, aB) == 1, "report count scoped to tenant B on PostgreSQL")

    # Item 6 — role clone copies grants on PostgreSQL
    rid = ap.clone_role(conn, "RGO", "approver", "approver_ci", "Approver CI", actor=plat)
    check(ap.effective_role_grants(conn, rid) == ap.effective_role_grants(conn, ap.role_by_code(conn, "RGO", "approver")["id"]),
          "role clone copies grants on PostgreSQL")

    # Item 1/6 — role comparison + SoD detection + config preview on PostgreSQL
    cmp = ap.compare_roles(conn, "RGO", "estimator", "approver")
    check("quotation.create" in cmp["only_a"] and "quotation.approve" in cmp["only_b"],
          "role comparison accurate on PostgreSQL")
    check(len(cmp["sod_conflicts"]) >= 1, "SoD conflict detected on PostgreSQL")
    su = core.create_user(conn, "sod@ci", "Demo1234Xy", "estimator", "S")
    ap.assign_role(conn, su, ap.role_by_code(conn, "RGO", "estimator")["id"])
    try:
        ap.assign_role(conn, su, ap.role_by_code(conn, "RGO", "approver")["id"])
        check(False, "SoD-conflicting assignment must be blocked")
    except core.ForbiddenError:
        check(True, "SoD-conflicting assignment blocked on PostgreSQL")
    import org
    pv = org.effective_config_preview(conn, "approval.quotation_threshold", "tenant", "RGO", "900000", tenant="RGO")
    before = conn.execute("SELECT value FROM platform_config WHERE key='approval.quotation_threshold' AND scope='platform'").fetchone()["value"]
    check(pv["proposed_effective"]["value"] == "900000", "config preview computes proposed value on PostgreSQL")
    after = conn.execute("SELECT value FROM platform_config WHERE key='approval.quotation_threshold' AND scope='platform'").fetchone()["value"]
    check(before == after, "config preview is non-mutating on PostgreSQL")

    # emit seed ids for the literal-browser E2E job
    core.create_user(conn, "admin@ci", "Demo1234Xy", "admin", "CI Admin")
    import json
    os.makedirs("ci", exist_ok=True)
    json.dump({"tA": tA, "tB": tB, "bkA": bkA, "bkB": bkB, "custA": custA, "custB": custB,
               "userA": "a@haula", "userB": "b@haulb", "admin": "admin@ci", "pw": "Demo1234Xy"},
              open("ci/seed_ids.json", "w"))

    if FAILED:
        print("\nPG VALIDATION FAILED:", len(FAILED), "checks", flush=True)
        sys.exit(1)
    print("\nPG VALIDATION PASSED", flush=True)


def actor_role(conn, tid, role, email):
    uid = core.create_user(conn, email, "Demo1234Xy", role, role)
    tenant.bind_user_tenant(conn, None, uid, tid)
    a = core.actor_for(conn, core.login(conn, email, "Demo1234Xy"))
    ap.apply_rbac(conn, a)
    return a


if __name__ == "__main__":
    main()
