"""Protected-transaction RED-TEAM (Workstream 3). Adversarial attempts to move funds unsafely.
Every dangerous case MUST fail closed. Evidence for PROTECTED_TRANSACTION_ATTACK_MATRIX.md.
"""
import hashlib
import hmac
import unittest

import admin_platform as ap
import core
import db
import marketplace_payments as pay
import marketplace_trust as mt
import marketplace_trust_closure as tc


class RedTeam(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.sup = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
        self.a1 = self._staff("r1@rt")
        self.a2 = self._staff("r2@rt")
        # a verified carrier with an approved payout account (cooling cleared) for the happy baseline
        self.cid = 1
        k = mt.submit_kyb(self.c, self.a1, "CARRIER", self.cid, "SEC", "SEC-1", "Co")
        mt.verify_kyb(self.c, self.a2, k, "VERIFIED", source="SEC")
        self.pid = tc.submit_payout_account(self.c, self.a1, self.cid, "Bene", "Ent", "ref", "1234567890", cooling_hours=0)
        tc.approve_payout_account(self.c, self.a2, self.pid, beneficiary_verified=True, mfa_ok=True)

    def _staff(self, e):
        core.create_user(self.c, e, "pw", "admin", "S")
        return core.actor_for(self.c, core.login(self.c, e, "pw"))

    def _gate(self, **over):
        base = dict(funding_confirmed=True, funds_protected=True, milestone_verified=True, pod_ok=True,
                    payout_account_id=self.pid, job_value=100000)
        base.update(over)
        return tc.release_gate(self.c, 555, self.cid, **base)

    # baseline: fully satisfied -> allowed
    def test_baseline_allows(self):
        self.assertTrue(self._gate()["allowed"])

    # --- release-gate attacks (all must DENY) ---
    def test_release_before_protection(self):
        self.assertFalse(self._gate(funds_protected=False)["allowed"])

    def test_release_before_funding(self):
        self.assertFalse(self._gate(funding_confirmed=False)["allowed"])

    def test_release_before_trip_milestone(self):
        self.assertFalse(self._gate(milestone_verified=False)["allowed"])

    def test_release_without_pod(self):
        self.assertFalse(self._gate(pod_ok=False)["allowed"])

    def test_release_with_open_dispute(self):
        tc.open_dispute(self.c, self.a1, 555, self.cid, 10000, "damage")
        r = self._gate()
        self.assertFalse(r["allowed"]); self.assertIn("blocking_dispute_open", r["denied_reasons"])

    def test_release_with_critical_fraud(self):
        mt.raise_fraud_flag(self.c, self.a1, "CARRIER", self.cid, "mismatched_payee", "CRITICAL")
        r = self._gate()
        self.assertFalse(r["allowed"]); self.assertIn("critical_fraud_flag", r["denied_reasons"])

    def test_release_to_unverified_beneficiary(self):
        pid2 = tc.submit_payout_account(self.c, self.a1, self.cid, "B", "E", "ref", "999", cooling_hours=0)
        tc.approve_payout_account(self.c, self.a2, pid2, beneficiary_verified=False, mfa_ok=True)
        r = self._gate(payout_account_id=pid2)
        self.assertFalse(r["allowed"]); self.assertIn("beneficiary_unverified", r["denied_reasons"])

    def test_release_during_payout_cooling(self):
        pid3 = tc.submit_payout_account(self.c, self.a1, self.cid, "B", "E", "ref", "888", cooling_hours=24)
        tc.approve_payout_account(self.c, self.a2, pid3, beneficiary_verified=True, mfa_ok=True)
        r = self._gate(payout_account_id=pid3, job_value=1000000)   # high value in cooling
        self.assertFalse(r["allowed"]); self.assertIn("cooling_period_high_value_blocked", r["denied_reasons"])

    def test_release_without_payout_destination(self):
        r = self._gate(payout_account_id=None)
        self.assertFalse(r["allowed"]); self.assertIn("payout_destination_unverified", r["denied_reasons"])

    def test_release_exceeding_risk_limit(self):
        r = self._gate(job_value=999999999)                # far above the carrier's progressive cap
        self.assertFalse(r["allowed"]); self.assertIn("exceeds_carrier_risk_limit", r["denied_reasons"])

    # --- payout-account attacks ---
    def test_maker_cannot_self_approve(self):
        pid = tc.submit_payout_account(self.c, self.a1, self.cid, "B", "E", "ref", "111")
        with self.assertRaises(core.ForbiddenError):
            tc.approve_payout_account(self.c, self.a1, pid, mfa_ok=True)

    def test_payout_approval_requires_mfa(self):
        pid = tc.submit_payout_account(self.c, self.a1, self.cid, "B", "E", "ref", "111")
        with self.assertRaises(core.ForbiddenError):
            tc.approve_payout_account(self.c, self.a2, pid, mfa_ok=False)

    def test_account_change_blocked_during_fraud_review(self):
        mt.raise_fraud_flag(self.c, self.a1, "CARRIER", self.cid, "abnormal_account_change", "CRITICAL")
        pid = tc.submit_payout_account(self.c, self.a1, self.cid, "B", "E", "ref", "111")
        with self.assertRaises(core.ForbiddenError):
            tc.approve_payout_account(self.c, self.a2, pid, mfa_ok=True)

    # --- webhook attacks ---
    def test_forged_webhook_signature_quarantined(self):
        r = tc.verify_webhook(self.c, "wise", "e1", "funded", b"{}", "deadbeef", "secret")
        self.assertFalse(r["accepted"]); self.assertEqual(r["status"], "QUARANTINED")

    def test_replayed_webhook_rejected(self):
        secret, payload = "s", b'{"x":1}'
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        self.assertTrue(tc.verify_webhook(self.c, "wise", "dup", "funded", payload, sig, secret)["accepted"])
        self.assertFalse(tc.verify_webhook(self.c, "wise", "dup", "funded", payload, sig, secret)["accepted"])

    def test_stale_webhook_rejected(self):
        secret, payload = "s", b'{"x":1}'
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        r = tc.verify_webhook(self.c, "wise", "old", "funded", payload, sig, secret,
                              timestamp="2000-01-01T00:00:00+00:00", tolerance_seconds=300)
        self.assertFalse(r["accepted"])

    # --- legality attacks ---
    def test_expired_vehicle_blocked(self):
        tc.record_vehicle_legality(self.c, self.a1, 70, or_number="OR", cr_number="CR",
                                   registration_expiry="2000-01-01", insurance_expiry="2999-01-01",
                                   capacity_kg=40000)
        self.assertFalse(tc.vehicle_legality_gate(self.c, 70)["ok"])

    def test_expired_driver_qualification_blocked(self):
        did = self.c.execute("INSERT INTO mkt_drivers(carrier_id,full_name,licence_expiry,"
                             "authorized_categories,status,created_at) VALUES(1,'D','2999-01-01',"
                             "'[\"crawler-crane\"]','ACTIVE',?)", (tc._now(),)).lastrowid
        self.c.commit()
        q = tc.record_qualification(self.c, self.a1, did, "crawler-crane", "NC2", expires_at="2000-01-01")
        tc.verify_qualification(self.c, self.a2, q, "VERIFIED", source="TESDA")
        self.assertIn("qualification_expired", tc.driver_assignment_gate(self.c, did, equipment_type="crawler-crane")["reasons"])

    # --- ledger attacks ---
    def test_ledger_imbalance_flagged(self):
        r = tc.reconcile_ledger(1000000, released=900000, refunded=200000, remaining_protected=0, fees=0)
        self.assertFalse(r["balanced"]); self.assertEqual(r["flag"], "LEDGER_IMBALANCE")

    def test_refund_exceeding_protected_is_imbalance(self):
        # a refund larger than what remains protected cannot reconcile
        r = tc.reconcile_ledger(1000000, released=0, refunded=1200000, remaining_protected=0, fees=0)
        self.assertFalse(r["balanced"])

    # --- LIVE-funds hard boundary (W9) ---
    def test_live_funds_disabled_by_default(self):
        self.assertFalse(pay.live_funds_enabled(self.c))
        with self.assertRaises(core.ForbiddenError):
            pay._assert_live_allowed(self.c, "WISE")               # live rail refused

    def test_live_funds_requires_all_three_prerequisites(self):
        ap.set_config(self.c, "platform", "", "payments.live_protected_funds_enabled", "true", actor=self.sup)
        self.assertFalse(pay.live_funds_enabled(self.c))           # flag alone is not enough
        ap.set_config(self.c, "platform", "", "payments.legal_operating_model_approved", "true", actor=self.sup)
        ap.set_config(self.c, "platform", "", "payments.licensed_provider_active", "true", actor=self.sup)
        self.assertTrue(pay.live_funds_enabled(self.c))            # only with all three


if __name__ == "__main__":
    unittest.main()
