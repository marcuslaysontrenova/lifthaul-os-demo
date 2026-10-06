"""Revenue & Compliance DNA — catalog, evidence, SoD and fail-closed runtime guard."""
import os
import unittest
from unittest import mock

import db
import core
import revenue_dna as rd


class RevenueDnaTest(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.maker = self.actor(101)
        self.verifier = self.actor(102)
        self.approver = self.actor(103)
        self.activator = self.actor(104)

    @staticmethod
    def actor(actor_id):
        return {"id": actor_id, "role": "admin", "perms": {"*"}, "tenant_id": None}

    def complete_evidence(self, code):
        detail = rd.get_scheme(self.c, self.maker, code)
        for control in detail["controls"]:
            result = rd.submit_evidence(
                self.c, self.maker, code, control["code"],
                f"GOV-REPO/{code}/{control['code']}/v1",
                description="Verified source document; content remains in governed repository.",
                issued_by="Accountable authority",
                expires_at="2099-12-31",
            )
            rd.verify_evidence(self.c, self.verifier, result["evidence_id"], "VERIFIED")

    def activate_scheme(self, code):
        self.complete_evidence(code)
        rd.submit_for_approval(self.c, self.maker, code, "all controls evidenced")
        rd.approve(self.c, self.approver, code, "independent legal/commercial approval")
        return rd.activate(self.c, self.activator, code, "controlled launch")

    def test_seeded_catalog_covers_core_and_regulated_income(self):
        schemes = rd.list_schemes(self.c, self.maker)
        codes = {s["code"] for s in schemes}
        self.assertGreaterEqual(len(codes), 13)
        self.assertIn("provider_success_fee", codes)
        self.assertIn("heavy_equipment_rental", codes)
        self.assertIn("insurance_referral", codes)
        self.assertIn("api_white_label", codes)
        self.assertTrue(all(s["status"] != "ACTIVE" for s in schemes))

    def test_activation_fails_closed_without_evidence(self):
        with self.assertRaises(core.ForbiddenError):
            rd.submit_for_approval(self.c, self.maker, "provider_success_fee")
        state = rd.readiness(self.c, "provider_success_fee")
        self.assertFalse(state["ready"])
        self.assertIn("business_registration", state["missing_controls"])

    def test_evidence_verifier_must_differ_from_submitter(self):
        result = rd.submit_evidence(self.c, self.maker, "provider_success_fee",
                                    "business_registration", "GOV-REPO/SEC/1")
        with self.assertRaises(core.ForbiddenError):
            rd.verify_evidence(self.c, self.maker, result["evidence_id"], "VERIFIED")

    def test_submit_approve_activate_enforces_three_actor_separation(self):
        code = "provider_success_fee"
        self.complete_evidence(code)
        rd.submit_for_approval(self.c, self.maker, code)
        with self.assertRaises(core.ForbiddenError):
            rd.approve(self.c, self.maker, code)
        rd.approve(self.c, self.approver, code)
        with self.assertRaises(core.ForbiddenError):
            rd.activate(self.c, self.approver, code)
        active = rd.activate(self.c, self.activator, code)
        self.assertEqual(active["status"], "ACTIVE")
        self.assertTrue(active["readiness"]["ready"])
        self.assertGreaterEqual(len(active["history"]), 3)

    def test_regulated_insurance_stream_requires_licensing_and_partner_controls(self):
        detail = rd.get_scheme(self.c, self.maker, "insurance_referral")
        control_codes = {c["code"] for c in detail["controls"]}
        self.assertEqual(detail["regulatory_level"], "LICENCE_REQUIRED")
        self.assertIn("insurance_licence", control_codes)
        self.assertIn("licensed_insurance_partner", control_codes)

    def test_payment_dna_contains_full_collection_settlement_and_payout_controls(self):
        detail = rd.get_scheme(self.c, self.maker, rd.PAYMENT_DNA_SCHEME)
        controls = {c["code"] for c in detail["controls"]}
        self.assertGreaterEqual(len(controls), 25)
        self.assertTrue({
            "merchant_operating_role", "bsp_ops_status", "merchant_acquisition_authority",
            "marketplace_capability", "channel_certification", "payment_webhooks",
            "payout_controls", "reconciliation", "postgres_recovery", "kyc_aml_fraud",
            "controlled_live_pilot", "real_money_evidence", "reconciliation_soak",
            "go_live_signoff",
        }.issubset(controls))

    def test_real_money_payment_guard_ignores_observe_mode_and_fails_closed(self):
        with mock.patch.dict(os.environ, {"REVENUE_DNA_ENFORCEMENT": "observe"}):
            with self.assertRaises(core.ForbiddenError):
                rd.guard_payment_operation(self.c, self.maker, "attempt_direct_payout")

    def test_expired_evidence_does_not_count_as_ready(self):
        result = rd.submit_evidence(self.c, self.maker, "api_white_label", "business_registration",
                                    "GOV-REPO/SEC/OLD", expires_at="2099-01-01")
        rd.verify_evidence(self.c, self.verifier, result["evidence_id"], "VERIFIED")
        self.c.execute("UPDATE revenue_evidence SET expires_at='2000-01-01' WHERE id=?",
                       (result["evidence_id"],))
        self.c.commit()
        state = rd.readiness(self.c, "api_white_label")
        self.assertFalse(state["ready"])
        self.assertIn("business_registration", state["expired_controls"])

    def test_runtime_guard_observes_during_migration_and_blocks_when_enforced(self):
        with mock.patch.dict(os.environ, {"REVENUE_DNA_ENFORCEMENT": "observe"}):
            observed = rd.guard(self.c, self.maker, "heavy_equipment_rental", operation="invoice")
            self.assertFalse(observed["allowed"])
            self.assertEqual(observed["enforcement_mode"], "observe")
        with mock.patch.dict(os.environ, {"REVENUE_DNA_ENFORCEMENT": "enforce"}):
            with self.assertRaises(core.ForbiddenError):
                rd.guard(self.c, self.maker, "heavy_equipment_rental", operation="invoice")

    def test_active_scheme_passes_enforced_guard(self):
        self.activate_scheme("enterprise_subscription")
        with mock.patch.dict(os.environ, {"REVENUE_DNA_ENFORCEMENT": "enforce"}):
            result = rd.guard(self.c, self.maker, "enterprise_subscription", operation="bill")
        self.assertTrue(result["allowed"])

    def test_active_scheme_is_immutable_until_suspended(self):
        self.activate_scheme("customer_platform_fee")
        with self.assertRaises(core.ConflictError):
            rd.update_scheme(self.c, self.maker, "customer_platform_fee", notes="silent live edit")
        suspended = rd.suspend(self.c, self.maker, "customer_platform_fee", "annual legal review")
        self.assertEqual(suspended["status"], "SUSPENDED")


if __name__ == "__main__":
    unittest.main()
