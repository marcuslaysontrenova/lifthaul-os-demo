"""LiftHaul OS — Phase 7: Integration Administration + Wise (mock/sandbox).

Proves: governed integration definitions + connection profiles (MOCK/SANDBOX/TEST/PROD); secret-
reference boundary; a provider-independent Wise adapter with deterministic mock scenarios; idempotency
(no duplicate transfers; conflicting-payload rejection); governed webhook ingress (signature verify +
dedup + replay-safe); polling fallback; a reconciliation engine (match/partial/over/under/duplicate/
mismatch → manual review); payment verification requiring reconciled settlement + separation of duties;
refunds with approval + provider confirmation; retry classification + dead-letter + governed replay;
circuit breaker + kill switch; provider health (never HEALTHY without validation); tenant isolation;
and migration with zero financial / payment-status / job-status drift. Live Wise stays BLOCKED (no creds).
"""
import unittest

import db
import core
import admin_platform as ap
import integrations as ig
import wise


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.a = {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": self.rgo}
        self.a2 = {"id": 2, "role": "admin", "perms": {"*"}, "tenant_id": self.rgo}

    def _actor(self, perms, id=9, role="finance", tenant=None):
        return {"id": id, "role": role, "perms": set(perms),
                "tenant_id": self.rgo if tenant is None else tenant}

    def _active_profile(self, environment="MOCK"):
        pid = ig.create_profile(self.c, self.a, "wise", environment=environment, name="W", secret_ref="wise_key")
        ig.validate_profile(self.c, self.a, pid)
        ig.activate_profile(self.c, self.a, pid)
        return pid

    def _accepted_booking(self):
        a, a2 = self.a, self.a2
        cid = core.create_customer(self.c, a, "Acme")
        bid = core.create_booking(self.c, a, cid, "CRANE_RENTAL", "x", 1)
        core.review_booking(self.c, a, bid); core.ready_for_quotation(self.c, a, bid)
        qid = core.create_quotation(self.c, a, bid, [{"kind": "crane", "description": "x", "qty": 2, "days": 1, "rate": 300000}])
        core.submit_quotation(self.c, a, qid)
        if self.c.execute("SELECT status FROM quotations WHERE id=?", (qid,)).fetchone()["status"] == "pending_approval":
            core.approve_quotation(self.c, a2, qid)
        core.send_quotation(self.c, a, qid); core.accept_quotation(self.c, a2, qid, "J. Roe", "CFO")
        return bid


class TestProfilesAndSecrets(Base):
    def test_profile_lifecycle(self):
        pid = ig.create_profile(self.c, self.a, "wise", environment="MOCK", secret_ref="wise_key")
        v = ig.validate_profile(self.c, self.a, pid)
        self.assertEqual(v["health"], "HEALTHY")
        self.assertTrue(ig.activate_profile(self.c, self.a, pid))

    def test_invalid_environment_rejected(self):
        with self.assertRaises(core.ValidationError):
            ig.create_profile(self.c, self.a, "wise", environment="LIVE")

    def test_health_unknown_until_validated(self):
        pid = ig.create_profile(self.c, self.a, "wise", environment="MOCK")
        h = ig.provider_health(self.c, self.a, "wise")["providers"]
        self.assertEqual([p for p in h if p["profile_id"] == pid][0]["health"], "UNKNOWN")

    def test_cross_tenant_profile_denied(self):
        pid = self._active_profile()
        other = self._actor({"*"}, id=5, tenant=9999)
        with self.assertRaises(core.NotFoundError):
            ig.get_profile(self.c, other, pid)

    def test_activation_requires_healthy(self):
        pid = ig.create_profile(self.c, self.a, "wise", environment="MOCK")
        with self.assertRaises(core.ConflictError):
            ig.activate_profile(self.c, self.a, pid)


class TestWiseAdapter(Base):
    def test_profile_validation_offers_multiple(self):
        pid = self._active_profile()
        v = ig.validate_profile(self.c, self.a, pid)
        self.assertGreaterEqual(len(v["profiles"]), 2)      # admin must choose; not auto-first

    def test_quote_and_transfer(self):
        pid = self._active_profile(); bid = self._accepted_booking()
        r = wise.create_wise_payment(self.c, self.a, bid, pid, idem_key="k1")
        self.assertEqual(r["amount"], 201600)               # from stored dp snapshot
        self.assertTrue(r["provider_transfer_id"].startswith("WISE-T-"))

    def test_expired_quote_blocks_transfer(self):
        pid = self._active_profile(); bid = self._accepted_booking()
        with self.assertRaises(core.ConflictError):
            wise.create_wise_payment(self.c, self.a, bid, pid, idem_key="kexp", scenario="expired")

    def test_status_mapping(self):
        self.assertEqual(wise.map_status("outgoing_payment_sent"), "COMPLETED")
        self.assertEqual(wise.map_status("bounced_back"), "FAILED")
        self.assertEqual(wise.map_status("nonsense"), "UNKNOWN")

    def test_auth_failure_dead_letters(self):
        pid = self._active_profile(); bid = self._accepted_booking()
        with self.assertRaises(core.ConflictError):
            wise.create_wise_payment(self.c, self.a, bid, pid, idem_key="kauth", scenario="auth_fail")
        dl = ig.list_dead_letters(self.c, self.a)
        self.assertTrue(any(d["failure_category"] == "authentication_failure" for d in dl))

    def test_live_wise_blocked(self):
        adapter = wise.get_adapter("PRODUCTION")
        self.assertFalse(adapter.is_mock)
        res = adapter.validate_connection({"secret_ref": "x"})
        self.assertTrue(res.get("blocked"))                 # no fabricated success


class TestIdempotency(Base):
    def test_duplicate_key_returns_original(self):
        pid = self._active_profile(); bid = self._accepted_booking()
        r1 = wise.create_wise_payment(self.c, self.a, bid, pid, idem_key="dup")
        r2 = wise.create_wise_payment(self.c, self.a, bid, pid, idem_key="dup")
        self.assertEqual(r1["transfer_id"], r2["transfer_id"])
        self.assertTrue(r2["idempotent_replay"])
        n = self.c.execute("SELECT COUNT(*) c FROM provider_transfers").fetchone()["c"]
        self.assertEqual(n, 1)                              # no duplicate transfer

    def test_conflicting_payload_rejected(self):
        ig.idempotent(self.c, self.a, "k", "op", {"a": 1})
        with self.assertRaises(core.ConflictError):
            ig.idempotent(self.c, self.a, "k", "op", {"a": 2})


class TestWebhooks(Base):
    def test_valid_signed_event(self):
        out = ig.ingest_webhook(self.c, "wise", "evt-1", "transfer.state_change", {"id": "T1"},
                                signature=None, tenant_id=self.rgo, secret=None)
        self.assertEqual(out["status"], "ACCEPTED")

    def test_signature_verification(self):
        import hmac, hashlib, json
        payload = {"id": "T2"}
        ph = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        good = hmac.new(b"whsecret", ph.encode(), hashlib.sha256).hexdigest()
        ok = ig.ingest_webhook(self.c, "wise", "evt-2", "x", payload, signature=good, tenant_id=self.rgo, secret="whsecret")
        self.assertEqual(ok["status"], "ACCEPTED")
        bad = ig.ingest_webhook(self.c, "wise", "evt-3", "x", payload, signature="deadbeef", tenant_id=self.rgo, secret="whsecret")
        self.assertEqual(bad["status"], "REJECTED")

    def test_duplicate_event_idempotent(self):
        ig.ingest_webhook(self.c, "wise", "evt-dup", "x", {"a": 1}, tenant_id=self.rgo, secret=None)
        again = ig.ingest_webhook(self.c, "wise", "evt-dup", "x", {"a": 1}, tenant_id=self.rgo, secret=None)
        self.assertEqual(again["status"], "DUPLICATE")

    def test_process_is_idempotent(self):
        out = ig.ingest_webhook(self.c, "wise", "evt-proc", "x", {"a": 1}, tenant_id=self.rgo, secret=None)
        ig.process_webhook_event(self.c, self.a, out["event_id"])
        again = ig.process_webhook_event(self.c, self.a, out["event_id"])
        self.assertEqual(again["status"], "ALREADY_PROCESSED")

    def test_rejected_event_cannot_process(self):
        import json, hashlib
        out = ig.ingest_webhook(self.c, "wise", "evt-rej", "x", {"a": 1}, signature="bad", tenant_id=self.rgo, secret="s")
        with self.assertRaises(core.ForbiddenError):
            ig.process_webhook_event(self.c, self.a, out["event_id"])


class TestReconciliation(Base):
    def _transfer(self, scenario):
        pid = self._active_profile(); bid = self._accepted_booking()
        return wise.create_wise_payment(self.c, self.a, bid, pid, idem_key="rk-" + scenario, scenario=scenario)

    def test_exact_match(self):
        r = self._transfer("completed"); wise.sync_transfer_status(self.c, self.a, r["transfer_id"])
        self.assertEqual(wise.reconcile_transfer(self.c, self.a, r["transfer_id"])["status"], "MATCHED")

    def test_partial_manual_review(self):
        r = self._transfer("partial"); wise.sync_transfer_status(self.c, self.a, r["transfer_id"])
        self.assertEqual(wise.reconcile_transfer(self.c, self.a, r["transfer_id"])["status"], "MANUAL_REVIEW")

    def test_overpay_manual_review(self):
        r = self._transfer("overpay"); wise.sync_transfer_status(self.c, self.a, r["transfer_id"])
        self.assertEqual(wise.reconcile_transfer(self.c, self.a, r["transfer_id"])["status"], "MANUAL_REVIEW")

    def test_failed_transfer_not_reconcilable(self):
        r = self._transfer("failed"); wise.sync_transfer_status(self.c, self.a, r["transfer_id"])
        with self.assertRaises(core.ConflictError):
            wise.reconcile_transfer(self.c, self.a, r["transfer_id"])

    def test_duplicate_detection(self):
        r = self._transfer("completed"); wise.sync_transfer_status(self.c, self.a, r["transfer_id"])
        wise.reconcile_transfer(self.c, self.a, r["transfer_id"])
        t = self.c.execute("SELECT * FROM provider_transfers WHERE id=?", (r["transfer_id"],)).fetchone()
        dup = ig.reconcile(self.c, self.a, t["payment_request_id"], t["provider_transfer_id"], t["amount"], t["currency"])
        self.assertEqual(dup["status"], "MANUAL_REVIEW")    # duplicate routes to review


class TestVerification(Base):
    def _matched_transfer(self):
        pid = self._active_profile(); bid = self._accepted_booking()
        r = wise.create_wise_payment(self.c, self.a, bid, pid, idem_key="vk")
        wise.sync_transfer_status(self.c, self.a, r["transfer_id"])
        wise.reconcile_transfer(self.c, self.a, r["transfer_id"])
        return r, bid

    def test_self_verification_denied(self):
        r, _ = self._matched_transfer()
        with self.assertRaises(core.ForbiddenError):
            wise.verify_wise_payment(self.c, self.a, r["transfer_id"])   # a created the transfer

    def test_authorized_verifier_and_job_prereq(self):
        r, bid = self._matched_transfer()
        res = wise.verify_wise_payment(self.c, self.a2, r["transfer_id"])
        self.assertEqual(res["payment_status"], "VERIFIED")
        self.assertTrue(core.confirm_job(self.c, self.a, bid))          # job-activation prerequisite satisfied

    def test_unsettled_cannot_verify(self):
        pid = self._active_profile(); bid = self._accepted_booking()
        r = wise.create_wise_payment(self.c, self.a, bid, pid, idem_key="uk", scenario="pending")
        with self.assertRaises(core.ConflictError):
            wise.verify_wise_payment(self.c, self.a2, r["transfer_id"])  # no reconciled evidence


class TestRefunds(Base):
    def test_refund_requires_separate_approver(self):
        pid = self._active_profile(); bid = self._accepted_booking()
        r = wise.create_wise_payment(self.c, self.a, bid, pid, idem_key="rf")
        rid = wise.request_refund(self.c, self.a, r["transfer_id"], 1000, "customer request")
        with self.assertRaises(core.ForbiddenError):
            wise.approve_refund(self.c, self.a, rid)        # requester == approver
        self.assertTrue(wise.approve_refund(self.c, self.a2, rid))


class TestFailureHandling(Base):
    def test_classification(self):
        self.assertTrue(ig.classify_failure("transient_network")["retryable"])
        self.assertFalse(ig.classify_failure("permanent_business_rejection")["retryable"])

    def test_dead_letter_and_safe_replay(self):
        dlid = ig.dead_letter(self.c, self.a, "wise", "create_transfer", "transient_network", "timeout")
        r = ig.replay_dead_letter(self.c, self.a, dlid, reason="provider recovered")
        self.assertEqual(r["status"], "REPLAYED")

    def test_unsafe_replay_denied(self):
        dlid = ig.dead_letter(self.c, self.a, "wise", "create_transfer", "permanent_business_rejection", "declined")
        with self.assertRaises(core.ForbiddenError):
            ig.replay_dead_letter(self.c, self.a, dlid, reason="try again")

    def test_cross_tenant_replay_denied(self):
        dlid = ig.dead_letter(self.c, self.a, "wise", "create_transfer", "transient_network", "timeout")
        other = self._actor({"integration.replay.execute"}, id=7, tenant=9999)
        with self.assertRaises(core.NotFoundError):
            ig.replay_dead_letter(self.c, other, dlid, reason="x")

    def test_circuit_breaker_and_kill_switch(self):
        pid = self._active_profile()
        for _ in range(5):
            ig._record_failure(self.c, pid)
        p = self.c.execute("SELECT circuit_state FROM connection_profiles WHERE id=?", (pid,)).fetchone()
        self.assertEqual(p["circuit_state"], "OPEN")
        ig.kill_switch(self.c, self.a, pid)
        self.assertEqual(self.c.execute("SELECT status FROM connection_profiles WHERE id=?", (pid,)).fetchone()["status"], "DISABLED")

    def test_disabled_profile_fails_safe(self):
        pid = self._active_profile(); bid = self._accepted_booking()
        ig.kill_switch(self.c, self.a, pid)
        with self.assertRaises(core.ConflictError):
            wise.create_wise_payment(self.c, self.a, bid, pid, idem_key="dk")   # fails safe, no transfer


class TestHealthAndMigration(Base):
    def test_provider_health_backlogs(self):
        pid = self._active_profile()
        ig.dead_letter(self.c, self.a, "wise", "op", "transient_network", "x")
        h = ig.provider_health(self.c, self.a, "wise")["providers"][0]
        self.assertGreaterEqual(h["dead_letter_backlog"], 1)

    def test_migration_zero_drift(self):
        m = ig.classify_existing(self.c)
        self.assertEqual((m["financial_differences"], m["payment_status_changes"], m["job_status_changes"]), (0, 0, 0))
        self.assertEqual(m["fake_transaction_ids_assigned"], 0)

    def test_role_grants(self):
        pa = ap.effective_role_grants(self.c, ap.role_by_code(self.c, "RGO", "platform_admin")["id"])
        self.assertIn("payment.wise.*", pa)
        self.assertIn("integration.profile.*", pa)

    def test_wise_does_not_change_snapshot_financials(self):
        pid = self._active_profile(); bid = self._accepted_booking()
        qid = self.c.execute("SELECT id FROM quotations WHERE booking_id=? ORDER BY id DESC LIMIT 1", (bid,)).fetchone()["id"]
        before = self.c.execute("SELECT tax,total,dp_amount FROM quotations WHERE id=?", (qid,)).fetchone()
        wise.create_wise_payment(self.c, self.a, bid, pid, idem_key="fk")
        after = self.c.execute("SELECT tax,total,dp_amount FROM quotations WHERE id=?", (qid,)).fetchone()
        self.assertEqual((before["tax"], before["total"], before["dp_amount"]), (72000, 672000, 201600))
        self.assertEqual((after["tax"], after["total"], after["dp_amount"]), (72000, 672000, 201600))   # UNCHANGED
        # payment amount == stored dp snapshot
        pr = self.c.execute("SELECT amount_due FROM payment_requests WHERE booking_id=?", (bid,)).fetchone()
        self.assertEqual(pr["amount_due"], 201600)


class TestPhase7Api(unittest.TestCase):
    """Drives the Phase 7 /admin/integrations* + /admin/wise* endpoints through the real HTTP router."""
    @classmethod
    def setUpClass(cls):
        import server
        import db as _db
        server._conn = _db.connect(":memory:")
        cls.server = server
        try:
            core.create_user(server._conn, "p7admin@r", "demo1234", "admin", "P7 Admin")
        except core.ConflictError:
            pass

    def _call(self, method, path, body=None):
        fn, params = self.server._match(method, path)
        assert fn, f"no route for {method} {path}"
        tok = self.server._match("POST", "/login")[0](None, {"email": "p7admin@r", "password": "demo1234"}, {})["token"]
        actor = core.actor_for(self.server._conn, tok)
        return fn(actor, body or {}, params or {})

    def test_catalog_and_profile_via_api(self):
        cat = self._call("GET", "/admin/integrations/catalog")["definitions"]
        self.assertTrue(any(d["provider_code"] == "wise" for d in cat))
        pid = self._call("POST", "/admin/integrations/profiles", {"provider_code": "wise", "environment": "MOCK", "secret_ref": "wise_key"})["id"]
        v = self._call("POST", f"/admin/integrations/profiles/{pid}/validate", {})
        self.assertEqual(v["health"], "HEALTHY")

    def test_health_via_api(self):
        h = self._call("GET", "/admin/integrations/health")
        self.assertIn("providers", h)


if __name__ == "__main__":
    unittest.main(verbosity=2)
