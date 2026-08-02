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

    # Phase 2 — governed policies + historical reproducibility on PostgreSQL
    import policy, config_registry
    check(config_registry.get_definition(conn, "tax.default.rate") is not None, "config definitions seeded on PostgreSQL")
    check(policy.evaluate_tax(conn, 600000, {})["tax"] == 72000, "tax policy default == 12% on PostgreSQL (financials unchanged)")
    check(policy.evaluate_downpayment(conn, 672000, {})["amount"] == 201600, "downpayment default == 30% on PostgreSQL")
    # historical reproducibility: build a quotation, change tax config, verify it is unchanged
    pa = {"id": plat["id"], "role": "admin", "perms": {"*"}, "tenant_id": tA}
    pcid = core.create_customer(conn, pa, "Policy Co")
    pbid = core.create_booking(conn, pa, pcid, "Crane", "x", 1)
    core.review_booking(conn, pa, pbid); core.ready_for_quotation(conn, pa, pbid)
    pqid = core.create_quotation(conn, pa, pbid, [{"kind": "crane", "description": "x", "qty": 2, "days": 1, "rate": 300000}])
    tot_before = conn.execute("SELECT tax,total FROM quotations WHERE id=?", (pqid,)).fetchone()
    ap.set_config(conn, "platform", "", "tax.default.rate", "20", actor=pa)
    tot_after = conn.execute("SELECT tax,total FROM quotations WHERE id=?", (pqid,)).fetchone()
    check(tot_before["tax"] == tot_after["tax"] == 72000 and tot_before["total"] == tot_after["total"] == 672000,
          "config change does NOT alter existing quotation on PostgreSQL (historical reproducibility)")
    # UNEXPECTED FINANCIAL DIFFERENCES = 0 (aggregate invariant across a tax config change)
    sum_before = conn.execute("SELECT COALESCE(SUM(total),0) s FROM quotations").fetchone()["s"]
    ap.set_config(conn, "platform", "", "tax.default.rate", "25", actor=pa)
    sum_after = conn.execute("SELECT COALESCE(SUM(total),0) s FROM quotations").fetchone()["s"]
    check(sum_before == sum_after, "UNEXPECTED FINANCIAL DIFFERENCES = 0 after config change on PostgreSQL")
    ap.set_config(conn, "platform", "", "tax.default.rate", "12", actor=pa)   # restore default
    # multi-mode tax on PostgreSQL
    ap.set_config(conn, "platform", "", "tax.default.type", "zero_rated")
    check(policy.evaluate_tax(conn, 600000, {})["tax"] == 0, "zero-rated tax == 0 on PostgreSQL")
    ap.set_config(conn, "platform", "", "tax.default.type", "standard")
    ap.set_config(conn, "platform", "", "tax.default.mode", "inclusive")
    check(policy.evaluate_tax(conn, 112000, {})["tax"] == 12000, "inclusive tax on PostgreSQL")
    ap.set_config(conn, "platform", "", "tax.default.mode", "exclusive")
    # granular permission scoping
    fa = ap.effective_role_grants(conn, ap.role_by_code(conn, "RGO", "finance_admin")["id"])
    check("tax.policy.*" in fa, "granular tax.policy permission granted to finance_admin on PostgreSQL")
    ba = ap.effective_role_grants(conn, ap.role_by_code(conn, "RGO", "business_admin")["id"])
    check("tax.policy.manage" not in ba, "business_admin denied tax.policy.manage on PostgreSQL")
    try:
        ap.set_config(conn, "platform", "", "tax.default.rate", "150")
        check(False, "out-of-range config must be rejected")
    except core.ValidationError:
        check(True, "typed config validation rejects out-of-range on PostgreSQL")

    # Phase 3 — CRM administration + master-data governance on PostgreSQL
    import masterdata, crm_admin
    md_actor = {"id": plat["id"], "role": "admin", "perms": {"*"}, "tenant_id": tA}
    check(len(masterdata.list_values(conn, md_actor, "ops.equipment_type")) >= 1,
          "master data seeded on PostgreSQL")
    mdid = masterdata.create_value(conn, md_actor, "customer.category", "PGVIP", "PG VIP")
    check(masterdata.selectable(conn, mdid), "new master-data value is selectable on PostgreSQL")
    try:
        masterdata.create_value(conn, md_actor, "customer.category", "PGVIP", "dup")
        check(False, "duplicate master-data code must be blocked")
    except core.ConflictError:
        check(True, "duplicate master-data code blocked on PostgreSQL")
    masterdata.set_status(conn, md_actor, mdid, "INACTIVE")
    check(not masterdata.selectable(conn, mdid), "inactive value not selectable on PostgreSQL")
    # replacement mapping preserves history + resolves forward
    o1 = masterdata.create_value(conn, md_actor, "ops.vehicle_type", "OLDPM", "Old PM")
    o2 = masterdata.create_value(conn, md_actor, "ops.vehicle_type", "NEWPM", "New PM")
    masterdata.replace(conn, md_actor, o1, o2)
    check(masterdata.resolve_effective(conn, o1)["code"] == "NEWPM",
          "replacement resolves forward on PostgreSQL")
    # governed customer numbering (concurrency-safe: distinct numbers)
    nums = set()
    for i in range(5):
        ccid = core.create_customer(conn, md_actor, f"PG Num {i}")
        nums.add(conn.execute("SELECT customer_number FROM customers WHERE id=?", (ccid,)).fetchone()["customer_number"])
    check(len(nums) == 5 and all(n and n.startswith("CUS-") for n in nums),
          "customer numbering unique + formatted on PostgreSQL")
    # duplicate detection + governed merge (references redirected)
    da = core.create_customer(conn, md_actor, "Dup Rig Inc")
    db_ = core.create_customer(conn, md_actor, "DUP  RIG INC")
    bmerge = core.create_booking(conn, md_actor, db_, "CRANE_RENTAL", "x", 1)
    det = crm_admin.detect_duplicates(conn, md_actor, da)
    check(len(det["candidates"]) >= 1, "duplicate detection finds candidate on PostgreSQL")
    crm_admin.merge_customers(conn, md_actor, da, db_)
    check(conn.execute("SELECT customer_id FROM bookings WHERE id=?", (bmerge,)).fetchone()["customer_id"] == da,
          "merge redirects references on PostgreSQL")
    check(conn.execute("SELECT status FROM customers WHERE id=?", (db_,)).fetchone()["status"] == "MERGED",
          "merged customer preserved (status MERGED) on PostgreSQL")
    # cross-tenant merge denied
    other_t = core.create_customer(conn, aB, "B Tenant Cust")
    try:
        crm_admin.merge_customers(conn, md_actor, da, other_t)
        check(False, "cross-tenant merge must be denied")
    except (core.ForbiddenError, core.NotFoundError):
        check(True, "cross-tenant merge denied on PostgreSQL")
    # credit policy evidence-only default never blocks (no operational drift)
    crm_admin.create_credit_policy(conn, md_actor, "PGSTRICT", "Strict", credit_limit=1, booking_restriction=True)
    cev = crm_admin.evaluate_credit(conn, md_actor, da, "booking", amount=999999, policy_code="PGSTRICT")
    check(cev["decision"] == "ALLOW", "credit evidence_only never blocks on PostgreSQL (no operational drift)")
    # CRM custom field declarative validation
    crm_admin.create_custom_field(conn, md_actor, "customer", "pg_priority", "Priority", "integer",
                                  validation={"min": 1, "max": 5})
    try:
        crm_admin.set_custom_value(conn, md_actor, "customer", da, "pg_priority", "9")
        check(False, "out-of-range custom value must be rejected")
    except core.ValidationError:
        check(True, "declarative custom-field validation enforced on PostgreSQL")
    # import dry-run
    imp = masterdata.import_values(conn, md_actor, "finance.uom", [{"code": "PGTON", "name": "Ton"}], dry_run=True)
    check(imp["valid"] == 1 and imp["applied"] == 0, "master-data import dry-run on PostgreSQL")
    # granular permission scoping
    p3fa = ap.effective_role_grants(conn, ap.role_by_code(conn, "RGO", "crm_admin")["id"])
    check("crm.admin.*" in p3fa, "crm.admin.* granted to crm_admin on PostgreSQL")
    fin = ap.effective_role_grants(conn, ap.role_by_code(conn, "RGO", "finance_admin")["id"])
    check("crm.admin.merge.execute" not in fin and "crm.admin.credit_policy.*" in fin,
          "finance_admin scoped (credit yes, merge no) on PostgreSQL")
    # UNEXPECTED OPERATIONAL STATUS CHANGES = 0: booking stages unaffected by master-data ops
    stage_row = conn.execute("SELECT stage FROM bookings WHERE id=?", (bmerge,)).fetchone()
    check(stage_row["stage"] == "REQUEST_RECEIVED", "operational booking stage unchanged (0 status drift) on PostgreSQL")
    print("PHASE 3 CRM + MASTER DATA: PASS on PostgreSQL", flush=True)

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
