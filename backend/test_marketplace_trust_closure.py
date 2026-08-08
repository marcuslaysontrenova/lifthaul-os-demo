"""Trust & Protected-Transaction closure tests (C/D/M/N/O + release gate + risk limits +
webhook security + ledger reconciliation). Existing controls are preserved."""
import json
import os
import tempfile
import unittest

import admin_platform as ap
import core
import db
import marketplace_trust as mt
import marketplace_trust_closure as tc
import tenant


class ClosureTests(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.sup = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
        self.a1 = self._staff("c1@mk")
        self.a2 = self._staff("c2@mk")

    def _staff(self, e):
        core.create_user(self.c, e, "pw", "admin", "S")
        return core.actor_for(self.c, core.login(self.c, e, "pw"))

    def _driver(self, licence_class='["low-bed"]', licence_expiry="2999-01-01", status="ACTIVE"):
        cur = self.c.execute(
            "INSERT INTO mkt_drivers(carrier_id,full_name,licence_number,licence_class,licence_expiry,"
            "authorized_categories,status,created_at) VALUES(1,'D','L1','PRO',?,?,?,?)",
            (licence_expiry, licence_class, status, tc._now()))
        self.c.commit(); return cur.lastrowid

    # ---- C: driver license / qualification gates ----
    def test_expired_license_blocks_assignment(self):
        d = self._driver(licence_expiry="2000-01-01")
        r = tc.driver_assignment_gate(self.c, d)
        self.assertFalse(r["ok"]); self.assertIn("license_expired", r["reasons"])

    def test_incompatible_license_class_blocks(self):
        d = self._driver(licence_class='["truck"]')
        r = tc.driver_assignment_gate(self.c, d, equipment_type="crawler-crane")
        self.assertIn("wrong_license_class", r["reasons"])

    def test_missing_and_expired_qualification(self):
        d = self._driver(licence_class='["crawler-crane"]')
        r = tc.driver_assignment_gate(self.c, d, equipment_type="crawler-crane")
        self.assertIn("required_qualification_absent", r["reasons"])
        q = tc.record_qualification(self.c, self.a1, d, "crawler-crane", "TESDA_NC2",
                                    certificate_number="NC2-1", expires_at="2000-01-01")
        tc.verify_qualification(self.c, self.a2, q, "VERIFIED", source="TESDA registry")
        r2 = tc.driver_assignment_gate(self.c, d, equipment_type="crawler-crane")
        self.assertIn("qualification_expired", r2["reasons"])

    def test_valid_driver_passes(self):
        d = self._driver(licence_class='["crawler-crane"]')
        q = tc.record_qualification(self.c, self.a1, d, "crawler-crane", "TESDA_NC2", expires_at="2999-01-01")
        tc.verify_qualification(self.c, self.a2, q, "VERIFIED", source="TESDA registry")
        self.assertTrue(tc.driver_assignment_gate(self.c, d, equipment_type="crawler-crane")["ok"])

    def test_suspended_driver_blocked(self):
        d = self._driver(status="SUSPENDED")
        self.assertIn("driver_suspended", tc.driver_assignment_gate(self.c, d)["reasons"])

    # ---- D: vehicle legality ----
    def test_vehicle_legality_gate(self):
        lid = tc.record_vehicle_legality(self.c, self.a1, 77, or_number=None, cr_number=None,
                                         registration_expiry="2000-01-01", insurance_expiry="2000-01-01",
                                         capacity_kg=1000)
        r = tc.vehicle_legality_gate(self.c, 77, required_capacity_kg=5000)
        self.assertFalse(r["ok"])
        for reason in ("legality_not_verified", "invalid_or_cr", "registration_expired",
                       "insurance_expired", "capacity_mismatch"):
            self.assertIn(reason, r["reasons"])

    def test_vehicle_legality_pass(self):
        lid = tc.record_vehicle_legality(self.c, self.a1, 88, or_number="OR1", cr_number="CR1",
                                         registration_expiry="2999-01-01", insurance_expiry="2999-01-01",
                                         maintenance_status="SAFE", capacity_kg=40000)
        tc.verify_vehicle_legality(self.c, self.a2, lid, "VERIFIED", source="LTO record")
        self.assertTrue(tc.vehicle_legality_gate(self.c, 88, required_capacity_kg=30000)["ok"])

    # ---- M: payout account security ----
    def test_payout_self_approval_blocked(self):
        pid = tc.submit_payout_account(self.c, self.a1, 1, "Bene", "Ent Inc", "wise:ref", "1234567890")
        with self.assertRaises(core.ForbiddenError):
            tc.approve_payout_account(self.c, self.a1, pid, mfa_ok=True)   # maker == checker
        with self.assertRaises(core.ForbiddenError):
            tc.approve_payout_account(self.c, self.a2, pid, mfa_ok=False)  # MFA required
        self.assertEqual(tc.approve_payout_account(self.c, self.a2, pid, mfa_ok=True), "ACTIVE")

    def test_account_masked_only(self):
        pid = tc.submit_payout_account(self.c, self.a1, 1, "Bene", "Ent", "ref", "9998887776")
        row = self.c.execute("SELECT account_masked FROM mkt_payout_accounts WHERE id=?", (pid,)).fetchone()
        self.assertEqual(row["account_masked"], "******7776")

    def test_cooling_period_blocks_high_value(self):
        pid = tc.submit_payout_account(self.c, self.a1, 1, "Bene", "Ent", "ref", "1234", cooling_hours=24)
        tc.approve_payout_account(self.c, self.a2, pid, beneficiary_verified=True, mfa_ok=True)
        self.assertFalse(tc.payout_allowed(self.c, pid, 1000000)["ok"])         # high value in cooling
        self.assertTrue(tc.payout_allowed(self.c, pid, 1000)["ok"])            # small value ok

    def test_unverified_beneficiary_blocks_payout(self):
        pid = tc.submit_payout_account(self.c, self.a1, 1, "Bene", "Ent", "ref", "1234")
        tc.approve_payout_account(self.c, self.a2, pid, beneficiary_verified=False, mfa_ok=True)
        self.assertIn("beneficiary_unverified", tc.payout_allowed(self.c, pid, 100)["reasons"])

    # ---- N: dispute lifecycle blocks release; SoD on resolution ----
    def test_open_dispute_blocks_release_and_sod(self):
        did = tc.open_dispute(self.c, self.a1, booking_id=555, carrier_id=1, amount_disputed=50000,
                              reason="cargo damage")
        self.assertTrue(tc.dispute_blocks_release(self.c, 555))
        with self.assertRaises(core.ForbiddenError):
            tc.resolve_dispute(self.c, self.a1, did, "RELEASE_PARTIAL", "opener cannot resolve")
        tc.resolve_dispute(self.c, self.a2, did, "REFUND_PARTIAL", "50% refund", {"refund": 25000})
        self.assertFalse(tc.dispute_blocks_release(self.c, 555))

    # ---- O: claims + risk influence ----
    def test_claim_affects_risk_limit(self):
        # verify a carrier so it has a base limit
        cid = 1
        k = mt.submit_kyb(self.c, self.a1, "CARRIER", cid, "SEC", "SEC-1", "Co")
        mt.verify_kyb(self.c, self.a2, k, "VERIFIED", source="SEC")
        before = tc.carrier_risk_limit(self.c, cid)["limit"]
        tc.open_claim(self.c, self.a1, "CARGO_LOSS", "Client", carrier_id=cid, claimed_amount=200000)
        after = tc.carrier_risk_limit(self.c, cid)["limit"]
        self.assertLess(after, before)                        # open severe claim halves the cap

    def test_progressive_limit_unproven_carrier(self):
        cid = 2
        # unverified carrier -> zero cap even with a big job
        self.assertFalse(tc.within_risk_limit(self.c, cid, 5000000)["ok"])
        k = mt.submit_kyb(self.c, self.a1, "CARRIER", cid, "SEC", "SEC-2", "Co2")
        mt.verify_kyb(self.c, self.a2, k, "VERIFIED", source="SEC")
        lim = tc.carrier_risk_limit(self.c, cid)
        self.assertGreater(lim["limit"], 0)
        self.assertLess(lim["limit"], 20000000)               # not immediately eligible for ₱20M

    # ---- composed release gate ----
    def test_release_gate_denies_until_all_conditions(self):
        cid = 1
        k = mt.submit_kyb(self.c, self.a1, "CARRIER", cid, "SEC", "SEC-1", "Co")
        mt.verify_kyb(self.c, self.a2, k, "VERIFIED", source="SEC")
        pid = tc.submit_payout_account(self.c, self.a1, cid, "Bene", "Ent", "ref", "1234", cooling_hours=0)
        tc.approve_payout_account(self.c, self.a2, pid, beneficiary_verified=True, mfa_ok=True)
        denied = tc.release_gate(self.c, 555, cid, funding_confirmed=False, funds_protected=True,
                                 milestone_verified=True, pod_ok=True, payout_account_id=pid, job_value=100000)
        self.assertFalse(denied["allowed"])
        self.assertIn("funding_not_confirmed", denied["denied_reasons"])
        ok = tc.release_gate(self.c, 555, cid, funding_confirmed=True, funds_protected=True,
                             milestone_verified=True, pod_ok=True, payout_account_id=pid, job_value=100000)
        self.assertTrue(ok["allowed"])

    def test_release_denied_during_dispute(self):
        cid = 1
        k = mt.submit_kyb(self.c, self.a1, "CARRIER", cid, "SEC", "SEC-1", "Co")
        mt.verify_kyb(self.c, self.a2, k, "VERIFIED", source="SEC")
        pid = tc.submit_payout_account(self.c, self.a1, cid, "B", "E", "ref", "1", cooling_hours=0)
        tc.approve_payout_account(self.c, self.a2, pid, beneficiary_verified=True, mfa_ok=True)
        tc.open_dispute(self.c, self.a1, 555, cid, 10000, "damage")
        g = tc.release_gate(self.c, 555, cid, funding_confirmed=True, funds_protected=True,
                            milestone_verified=True, pod_ok=True, payout_account_id=pid, job_value=100000)
        self.assertFalse(g["allowed"]); self.assertIn("blocking_dispute_open", g["denied_reasons"])

    # ---- webhook security ----
    def test_webhook_signature_and_replay(self):
        secret, payload = "whsec", b'{"event":"funded"}'
        import hashlib, hmac
        good = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        self.assertTrue(tc.verify_webhook(self.c, "wise", "evt1", "funded", payload, good, secret)["accepted"])
        # replay of same event id -> rejected
        self.assertFalse(tc.verify_webhook(self.c, "wise", "evt1", "funded", payload, good, secret)["accepted"])
        # bad signature -> quarantined, not accepted
        r = tc.verify_webhook(self.c, "wise", "evt2", "funded", payload, "deadbeef", secret)
        self.assertFalse(r["accepted"]); self.assertEqual(r["status"], "QUARANTINED")

    # ---- ledger reconciliation ----
    def test_ledger_reconciliation(self):
        self.assertTrue(tc.reconcile_ledger(1000000, released=700000, refunded=100000,
                                            remaining_protected=180000, fees=20000)["balanced"])
        bad = tc.reconcile_ledger(1000000, released=700000, refunded=100000, remaining_protected=100000, fees=0)
        self.assertFalse(bad["balanced"]); self.assertEqual(bad["flag"], "LEDGER_IMBALANCE")


class ClosureTenantAndRestartTests(unittest.TestCase):
    def test_tenant_isolation_and_restart(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite"); os.close(fd)
        try:
            c = db.connect(path)
            tA = ap.create_tenant(c, "CLA", "A"); tB = ap.create_tenant(c, "CLB", "B")
            uA = core.create_user(c, "a@cl", "pw", "admin", "A"); tenant.bind_user_tenant(c, None, uA, tA)
            uB = core.create_user(c, "b@cl", "pw", "admin", "B"); tenant.bind_user_tenant(c, None, uB, tB)
            aA = core.actor_for(c, core.login(c, "a@cl", "pw"))
            pid = tc.submit_payout_account(c, aA, 1, "Bene", "Ent", "ref", "9990001234")
            c.commit(); c.close()
            c2 = db.connect(path)                              # restart
            row = c2.execute("SELECT account_masked,tenant_id FROM mkt_payout_accounts WHERE id=?", (pid,)).fetchone()
            self.assertEqual(row["account_masked"], "******1234")   # survives restart, masked
            self.assertEqual(row["tenant_id"], tA)                  # tenant-stamped
            c2.close()
        finally:
            try: os.unlink(path)
            except PermissionError: pass


if __name__ == "__main__":
    unittest.main()
