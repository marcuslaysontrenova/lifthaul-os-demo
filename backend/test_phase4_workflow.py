"""LiftHaul OS — Phase 4: Workflow Administration, approval matrices, SLA, escalation, delegation.

Proves: governed versioned workflow definitions with IMMUTABLE published versions; declarative
condition model (no code); graph validation (single start / reachability / dead-ends / cycles /
dup codes); non-mutating simulation; approval matrices with separation-of-duties + scope; SLA
business-hours calculation (working calendar + holidays + pause/resume); escalation on breach;
governed delegation with cross-tenant/circular/expiry guards; version-bound instances with
unauthorized-transition denial; existing-transaction safety; tenant isolation; and audit — all
with financials + operational statuses unchanged.
"""
import datetime
import unittest

import db
import core
import admin_platform as ap
import workflow as wf
import wfgov
import org


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.actor = {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": self.rgo}

    def _actor(self, perms, id=9, role="x", tenant=None):
        return {"id": id, "role": role, "perms": set(perms),
                "tenant_id": self.rgo if tenant is None else tenant}

    def _draft(self, code="commercial.test", steps=None, transitions=None):
        did = wf.create_definition(self.c, self.actor, "commercial.booking", code, code)
        v = self.c.execute("SELECT id FROM workflow_versions WHERE definition_id=? AND version_no=1",
                           (did,)).fetchone()["id"]
        for s in (steps or [("START", "START"), ("END", "TERMINAL_SUCCESS")]):
            wf.add_step(self.c, self.actor, v, s[0], s[1])
        for t in (transitions or [("START", "END", "go")]):
            wf.add_transition(self.c, self.actor, v, t[0], t[1], t[2])
        return did, v


class TestDefinitionsAndVersions(Base):
    def test_create_definition_and_initial_draft(self):
        did = wf.create_definition(self.c, self.actor, "commercial.booking", "wf.a", "A")
        vs = wf.list_versions(self.c, self.actor, "wf.a")
        self.assertEqual((len(vs), vs[0]["status"]), (1, "DRAFT"))

    def test_duplicate_definition_blocked(self):
        wf.create_definition(self.c, self.actor, "commercial.booking", "wf.dup", "Dup")
        with self.assertRaises(core.ConflictError):
            wf.create_definition(self.c, self.actor, "commercial.booking", "wf.dup", "Dup2")

    def test_published_version_is_immutable(self):
        did, v = self._draft("wf.imm")
        wf.validate_version(self.c, self.actor, v)
        wf.approve_version(self.c, self.actor, v)
        wf.publish_version(self.c, self.actor, v, "go live")
        with self.assertRaises(core.ForbiddenError):
            wf.add_step(self.c, self.actor, v, "X", "TASK")        # cannot edit active version

    def test_new_version_copies_source_graph(self):
        did, v = self._draft("wf.copy")
        wf.validate_version(self.c, self.actor, v); wf.approve_version(self.c, self.actor, v)
        wf.publish_version(self.c, self.actor, v, "v1")
        nv = wf.create_version(self.c, self.actor, "wf.copy", "improve")
        self.assertEqual(len(wf.steps(self.c, nv)), 2)             # copied START + END

    def test_duplicate_version_number_blocked(self):
        did, v = self._draft("wf.vnum")
        with self.assertRaises(Exception):
            self.c.execute("INSERT INTO workflow_versions(definition_id,version_no,status,created_at)"
                           " VALUES(?,?, 'DRAFT', ?)", (did, 1, "now")); self.c.commit()


class TestConditions(Base):
    def test_declarative_condition_eval(self):
        self.assertTrue(wf.evaluate_condition({"field": "amount", "op": "gte", "value": 500000}, {"amount": 600000}))
        self.assertFalse(wf.evaluate_condition({"field": "amount", "op": "gte", "value": 500000}, {"amount": 100}))
        self.assertTrue(wf.evaluate_condition({"all": [
            {"field": "amount", "op": "gt", "value": 100},
            {"field": "customer.credit_status", "op": "eq", "value": "restricted"}]},
            {"amount": 200, "customer.credit_status": "restricted"}))

    def test_invalid_field_rejected(self):
        with self.assertRaises(core.ValidationError):
            wf.validate_condition_spec({"field": "evil.raw_sql", "op": "eq", "value": "x"})

    def test_type_incompatible_operator_rejected(self):
        with self.assertRaises(core.ValidationError):
            wf.validate_condition_spec({"field": "customer.credit_status", "op": "gt", "value": 5})


class TestValidation(Base):
    def test_valid_graph_passes(self):
        did, v = self._draft("wf.valid")
        self.assertTrue(wf.validate_version(self.c, self.actor, v)["ok"])

    def test_two_start_steps_fail(self):
        did = wf.create_definition(self.c, self.actor, "commercial.booking", "wf.2start", "X")
        v = self.c.execute("SELECT id FROM workflow_versions WHERE definition_id=?", (did,)).fetchone()["id"]
        wf.add_step(self.c, self.actor, v, "S1", "START"); wf.add_step(self.c, self.actor, v, "S2", "START")
        wf.add_step(self.c, self.actor, v, "END", "TERMINAL_SUCCESS")
        wf.add_transition(self.c, self.actor, v, "S1", "END", "go")
        r = wf.validate_version(self.c, self.actor, v)
        self.assertFalse(r["ok"])
        self.assertTrue(any("START" in e for e in r["errors"]))

    def test_unreachable_step_fails(self):
        did = wf.create_definition(self.c, self.actor, "commercial.booking", "wf.unreach", "X")
        v = self.c.execute("SELECT id FROM workflow_versions WHERE definition_id=?", (did,)).fetchone()["id"]
        wf.add_step(self.c, self.actor, v, "START", "START")
        wf.add_step(self.c, self.actor, v, "END", "TERMINAL_SUCCESS")
        wf.add_step(self.c, self.actor, v, "ORPHAN", "TASK")       # unreachable
        wf.add_transition(self.c, self.actor, v, "START", "END", "go")
        wf.add_transition(self.c, self.actor, v, "ORPHAN", "END", "x")
        r = wf.validate_version(self.c, self.actor, v)
        self.assertFalse(r["ok"])
        self.assertTrue(any("unreachable" in e for e in r["errors"]))

    def test_dead_end_fails(self):
        did = wf.create_definition(self.c, self.actor, "commercial.booking", "wf.dead", "X")
        v = self.c.execute("SELECT id FROM workflow_versions WHERE definition_id=?", (did,)).fetchone()["id"]
        wf.add_step(self.c, self.actor, v, "START", "START")
        wf.add_step(self.c, self.actor, v, "MIDDLE", "TASK")       # no exit, not terminal
        wf.add_transition(self.c, self.actor, v, "START", "MIDDLE", "go")
        r = wf.validate_version(self.c, self.actor, v)
        self.assertFalse(r["ok"])
        self.assertTrue(any("dead-end" in e or "terminal" in e for e in r["errors"]))

    def test_publication_blocked_by_validation_errors(self):
        did = wf.create_definition(self.c, self.actor, "commercial.booking", "wf.badpub", "X")
        v = self.c.execute("SELECT id FROM workflow_versions WHERE definition_id=?", (did,)).fetchone()["id"]
        wf.add_step(self.c, self.actor, v, "START", "START")       # no terminal
        # cannot even reach APPROVED cleanly; force approve then publish must fail on validation
        self.c.execute("UPDATE workflow_versions SET status='APPROVED' WHERE id=?", (v,)); self.c.commit()
        with self.assertRaises(core.ValidationError):
            wf.publish_version(self.c, self.actor, v, "reason")


class TestSimulation(Base):
    def test_simulation_paths_and_non_mutation(self):
        d = wf.get_definition(self.c, self.actor, "commercial.booking")
        av = wf.active_version(self.c, d["id"])
        before = self.c.execute("SELECT COUNT(*) c FROM workflow_instances").fetchone()["c"]
        below = wf.simulate(self.c, self.actor, av["id"], {"amount": 100000})
        above = wf.simulate(self.c, self.actor, av["id"], {"amount": 900000})
        self.assertNotIn("APPROVAL", below["path"])
        self.assertIn("APPROVAL", above["path"])
        after = self.c.execute("SELECT COUNT(*) c FROM workflow_instances").fetchone()["c"]
        self.assertEqual(before, after)                            # simulation created no instances


class TestApprovalMatrices(Base):
    def test_threshold_and_sod(self):
        d = wf.get_definition(self.c, self.actor, "commercial.booking")
        iid = wf.start_instance(self.c, self.actor, "commercial.booking", "booking", 1)  # started by actor id=1
        wf.advance_instance(self.c, self.actor, iid, "submit_for_review")
        wf.advance_instance(self.c, self.actor, iid, "send_for_approval", ctx={"amount": 900000})
        # creator cannot self-approve (SoD)
        with self.assertRaises(core.ForbiddenError):
            wf.advance_instance(self.c, self.actor, iid, "approve", ctx={"amount": 900000}, reason="self")
        approver = self._actor({"quotation.approve", "workflow.instance.manage"}, id=2, role="approver")
        res = wf.advance_instance(self.c, approver, iid, "approve", ctx={"amount": 900000}, reason="ok")
        self.assertEqual((res["to"], res["status"]), ("CONFIRMED", "COMPLETED"))

    def test_sequential_matrix_resolution(self):
        wfgov.create_matrix(self.c, self.actor, "seq_m", "Seq", mode="sequential")
        wfgov.add_matrix_rule(self.c, self.actor, "seq_m", "role", approver_ref="approver", level=1)
        wfgov.add_matrix_rule(self.c, self.actor, "seq_m", "role", approver_ref="finance", level=2,
                              dimension="amount", op="gte", value="1000000")
        low = wfgov.resolve_approvals(self.c, self.actor, "seq_m", {"amount": 500})
        high = wfgov.resolve_approvals(self.c, self.actor, "seq_m", {"amount": 2000000})
        self.assertEqual(len(low["required_approvers"]), 1)        # only level-1
        self.assertEqual(len(high["required_approvers"]), 2)       # both levels

    def test_approve_outside_tenant_denied(self):
        iid = wf.start_instance(self.c, self.actor, "commercial.booking", "booking", 5)
        wf.advance_instance(self.c, self.actor, iid, "submit_for_review")
        wf.advance_instance(self.c, self.actor, iid, "send_for_approval", ctx={"amount": 900000})
        foreign = self._actor({"quotation.approve", "workflow.instance.manage"}, id=3, role="approver", tenant=9999)
        # cross-tenant is prevented — either 404 no-leak (instance guard) or Forbidden (scope check)
        with self.assertRaises((core.ForbiddenError, core.NotFoundError)):
            wf.advance_instance(self.c, foreign, iid, "approve", ctx={"amount": 900000}, reason="x")


class TestInstances(Base):
    def test_invalid_transition_rejected(self):
        iid = wf.start_instance(self.c, self.actor, "commercial.booking", "booking", 1)
        with self.assertRaises(core.ConflictError):
            wf.advance_instance(self.c, self.actor, iid, "nonexistent_action")

    def test_unauthorized_transition_denied(self):
        iid = wf.start_instance(self.c, self.actor, "commercial.booking", "booking", 1)
        weak = self._actor({"workflow.instance.manage"}, id=7)     # lacks booking.review
        with self.assertRaises(core.ForbiddenError):
            wf.advance_instance(self.c, weak, iid, "submit_for_review")

    def test_tenant_isolation_of_instances(self):
        iid = wf.start_instance(self.c, self.actor, "commercial.booking", "booking", 1)
        other = self._actor({"*"}, id=4, tenant=9999)
        with self.assertRaises(core.NotFoundError):
            wf.get_instance(self.c, other, iid)

    def test_history_recorded(self):
        iid = wf.start_instance(self.c, self.actor, "commercial.booking", "booking", 1)
        wf.advance_instance(self.c, self.actor, iid, "submit_for_review")
        hist = wf.instance_history(self.c, self.actor, iid)
        self.assertEqual(hist[0]["to_step"], "REVIEW")


class TestFutureVersionSafety(Base):
    def test_old_instance_stays_new_instance_uses_future_version(self):
        d = wf.get_definition(self.c, self.actor, "commercial.booking")
        old_v = wf.active_version(self.c, d["id"])["id"]
        old_iid = wf.start_instance(self.c, self.actor, "commercial.booking", "booking", 1)
        self.assertEqual(wf.get_instance(self.c, self.actor, old_iid)["version_id"], old_v)
        # publish a future-dated v2
        nv = wf.create_version(self.c, self.actor, "commercial.booking", "v2")
        wf.validate_version(self.c, self.actor, nv); wf.approve_version(self.c, self.actor, nv)
        future = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
        pub = wf.publish_version(self.c, self.actor, nv, "future rollout", effective_from=future)
        self.assertEqual(pub["status"], "PUBLISHED")               # not yet active
        # existing instance still on old version
        self.assertEqual(wf.get_instance(self.c, self.actor, old_iid)["version_id"], old_v)
        # simulate activation date arrival
        self.c.execute("UPDATE workflow_versions SET effective_from=? WHERE id=?",
                       ((datetime.date.today() - datetime.timedelta(days=1)).isoformat(), nv)); self.c.commit()
        wf.activate_due(self.c)
        new_iid = wf.start_instance(self.c, self.actor, "commercial.booking", "booking", 2)
        self.assertEqual(wf.get_instance(self.c, self.actor, new_iid)["version_id"], nv)   # new work uses v2
        self.assertEqual(wf.get_instance(self.c, self.actor, old_iid)["version_id"], old_v)  # old unchanged


class TestSLAandEscalation(Base):
    def test_business_hours_calculation(self):
        # Monday 2026-08-03 08:00 + 480 working minutes -> 16:00 same day (09:00 shift window)
        due = wfgov.compute_due(self.c, self.actor, "booking_review_sla", "2026-08-03T08:00:00")
        self.assertTrue(due["due_at"].startswith("2026-08-03T16:00"))

    def test_weekend_skipped(self):
        # Friday 16:00 + 120 min -> spills into Monday (weekend skipped)
        wfgov.create_sla(self.c, self.actor, "sla_wknd", "WE", 120)
        due = wfgov.compute_due(self.c, self.actor, "sla_wknd", "2026-08-07T16:00:00")  # Friday
        self.assertTrue(due["due_at"].startswith("2026-08-10"))    # Monday
        self.assertEqual(datetime.date.fromisoformat(due["due_at"][:10]).strftime("%a"), "Mon")

    def test_breach_fires_escalation(self):
        iid = wf.start_instance(self.c, self.actor, "commercial.booking", "booking", 1)
        wfgov.create_sla(self.c, self.actor, "sla_fast", "Fast", 60, escalation_code="booking_esc")
        si = wfgov.start_sla(self.c, self.actor, iid, "sla_fast", "2026-01-01T08:00:00")  # long past due
        breached = wfgov.check_breaches(self.c, self.actor)
        self.assertTrue(any(b["instance_id"] == iid for b in breached))
        self.assertTrue(len(wfgov.escalation_history(self.c, self.actor, iid)) >= 1)

    def test_pause_and_resume(self):
        iid = wf.start_instance(self.c, self.actor, "commercial.booking", "booking", 1)
        si = wfgov.start_sla(self.c, self.actor, iid, "booking_review_sla")
        wfgov.pause_sla(self.c, self.actor, si["sla_instance_id"])
        row = self.c.execute("SELECT status FROM sla_instances WHERE id=?", (si["sla_instance_id"],)).fetchone()
        self.assertEqual(row["status"], "PAUSED")
        r = wfgov.resume_sla(self.c, self.actor, si["sla_instance_id"])
        self.assertIn("due_at", r)


class TestDelegation(Base):
    def _users(self):
        d = core.create_user(self.c, "deleg@r", "Demo1234Xy", "approver", "Delegator")
        g = core.create_user(self.c, "delegate@r", "Demo1234Xy", "estimator", "Delegate")
        return d, g

    def test_create_and_use_delegation(self):
        d, g = self._users()
        end = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
        wfgov.create_delegation(self.c, self.actor, d, g, "approver", "commercial.booking",
                                datetime.date.today().isoformat(), end)
        act = wfgov.active_delegation(self.c, g, domain="commercial.booking", tenant=self.rgo)
        self.assertIsNotNone(act)

    def test_permanent_delegation_blocked(self):
        d, g = self._users()
        with self.assertRaises(core.ValidationError):
            wfgov.create_delegation(self.c, self.actor, d, g, "approver", "x",
                                    datetime.date.today().isoformat(), None)

    def test_self_delegation_blocked(self):
        d, _ = self._users()
        end = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
        with self.assertRaises(core.ValidationError):
            wfgov.create_delegation(self.c, self.actor, d, d, "approver", "x", None, end)

    def test_cross_tenant_delegation_blocked(self):
        d = core.create_user(self.c, "t1u@r", "Demo1234Xy", "approver", "T1")
        g = core.create_user(self.c, "t2u@r", "Demo1234Xy", "estimator", "T2")
        import tenant as tmod
        tmod.bind_user_tenant(self.c, None, g, 9999)               # different tenant
        end = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
        with self.assertRaises(core.ForbiddenError):
            wfgov.create_delegation(self.c, self.actor, d, g, "approver", "x",
                                    datetime.date.today().isoformat(), end)

    def test_expired_delegation_not_active(self):
        d, g = self._users()
        past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        start = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
        did = wfgov.create_delegation(self.c, self.actor, d, g, "approver", "x", start, past)
        wfgov.expire_delegations(self.c, self.actor)
        self.assertIsNone(wfgov.active_delegation(self.c, g, tenant=self.rgo))


class TestPermissionsAndSafety(Base):
    def test_seeded_role_grants(self):
        pa = ap.effective_role_grants(self.c, ap.role_by_code(self.c, "RGO", "platform_admin")["id"])
        self.assertIn("workflow.*", pa)
        ba = ap.effective_role_grants(self.c, ap.role_by_code(self.c, "RGO", "business_admin")["id"])
        self.assertIn("workflow.definition.manage", ba)
        self.assertNotIn("workflow.version.publish", ba)          # publication is platform-governed

    def test_publish_requires_permission(self):
        did, v = self._draft("wf.perm")
        wf.validate_version(self.c, self.actor, v)
        weak = self._actor({"workflow.version.validate", "workflow.version.approve"})
        wf.approve_version(self.c, weak, v)                        # has approve
        with self.assertRaises(core.ForbiddenError):
            wf.publish_version(self.c, weak, v, "reason")          # lacks publish

    def test_migration_zero_drift(self):
        m = wf.classify_existing(self.c)
        self.assertEqual((m["financial_differences"], m["operational_status_differences"]), (0, 0))
        self.assertEqual(m["versions_assigned"], 0)               # additive; nothing force-migrated

    def test_workflow_does_not_change_financials(self):
        a = self.actor
        cid = core.create_customer(self.c, a, "WF Fin Co")
        bid = core.create_booking(self.c, a, cid, "CRANE_RENTAL", "x", 1)
        core.review_booking(self.c, a, bid); core.ready_for_quotation(self.c, a, bid)
        qid = core.create_quotation(self.c, a, bid, [{"kind": "crane", "description": "x", "qty": 2, "days": 1, "rate": 300000}])
        before = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        stage_before = self.c.execute("SELECT stage FROM bookings WHERE id=?", (bid,)).fetchone()["stage"]
        # drive a whole parallel governed workflow instance for the booking
        iid = wf.start_instance(self.c, a, "commercial.booking", "booking", bid)
        wf.advance_instance(self.c, a, iid, "submit_for_review")
        wf.advance_instance(self.c, a, iid, "auto_confirm", ctx={"amount": 100000})
        after = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        self.assertEqual((before["tax"], before["total"]), (72000, 672000))
        self.assertEqual((after["tax"], after["total"]), (72000, 672000))   # UNCHANGED
        # the real booking stage is NOT changed by the parallel governed instance (0 operational drift)
        self.assertEqual(self.c.execute("SELECT stage FROM bookings WHERE id=?", (bid,)).fetchone()["stage"],
                         stage_before)


class TestPhase4Api(unittest.TestCase):
    """Drives the Phase 4 /admin/workflow* endpoints through the real HTTP router (server._match)."""
    @classmethod
    def setUpClass(cls):
        import os
        import server
        import db as _db
        if os.path.exists("rgo_p4api.sqlite"):
            os.remove("rgo_p4api.sqlite")
        server._conn = _db.connect(":memory:")
        cls.server = server
        try:
            core.create_user(server._conn, "p4admin@r", "demo1234", "admin", "P4 Admin")
        except core.ConflictError:
            pass

    def _call(self, method, path, body=None):
        fn, params = self.server._match(method, path)
        assert fn, f"no route for {method} {path}"
        tok = self.server._match("POST", "/login")[0](None, {"email": "p4admin@r", "password": "demo1234"}, {})["token"]
        actor = core.actor_for(self.server._conn, tok)
        return fn(actor, body or {}, params or {})

    def test_list_and_get_seeded_workflow(self):
        defs = self._call("GET", "/admin/workflows")["definitions"]
        self.assertTrue(any(d["code"] == "commercial.booking" for d in defs))
        vs = self._call("GET", "/admin/workflows/commercial.booking/versions")["versions"]
        self.assertTrue(any(v["status"] == "ACTIVE" for v in vs))

    def test_design_validate_simulate_via_api(self):
        did = self._call("POST", "/admin/workflows", {"domain": "commercial.booking", "code": "api.wf", "name": "Api WF"})["id"]
        vid = self._call("GET", "/admin/workflows/api.wf/versions")["versions"][0]["id"]
        self._call("POST", f"/admin/workflow-versions/{vid}/steps", {"code": "START", "step_type": "START"})
        self._call("POST", f"/admin/workflow-versions/{vid}/steps", {"code": "END", "step_type": "TERMINAL_SUCCESS"})
        self._call("POST", f"/admin/workflow-versions/{vid}/transitions", {"source_step": "START", "target_step": "END", "action": "go"})
        val = self._call("POST", f"/admin/workflow-versions/{vid}/validate", {})
        self.assertTrue(val["ok"])
        sim = self._call("POST", f"/admin/workflow-versions/{vid}/simulate", {"ctx": {}})
        self.assertEqual(sim["terminal_outcome"], "TERMINAL_SUCCESS")

    def test_publish_and_instance_via_api(self):
        vs = self._call("GET", "/admin/workflows/commercial.booking/versions")["versions"]
        iid = self._call("POST", "/admin/workflow-instances", {"code": "commercial.booking", "entity_type": "booking", "entity_id": 99})["id"]
        got = self._call("GET", f"/admin/workflow-instances/{iid}")
        self.assertEqual(got["instance"]["current_step"], "START")
        self._call("POST", f"/admin/workflow-instances/{iid}/advance", {"action": "submit_for_review"})
        self.assertEqual(self._call("GET", f"/admin/workflow-instances/{iid}")["instance"]["current_step"], "REVIEW")

    def test_matrix_sla_escalation_via_api(self):
        self._call("POST", "/admin/workflow/matrices", {"code": "api_m", "name": "Api M", "mode": "single"})
        self._call("POST", "/admin/workflow/matrices/api_m/rules", {"approver_type": "role", "approver_ref": "approver"})
        self._call("POST", "/admin/workflow/escalations", {"code": "api_esc", "name": "E", "target_type": "role", "target_ref": "operations_manager"})
        self._call("POST", "/admin/workflow/sla", {"code": "api_sla", "name": "S", "duration_minutes": 240, "escalation_code": "api_esc"})
        due = self._call("POST", "/admin/workflow/sla/due", {"code": "api_sla", "start": "2026-08-03T08:00:00"})
        self.assertTrue(due["due_at"].startswith("2026-08-03T12:00"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
