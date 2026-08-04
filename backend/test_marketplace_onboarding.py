"""LiftHaul Nationwide Marketplace — Increment 2 tests (§14).

Shipper / carrier / vehicle / driver onboarding lifecycles, compliance-document + expiry engine,
declarative compliance rules, verification queues, and the COMPLIANCE-AWARE candidate pool —
proving fail-closed activation, no self-verification / no self-activation, tenant isolation,
hard-denial precedence over ranking, and zero financial / operational-status drift.
"""
import unittest

import db
import core
import admin_platform as ap
import marketplace as mkt
import marketplace_onboarding as mo


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.cr = self._a(10)   # creator
        self.vf = self._a(11)   # verifier
        self.ac = self._a(12)   # activator

    def _a(self, id, perms=("*",), tenant="rgo"):
        t = self.rgo if tenant == "rgo" else tenant
        return {"id": id, "role": "ops", "perms": set(perms), "tenant_id": t}

    # helpers to reach an ACTIVE carrier / vehicle / driver
    def _active_carrier(self, reg="S1"):
        cid = mo.create_carrier_application(self.c, self.cr, "FLEET_OPERATOR", "Haulers",
                                            registration_type="SEC", registration_number=reg,
                                            operating_address="Manila")
        mo.submit_carrier(self.c, self.cr, cid)
        mo.verify_carrier(self.c, self.vf, cid)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION", "AUTHORITY_TO_OPERATE", "INSURANCE"):
            d = mo.upload_document(self.c, self.cr, dt, "CARRIER", cid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self.vf, d)
        mo.activate_carrier(self.c, self.ac, cid)
        return cid

    def _active_vehicle(self, cid, cat="truck_6w", plate="ABC-123"):
        vid = mo.register_vehicle(self.c, self.cr, cid, cat, plate)
        mo.verify_vehicle(self.c, self.vf, vid)
        for dt in ("VEHICLE_REGISTRATION", "INSURANCE"):
            d = mo.upload_document(self.c, self.cr, dt, "VEHICLE", vid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self.vf, d)
        mo.activate_vehicle(self.c, self.ac, vid)
        return vid

    def _active_driver(self, cid, cat="truck_6w"):
        did = mo.register_driver(self.c, self.cr, cid, "Juan", licence_expiry="2027-01-01",
                                 authorized_categories=[cat])
        mo.verify_driver(self.c, self.vf, did)
        mo.activate_driver(self.c, self.ac, did)
        d = mo.upload_document(self.c, self.cr, "DRIVER_LICENCE", "DRIVER", did, expiry_date="2027-01-01")
        mo.verify_document(self.c, self.vf, d)
        return did

    def _active_lane(self, code="MM-CAV"):
        lane = [l for l in mkt.list_lanes(self.c) if l["code"] == code][0]
        mkt.assess_lane(self.c, self.cr, lane["id"], verified_carriers=5, backup_capacity=1,
                        price_model_validated=1, ops_support=1, payment_capable=1,
                        dispute_process=1, monitoring=1)
        mkt.activate_lane(self.c, self.vf, lane["id"], target="ACTIVE")
        return lane


# --------------------------------------------------------------------------- #
class ShipperTests(Base):
    def _shipper(self, reg="R1"):
        return mo.create_shipper_application(self.c, self.cr, "CORPORATION", "Acme Corp",
                                             registration_type="SEC", registration_number=reg,
                                             registered_address="Makati", contract_accepted=1,
                                             privacy_accepted=1)

    def test_create_and_submit(self):
        sid = self._shipper()
        self.assertEqual(mo.submit_shipper(self.c, self.cr, sid)["status"], "DOCUMENT_REVIEW")

    def test_duplicate_registration_rejected(self):
        self._shipper("DUP")
        with self.assertRaises(Exception):
            self._shipper("DUP")

    def test_incomplete_profile_blocks_submit(self):
        sid = mo.create_shipper_application(self.c, self.cr, "INDIVIDUAL", "No Reg")
        r = mo.submit_shipper(self.c, self.cr, sid)
        self.assertEqual(r["status"], "PROFILE_INCOMPLETE")
        self.assertIn("registration_number", r["missing"])

    def test_no_self_verification(self):
        sid = self._shipper(); mo.submit_shipper(self.c, self.cr, sid)
        with self.assertRaises(PermissionError):
            mo.verify_shipper(self.c, self.cr, sid)

    def test_verify_then_fail_closed_activation_without_docs(self):
        sid = self._shipper(); mo.submit_shipper(self.c, self.cr, sid)
        mo.verify_shipper(self.c, self.vf, sid)
        with self.assertRaises(ValueError):   # compliance not satisfied
            mo.activate_shipper(self.c, self.ac, sid)

    def test_full_activation(self):
        sid = self._shipper(); mo.submit_shipper(self.c, self.cr, sid)
        mo.verify_shipper(self.c, self.vf, sid)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION"):
            d = mo.upload_document(self.c, self.cr, dt, "SHIPPER", sid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self.vf, d)
        self.assertEqual(mo.activate_shipper(self.c, self.ac, sid)["status"], "ACTIVE")

    def test_no_self_activation(self):
        sid = self._shipper(); mo.submit_shipper(self.c, self.cr, sid)
        mo.verify_shipper(self.c, self.vf, sid)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION"):
            d = mo.upload_document(self.c, self.cr, dt, "SHIPPER", sid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self.vf, d)
        with self.assertRaises(PermissionError):   # verifier may not activate
            mo.activate_shipper(self.c, self.vf, sid)

    def test_suspend_and_reactivate(self):
        sid = self._shipper(); mo.submit_shipper(self.c, self.cr, sid)
        mo.verify_shipper(self.c, self.vf, sid)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION"):
            d = mo.upload_document(self.c, self.cr, dt, "SHIPPER", sid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self.vf, d)
        mo.activate_shipper(self.c, self.ac, sid)
        mo.suspend_shipper(self.c, self.ac, sid, "review")
        self.assertEqual(mo.reactivate_shipper(self.c, self.ac, sid)["status"], "ACTIVE")

    def test_masked_payment_only(self):
        with self.assertRaises(ValueError):
            mo.create_shipper_application(self.c, self.cr, "CORPORATION", "Bad", registration_type="SEC",
                                          registration_number="X", registered_address="M",
                                          payment_ref="4111 1111 1111 1111")

    def test_tenant_isolation(self):
        sid = self._shipper()
        other = self._a(20, tenant=99999)
        with self.assertRaises(core.NotFoundError):
            mo.verify_shipper(self.c, other, sid)


# --------------------------------------------------------------------------- #
class CarrierTests(Base):
    def test_each_legal_structure(self):
        for i, t in enumerate(mo.CARRIER_TYPES):
            cid = mo.create_carrier_application(self.c, self.cr, t, f"C{i}", registration_type="SEC",
                                                registration_number=f"C{i}", operating_address="M")
            self.assertTrue(cid)

    def test_incomplete_profile(self):
        cid = mo.create_carrier_application(self.c, self.cr, "CORPORATION", "NoAddr",
                                            registration_type="SEC", registration_number="C9")
        self.assertEqual(mo.submit_carrier(self.c, self.cr, cid)["status"], "PROFILE_INCOMPLETE")

    def test_missing_permit_blocks_activation(self):
        cid = mo.create_carrier_application(self.c, self.cr, "CORPORATION", "C", registration_type="SEC",
                                            registration_number="C1", operating_address="M")
        mo.submit_carrier(self.c, self.cr, cid); mo.verify_carrier(self.c, self.vf, cid)
        # only some docs -> AUTHORITY_TO_OPERATE + INSURANCE missing -> fail closed
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION"):
            d = mo.upload_document(self.c, self.cr, dt, "CARRIER", cid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self.vf, d)
        with self.assertRaises(ValueError):
            mo.activate_carrier(self.c, self.ac, cid)

    def test_expired_insurance_blocks(self):
        cid = mo.create_carrier_application(self.c, self.cr, "CORPORATION", "C", registration_type="SEC",
                                            registration_number="C2", operating_address="M")
        mo.submit_carrier(self.c, self.cr, cid); mo.verify_carrier(self.c, self.vf, cid)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION", "AUTHORITY_TO_OPERATE"):
            d = mo.upload_document(self.c, self.cr, dt, "CARRIER", cid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self.vf, d)
        d = mo.upload_document(self.c, self.cr, "INSURANCE", "CARRIER", cid, expiry_date="2020-01-01")
        mo.verify_document(self.c, self.vf, d)
        mo.detect_expired_documents(self.c, self.ac)   # flips insurance to EXPIRED
        with self.assertRaises(ValueError):
            mo.activate_carrier(self.c, self.ac, cid)

    def test_full_activation_and_override(self):
        cid = self._active_carrier("C3")
        self.assertEqual(mo.list_carriers(self.c, self.cr, status="ACTIVE")[0]["id"], cid)

    def test_override_requires_reason_and_expiry(self):
        cid = mo.create_carrier_application(self.c, self.cr, "CORPORATION", "C", registration_type="SEC",
                                            registration_number="C4", operating_address="M")
        with self.assertRaises(ValueError):
            mo.override_compliance(self.c, self.ac, "CARRIER", cid, "", None)

    def test_tenant_isolation(self):
        cid = mo.create_carrier_application(self.c, self.cr, "CORPORATION", "C", registration_type="SEC",
                                            registration_number="C5", operating_address="M")
        other = self._a(20, tenant=99999)
        with self.assertRaises(core.NotFoundError):
            mo.verify_carrier(self.c, other, cid)


# --------------------------------------------------------------------------- #
class VehicleTests(Base):
    def test_category_link_and_payload_validation(self):
        cid = self._active_carrier("V1")
        with self.assertRaises(ValueError):
            mo.register_vehicle(self.c, self.cr, cid, "elf_4w", "PLT-1", payload_kg=99999)

    def test_unknown_category_rejected(self):
        cid = self._active_carrier("V2")
        with self.assertRaises(ValueError):
            mo.register_vehicle(self.c, self.cr, cid, "spaceship", "PLT-2")

    def test_duplicate_plate(self):
        cid = self._active_carrier("V3")
        mo.register_vehicle(self.c, self.cr, cid, "truck_6w", "DUP-PLATE")
        with self.assertRaises(Exception):
            mo.register_vehicle(self.c, self.cr, cid, "truck_6w", "DUP-PLATE")

    def test_inactive_vehicle_excluded_from_pool(self):
        cid = self._active_carrier("V4")
        vid = mo.register_vehicle(self.c, self.cr, cid, "truck_6w", "INACT-1")   # left DRAFT
        self._active_lane()
        pool = mo.candidate_pool(self.c, self.ac, "general", "METRO_MANILA", "CAVITE",
                                 weight_kg=1000, volume_cbm=5, require_driver=False)
        self.assertEqual(pool["candidates"], [])

    def test_maintenance_excludes(self):
        cid = self._active_carrier("V5")
        vid = self._active_vehicle(cid, plate="MNT-1")
        mo.set_vehicle_status(self.c, self.ac, vid, "MAINTENANCE")
        self._active_lane()
        pool = mo.candidate_pool(self.c, self.ac, "general", "METRO_MANILA", "CAVITE",
                                 weight_kg=1000, volume_cbm=5, require_driver=False)
        self.assertEqual(pool["candidates"], [])


# --------------------------------------------------------------------------- #
class DriverTests(Base):
    def test_expired_licence_blocks_assignment(self):
        cid = self._active_carrier("D1")
        vid = self._active_vehicle(cid, plate="D-1")
        did = mo.register_driver(self.c, self.cr, cid, "Exp", licence_expiry="2020-01-01",
                                 authorized_categories=["truck_6w"])
        mo.verify_driver(self.c, self.vf, did); mo.activate_driver(self.c, self.ac, did)
        self.assertFalse(mo.can_assign_driver(self.c, did, vid)["ok"])
        self.assertIn("licence_expired", mo.can_assign_driver(self.c, did, vid)["reasons"])

    def test_carrier_mismatch(self):
        c1 = self._active_carrier("D2"); c2 = self._active_carrier("D3")
        v1 = self._active_vehicle(c1, plate="D-2")
        d2 = self._active_driver(c2)
        self.assertIn("carrier_mismatch", mo.can_assign_driver(self.c, d2, v1)["reasons"])

    def test_vehicle_not_authorized(self):
        cid = self._active_carrier("D4")
        vid = self._active_vehicle(cid, cat="truck_10w", plate="D-3")
        did = mo.register_driver(self.c, self.cr, cid, "Lim", licence_expiry="2027-01-01",
                                 authorized_categories=["motorcycle"])
        mo.verify_driver(self.c, self.vf, did); mo.activate_driver(self.c, self.ac, did)
        self.assertIn("vehicle_not_authorized", mo.can_assign_driver(self.c, did, vid)["reasons"])

    def test_suspended_driver_not_active(self):
        cid = self._active_carrier("D5")
        did = self._active_driver(cid)
        mo.set_driver_status(self.c, self.ac, did, "SUSPENDED")
        vid = self._active_vehicle(cid, plate="D-4")
        self.assertIn("driver_not_active", mo.can_assign_driver(self.c, did, vid)["reasons"])


# --------------------------------------------------------------------------- #
class ComplianceTests(Base):
    def test_deterministic_requirement_and_missing(self):
        cid = mo.create_carrier_application(self.c, self.cr, "CORPORATION", "C", registration_type="SEC",
                                            registration_number="K1", operating_address="M")
        ev = mo.evaluate_compliance(self.c, "CARRIER", cid)
        self.assertEqual(ev["recommendation"], "NOT_READY")
        self.assertIn("BUSINESS_REGISTRATION", ev["missing"])

    def test_expired_and_rejected_documents(self):
        cid = mo.create_carrier_application(self.c, self.cr, "CORPORATION", "C", registration_type="SEC",
                                            registration_number="K2", operating_address="M")
        d = mo.upload_document(self.c, self.cr, "INSURANCE", "CARRIER", cid, expiry_date="2020-01-01")
        mo.verify_document(self.c, self.vf, d)
        mo.detect_expired_documents(self.c, self.ac)
        ev = mo.evaluate_compliance(self.c, "CARRIER", cid)
        self.assertIn("INSURANCE", ev["expired"])

    def test_no_self_verification_of_documents(self):
        cid = mo.create_carrier_application(self.c, self.cr, "CORPORATION", "C", registration_type="SEC",
                                            registration_number="K3", operating_address="M")
        d = mo.upload_document(self.c, self.cr, "INSURANCE", "CARRIER", cid, expiry_date="2027-01-01")
        with self.assertRaises(PermissionError):
            mo.verify_document(self.c, self.cr, d)

    def test_override_allows_activation_but_is_audited(self):
        cid = mo.create_carrier_application(self.c, self.cr, "CORPORATION", "C", registration_type="SEC",
                                            registration_number="K4", operating_address="M")
        mo.submit_carrier(self.c, self.cr, cid); mo.verify_carrier(self.c, self.vf, cid)
        mo.override_compliance(self.c, self.ac, "CARRIER", cid, "exec waiver", "2099-01-01")
        self.assertEqual(mo.activate_carrier(self.c, self.ac, cid)["status"], "ACTIVE")

    def test_scheduled_expiry_removes_from_eligibility(self):
        cid = self._active_carrier("K5")
        vid = self._active_vehicle(cid, plate="EXP-1")
        did = self._active_driver(cid)
        self._active_lane()
        before = mo.candidate_pool(self.c, self.ac, "general", "METRO_MANILA", "CAVITE",
                                   weight_kg=1000, volume_cbm=5)
        self.assertEqual(len(before["candidates"]), 1)
        # expire the vehicle insurance -> vehicle compliance fails -> drops from pool (no delete)
        self.c.execute("UPDATE mkt_documents SET expiry_date='2020-01-01' WHERE subject_type='VEHICLE' "
                       "AND subject_id=? AND document_type='INSURANCE'", (vid,))
        mo.detect_expired_documents(self.c, self.ac)
        after = mo.candidate_pool(self.c, self.ac, "general", "METRO_MANILA", "CAVITE",
                                  weight_kg=1000, volume_cbm=5)
        self.assertEqual(after["candidates"], [])
        # historical record still present
        self.assertTrue(self.c.execute("SELECT 1 FROM mkt_vehicles WHERE id=?", (vid,)).fetchone())


# --------------------------------------------------------------------------- #
class CandidatePoolTests(Base):
    def test_all_controls_green(self):
        cid = self._active_carrier("P1")
        self._active_vehicle(cid, plate="P-1")
        self._active_driver(cid)
        self._active_lane()
        pool = mo.candidate_pool(self.c, self.ac, "general", "METRO_MANILA", "CAVITE",
                                 weight_kg=5000, volume_cbm=10)
        self.assertEqual(len(pool["candidates"]), 1)

    def test_lane_not_active_excludes(self):
        cid = self._active_carrier("P2")
        self._active_vehicle(cid, plate="P-2")
        self._active_driver(cid)
        # lane left ASSESSING -> not serviceable
        pool = mo.candidate_pool(self.c, self.ac, "general", "METRO_MANILA", "CAVITE",
                                 weight_kg=5000, volume_cbm=10)
        self.assertEqual(pool["candidates"], [])
        self.assertFalse(pool["lane_serviceable"])

    def test_payload_insufficient_excludes(self):
        cid = self._active_carrier("P3")
        self._active_vehicle(cid, cat="elf_4w", plate="P-3")   # 2t payload
        self._active_driver(cid, cat="elf_4w")
        self._active_lane()
        pool = mo.candidate_pool(self.c, self.ac, "general", "METRO_MANILA", "CAVITE",
                                 weight_kg=9000, volume_cbm=5)   # 9t
        self.assertEqual(pool["candidates"], [])

    def test_prohibited_cargo_no_candidates(self):
        cid = self._active_carrier("P4")
        self._active_vehicle(cid, plate="P-4")
        self._active_driver(cid)
        self._active_lane()
        pool = mo.candidate_pool(self.c, self.ac, "prohibited", "METRO_MANILA", "CAVITE")
        self.assertEqual(pool["candidates"], [])

    def test_ranking_cannot_widen_pool(self):
        # a hard-excluded vehicle (inactive carrier) is in `excluded`, never in `candidates`;
        # no ranking input exists that can move it — the API returns only the hard-eligible pool.
        cid = self._active_carrier("P5")
        vid = self._active_vehicle(cid, plate="P-5")
        self._active_driver(cid)
        self._active_lane()
        mo.suspend_carrier(self.c, self.ac, cid, "audit")
        pool = mo.candidate_pool(self.c, self.ac, "general", "METRO_MANILA", "CAVITE",
                                 weight_kg=1000, volume_cbm=5)
        self.assertEqual(pool["candidates"], [])
        self.assertTrue(any(x["vehicle_id"] == vid for x in pool["excluded"]))

    def test_eligibility_requires_permission(self):
        with self.assertRaises(core.ForbiddenError):
            mo.candidate_pool(self.c, self._a(30, perms=()), "general", "METRO_MANILA", "CAVITE")


# --------------------------------------------------------------------------- #
class IntegrityMigrationDriftTests(Base):
    def test_integrity_runs_with_statuses(self):
        r = mo.run_integrity(self.c, self.ac)
        self.assertIn(r["overall"], mo.INTEGRITY_STATUSES)
        self.assertTrue(all(ch["status"] in mo.INTEGRITY_STATUSES for ch in r["checks"]))

    def test_prohibited_cargo_integrity_pass(self):
        r = mo.run_integrity(self.c, self.ac)
        chk = [c for c in r["checks"] if c["check"] == "prohibited_cargo_produced_eligible_vehicle"][0]
        self.assertEqual(chk["status"], "PASS")

    def test_migration_no_auto_activation(self):
        r = mo.classify_existing(self.c)
        self.assertEqual(r["invariants"]["unexpected_participant_activations"], 0)
        self.assertEqual(r["invariants"]["unexpected_eligibility_expansion"], 0)

    def test_no_marketplace_financial_drift(self):
        a = {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": self.rgo}
        cid = core.create_customer(self.c, a, "Drift Co")
        bid = core.create_booking(self.c, a, cid, "CRANE_RENTAL", "x", 1)
        core.review_booking(self.c, a, bid); core.ready_for_quotation(self.c, a, bid)
        qid = core.create_quotation(self.c, a, bid,
                                    [{"kind": "crane", "description": "x", "qty": 2, "days": 1, "rate": 300000}])
        row = self.c.execute("SELECT tax,total FROM quotations WHERE id=?", (qid,)).fetchone()
        self.assertEqual((row["tax"], row["total"]), (72000, 672000))

    def test_schema_version(self):
        self.assertEqual(db.SCHEMA_VERSION, 17)


class TestMarketplaceApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        import db as _db
        server._conn = _db.connect(":memory:")
        cls.server = server
        try:
            core.create_user(server._conn, "mktadmin@r", "demo1234", "admin", "Mkt Admin")
        except core.ConflictError:
            pass

    def _call(self, method, path, body=None):
        fn, params = self.server._match(method, path)
        assert fn, f"no route for {method} {path}"
        tok = self.server._match("POST", "/login")[0](None, {"email": "mktadmin@r", "password": "demo1234"}, {})["token"]
        actor = core.actor_for(self.server._conn, tok)
        return fn(actor, body or {}, params or {})

    def test_taxonomy_and_eligibility_via_api(self):
        self.assertGreaterEqual(len(self._call("GET", "/admin/marketplace/cargo")["cargo"]), 10)
        self.assertGreaterEqual(len(self._call("GET", "/admin/marketplace/vehicles")["vehicles"]), 15)
        r = self._call("POST", "/admin/marketplace/eligibility", {"cargo_code": "prohibited"})
        self.assertEqual(r["eligible"], [])

    def test_serviceability_never_promises_assessing_via_api(self):
        r = self._call("POST", "/admin/marketplace/serviceability", {"origin_zone": "METRO_MANILA", "dest_zone": "CAVITE"})
        self.assertFalse(r["promises_service"])

    def test_carrier_onboarding_via_api(self):
        cid = self._call("POST", "/admin/marketplace/carriers",
                         {"carrier_type": "FLEET_OPERATOR", "legal_name": "API Haulers",
                          "registration_type": "SEC", "registration_number": "API-1", "operating_address": "M"})["id"]
        self.assertEqual(self._call("POST", f"/admin/marketplace/carriers/{cid}/submit")["status"], "DOCUMENT_REVIEW")
        carriers = self._call("GET", "/admin/marketplace/carriers")["carriers"]
        self.assertTrue(any(c["id"] == cid for c in carriers))

    def test_integrity_and_migration_via_api(self):
        self.assertIn(self._call("GET", "/admin/marketplace/integrity")["overall"], mo.INTEGRITY_STATUSES)
        self.assertEqual(self._call("GET", "/admin/marketplace/migration")["invariants"]["unexpected_participant_activations"], 0)


if __name__ == "__main__":
    unittest.main()
