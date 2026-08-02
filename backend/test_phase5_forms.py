"""LiftHaul OS — Phase 5: Form & Custom-Field Administration.

Proves: governed form definitions with IMMUTABLE published versions; declarative validation +
visibility/required/editability (no code); protected system/financial fields cannot be created;
graph validation (circular visibility/required, unknown-section, invalid rules); non-mutating
simulation with role/portal differences; runtime submission SERVER-validated (unknown / inactive /
unauthorized / invalid-type / invalid-option / wrong-stage rejected); typed+JSON value persistence;
historical version preservation; sensitivity masking + export exclusion; file/signature governance;
tenant isolation; and migration with zero value loss — all with financials + operational statuses
unchanged.
"""
import datetime
import unittest

import db
import core
import admin_platform as ap
import forms


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.actor = {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": self.rgo}

    def _actor(self, perms, id=9, role="estimator", tenant=None):
        return {"id": id, "role": role, "perms": set(perms),
                "tenant_id": self.rgo if tenant is None else tenant}

    def _draft(self, code="booking.custom", entity="booking"):
        did = forms.create_definition(self.c, self.actor, entity, code, code)
        v = self.c.execute("SELECT id FROM form_versions WHERE definition_id=? AND version_no=1", (did,)).fetchone()["id"]
        return did, v

    def _publish(self, v):
        forms.validate_version(self.c, self.actor, v)
        forms.approve_version(self.c, self.actor, v)
        return forms.publish_version(self.c, self.actor, v, "go live")


class TestDefinitionsAndVersions(Base):
    def test_create_definition(self):
        did, v = self._draft("f.a")
        self.assertEqual(forms.list_versions(self.c, self.actor, "f.a")[0]["status"], "DRAFT")

    def test_duplicate_form_code_blocked(self):
        self._draft("f.dup")
        with self.assertRaises(core.ConflictError):
            forms.create_definition(self.c, self.actor, "booking", "f.dup", "Dup2")

    def test_version_creation_copies_graph(self):
        did, v = self._draft("f.copy")
        forms.add_section(self.c, self.actor, v, "s1", "S1")
        forms.add_field(self.c, self.actor, v, "note", "Note", "short_text", section_code="s1")
        self._publish(v)
        nv = forms.create_version(self.c, self.actor, "f.copy", "tweak")
        self.assertEqual(len(forms.fields(self.c, nv)), 1)

    def test_published_version_immutable(self):
        did, v = self._draft("f.imm")
        forms.add_field(self.c, self.actor, v, "note", "Note", "short_text")
        self._publish(v)
        with self.assertRaises(core.ForbiddenError):
            forms.add_field(self.c, self.actor, v, "another", "A", "short_text")


class TestFieldGovernance(Base):
    def test_invalid_field_type(self):
        did, v = self._draft("f.type")
        with self.assertRaises(core.ValidationError):
            forms.add_field(self.c, self.actor, v, "x", "X", "nonsense_type")

    def test_duplicate_field_code(self):
        did, v = self._draft("f.dupf")
        forms.add_field(self.c, self.actor, v, "x", "X", "short_text")
        with self.assertRaises(core.ConflictError):
            forms.add_field(self.c, self.actor, v, "x", "X2", "short_text")

    def test_protected_financial_field_blocked(self):
        did, v = self._draft("f.fin", entity="quotation")
        with self.assertRaises(core.ForbiddenError):
            forms.add_field(self.c, self.actor, v, "total", "Total", "currency")   # authoritative money

    def test_protected_workflow_field_blocked(self):
        did, v = self._draft("f.stage", entity="booking")
        with self.assertRaises(core.ForbiddenError):
            forms.add_field(self.c, self.actor, v, "stage", "Stage", "short_text")

    def test_invalid_validation_rule(self):
        did, v = self._draft("f.badrule")
        with self.assertRaises(core.ValidationError):
            forms.add_field(self.c, self.actor, v, "x", "X", "short_text", validation={"evil_key": 1})

    def test_unsafe_pattern_blocked(self):
        did, v = self._draft("f.badpat")
        with self.assertRaises(core.ValidationError):
            forms.add_field(self.c, self.actor, v, "x", "X", "short_text", validation={"pattern": "(a+)+"})

    def test_sensitive_field_requires_permission(self):
        did, v = self._draft("f.sens")
        weak = self._actor({"form.field.manage"})   # lacks form.field.sensitive.manage
        with self.assertRaises(core.ForbiddenError):
            forms.add_field(self.c, weak, v, "ssn", "SSN", "short_text", sensitivity="PERSONAL_DATA")


class TestValidationRules(Base):
    def test_circular_visibility_blocks_publication(self):
        did, v = self._draft("f.circ")
        forms.add_field(self.c, self.actor, v, "a", "A", "short_text", visibility={"field": "b", "op": "exists"})
        forms.add_field(self.c, self.actor, v, "b", "B", "short_text", visibility={"field": "a", "op": "exists"})
        r = forms.validate_version(self.c, self.actor, v)
        self.assertFalse(r["ok"])
        self.assertTrue(any("circular visibility" in e for e in r["errors"]))

    def test_unknown_condition_field_flagged(self):
        did, v = self._draft("f.unkcond")
        forms.add_field(self.c, self.actor, v, "a", "A", "short_text", visibility={"field": "ghost", "op": "exists"})
        r = forms.validate_version(self.c, self.actor, v)
        self.assertFalse(r["ok"])

    def test_select_without_options_fails(self):
        did, v = self._draft("f.noopt")
        forms.add_field(self.c, self.actor, v, "sel", "Sel", "single_select")
        r = forms.validate_version(self.c, self.actor, v)
        self.assertFalse(r["ok"])

    def test_hidden_required_warns(self):
        did, v = self._draft("f.hidreq")
        forms.add_field(self.c, self.actor, v, "gate", "Gate", "boolean")
        forms.add_field(self.c, self.actor, v, "dep", "Dep", "short_text", required=True,
                        visibility={"field": "gate", "op": "is_true"})
        r = forms.validate_version(self.c, self.actor, v)
        self.assertTrue(any("hidden" in w for w in r["warnings"]))


class TestSimulation(Base):
    def test_simulation_role_and_portal_non_mutating(self):
        ef = forms.effective_form(self.c, self.actor, "booking", role="admin")
        before = self.c.execute("SELECT COUNT(*) c FROM form_values").fetchone()["c"]
        sim_internal = forms.simulate(self.c, self.actor, ef["version_id"], {"role": "dispatcher"}, {"insured": "true"})
        sim_portal = forms.simulate(self.c, self.actor, ef["version_id"], {"role": "customer", "portal": True}, {"insured": "true"})
        self.assertIn("client_contact_private", sim_internal["visible"])       # internal sees PERSONAL_DATA
        self.assertNotIn("client_contact_private", sim_portal["visible"])      # portal hides it
        after = self.c.execute("SELECT COUNT(*) c FROM form_values").fetchone()["c"]
        self.assertEqual(before, after)

    def test_required_condition_in_simulation(self):
        ef = forms.effective_form(self.c, self.actor, "booking", role="admin")
        s = forms.simulate(self.c, self.actor, ef["version_id"], {"role": "admin"}, {"insured": "true"})
        self.assertIn("insurance_policy_no", s["required"])                    # required when insured=true
        s2 = forms.simulate(self.c, self.actor, ef["version_id"], {"role": "admin"}, {"insured": "false"})
        self.assertNotIn("insurance_policy_no", s2["required"])


class TestRuntimeSubmission(Base):
    def test_required_condition_enforced(self):
        with self.assertRaises(core.ValidationError):
            forms.submit_values(self.c, self.actor, "booking", 500, {"insured": "true", "service_type": "CRANE_RENTAL"})
        r = forms.submit_values(self.c, self.actor, "booking", 500,
                                {"insured": "true", "insurance_policy_no": "POL-1", "service_type": "CRANE_RENTAL"})
        self.assertEqual(r["stored"], 3)

    def test_unknown_field_rejected(self):
        with self.assertRaises(core.ValidationError):
            forms.submit_values(self.c, self.actor, "booking", 501, {"ghost": "x"})

    def test_invalid_option_rejected(self):
        with self.assertRaises(core.ValidationError):
            forms.submit_values(self.c, self.actor, "booking", 502, {"service_type": "NOPE"})

    def test_master_data_option_accepted(self):
        r = forms.submit_values(self.c, self.actor, "booking", 503, {"service_type": "RIGGING"})
        self.assertEqual(r["stored"], 1)

    def test_invalid_value_type_rejected(self):
        did, v = self._draft("f.num", entity="equipment")
        forms.add_field(self.c, self.actor, v, "capacity", "Capacity", "integer")
        self._publish(v)
        with self.assertRaises(core.ValidationError):
            forms.submit_values(self.c, self.actor, "equipment", 1, {"capacity": "not-a-number"})

    def test_unauthorized_field_submission_denied(self):
        did, v = self._draft("f.rolefld", entity="job")
        forms.add_field(self.c, self.actor, v, "internal_note", "Internal", "short_text", role_restriction="manager")
        self._publish(v)
        weak = self._actor({"form.data.edit", "form.data.view"}, role="estimator")
        with self.assertRaises(core.ForbiddenError):
            forms.submit_values(self.c, weak, "job", 1, {"internal_note": "x"})

    def test_custom_value_persistence(self):
        forms.submit_values(self.c, self.actor, "booking", 600, {"insured": "false", "service_type": "CRANE_RENTAL"})
        vals = forms.get_values(self.c, self.actor, "booking", 600)
        self.assertEqual(vals["service_type"]["value"], "CRANE_RENTAL")


class TestSensitivityAndExport(Base):
    def test_sensitive_value_masked_for_unprivileged(self):
        forms.submit_values(self.c, self.actor, "booking", 700, {"client_contact_private": "+639170000000"})
        viewer = self._actor({"form.data.view"})
        self.assertTrue(forms.get_values(self.c, viewer, "booking", 700)["client_contact_private"]["masked"])
        priv = self._actor({"form.data.view", "form.data.sensitive.view"})
        self.assertEqual(forms.get_values(self.c, priv, "booking", 700)["client_contact_private"]["value"], "+639170000000")

    def test_export_excludes_sensitive(self):
        forms.submit_values(self.c, self.actor, "booking", 701,
                            {"client_contact_private": "+639171111111", "service_type": "CRANE_RENTAL"})
        exp = forms.export_values(self.c, self._actor({"form.data.export", "form.data.view"}), "booking")
        self.assertIn("client_contact_private", exp["excluded_sensitive"])     # PERSONAL_DATA excluded
        self.assertIn("service_type", exp["exported_fields"])

    def test_search_requires_searchable(self):
        forms.submit_values(self.c, self.actor, "booking", 702, {"insurance_policy_no": "POL-XYZ", "insured": "true"})
        res = forms.search_values(self.c, self.actor, "booking", "insurance_policy_no", "POL-XYZ")
        self.assertTrue(any(r["entity_id"] == 702 for r in res))
        with self.assertRaises(core.ValidationError):
            forms.search_values(self.c, self.actor, "booking", "insured", "true")   # not searchable


class TestHistoricalPreservation(Base):
    def test_old_record_keeps_old_field_version(self):
        forms.submit_values(self.c, self.actor, "booking", 800, {"service_type": "CRANE_RENTAL"})
        v1 = forms.get_values(self.c, self.actor, "booking", 800)["service_type"]["field_version"]
        # publish a new version (v2) of the booking form
        nv = forms.create_version(self.c, self.actor, "booking_form", "relabel")
        forms.validate_version(self.c, self.actor, nv); forms.approve_version(self.c, self.actor, nv)
        forms.publish_version(self.c, self.actor, nv, "v2")
        forms.submit_values(self.c, self.actor, "booking", 801, {"service_type": "RIGGING"})
        v2 = forms.get_values(self.c, self.actor, "booking", 801)["service_type"]["field_version"]
        self.assertEqual(v1, 1)
        self.assertEqual(v2, 2)                                                # new record uses v2
        self.assertEqual(forms.get_values(self.c, self.actor, "booking", 800)["service_type"]["field_version"], 1)  # old unchanged


class TestTenantIsolation(Base):
    def test_value_tenant_isolation(self):
        forms.submit_values(self.c, self.actor, "booking", 900, {"service_type": "CRANE_RENTAL"})
        other = self._actor({"form.data.view"}, id=5, tenant=9999)
        self.assertEqual(forms.get_values(self.c, other, "booking", 900), {})   # no cross-tenant leak

    def test_cross_tenant_entity_submit_denied(self):
        a = self.actor
        cid = core.create_customer(self.c, a, "Iso Co")
        other = self._actor({"form.data.edit", "form.data.view"}, id=6, tenant=9999)
        with self.assertRaises(core.NotFoundError):
            forms.submit_values(self.c, other, "customer", cid, {})   # customer belongs to RGO


class TestFilesAndSignatures(Base):
    def test_file_type_and_size_control(self):
        with self.assertRaises(core.ValidationError):
            forms.upload_file(self.c, self.actor, "booking", 1, "attachment", "x.exe", "application/x-msdownload", 10,
                              allowed_types=["application/pdf"])
        with self.assertRaises(core.ValidationError):
            forms.upload_file(self.c, self.actor, "booking", 1, "attachment", "big.pdf", "application/pdf", 10_000_000, max_size=1_000_000)
        r = forms.upload_file(self.c, self.actor, "booking", 1, "attachment", "ok.pdf", "application/pdf", 500,
                              content_bytes=b"data", allowed_types=["application/pdf"], max_size=1_000_000)
        self.assertEqual(len(r["file_ref"]), 32)                               # non-guessable id
        self.assertTrue(r["checksum"])

    def test_signature_requires_meaning(self):
        with self.assertRaises(core.ValidationError):
            forms.add_signature(self.c, self.actor, "booking", 1, "sig", "hash123", "")
        sid = forms.add_signature(self.c, self.actor, "booking", 1, "sig", "hash123", "I approve this booking")
        self.assertTrue(sid)


class TestPermissionsAndMigration(Base):
    def test_role_grants(self):
        pa = ap.effective_role_grants(self.c, ap.role_by_code(self.c, "RGO", "platform_admin")["id"])
        self.assertIn("form.*", pa)
        ba = ap.effective_role_grants(self.c, ap.role_by_code(self.c, "RGO", "business_admin")["id"])
        self.assertIn("form.field.manage", ba)
        self.assertNotIn("form.version.publish", ba)                          # publication platform-governed
        self.assertNotIn("form.field.sensitive.manage", ba)

    def test_publish_requires_permission(self):
        did, v = self._draft("f.pubperm")
        forms.add_field(self.c, self.actor, v, "n", "N", "short_text")
        forms.validate_version(self.c, self.actor, v)
        weak = self._actor({"form.version.validate", "form.version.approve"})
        forms.approve_version(self.c, weak, v)
        with self.assertRaises(core.ForbiddenError):
            forms.publish_version(self.c, weak, v, "reason")

    def test_migration_zero_loss(self):
        m = forms.classify_existing(self.c)
        self.assertEqual((m["financial_differences"], m["operational_status_differences"], m["field_value_losses"]), (0, 0, 0))
        self.assertEqual(m["columns_removed"], 0)

    def test_field_dependency_analysis(self):
        forms.submit_values(self.c, self.actor, "booking", 950, {"service_type": "CRANE_RENTAL"})
        av = forms.active_version_for_entity(self.c, self.actor, "booking")
        dep = forms.field_dependencies(self.c, self.actor, av["id"], "service_type")
        self.assertGreaterEqual(dep["records_with_values"], 1)
        self.assertFalse(dep["safe_to_retire"])                               # has values

    def test_forms_do_not_change_financials(self):
        a = self.actor
        cid = core.create_customer(self.c, a, "Form Fin Co")
        bid = core.create_booking(self.c, a, cid, "CRANE_RENTAL", "x", 1)
        core.review_booking(self.c, a, bid); core.ready_for_quotation(self.c, a, bid)
        qid = core.create_quotation(self.c, a, bid, [{"kind": "crane", "description": "x", "qty": 2, "days": 1, "rate": 300000}])
        before = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        forms.submit_values(self.c, a, "booking", bid, {"insured": "false", "service_type": "CRANE_RENTAL"})
        after = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        self.assertEqual((before["tax"], before["total"]), (72000, 672000))
        self.assertEqual((after["tax"], after["total"]), (72000, 672000))     # UNCHANGED


class TestPhase5Api(unittest.TestCase):
    """Drives the Phase 5 /admin/forms* + runtime endpoints through the real HTTP router."""
    @classmethod
    def setUpClass(cls):
        import server
        import db as _db
        server._conn = _db.connect(":memory:")
        cls.server = server
        try:
            core.create_user(server._conn, "p5admin@r", "demo1234", "admin", "P5 Admin")
        except core.ConflictError:
            pass

    def _call(self, method, path, body=None):
        fn, params = self.server._match(method, path)
        assert fn, f"no route for {method} {path}"
        tok = self.server._match("POST", "/login")[0](None, {"email": "p5admin@r", "password": "demo1234"}, {})["token"]
        actor = core.actor_for(self.server._conn, tok)
        return fn(actor, body or {}, params or {})

    def test_list_seeded_form(self):
        d = self._call("GET", "/admin/forms")["definitions"]
        self.assertTrue(any(x["code"] == "booking_form" for x in d))

    def test_design_validate_simulate_publish_via_api(self):
        did = self._call("POST", "/admin/forms", {"entity_type": "booking", "code": "api.form", "name": "Api Form"})["id"]
        vid = self._call("GET", "/admin/forms/api.form/versions")["versions"][0]["id"]
        self._call("POST", f"/admin/form-versions/{vid}/sections", {"code": "s1", "title": "Sec"})
        self._call("POST", f"/admin/form-versions/{vid}/fields", {"code": "note", "label": "Note", "data_type": "short_text", "section_code": "s1"})
        val = self._call("POST", f"/admin/form-versions/{vid}/validate", {})
        self.assertTrue(val["ok"])
        sim = self._call("POST", f"/admin/form-versions/{vid}/simulate", {"ctx": {"role": "admin"}, "values": {}})
        self.assertIn("note", sim["visible"])
        self._call("POST", f"/admin/form-versions/{vid}/approve", {})
        pub = self._call("POST", f"/admin/form-versions/{vid}/publish", {"change_reason": "go"})
        self.assertIn(pub["status"], ("ACTIVE", "PUBLISHED"))

    def test_runtime_effective_form_and_submit_via_api(self):
        eff = self._call("POST", "/admin/forms/effective", {"entity_type": "booking", "role": "admin"})
        self.assertTrue(any(f["code"] == "service_type" for f in eff["fields"]))
        r = self._call("POST", "/admin/forms/values", {"entity_type": "booking", "entity_id": 4242,
                                                        "values": {"service_type": "CRANE_RENTAL"}})
        self.assertEqual(r["stored"], 1)
        got = self._call("POST", "/admin/forms/values/get", {"entity_type": "booking", "entity_id": 4242})
        self.assertEqual(got["values"]["service_type"]["value"], "CRANE_RENTAL")


if __name__ == "__main__":
    unittest.main(verbosity=2)
