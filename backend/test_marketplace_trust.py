"""LiftHaul Marketplace — Trust / KYB / fraud / trust-score tests.

Covers the trust-layer controls from the increment directive: KYB verification state machine,
never-fabricated verification (adapters), hard eligibility gating, trust-score-cannot-override-
compliance, fraud blocking, separation of duties, immutable KYB history, tenant isolation and
restart persistence. Existing marketplace eligibility / payment-gating controls are untouched.
"""
import os
import tempfile
import unittest

import admin_platform as ap
import core
import db
import marketplace_onboarding as ob
import marketplace_trust as mt
import tenant


class TrustLayerTests(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.sup = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
        # two distinct staff so separation-of-duties is real
        self.a1 = self._staff("t1@mk")
        self.a2 = self._staff("t2@mk")

    def _staff(self, email):
        core.create_user(self.c, email, "pw", "admin", "S")   # 'admin' core role has '*'
        return core.actor_for(self.c, core.login(self.c, email, "pw"))

    def _carrier(self, actor, name="Acme Hauling"):
        return ob.create_carrier_application(self.c, actor, "CORPORATION", name,
                                             registration_type="SEC", registration_number="SEC-"+name[:3])

    # ---- A: a document upload is SUBMITTED, not VERIFIED ----
    def test_submit_is_not_verification(self):
        cid = self._carrier(self.a1)
        kid = mt.submit_kyb(self.c, self.a2, "CARRIER", cid, "SEC", "SEC-123", "Acme Inc")
        self.assertEqual(self.c.execute("SELECT status FROM mkt_kyb_profiles WHERE id=?", (kid,)).fetchone()["status"],
                         "SUBMITTED")
        self.assertNotIn(mt.carrier_kyb_status(self.c, cid), mt._TERMINAL_OK)

    # ---- B: adapters never fabricate verification ----
    def test_adapter_never_fabricates_verified(self):
        for auth in ("DTI", "SEC", "BIR", "LTFRB", "LTO", "LGU", "INSURANCE"):
            self.assertEqual(mt.run_adapter(auth, "X", "Y")["status"], "MANUAL_VERIFICATION_REQUIRED")
        cid = self._carrier(self.a1)
        kid = mt.submit_kyb(self.c, self.a2, "CARRIER", cid, "SEC", "SEC-1", "Acme")
        res = mt.check_kyb(self.c, self.a2, kid)
        self.assertEqual(res["adapter_result"]["status"], "MANUAL_VERIFICATION_REQUIRED")
        self.assertEqual(res["status"], "NEEDS_REVIEW")       # NOT auto-verified

    # ---- L: no self-verification of your own carrier ----
    def test_no_self_verification(self):
        cid = self._carrier(self.a1)                          # created_by = a1
        kid = mt.submit_kyb(self.c, self.a1, "CARRIER", cid, "SEC", "SEC-1", "Acme")
        with self.assertRaises(core.ForbiddenError):
            mt.verify_kyb(self.c, self.a1, kid, "VERIFIED", source="SEC portal check")  # a1 == creator
        # a different officer may verify with a recorded source
        self.assertEqual(mt.verify_kyb(self.c, self.a2, kid, "VERIFIED", source="SEC portal check"), "VERIFIED")

    # ---- verification requires a recorded source (no forged VERIFIED) ----
    def test_verify_requires_source(self):
        cid = self._carrier(self.a1)
        kid = mt.submit_kyb(self.c, self.a2, "CARRIER", cid, "SEC", "SEC-1", "Acme")
        with self.assertRaises(core.ValidationError):
            mt.verify_kyb(self.c, self.a2, kid, "VERIFIED", source=None)
        self.assertTrue(mt.run_integrity(self.c)["ok"])

    # ---- F + matching denial: unverified carrier is NOT eligible ----
    def test_unverified_carrier_not_eligible(self):
        cid = self._carrier(self.a1)
        r = mt.assess_eligibility(self.c, self.sup, cid)
        self.assertFalse(r["eligible"])
        self.assertTrue(any("not verified" in x for x in r["hard_reasons"]))

    # ---- Q: trust score can NEVER override a hard compliance denial ----
    def test_trust_score_cannot_override_compliance(self):
        cid = self._carrier(self.a1)
        # even with maximal advisory factors, an unverified carrier stays ineligible
        ts = mt.trust_score(self.c, cid, factors={k: 100 for k in mt.DEFAULT_TRUST_WEIGHTS})
        self.assertGreater(ts["trust_score"], 0)
        self.assertFalse(mt.assess_eligibility(self.c, self.sup, cid)["eligible"])

    # ---- verified carrier becomes eligible; score is exposed only then ----
    def test_verified_carrier_eligible(self):
        cid = self._carrier(self.a1)
        kid = mt.submit_kyb(self.c, self.a2, "CARRIER", cid, "SEC", "SEC-1", "Acme")
        mt.verify_kyb(self.c, self.a2, kid, "VERIFIED", source="SEC portal check")
        r = mt.assess_eligibility(self.c, self.sup, cid)
        self.assertTrue(r["eligible"])
        self.assertIsNotNone(r["trust_score"])

    # ---- P: a HIGH/CRITICAL fraud flag blocks eligibility ----
    def test_fraud_flag_blocks_eligibility(self):
        cid = self._carrier(self.a1)
        kid = mt.submit_kyb(self.c, self.a2, "CARRIER", cid, "SEC", "SEC-1", "Acme")
        mt.verify_kyb(self.c, self.a2, kid, "VERIFIED", source="SEC portal check")
        self.assertTrue(mt.assess_eligibility(self.c, self.sup, cid)["eligible"])
        mt.raise_fraud_flag(self.c, self.a1, "CARRIER", cid, "mismatched_payee", "CRITICAL", "payee != registered")
        self.assertTrue(mt.is_blocked(self.c, "CARRIER", cid))
        self.assertFalse(mt.assess_eligibility(self.c, self.sup, cid)["eligible"])

    # ---- L: no self-clearing of a fraud flag you raised ----
    def test_no_self_clear_fraud(self):
        cid = self._carrier(self.a1)
        fid = mt.raise_fraud_flag(self.c, self.a1, "CARRIER", cid, "reused_docs", "HIGH")
        with self.assertRaises(core.ForbiddenError):
            mt.clear_fraud_flag(self.c, self.a1, fid, "looks fine")   # raiser cannot clear
        mt.clear_fraud_flag(self.c, self.a2, fid, "verified against SEC record")
        self.assertFalse(mt.is_blocked(self.c, "CARRIER", cid))

    # ---- fraud detector: reused business registration across carriers = HIGH ----
    def test_reused_registration_detected(self):
        c1 = self._carrier(self.a1, "Alpha")
        c2 = self._carrier(self.a2, "Beta")
        mt.submit_kyb(self.c, self.a2, "CARRIER", c1, "SEC", "DUP-REG", "Alpha")
        mt.submit_kyb(self.c, self.a1, "CARRIER", c2, "SEC", "DUP-REG", "Beta")
        ev = mt.evaluate_fraud(self.c, "CARRIER", c1)
        self.assertTrue(any(i["indicator"] == "reused_business_registration" for i in ev["indicators"]))
        self.assertEqual(ev["risk_level"], "HIGH")

    # ---- E: expiry scan expires a lapsed permit (blocks new eligibility) ----
    def test_expired_permit_blocks(self):
        cid = self._carrier(self.a1)
        kid = mt.submit_kyb(self.c, self.a2, "CARRIER", cid, "SEC", "SEC-1", "Acme",
                            expiry_date="2000-01-01")
        mt.verify_kyb(self.c, self.a2, kid, "VERIFIED", source="SEC portal check")
        scan = mt.expire_due_kyb(self.c)
        self.assertGreaterEqual(scan["expired"], 1)
        self.assertEqual(mt.carrier_kyb_status(self.c, cid), "EXPIRED")
        self.assertFalse(mt.assess_eligibility(self.c, self.sup, cid)["eligible"])

    # ---- immutable KYB history (append-only transition log) ----
    def test_kyb_history_is_appended(self):
        cid = self._carrier(self.a1)
        kid = mt.submit_kyb(self.c, self.a2, "CARRIER", cid, "SEC", "SEC-1", "Acme")
        mt.check_kyb(self.c, self.a2, kid)
        mt.verify_kyb(self.c, self.a2, kid, "VERIFIED", source="SEC portal check")
        hist = [r["to_status"] for r in self.c.execute(
            "SELECT to_status FROM mkt_kyb_history WHERE kyb_id=? ORDER BY id", (kid,)).fetchall()]
        self.assertEqual(hist, ["SUBMITTED", "NEEDS_REVIEW", "VERIFIED"])


class TrustTenantIsolationTests(unittest.TestCase):
    def test_kyb_is_tenant_isolated(self):
        c = db.connect(":memory:")
        sup = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
        tA = ap.create_tenant(c, "TRA", "Trust A")
        tB = ap.create_tenant(c, "TRB", "Trust B")
        uA = core.create_user(c, "a@tr", "pw", "admin", "A"); tenant.bind_user_tenant(c, None, uA, tA)
        uB = core.create_user(c, "b@tr", "pw", "admin", "B"); tenant.bind_user_tenant(c, None, uB, tB)
        aA = core.actor_for(c, core.login(c, "a@tr", "pw"))
        aB = core.actor_for(c, core.login(c, "b@tr", "pw"))
        kid = mt.submit_kyb(c, aA, "CARRIER", 501, "DTI", "DTI-1", "A Co")
        self.assertEqual(len(mt.list_kyb(c, aA)), 1)          # A sees its own
        self.assertEqual(len(mt.list_kyb(c, aB)), 0)          # B sees nothing of A's
        with self.assertRaises(core.NotFoundError):
            mt.verify_kyb(c, aB, kid, "VERIFIED", source="forged")   # cross-tenant → 404, no leak


class TrustRestartPersistenceTests(unittest.TestCase):
    def test_kyb_survives_restart(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite"); os.close(fd)
        try:
            c = db.connect(path)
            sup = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
            u = core.create_user(c, "p@tr", "pw", "admin", "P")
            a = core.actor_for(c, core.login(c, "p@tr", "pw"))
            cid = ob.create_carrier_application(c, a, "CORPORATION", "Persist Co",
                                                registration_type="SEC", registration_number="SEC-P")
            u2 = core.create_user(c, "q@tr", "pw", "admin", "Q")
            a2 = core.actor_for(c, core.login(c, "q@tr", "pw"))
            kid = mt.submit_kyb(c, a2, "CARRIER", cid, "SEC", "SEC-P", "Persist")
            mt.verify_kyb(c, a2, kid, "VERIFIED", source="SEC portal check")
            c.commit(); c.close()
            c2 = db.connect(path)                              # reopen same file
            self.assertEqual(mt.carrier_kyb_status(c2, cid), "VERIFIED")
            self.assertTrue(mt.run_integrity(c2)["ok"])
            c2.close()
        finally:
            try: os.unlink(path)
            except PermissionError: pass


if __name__ == "__main__":
    unittest.main()
