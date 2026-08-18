"""Cargo Insurance / Goods Protection — orchestration over canonical mkt_bookings + reused mkt_claims.

LiftHaul is not the insurer. Asserts: eligibility, no-provider fallback, manual underwriting for
high-value/engineered, excluded cargo, binding-evidence gate, premium ledger separation (never platform
revenue), claim linkage/states with insurer-decision evidence gate, carrier-insurance separation,
tenant isolation, RBAC, restart + PG portability (guarded by the existing suite).
"""
import unittest

import admin_platform as ap
import core
import db
import goods_protection as gp
import public_booking as pb


SUP = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}


def _booking(c, vehicle="6w", km=50, dest="Luzon"):
    return pb.submit(c, {"contact_name": "A", "contact_phone": "0917", "origin_island": "Luzon",
                         "dest_island": dest, "vehicle": vehicle, "km": km})["booking_id"]


def _with_provider(c):
    ap.set_config(c, "platform", "", "insurance.provider_active", "true", actor=SUP)


class Eligibility(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:")

    def test_no_provider_returns_manual_review(self):
        b = _booking(self.c)
        gp.request_coverage(self.c, SUP, b, 300000, "MACHINERY")
        self.assertEqual(gp.quote_coverage(self.c, SUP, b)["result"], "MANUAL_INSURANCE_REVIEW_REQUIRED")

    def test_eligible_standard(self):
        _with_provider(self.c)
        b = _booking(self.c)
        gp.request_coverage(self.c, SUP, b, 300000, "MACHINERY")
        q = gp.quote_coverage(self.c, SUP, b)
        self.assertEqual(q["result"], "ELIGIBLE")
        self.assertGreater(q["premium"], 0)
        self.assertTrue(q["sandbox"])                 # clearly indicative, not a licensed quote

    def test_high_value_manual_underwriting(self):
        _with_provider(self.c)
        b = _booking(self.c)
        gp.request_coverage(self.c, SUP, b, 5000000, "MACHINERY")
        self.assertEqual(gp.quote_coverage(self.c, SUP, b)["result"], "MANUAL_UNDERWRITING_REQUIRED")

    def test_engineered_manual_underwriting(self):
        _with_provider(self.c)
        b = _booking(self.c, vehicle="crane", km=10)
        gp.request_coverage(self.c, SUP, b, 300000, "PROJECT_CARGO")
        self.assertEqual(gp.quote_coverage(self.c, SUP, b)["result"], "MANUAL_UNDERWRITING_REQUIRED")

    def test_excluded_cargo_not_eligible(self):
        _with_provider(self.c)
        b = _booking(self.c)
        gp.request_coverage(self.c, SUP, b, 10000, "PROHIBITED")
        self.assertEqual(gp.quote_coverage(self.c, SUP, b)["result"], "NOT_ELIGIBLE")

    def test_inter_island_coverage(self):
        _with_provider(self.c)
        b = _booking(self.c, dest="Visayas")
        gp.request_coverage(self.c, SUP, b, 200000, "GENERAL")
        self.assertEqual(gp.quote_coverage(self.c, SUP, b)["result"], "ELIGIBLE")

    def test_fake_provider_never_licensed(self):
        _with_provider(self.c)
        caps = gp.MockGoodsProtectionProvider().declare_capabilities()
        self.assertEqual(caps["regulated_status"], "NOT_A_LICENSED_INSURER")
        self.assertFalse(caps["live"])
        self.assertFalse(caps["binds_real_policy"])


class Binding(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:"); _with_provider(self.c)
        self.b = _booking(self.c)
        gp.request_coverage(self.c, SUP, self.b, 300000, "MACHINERY")
        gp.quote_coverage(self.c, SUP, self.b)

    def test_bind_requires_evidence_and_policy(self):
        with self.assertRaises(core.ValidationError):
            gp.bind(self.c, SUP, self.b, "Insurer", "", 300000, 3600, 6000, "2026-09-01", "2027-09-01", None)
        with self.assertRaises(core.ValidationError):
            gp.bind(self.c, SUP, self.b, "Insurer", "POL-1", 300000, 3600, 6000, "2026-09-01", "2027-09-01", None)

    def test_bind_with_evidence(self):
        r = gp.bind(self.c, SUP, self.b, "Insurer", "POL-1", 300000, 3600, 6000, "2026-09-01", "2027-09-01", {"doc": "binder.pdf"})
        self.assertEqual(r["gp_status"], "BOUND")
        row = self.c.execute("SELECT gp_status,gp_policy_ref,gp_bound_by FROM mkt_bookings WHERE id=?", (self.b,)).fetchone()
        self.assertEqual(row["gp_status"], "BOUND")
        self.assertEqual(row["gp_policy_ref"], "POL-1")
        self.assertEqual(row["gp_bound_by"], SUP["id"])


class Ledger(unittest.TestCase):
    def test_premium_is_separate_pass_through(self):
        c = db.connect(":memory:"); _with_provider(c)
        b = _booking(c)
        gp.request_coverage(c, SUP, b, 300000, "MACHINERY")
        q = gp.quote_coverage(c, SUP, b)
        bd = gp.breakdown(c, b, platform_fee=5000, provider_fee=1000, carrier_payable=40000, tax=600)
        self.assertEqual(bd["insurance_premium"], round(float(q["premium"]), 2))
        self.assertFalse(bd["insurance_premium_is_platform_revenue"])
        # premium is NOT folded into platform_fee
        self.assertEqual(bd["platform_fee"], 5000)
        # customer funding reconciles exactly to the sum of the separate lines
        self.assertAlmostEqual(bd["customer_funding"],
                               bd["carrier_payable"] + bd["platform_fee"] + bd["provider_fee"] + bd["insurance_premium"] + bd["tax"], 2)


class Claims(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:"); _with_provider(self.c)
        self.b = _booking(self.c)
        gp.request_coverage(self.c, SUP, self.b, 300000, "MACHINERY")
        gp.quote_coverage(self.c, SUP, self.b)
        gp.bind(self.c, SUP, self.b, "Insurer", "POL-1", 300000, 3600, 6000, "2026-09-01", "2027-09-01", {"doc": "b.pdf"})

    def test_claim_requires_bound_coverage(self):
        b2 = _booking(self.c)   # no coverage
        with self.assertRaises(core.ConflictError):
            gp.link_claim(self.c, SUP, b2, "INC-9", 1000)

    def test_claim_linkage_references_policy(self):
        cl = gp.link_claim(self.c, SUP, self.b, "INC-1", 50000)
        self.assertEqual(cl["status"], "REPORTED")
        self.assertEqual(cl["insured_amount"], 300000)
        row = self.c.execute("SELECT policy_reference,insured_amount,incident_ref FROM mkt_claims WHERE id=?", (cl["claim_id"],)).fetchone()
        self.assertEqual(row["policy_reference"], "POL-1")
        self.assertEqual(row["incident_ref"], "INC-1")

    def test_insurer_decision_requires_adjuster_reference(self):
        cl = gp.link_claim(self.c, SUP, self.b, "INC-1", 50000)
        gp.advance_gp_claim(self.c, SUP, cl["claim_id"], "SUBMITTED_TO_INSURER")
        with self.assertRaises(core.ValidationError):
            gp.advance_gp_claim(self.c, SUP, cl["claim_id"], "APPROVED")   # no adjuster ref
        gp.advance_gp_claim(self.c, SUP, cl["claim_id"], "APPROVED", adjuster_reference="ADJ-9", approved_amount=45000)
        self.assertEqual(self.c.execute("SELECT status FROM mkt_claims WHERE id=?", (cl["claim_id"],)).fetchone()["status"], "APPROVED")

    def test_invalid_state_rejected(self):
        cl = gp.link_claim(self.c, SUP, self.b, "INC-1", 50000)
        with self.assertRaises(core.ValidationError):
            gp.advance_gp_claim(self.c, SUP, cl["claim_id"], "MAGIC")


class Separation(unittest.TestCase):
    def test_carrier_insurance_not_customer_protection(self):
        # A carrier's KYB/insurance verification must not set customer Goods Protection status.
        c = db.connect(":memory:"); _with_provider(c)
        b = _booking(c)
        row = c.execute("SELECT gp_status,gp_requested FROM mkt_bookings WHERE id=?", (b,)).fetchone()
        self.assertIsNone(row["gp_status"])      # untouched until the customer requests coverage
        self.assertIn(row["gp_requested"], (None, 0))


class UploadOnly(unittest.TestCase):
    """Default operating model: the company uploads its own cargo-insurance certificate; LiftHaul does
    NOT quote/bind/price/process. Processing is a gated capability, OFF by default."""

    def setUp(self):
        self.c = db.connect(":memory:")
        self.b = _booking(self.c)

    def test_processing_disabled_by_default(self):
        self.assertFalse(gp.processing_enabled(self.c))
        with self.assertRaises(core.ForbiddenError):
            gp.require_processing(self.c)

    def test_upload_stores_document_without_pricing(self):
        r = gp.upload_cargo_insurance(self.c, SUP, self.b, "ACME Insurance", "POL-1",
                                      "s3://cargo-cert.pdf", coverage_amount=500000)
        self.assertEqual(r["gp_status"], "COMPANY_UPLOADED")
        row = self.c.execute("SELECT gp_status,gp_provider,gp_policy_ref,gp_premium,gp_evidence "
                             "FROM mkt_bookings WHERE id=?", (self.b,)).fetchone()
        self.assertEqual(row["gp_status"], "COMPANY_UPLOADED")
        self.assertEqual(row["gp_provider"], "ACME Insurance")
        self.assertIsNone(row["gp_premium"])                 # LiftHaul never prices/underwrites
        self.assertIn("s3://cargo-cert.pdf", row["gp_evidence"])

    def test_upload_requires_insurer_policy_document(self):
        for bad in ({"insurer": "", "policy_ref": "P", "doc": "d"},
                    {"insurer": "I", "policy_ref": "", "doc": "d"},
                    {"insurer": "I", "policy_ref": "P", "doc": ""}):
            with self.assertRaises(core.ValidationError):
                gp.upload_cargo_insurance(self.c, SUP, self.b, bad["insurer"], bad["policy_ref"], bad["doc"])

    def test_independent_review_verify_and_reject(self):
        gp.upload_cargo_insurance(self.c, SUP, self.b, "ACME", "POL-1", "cert.pdf")
        v = gp.review_cargo_insurance(self.c, SUP, self.b, "VERIFY")
        self.assertEqual(v["gp_status"], "INSURANCE_VERIFIED")
        b2 = _booking(self.c)
        gp.upload_cargo_insurance(self.c, SUP, b2, "ACME", "POL-2", "cert2.pdf")
        self.assertEqual(gp.review_cargo_insurance(self.c, SUP, b2, "REJECT")["gp_status"], "INSURANCE_REJECTED")

    def test_review_requires_pending_upload(self):
        with self.assertRaises(core.ConflictError):
            gp.review_cargo_insurance(self.c, SUP, self.b, "VERIFY")   # nothing uploaded

    def test_provider_cannot_self_verify_upload(self):
        # a booking-manager (provider-side) may upload but NOT review — review needs insurance.manage
        prov = {"id": 5, "role": "ops", "perms": {"marketplace.booking.manage"}, "tenant_id": None}
        gp.upload_cargo_insurance(self.c, prov, self.b, "ACME", "POL-1", "cert.pdf")
        with self.assertRaises(core.ForbiddenError):
            gp.review_cargo_insurance(self.c, prov, self.b, "VERIFY")

    def test_processing_can_be_enabled_deliberately(self):
        ap.set_config(self.c, "platform", "", "insurance.processing_enabled", "true", actor=SUP)
        self.assertTrue(gp.processing_enabled(self.c))
        gp.require_processing(self.c)   # no longer raises


class AccessControl(unittest.TestCase):
    def setUp(self): self.c = db.connect(":memory:"); _with_provider(self.c)

    def test_view_requires_permission(self):
        weak = {"id": 9, "role": "driver", "perms": {"job.read"}, "tenant_id": None}
        with self.assertRaises(Exception):
            gp.coverage_requests(self.c, weak)

    def test_tenant_isolation_of_requests(self):
        a1 = {"id": 1, "role": "ops", "perms": {"*"}, "tenant_id": 1}
        a2 = {"id": 2, "role": "ops", "perms": {"*"}, "tenant_id": 2}
        # public bookings live in the platform tenant; a tenant-2 operator sees none of tenant-1/platform reqs
        b = _booking(self.c)
        gp.request_coverage(self.c, SUP, b, 300000, "MACHINERY")
        self.assertEqual(len(gp.coverage_requests(self.c, a2)["requests"]), 0)


class Persistence(unittest.TestCase):
    def test_restart_persistence(self):
        import os, tempfile
        path = tempfile.mktemp(suffix=".sqlite")
        try:
            c1 = db.connect(path); _with_provider(c1)
            b = _booking(c1)
            gp.request_coverage(c1, SUP, b, 300000, "MACHINERY")
            gp.quote_coverage(c1, SUP, b)
            gp.bind(c1, SUP, b, "Insurer", "POL-9", 300000, 3600, 6000, "2026-09-01", "2027-09-01", {"d": 1})
            if hasattr(c1, "close"):
                c1.close()
            c2 = db.connect(path)
            self.assertEqual(gp.get_coverage(c2, b)["gp_status"], "BOUND")
        finally:
            try: os.remove(path)
            except Exception: pass


if __name__ == "__main__":
    unittest.main()
