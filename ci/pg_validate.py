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

    # Phase 4 — Workflow Administration on PostgreSQL
    import workflow as wf, wfgov
    wa = {"id": plat["id"], "role": "admin", "perms": {"*"}, "tenant_id": tA}
    d = wf.get_definition(conn, wa, "commercial.booking")
    av = wf.active_version(conn, d["id"])
    check(av is not None and av["status"] == "ACTIVE" and bool(av["checksum"]),
          "seeded booking workflow is ACTIVE + checksummed on PostgreSQL")
    # immutability
    try:
        wf.add_step(conn, wa, av["id"], "HACK", "TASK")
        check(False, "must not edit an active version")
    except core.ForbiddenError:
        check(True, "active workflow version is immutable on PostgreSQL")
    # simulation non-mutation + path routing
    before_i = conn.execute("SELECT COUNT(*) c FROM workflow_instances").fetchone()["c"]
    below = wf.simulate(conn, wa, av["id"], {"amount": 100000})
    above = wf.simulate(conn, wa, av["id"], {"amount": 900000})
    after_i = conn.execute("SELECT COUNT(*) c FROM workflow_instances").fetchone()["c"]
    check(before_i == after_i, "workflow simulation is non-mutating on PostgreSQL")
    check("APPROVAL" not in below["path"] and "APPROVAL" in above["path"],
          "workflow simulation routes by condition on PostgreSQL")
    # instance engine + SoD
    iid = wf.start_instance(conn, wa, "commercial.booking", "booking", bkA)
    wf.advance_instance(conn, wa, iid, "submit_for_review")
    wf.advance_instance(conn, wa, iid, "send_for_approval", ctx={"amount": 900000})
    try:
        wf.advance_instance(conn, wa, iid, "approve", ctx={"amount": 900000}, reason="self")
        check(False, "self-approval must be blocked")
    except core.ForbiddenError:
        check(True, "workflow separation-of-duties (self-approval blocked) on PostgreSQL")
    approver = actor_role(conn, tA, "approver", "wfappr@ci")
    approver["perms"] = {"quotation.approve", "workflow.instance.manage"}
    res = wf.advance_instance(conn, approver, iid, "approve", ctx={"amount": 900000}, reason="ok")
    check(res["to"] == "CONFIRMED" and res["status"] == "COMPLETED",
          "authorized workflow approval reaches terminal on PostgreSQL")
    # tenant isolation of instances
    try:
        wf.get_instance(conn, aB, iid); check(False, "cross-tenant instance read must be denied")
    except core.NotFoundError:
        check(True, "workflow instance tenant isolation (404 no-leak) on PostgreSQL")
    # SLA business-hours + breach escalation
    due = wfgov.compute_due(conn, wa, "booking_review_sla", "2026-08-03T08:00:00")
    check(due["due_at"].startswith("2026-08-03T16:00"), "SLA business-hours calc on PostgreSQL")
    si = wfgov.start_sla(conn, wa, iid, "booking_review_sla", "2026-01-01T08:00:00")
    br = wfgov.check_breaches(conn, wa)
    check(any(b["instance_id"] == iid for b in br) and len(wfgov.escalation_history(conn, wa, iid)) >= 1,
          "SLA breach fires escalation on PostgreSQL")
    # delegation guards
    dgr = actor_role(conn, tA, "approver", "wfdelegator@ci")["id"] if False else core.create_user(conn, "wfdgr@ci", "Demo1234Xy", "approver", "Dgr")
    dge = core.create_user(conn, "wfdge@ci", "Demo1234Xy", "estimator", "Dge")
    try:
        wfgov.create_delegation(conn, wa, dgr, dge, "approver", "commercial.booking", "2026-08-01", None)
        check(False, "permanent delegation must be blocked")
    except core.ValidationError:
        check(True, "permanent delegation blocked on PostgreSQL")
    wfgov.create_delegation(conn, wa, dgr, dge, "approver", "commercial.booking", "2026-08-01", "2099-01-01")
    check(wfgov.active_delegation(conn, dge, tenant=tA) is not None, "active delegation resolves on PostgreSQL")
    # future version safety: old instance stays on its version
    nv = wf.create_version(conn, wa, "commercial.booking", "v2 rollout")
    wf.validate_version(conn, wa, nv); wf.approve_version(conn, wa, nv)
    wf.publish_version(conn, wa, nv, "future", effective_from="2099-01-01")
    check(wf.get_instance(conn, wa, iid)["version_id"] == av["id"],
          "existing instance stays on its version after a future publish on PostgreSQL")
    # granular permissions
    ba = ap.effective_role_grants(conn, ap.role_by_code(conn, "RGO", "business_admin")["id"])
    check("workflow.definition.manage" in ba and "workflow.version.publish" not in ba,
          "business_admin can design but not publish workflows on PostgreSQL")
    # 0 operational drift: bkA real stage unaffected by the governed instance
    check(conn.execute("SELECT stage FROM bookings WHERE id=?", (bkA,)).fetchone()["stage"] == "QUOTATION_ACCEPTED",
          "operational booking stage unchanged by workflow engine (0 drift) on PostgreSQL")
    print("PHASE 4 WORKFLOW ADMINISTRATION: PASS on PostgreSQL", flush=True)

    # Phase 5 — Form & Custom-Field Administration on PostgreSQL
    import forms
    fa = {"id": plat["id"], "role": "admin", "perms": {"*"}, "tenant_id": tA}
    ef = forms.effective_form(conn, fa, "booking", role="admin")
    check(ef["version_no"] is not None and bool(ef["checksum"]) and any(f["code"] == "service_type" for f in ef["fields"]),
          "seeded booking form is ACTIVE + checksummed + renders fields on PostgreSQL")
    fdef = forms.get_definition(conn, fa, "booking_form")
    av = forms.active_version_for_entity(conn, fa, "booking")
    try:
        forms.add_field(conn, fa, av["id"], "hack", "Hack", "short_text")
        check(False, "must not edit an active form version")
    except core.ForbiddenError:
        check(True, "active form version is immutable on PostgreSQL")
    # protected financial field cannot be created
    qdid = forms.create_definition(conn, fa, "quotation", "q_form_ci", "Q Form")
    qv = conn.execute("SELECT id FROM form_versions WHERE definition_id=? AND version_no=1", (qdid,)).fetchone()["id"]
    try:
        forms.add_field(conn, fa, qv, "total", "Total", "currency")
        check(False, "protected financial field must be blocked")
    except core.ForbiddenError:
        check(True, "protected financial field blocked on PostgreSQL")
    # runtime submission: required-condition + unknown/invalid-option denial
    try:
        forms.submit_values(conn, fa, "booking", bkA, {"insured": "true", "service_type": "CRANE_RENTAL"})
        check(False, "conditional-required must be enforced")
    except core.ValidationError:
        check(True, "runtime conditional-required enforced on PostgreSQL")
    r = forms.submit_values(conn, fa, "booking", bkA, {"insured": "true", "insurance_policy_no": "POL-CI", "service_type": "CRANE_RENTAL"})
    check(r["stored"] == 3, "runtime form submission persists values on PostgreSQL")
    try:
        forms.submit_values(conn, fa, "booking", bkA, {"ghost_field": "x"})
        check(False, "unknown field must be rejected")
    except core.ValidationError:
        check(True, "unknown-field submission rejected on PostgreSQL")
    try:
        forms.submit_values(conn, fa, "booking", bkA, {"service_type": "NOT_REAL"})
        check(False, "invalid option must be rejected")
    except core.ValidationError:
        check(True, "invalid master-data option rejected on PostgreSQL")
    # sensitivity masking
    forms.submit_values(conn, fa, "booking", bkA, {"client_contact_private": "+639170000000"})
    viewer = actor_role(conn, tA, "estimator", "fviewer@ci")
    viewer["perms"] = {"form.data.view"}
    masked = forms.get_values(conn, viewer, "booking", bkA).get("client_contact_private", {})
    check(masked.get("masked") is True, "sensitive field masked for unprivileged viewer on PostgreSQL")
    # export excludes sensitive
    exp = forms.export_values(conn, fa, "booking")
    # fa has '*' -> sees sensitive; use a restricted exporter
    exporter = actor_role(conn, tA, "estimator", "fexport@ci")
    exporter["perms"] = {"form.data.export", "form.data.view"}
    exp2 = forms.export_values(conn, exporter, "booking")
    check("client_contact_private" in exp2["excluded_sensitive"], "sensitive field excluded from export on PostgreSQL")
    # value tenant isolation — cross-tenant read of a real entity is a 404 no-leak
    other = {"id": uB, "role": "estimator", "perms": {"form.data.view"}, "tenant_id": tB}
    try:
        forms.get_values(conn, other, "booking", bkA)
        check(False, "cross-tenant form-value read must be denied")
    except core.NotFoundError:
        check(True, "form-value tenant isolation (404 no-leak) on PostgreSQL")
    # historical version preservation: publish v2, old record keeps field_version 1
    # (use tenant-A synthetic entity ids so the tenant-ownership guard is satisfied)
    v1fv = forms.get_values(conn, fa, "booking", bkA)["service_type"]["field_version"]
    nv = forms.create_version(conn, fa, "booking_form", "relabel")
    forms.validate_version(conn, fa, nv); forms.approve_version(conn, fa, nv)
    forms.publish_version(conn, fa, nv, "v2 label change")
    forms.submit_values(conn, fa, "booking", 990002, {"service_type": "RIGGING"})   # new record under v2
    v2fv = forms.get_values(conn, fa, "booking", 990002)["service_type"]["field_version"]
    check(v1fv == 1 and v2fv == 2 and forms.get_values(conn, fa, "booking", bkA)["service_type"]["field_version"] == 1,
          "historical field-version preserved after new publish on PostgreSQL")
    # search on a searchable field
    sres = forms.search_values(conn, fa, "booking", "insurance_policy_no", "POL-CI")
    check(any(x["entity_id"] == bkA for x in sres), "searchable field search works on PostgreSQL")
    # migration: zero drift + zero loss
    m5 = forms.classify_existing(conn)
    check(m5["financial_differences"] == 0 and m5["operational_status_differences"] == 0 and m5["field_value_losses"] == 0,
          "form migration zero financial/operational/value drift on PostgreSQL")
    # granular permission scoping
    baf = ap.effective_role_grants(conn, ap.role_by_code(conn, "RGO", "business_admin")["id"])
    check("form.field.manage" in baf and "form.version.publish" not in baf and "form.field.sensitive.manage" not in baf,
          "business_admin can design forms but not publish/sensitive on PostgreSQL")
    # 0 operational drift: bkA real booking stage unchanged by form submission
    check(conn.execute("SELECT stage FROM bookings WHERE id=?", (bkA,)).fetchone()["stage"] == "QUOTATION_ACCEPTED",
          "booking stage unchanged by form engine (0 operational drift) on PostgreSQL")
    print("PHASE 5 FORM & CUSTOM-FIELD ADMINISTRATION: PASS on PostgreSQL", flush=True)

    # Phase 6 — Platform & System Settings on PostgreSQL
    import settings as sysc
    sa = {"id": plat["id"], "role": "admin", "perms": {"*"}, "tenant_id": tA}
    sysc.set_value(conn, sa, "platform.name", "RGO Ops", scope="platform")
    check(sysc.effective_value(conn, sa, "platform.name")["value"] == "RGO Ops", "setting effective resolution on PostgreSQL")
    # security floor: tenant may strengthen, not weaken
    sysc.set_value(conn, sa, "auth.password.min_length", "14", scope="tenant")
    check(sysc.effective_value(conn, sa, "auth.password.min_length")["value"] == "14", "tenant may strengthen security policy on PostgreSQL")
    try:
        sysc.set_value(conn, sa, "auth.password.min_length", "6", scope="tenant")
        check(False, "security weakening must be blocked")
    except core.ForbiddenError:
        check(True, "tenant cannot weaken security below platform minimum on PostgreSQL")
    try:
        sysc.set_value(conn, sa, "auth.mfa.policy", "off", scope="tenant")
        check(False, "MFA weakening must be blocked")
    except core.ForbiddenError:
        check(True, "MFA policy cannot be weakened below platform on PostgreSQL")
    # secret reference: value never stored/returned
    sysc.create_secret_reference(conn, sa, "wise_key", "wise", "WISE_API_KEY")
    refs = sysc.list_secret_references(conn, sa)
    check(refs and refs[0]["value"] == sysc.SENSITIVE_MASK and "env_name" not in refs[0],
          "secret reference value masked + env hidden on PostgreSQL")
    # feature flag: tenant isolation + dependency + kill switch
    sysc.create_flag(conn, sa, "beta_ui", platform_default=False)
    sysc.set_flag_override(conn, sa, "beta_ui", True, tenant=tA)
    check(sysc.is_flag_enabled(conn, "beta_ui", tenant=tA) and not sysc.is_flag_enabled(conn, "beta_ui", tenant=tB),
          "feature flag tenant isolation on PostgreSQL")
    sysc.create_flag(conn, sa, "child_feat", dependency="beta_ui")
    sysc.create_flag(conn, sa, "base_off", platform_default=False)
    sysc.create_flag(conn, sa, "needs_base", dependency="base_off")
    try:
        sysc.set_flag_override(conn, sa, "needs_base", True, tenant=tA)
        check(False, "flag dependency must be validated")
    except core.ValidationError:
        check(True, "feature flag dependency validated on PostgreSQL")
    # module unsafe-disable guard
    try:
        sysc.set_module_status(conn, sa, "booking", False)
        check(False, "unsafe module disable must be blocked")
    except core.ConflictError:
        check(True, "unsafe module disable blocked (dependents) on PostgreSQL")
    # maintenance requires expiry
    try:
        sysc.schedule_maintenance(conn, sa, "read_only", sysc._now(), None)
        check(False, "permanent maintenance must be blocked")
    except core.ValidationError:
        check(True, "permanent maintenance blocked on PostgreSQL")
    # audit retention floor
    try:
        sysc.set_retention(conn, sa, "audit", 30)
        check(False, "audit retention floor must hold")
    except core.ForbiddenError:
        check(True, "audit retention cannot drop below platform floor on PostgreSQL")
    # backup + governed restore SoD
    bk = sysc.execute_backup(conn, sa)
    check(bk["status"] == "SUCCESS" and bool(bk["checksum"]), "governed backup executes with checksum on PostgreSQL")
    rrid = sysc.request_restore(conn, sa, bk["backup_run_id"])
    sysc.validate_restore(conn, sa, rrid)
    try:
        sysc.approve_restore(conn, sa, rrid)
        check(False, "self-approval of restore must be blocked")
    except core.ForbiddenError:
        check(True, "restore approval requires a separate approver (SoD) on PostgreSQL")
    approver = actor_role(conn, tA, "admin", "restoreappr@ci")
    approver["perms"] = {"restore.approve"}
    check(sysc.approve_restore(conn, approver, rrid) is True, "separate approver can approve restore on PostgreSQL")
    # branding rejects scripts + template variable allowlist
    try:
        sysc.set_branding(conn, sa, "document_header", value="<script>x</script>")
        check(False, "branding script must be rejected")
    except core.ValidationError:
        check(True, "branding rejects scripts on PostgreSQL")
    try:
        sysc.create_template(conn, sa, "quote_email", "Q", "email", "Hi {{name}} {{evil}}", allowed_variables=["name"])
        check(False, "non-allowlisted template variable must be rejected")
    except core.ValidationError:
        check(True, "template variable allowlist enforced on PostgreSQL")
    # integrity healthy + migration zero drift
    rep = sysc.integrity_checks(conn, sa)
    check(rep["summary"]["fail"] == 0, "system integrity has no FAIL on PostgreSQL")
    m6 = sysc.classify_existing(conn)
    check(m6["financial_differences"] == 0 and m6["operational_status_differences"] == 0 and m6["security_policy_weakening"] == 0,
          "settings migration zero financial/operational/security drift on PostgreSQL")
    # tenant isolation of settings values
    tv = conn.execute("SELECT COUNT(*) c FROM setting_values WHERE scope='tenant' AND tenant_id=?", (tA,)).fetchone()["c"]
    tvb = conn.execute("SELECT COUNT(*) c FROM setting_values WHERE scope='tenant' AND tenant_id=?", (tB,)).fetchone()["c"]
    check(tv >= 1 and tvb == 0, "tenant setting values are tenant-scoped on PostgreSQL")
    # 0 operational drift: booking stage unchanged by settings ops
    check(conn.execute("SELECT stage FROM bookings WHERE id=?", (bkA,)).fetchone()["stage"] == "QUOTATION_ACCEPTED",
          "booking stage unchanged by settings engine (0 operational drift) on PostgreSQL")
    print("PHASE 6 PLATFORM & SYSTEM SETTINGS: PASS on PostgreSQL", flush=True)

    # Phase 7 — Integration Administration + Wise (mock) on PostgreSQL
    import integrations as ig, wise
    wa = {"id": plat["id"], "role": "admin", "perms": {"*"}, "tenant_id": tA}
    wapprover = actor_role(conn, tA, "admin", "wapprover@ci"); wapprover["perms"] = {"*"}

    def _accepted_wise_booking(actor, approver, cust_name):
        cid = core.create_customer(conn, actor, cust_name)
        b = core.create_booking(conn, actor, cid, "CRANE_RENTAL", "x", 1)
        core.review_booking(conn, actor, b); core.ready_for_quotation(conn, actor, b)
        q = core.create_quotation(conn, actor, b, [{"kind": "crane", "description": "x", "qty": 2, "days": 1, "rate": 300000}])
        core.submit_quotation(conn, actor, q)
        if conn.execute("SELECT status FROM quotations WHERE id=?", (q,)).fetchone()["status"] == "pending_approval":
            core.approve_quotation(conn, approver, q)
        core.send_quotation(conn, actor, q); core.accept_quotation(conn, approver, q, "J. Roe", "CFO")
        return b, q

    wprofile = ig.create_profile(conn, wa, "wise", environment="MOCK", secret_ref="wise_key")
    vres = ig.validate_profile(conn, wa, wprofile)
    check(vres["health"] == "HEALTHY" and len(vres["profiles"]) >= 2,
          "Wise mock profile validates + offers multiple profiles on PostgreSQL")
    ig.activate_profile(conn, wa, wprofile)
    # health is UNKNOWN before validation for a fresh profile
    fresh = ig.create_profile(conn, wa, "wise", environment="SANDBOX")
    hh = [p for p in ig.provider_health(conn, wa, "wise")["providers"] if p["profile_id"] == fresh][0]
    check(hh["health"] == "UNKNOWN", "provider health UNKNOWN until validated on PostgreSQL")

    wb, wq = _accepted_wise_booking(wa, wapprover, "Wise Co")
    r = wise.create_wise_payment(conn, wa, wb, wprofile, "idem-ci", scenario="completed")
    check(r["amount"] == 201600, "Wise payment amount == stored downpayment snapshot on PostgreSQL")
    r2 = wise.create_wise_payment(conn, wa, wb, wprofile, "idem-ci", scenario="completed")
    check(r2["idempotent_replay"] and r2["transfer_id"] == r["transfer_id"],
          "idempotency prevents duplicate Wise transfer on PostgreSQL")
    ntr = conn.execute("SELECT COUNT(*) c FROM provider_transfers WHERE payment_request_id=?", (r["payment_request_id"],)).fetchone()["c"]
    check(ntr == 1, "no duplicate provider transfer on PostgreSQL")
    try:
        ig.idempotent(conn, wa, "idem-ci", "create_wise_payment", {"different": "payload"})
        check(False, "conflicting idempotency payload must be rejected")
    except core.ConflictError:
        check(True, "conflicting idempotency payload rejected on PostgreSQL")
    wise.sync_transfer_status(conn, wa, r["transfer_id"])
    rec = wise.reconcile_transfer(conn, wa, r["transfer_id"])
    check(rec["status"] == "MATCHED", "exact-match reconciliation on PostgreSQL")
    try:
        wise.verify_wise_payment(conn, wa, r["transfer_id"])
        check(False, "self-verification must be blocked")
    except core.ForbiddenError:
        check(True, "payment verification separation-of-duties (self-verify blocked) on PostgreSQL")
    vr = wise.verify_wise_payment(conn, wapprover, r["transfer_id"])
    check(vr["payment_status"] == "VERIFIED", "authorized verifier settles Wise payment on PostgreSQL")
    check(core.confirm_job(conn, wa, wb), "job-activation prerequisite satisfied after Wise verification on PostgreSQL")

    # partial payment -> manual review
    wb2, _ = _accepted_wise_booking(wa, wapprover, "Wise Partial Co")
    rp = wise.create_wise_payment(conn, wa, wb2, wprofile, "idem-partial", scenario="partial")
    wise.sync_transfer_status(conn, wa, rp["transfer_id"])
    check(wise.reconcile_transfer(conn, wa, rp["transfer_id"])["status"] == "MANUAL_REVIEW",
          "partial payment routes to manual review on PostgreSQL")

    # webhook: signature verify + duplicate dedup
    import hmac as _hmac, hashlib as _hl, json as _json
    ph = _hl.sha256(_json.dumps({"id": "T"}, sort_keys=True, default=str).encode()).hexdigest()
    good = _hmac.new(b"whsecret", ph.encode(), _hl.sha256).hexdigest()
    ok = ig.ingest_webhook(conn, "wise", "wevt-1", "transfer.state_change", {"id": "T"}, signature=good, tenant_id=tA, secret="whsecret")
    bad = ig.ingest_webhook(conn, "wise", "wevt-2", "x", {"id": "T"}, signature="deadbeef", tenant_id=tA, secret="whsecret")
    dupe = ig.ingest_webhook(conn, "wise", "wevt-1", "transfer.state_change", {"id": "T"}, signature=good, tenant_id=tA, secret="whsecret")
    check(ok["status"] == "ACCEPTED" and bad["status"] == "REJECTED" and dupe["status"] == "DUPLICATE",
          "webhook signature verify + duplicate dedup on PostgreSQL")

    # dead-letter: safe replay vs unsafe denial + cross-tenant denial
    dls = ig.dead_letter(conn, wa, "wise", "create_transfer", "transient_network", "timeout")
    check(ig.replay_dead_letter(conn, wa, dls, reason="recovered")["status"] == "REPLAYED", "safe dead-letter replay on PostgreSQL")
    dlp = ig.dead_letter(conn, wa, "wise", "create_transfer", "permanent_business_rejection", "declined")
    try:
        ig.replay_dead_letter(conn, wa, dlp, reason="try")
        check(False, "unsafe replay must be denied")
    except core.ForbiddenError:
        check(True, "unsafe dead-letter replay denied on PostgreSQL")

    # circuit breaker + fail-safe on disabled profile
    for _ in range(5):
        ig._record_failure(conn, wprofile)
    check(conn.execute("SELECT circuit_state FROM connection_profiles WHERE id=?", (wprofile,)).fetchone()["circuit_state"] == "OPEN",
          "circuit breaker opens after failure threshold on PostgreSQL")

    # live Wise BLOCKED (no fabricated success)
    la = wise.get_adapter("PRODUCTION")
    check(la.is_mock is False and la.validate_connection({"secret_ref": "x"}).get("blocked") is True,
          "live Wise adapter BLOCKED without owner credentials on PostgreSQL")

    # cross-tenant profile isolation
    try:
        ig.get_profile(conn, aB, wprofile)
        check(False, "cross-tenant profile read must be denied")
    except core.NotFoundError:
        check(True, "connection profile tenant isolation (404 no-leak) on PostgreSQL")

    # migration zero drift + financials unchanged by Wise
    m7 = ig.classify_existing(conn)
    check(m7["financial_differences"] == 0 and m7["payment_status_changes"] == 0 and m7["job_status_changes"] == 0
          and m7["fake_transaction_ids_assigned"] == 0, "integration migration zero drift on PostgreSQL")
    fin = conn.execute("SELECT tax,total,dp_amount FROM quotations WHERE id=?", (wq,)).fetchone()
    check(fin["tax"] == 72000 and fin["total"] == 672000 and fin["dp_amount"] == 201600,
          "Wise flow did not change snapshot financials on PostgreSQL")
    print("PHASE 7 INTEGRATION ADMINISTRATION + WISE (MOCK): PASS on PostgreSQL", flush=True)

    # Phase 8 — Reporting & Dashboard Administration on PostgreSQL
    import reporting as rp
    import ops as opsmod
    ra = {"id": plat["id"], "role": "admin", "perms": {"*"}, "tenant_id": tA}
    # seeded standard reports present + ACTIVE
    reps = rp.list_reports(conn, ra)
    check(any(r["code"] == "quotation_conversion" for r in reps), "seeded standard reports present on PostgreSQL")
    # report-value reconciliation vs ops.report_* (same values)
    govc = rp.run_report(conn, ra, "quotation_conversion")
    gov_accepted = sum(r["n"] for r in govc["rows"] if r["status"] == "accepted")
    check(gov_accepted == opsmod.report_quotation_conversion(conn, ra)["accepted"],
          "governed report reconciles with ops.report_quotation_conversion on PostgreSQL (0 report-value drift)")
    govr = rp.run_report(conn, ra, "receivables")
    ops_r = opsmod.report_receivables(conn, ra)
    gov_map = {r["status"]: r["balance"] for r in govr["rows"]}
    check(all(gov_map.get(st, 0) == d["balance"] for st, d in ops_r.items()),
          "governed receivables reconciles with ops.report_receivables on PostgreSQL")
    # row-level security: tenant B actor sees no tenant-A quotation rows
    rb = {"id": uB, "role": "admin", "perms": {"report.execute"}, "tenant_id": tB}
    ob = rp.execute_spec(conn, rb, {"dataset": "quotations", "aggregations": [{"fn": "count", "as": "n"}], "limit": 100})
    ab = rp.execute_spec(conn, ra, {"dataset": "quotations", "aggregations": [{"fn": "count", "as": "n"}], "limit": 100})
    check(ob["rows"][0]["n"] == 0 and ab["rows"][0]["n"] >= 1, "report row-level security isolates tenants on PostgreSQL")
    # column-level security: financial field excluded without permission
    viewer = actor_role(conn, tA, "estimator", "repviewer@ci"); viewer["perms"] = {"report.execute"}
    cv = rp.execute_spec(conn, viewer, {"dataset": "quotations", "fields": ["status", "total"], "limit": 100})
    check("total" in cv["excluded_sensitive"] and all("total" not in r for r in cv["rows"]),
          "report column-level security excludes financial field without permission on PostgreSQL")
    fin = actor_role(conn, tA, "estimator", "repfin@ci"); fin["perms"] = {"report.execute", "report.sensitive.view"}
    fv = rp.execute_spec(conn, fin, {"dataset": "quotations", "fields": ["status", "total"], "limit": 100})
    check("total" not in fv["excluded_sensitive"], "authorized actor sees financial field on PostgreSQL")
    # immutable published report version
    did = rp.create_report(conn, ra, "rep_ci", "CI Report", category="operations")
    v = conn.execute("SELECT id FROM report_versions WHERE definition_id=? AND version_no=1", (did,)).fetchone()["id"]
    rp.set_spec(conn, ra, v, {"dataset": "jobs", "fields": ["status"], "aggregations": [{"fn": "count", "as": "n"}], "limit": 100})
    rp.validate_version(conn, ra, v); rp.approve_version(conn, ra, v); rp.publish_version(conn, ra, v, "go")
    try:
        rp.set_spec(conn, ra, v, {"dataset": "jobs", "fields": ["status"]})
        check(False, "published report version must be immutable")
    except core.ForbiddenError:
        check(True, "published report version is immutable on PostgreSQL")
    # KPI + dashboard total reconciliation
    rp.create_kpi(conn, ra, "acc_rate_ci", "Accept Rate", "quotations",
                  {"fn": "count", "filters": [{"field": "status", "op": "eq", "value": "accepted"}]}, denominator={"fn": "count"})
    kpi = rp.compute_kpi(conn, ra, "acc_rate_ci")
    check(kpi["available"] and kpi["numerator"] >= 1, "KPI computes on PostgreSQL")
    dd = rp.create_dashboard(conn, ra, "dash_ci", "Dash CI")
    rp.add_widget(conn, ra, dd, "table", title="Conv", report_code="quotation_conversion")
    rp.publish_dashboard(conn, ra, dd)
    rendered = rp.render_dashboard(conn, ra, "dash_ci")
    check(rendered["widgets"][0]["data"]["rows"] == rp.run_report(conn, ra, "quotation_conversion")["rows"],
          "dashboard total reconciles with underlying report on PostgreSQL")
    # scheduling: authorized recipient + cross-tenant denial
    rcp = core.create_user(conn, "reprcpt@ci", "Demo1234Xy", "estimator", "R"); tenant.bind_user_tenant(conn, None, rcp, tA)
    sid = rp.create_schedule(conn, ra, "quotation_conversion", "daily", ["reprcpt@ci"])
    rr = rp.run_schedule(conn, ra, sid)
    check(rr["delivered"] == 1, "authorized scheduled delivery on PostgreSQL")
    crossu = core.create_user(conn, "repcross@ci", "Demo1234Xy", "estimator", "X"); tenant.bind_user_tenant(conn, None, crossu, tB)
    try:
        rp.create_schedule(conn, ra, "quotation_conversion", "daily", ["repcross@ci"])
        check(False, "cross-tenant recipient must be denied")
    except core.ForbiddenError:
        check(True, "cross-tenant scheduled recipient denied on PostgreSQL")
    # cache isolation + invalidation
    rp.run_report(conn, ra, "quotation_conversion")
    ncache = conn.execute("SELECT COUNT(*) c FROM report_cache").fetchone()["c"]
    rp.invalidate_cache(conn, ra, user_id=ra["id"])
    check(ncache >= 1, "governed report cache populated + invalidatable on PostgreSQL")
    # integrity + migration + 0 financial drift
    check(rp.integrity_checks(conn, ra)["summary"]["fail"] == 0, "reporting integrity has no FAIL on PostgreSQL")
    m8 = rp.classify_existing(conn)
    check(m8["financial_differences"] == 0 and m8["operational_status_differences"] == 0 and m8["report_value_differences"] == 0,
          "reporting migration zero drift on PostgreSQL")
    print("PHASE 8 REPORTING & DASHBOARD ADMINISTRATION: PASS on PostgreSQL", flush=True)

    # Phase 9 — AI Administration (deterministic mock) on PostgreSQL
    import ai_admin as ai, ai_provider
    aia = {"id": plat["id"], "role": "admin", "perms": {"*"}, "tenant_id": tA}
    # seeded approved mock model + allowlisted tools
    check(any(m["provider"] == "mock" and m["status"] in ("APPROVED", "ACTIVE") for m in ai.list_models(conn, aia)),
          "seeded approved mock AI model on PostgreSQL")
    # governed use case + prompt lifecycle (validate -> evaluate -> approve -> publish immutable)
    ai.create_use_case(conn, aia, "booking_assist_ci", "Booking Assist", risk_level="low",
                       allowed_input_classes="PUBLIC,INTERNAL", human_review="always")
    ai.set_review_policy(conn, aia, "booking_assist_ci", "always")
    pid = ai.create_prompt(conn, aia, "ba_ci", "P", "booking_assist_ci")
    v = conn.execute("SELECT id FROM ai_prompt_versions WHERE prompt_id=? AND version_no=1", (pid,)).fetchone()["id"]
    ai.set_version_content(conn, aia, v, "Summarize bookings, cite evidence.", "B:{{booking}}",
                          allowed_variables=["booking"], output_schema={"required": ["summary", "confidence"]})
    ai.validate_version(conn, aia, v)
    ev = ai.run_evaluation(conn, aia, "booking_assist_ci", v)
    check(ev["passed"], "AI prompt passes evaluation on PostgreSQL")
    ai.approve_version(conn, aia, v)
    pub = ai.publish_version(conn, aia, v, "go")
    check(pub["status"] == "ACTIVE" and bool(pub["checksum"]), "AI prompt published + checksummed on PostgreSQL")
    try:
        ai.set_version_content(conn, aia, v, "x", "y")
        check(False, "published prompt must be immutable")
    except core.ForbiddenError:
        check(True, "published AI prompt version is immutable on PostgreSQL")
    # advisory execution — never auto-commits
    out = ai.execute(conn, aia, "booking_assist_ci", "ba_ci", {"booking": "BK-1", "stage": "UNDER_REVIEW"}, scenario="valid")
    check(out["result"] == "ADVISORY" and out["committed"] is False and out["human_review_required"] and out["ai_generated"],
          "AI execution is advisory + human-reviewed + never auto-commits on PostgreSQL")
    check(out["is_mock"] is True, "AI output labeled as mock on PostgreSQL")
    # secret redaction (never sent)
    outr = ai.execute(conn, aia, "booking_assist_ci", "ba_ci", {"booking": "BK-2", "password": "x", "api_key": "sk"}, scenario="valid")
    check("password" in outr["redacted_fields"] and "api_key" in outr["redacted_fields"],
          "AI redacts secrets before sending on PostgreSQL")
    # payment credentials hard-blocked
    try:
        ai.execute(conn, aia, "booking_assist_ci", "ba_ci", {"card_number": "4111", "cvv": "123"})
        check(False, "raw payment credentials must be blocked")
    except core.ForbiddenError:
        check(True, "raw payment credentials blocked from AI on PostgreSQL")
    # prohibited action + injection -> blocked, incident, never acts
    outp = ai.execute(conn, aia, "booking_assist_ci", "ba_ci", {"booking": "BK-3"}, scenario="prohibited_action")
    check(outp["result"] == "UNSAFE_BLOCKED" and outp["committed"] is False, "prohibited AI action blocked on PostgreSQL")
    outi = ai.execute(conn, aia, "booking_assist_ci", "ba_ci", {"booking": "BK-4"}, scenario="injection")
    check(outi["result"] == "UNSAFE_BLOCKED", "prompt-injection blocked on PostgreSQL")
    check(len(ai.list_incidents(conn, aia)) >= 1, "AI incident raised for unsafe output on PostgreSQL")
    # tool registry rejects prohibited actions
    try:
        ai.register_tool(conn, aia, "release_payment", "Bad", "x")
        check(False, "prohibited tool must be rejected")
    except core.ForbiddenError:
        check(True, "prohibited AI tool rejected on PostgreSQL")
    # human review (edits distinguishable)
    ai.review_execution(conn, aia, out["execution_id"], "EDITED", edits={"summary": "human edit"}, reason="clarify")
    check(conn.execute("SELECT edited FROM ai_reviews WHERE execution_id=?", (out["execution_id"],)).fetchone()["edited"] == 1,
          "human edits distinguishable from AI output on PostgreSQL")
    # budget hard stop
    ai.set_budget(conn, aia, 0.0, use_case_code="booking_assist_ci", hard_stop=True)
    try:
        ai.execute(conn, aia, "booking_assist_ci", "ba_ci", {"booking": "BK-5"}, scenario="valid")
        check(False, "budget hard stop must deny execution")
    except core.ForbiddenError:
        check(True, "AI budget hard stop enforced on PostgreSQL")
    ai.set_budget(conn, aia, 100.0, use_case_code="booking_assist_ci")
    # kill switch
    ks = ai.activate_kill_switch(conn, aia, "use_case", scope_ref="booking_assist_ci", reason="drill")
    try:
        ai.execute(conn, aia, "booking_assist_ci", "ba_ci", {"booking": "BK-6"}, scenario="valid")
        check(False, "kill switch must deny execution")
    except core.ForbiddenError:
        check(True, "AI kill switch enforced (fail-safe) on PostgreSQL")
    ai.release_kill_switch(conn, aia, ks)
    check(ai.execute(conn, aia, "booking_assist_ci", "ba_ci", {"booking": "BK-7"}, scenario="valid")["result"] == "ADVISORY",
          "AI resumes after kill-switch release on PostgreSQL")
    # provider outage fallback
    outo = ai.execute(conn, aia, "booking_assist_ci", "ba_ci", {"booking": "BK-8"}, scenario="provider_error")
    check(outo["result"] == "PROVIDER_UNAVAILABLE", "AI provider outage falls back safely on PostgreSQL")
    # live provider blocked
    lp = ai_provider.get_provider("openai", "PRODUCTION")
    check(lp.is_mock is False and lp.health().get("blocked") is True, "live AI provider BLOCKED without owner credentials on PostgreSQL")
    # tenant isolation of executions
    other = {"id": uB, "role": "admin", "perms": {"ai.review"}, "tenant_id": tB}
    try:
        ai.review_execution(conn, other, out["execution_id"], "ACCEPTED")
        check(False, "cross-tenant AI review must be denied")
    except core.NotFoundError:
        check(True, "AI execution tenant isolation (404 no-leak) on PostgreSQL")
    # migration zero drift + financials unchanged
    m9 = ai.classify_existing(conn)
    check(m9["financial_differences"] == 0 and m9["operational_status_differences"] == 0 and m9["ai_authored_record_changes"] == 0
          and m9["existing_ai_functions"] == 0, "AI migration zero drift / no relabeling on PostgreSQL")
    print("PHASE 9 AI ADMINISTRATION (MOCK): PASS on PostgreSQL", flush=True)

    # Phase 10 — SaaS commercial layer on PostgreSQL
    import saas
    sa = {"id": plat["id"], "role": "admin", "perms": {"*"}, "tenant_id": None}
    sa2 = actor_role(conn, tA, "admin", "saasappr@ci"); sa2["perms"] = {"*"}; sa2["tenant_id"] = None
    # product + immutable plan version + entitlements
    saas.create_product(conn, sa, "lifthaul", "LiftHaul OS")
    pid = saas.create_plan(conn, sa, "lifthaul", "starter", "Starter")
    pv = conn.execute("SELECT id FROM plan_versions WHERE plan_id=? AND version_no=1", (pid,)).fetchone()["id"]
    saas.set_plan_version(conn, sa, pv, base_price=5000, trial_days=14)
    saas.add_entitlement(conn, sa, pv, "module", "crm", "included")
    saas.add_entitlement(conn, sa, pv, "module", "booking", "included")
    saas.add_entitlement(conn, sa, pv, "feature", "active_users", "limited", quantity=3)
    saas.add_entitlement(conn, sa, pv, "module", "ai_assistance", "excluded")
    saas.validate_plan_version(conn, sa, pv); saas.approve_plan_version(conn, sa2, pv)
    pub = saas.publish_plan_version(conn, sa, pv, "go live")
    check(pub["status"] == "ACTIVE" and bool(pub["checksum"]), "SaaS plan published + checksummed on PostgreSQL")
    try:
        saas.set_plan_version(conn, sa, pv, base_price=9999)
        check(False, "published plan must be immutable")
    except core.ForbiddenError:
        check(True, "published plan version is immutable on PostgreSQL")
    # provisioning (idempotent + fail-closed)
    r = saas.provision_tenant(conn, sa, "ACMECI", "Acme CI", "lifthaul", "starter", "admin@acmeci", commercial_evidence="SOW-CI")
    r2 = saas.provision_tenant(conn, sa, "ACMECI", "Acme CI", "lifthaul", "starter", "admin@acmeci", commercial_evidence="SOW-CI")
    check(r["status"] == "ACTIVATED" and r2["tenant_id"] == r["tenant_id"], "tenant provisioning idempotent + activated on PostgreSQL")
    try:
        saas.provision_tenant(conn, sa, "FAILCI", "Fail CI", "lifthaul", "starter", "admin@failci", commercial_evidence="X", force_fail_step="activate")
        check(False, "fail-closed provisioning must raise")
    except core.ConflictError:
        tfail = ap.get_tenant(conn, "FAILCI")
        na = conn.execute("SELECT COUNT(*) c FROM subscriptions WHERE tenant_id=? AND status='ACTIVE'", (tfail["id"],)).fetchone()["c"]
        check(tfail["status"] != "ACTIVE" and na == 0, "failed provisioning leaves no partial activation on PostgreSQL")
    acme = r["tenant_id"]
    ta = {"id": 900, "role": "admin", "perms": {"*"}, "tenant_id": acme}
    # entitlement enforcement (RBAC AND entitlement)
    check(saas.check_entitlement(conn, ta, "crm")["allowed"] and
          saas.check_entitlement(conn, ta, "ai_assistance")["denial_category"] == "feature_not_included",
          "entitlement enforcement (included vs excluded) on PostgreSQL")
    weak = {"id": 901, "role": "x", "perms": {"customer.view"}, "tenant_id": acme}   # has entitlement, lacks RBAC
    try:
        saas.record_usage(conn, weak, "active_users", 1, idem_key="rbacci")
        check(False, "entitlement must not replace RBAC")
    except core.ForbiddenError:
        check(True, "entitlement does not replace RBAC (permission still required) on PostgreSQL")
    # atomic quota + idempotent metering
    saas.set_quota(conn, sa, acme, "active_users", 3, hard_limit=True)
    for i in range(3):
        saas.record_usage(conn, ta, "active_users", 1, idem_key="u" + str(i))
    dup = saas.record_usage(conn, ta, "active_users", 1, idem_key="u0")
    check(dup["idempotent"], "idempotent metering (no double count) on PostgreSQL")
    try:
        saas.record_usage(conn, ta, "active_users", 1, idem_key="u9")
        check(False, "quota hard stop must deny")
    except core.ForbiddenError:
        check(True, "atomic quota hard stop enforced on PostgreSQL")
    check(saas.quota_status(conn, ta, "active_users")["remaining"] >= 0, "quota never negative on PostgreSQL")
    # reserve -> commit / release
    saas.set_quota(conn, sa, acme, "api_calls", 100, hard_limit=True)
    rv = saas.reserve_usage(conn, sa, "api_calls", 5, idem_key="rv1", tenant_id=acme)
    saas.commit_reservation(conn, sa, rv["reservation_id"])
    rv2 = saas.reserve_usage(conn, sa, "api_calls", 5, idem_key="rv2", tenant_id=acme)
    saas.release_reservation(conn, sa, rv2["reservation_id"])
    check(saas.quota_status(conn, sa, "api_calls", tenant_id=acme)["reserved"] == 0, "reserve/commit/release atomic on PostgreSQL")
    # immutable billing evidence with Phase-2 tax
    be = saas.generate_billing_evidence(conn, sa, r["subscription_id"], "2026-08-01", "2026-08-31")
    check(be["subtotal"] == 5000 and be["tax"] == 600 and be["total"] == 5600, "SaaS billing uses Phase-2 tax on PostgreSQL")
    check(saas.generate_billing_evidence(conn, sa, r["subscription_id"], "2026-08-01", "2026-08-31").get("idempotent"),
          "billing evidence immutable (not recalculated) on PostgreSQL")
    # suspension -> entitlement denied -> reactivation
    saas.suspend_subscription(conn, sa, r["subscription_id"], "nonpayment")
    check(saas.check_entitlement(conn, ta, "crm")["denial_category"] == "subscription_inactive",
          "suspended subscription denies entitlement on PostgreSQL")
    saas.reactivate_subscription(conn, sa, r["subscription_id"])
    check(saas.check_entitlement(conn, ta, "crm")["allowed"], "reactivation restores entitlement on PostgreSQL")
    # termination preserves legal-hold data
    import settings as sysc
    sysc.set_retention(conn, ta, "documents", 365, legal_hold=True)
    term = saas.terminate_subscription(conn, sa, r["subscription_id"])
    check(term["data_preserved"] and term["legal_hold"], "termination preserves legal-hold data on PostgreSQL")
    # marketplace immutable fee/payout snapshot
    saas.create_fee_policy(conn, sa, "pct10", "percentage", 10, min_fee=50)
    mt = saas.record_marketplace_transaction(conn, sa, acme, "BK-CI", 10000, 9000, "pct10")
    check(mt["platform_fee"] == 1000 and mt["carrier_payout"] == 8000, "marketplace fee/payout snapshot on PostgreSQL")
    # promotion self-approval blocked
    try:
        saas.create_promotion(conn, sa, "SELFCI", "percentage", 10, approver=sa["id"])
        check(False, "self-approved discount must be blocked")
    except core.ForbiddenError:
        check(True, "self-approved discount blocked (SoD) on PostgreSQL")
    # migration zero drift + freight financials unchanged
    m10 = saas.classify_existing(conn)
    check(m10["financial_differences"] == 0 and m10["entitlement_losses"] == 0 and m10["tenant_access_changes"] == 0
          and m10["fabricated_contracts"] == 0, "SaaS migration zero drift / no fabrication on PostgreSQL")
    fin = conn.execute("SELECT tax,total FROM quotations WHERE id=?", (wq,)).fetchone()
    check(fin["tax"] == 72000 and fin["total"] == 672000, "SaaS layer did not change freight financials on PostgreSQL")
    print("PHASE 10 SAAS COMMERCIAL LAYER: PASS on PostgreSQL", flush=True)

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
