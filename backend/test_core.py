"""RGO OS backend — control & workflow tests (stdlib unittest, zero deps).

Covers the directive's required controls: auth, RBAC denial, booking transitions,
quotation versioning, approval + separation-of-duties, no-send-without-approval,
no-payment-without-accepted-quote, no-confirm-without-verified-payment, idempotent
verification, partial payment, duplicate-job prevention, soft-delete/restore,
customer data isolation, audit recording — plus the full 1→confirmed-job e2e.
"""
import unittest
import core
from core import (connect, create_user, login, actor_for, MockWiseProvider,
                  ForbiddenError, ConflictError, AuthError,
                  create_customer, create_booking, get_booking, review_booking,
                  ready_for_quotation, create_quotation, submit_quotation,
                  approve_quotation, send_quotation, accept_quotation, request_revision,
                  create_payment_request, register_payment_link, submit_payment_evidence,
                  verify_payment, confirm_job, soft_delete, restore, list_audit)


def big_lines():   # subtotal 600k -> requires approval
    return [{"kind": "crane", "description": "350t crane", "qty": 1, "days": 3, "rate": 200000}]


class Base(unittest.TestCase):
    def setUp(self):
        self.c = connect(":memory:")
        create_user(self.c, "admin@rgo", "pw", "admin", "Admin")
        create_user(self.c, "est@rgo", "pw", "estimator", "Estimator")
        create_user(self.c, "appr@rgo", "pw", "approver", "Approver")
        create_user(self.c, "fin@rgo", "pw", "finance", "Finance")
        create_user(self.c, "disp@rgo", "pw", "dispatcher", "Dispatcher")
        self.admin = actor_for(self.c, login(self.c, "admin@rgo", "pw"))
        self.est = actor_for(self.c, login(self.c, "est@rgo", "pw"))
        self.appr = actor_for(self.c, login(self.c, "appr@rgo", "pw"))
        self.fin = actor_for(self.c, login(self.c, "fin@rgo", "pw"))
        self.disp = actor_for(self.c, login(self.c, "disp@rgo", "pw"))
        self.cid = create_customer(self.c, self.admin, "Acme Bank", "J. Roe", "jroe@acme.demo")
        create_user(self.c, "cust@acme", "pw", "customer", "J. Roe", customer_id=self.cid)
        self.cust = actor_for(self.c, login(self.c, "cust@acme", "pw"))
        self.wise = MockWiseProvider()

    def to_ready(self, bid):
        review_booking(self.c, self.est, bid)
        ready_for_quotation(self.c, self.est, bid)


class TestAuth(Base):
    def test_login_and_bad_password(self):
        self.assertTrue(login(self.c, "admin@rgo", "pw"))
        with self.assertRaises(AuthError):
            login(self.c, "admin@rgo", "wrong")

    def test_invalid_token(self):
        with self.assertRaises(AuthError):
            actor_for(self.c, "not-a-token")


class TestRBAC(Base):
    def test_estimator_cannot_approve(self):
        bid = create_booking(self.c, self.est, self.cid, "Crane", "load"); self.to_ready(bid)
        qid = create_quotation(self.c, self.est, bid, big_lines())
        submit_quotation(self.c, self.est, qid)
        with self.assertRaises(ForbiddenError):
            approve_quotation(self.c, self.est, qid)

    def test_dispatcher_cannot_create_quotation(self):
        bid = create_booking(self.c, self.admin, self.cid, "Crane", "load"); self.to_ready(bid)
        with self.assertRaises(ForbiddenError):
            create_quotation(self.c, self.disp, bid, big_lines())

    def test_customer_cannot_verify_payment(self):
        with self.assertRaises(ForbiddenError):
            verify_payment(self.c, self.cust, 1, 1000, "x")


class TestControls(Base):
    def test_no_send_without_approval(self):
        bid = create_booking(self.c, self.est, self.cid, "Crane", "load"); self.to_ready(bid)
        qid = create_quotation(self.c, self.est, bid, big_lines())
        submit_quotation(self.c, self.est, qid)          # -> pending_approval
        with self.assertRaises(ConflictError):
            send_quotation(self.c, self.est, qid)

    def test_no_payment_without_accepted_quote(self):
        bid = create_booking(self.c, self.est, self.cid, "Crane", "load"); self.to_ready(bid)
        qid = create_quotation(self.c, self.est, bid, big_lines())
        submit_quotation(self.c, self.est, qid); approve_quotation(self.c, self.appr, qid)
        send_quotation(self.c, self.est, qid)            # sent, not accepted
        with self.assertRaises(ConflictError):
            create_payment_request(self.c, self.fin, bid, self.wise)

    def test_no_confirm_without_verified_payment(self):
        bid = self._accepted_booking()
        create_payment_request(self.c, self.fin, bid, self.wise)   # created, not verified
        with self.assertRaises(ConflictError):
            confirm_job(self.c, self.admin, bid)

    def test_separation_of_duties(self):
        # admin can create AND approve -> SoD must block self-approval
        bid = create_booking(self.c, self.admin, self.cid, "Crane", "load"); self.to_ready(bid)
        qid = create_quotation(self.c, self.admin, bid, big_lines())
        submit_quotation(self.c, self.admin, qid)
        with self.assertRaises(ForbiddenError):
            approve_quotation(self.c, self.admin, qid)

    def _accepted_booking(self):
        bid = create_booking(self.c, self.est, self.cid, "Crane", "load"); self.to_ready(bid)
        qid = create_quotation(self.c, self.est, bid, big_lines())
        submit_quotation(self.c, self.est, qid); approve_quotation(self.c, self.appr, qid)
        send_quotation(self.c, self.est, qid)
        accept_quotation(self.c, self.cust, qid, "J. Roe", "CFO")
        return bid


class TestVersioning(Base):
    def test_revision_supersedes(self):
        bid = create_booking(self.c, self.est, self.cid, "Crane", "load"); self.to_ready(bid)
        qid = create_quotation(self.c, self.est, bid, big_lines())
        submit_quotation(self.c, self.est, qid); approve_quotation(self.c, self.appr, qid)
        send_quotation(self.c, self.est, qid)
        request_revision(self.c, self.est, qid, "reduce standby")
        qid2 = create_quotation(self.c, self.est, bid, [{"rate": 150000, "days": 3}])
        rows = self.c.execute("SELECT version,status,superseded FROM quotations WHERE booking_id=? ORDER BY version", (bid,)).fetchall()
        self.assertEqual([r["version"] for r in rows], [1, 2])
        self.assertEqual(rows[0]["superseded"], 1)      # v1 superseded
        self.assertEqual(rows[1]["version"], 2)


class TestPayment(Base):
    def _accepted(self):
        bid = create_booking(self.c, self.est, self.cid, "Crane", "load"); self.to_ready(bid)
        qid = create_quotation(self.c, self.est, bid, big_lines())
        submit_quotation(self.c, self.est, qid); approve_quotation(self.c, self.appr, qid)
        send_quotation(self.c, self.est, qid); accept_quotation(self.c, self.cust, qid, "J. Roe")
        return bid

    def test_idempotent_verification(self):
        bid = self._accepted(); prid = create_payment_request(self.c, self.fin, bid, self.wise)
        due = self.c.execute("SELECT amount_due FROM payment_requests WHERE id=?", (prid,)).fetchone()["amount_due"]
        verify_payment(self.c, self.fin, prid, due, "TXN1", fees=100)
        r1 = self.c.execute("SELECT status,amount_received FROM payment_requests WHERE id=?", (prid,)).fetchone()
        verify_payment(self.c, self.fin, prid, due, "TXN1", fees=100)   # again
        r2 = self.c.execute("SELECT status,amount_received FROM payment_requests WHERE id=?", (prid,)).fetchone()
        self.assertEqual(r1["status"], "VERIFIED")
        self.assertEqual(r2["amount_received"], r1["amount_received"])   # not doubled

    def test_partial_payment(self):
        bid = self._accepted(); prid = create_payment_request(self.c, self.fin, bid, self.wise)
        due = self.c.execute("SELECT amount_due FROM payment_requests WHERE id=?", (prid,)).fetchone()["amount_due"]
        st = verify_payment(self.c, self.fin, prid, due - 1000, "TXN")
        self.assertEqual(st, "PARTIALLY_PAID")

    def test_link_has_no_secret(self):
        bid = self._accepted(); prid = create_payment_request(self.c, self.fin, bid, self.wise)
        link = register_payment_link(self.c, self.fin, prid, self.wise)
        self.assertTrue(link.startswith("https://wise.com/pay/"))
        # provider ref stored, but no api key/secret/token anywhere in the row
        blob = str(dict(self.c.execute("SELECT * FROM payment_requests WHERE id=?", (prid,)).fetchone())).lower()
        for bad in ("secret", "api_key", "apikey", "token", "bearer"):
            self.assertNotIn(bad, blob)


class TestConfirmAndIsolation(Base):
    def test_duplicate_job_prevention(self):
        bid = self._confirmable()
        no1 = confirm_job(self.c, self.admin, bid)
        no2 = confirm_job(self.c, self.admin, bid)          # idempotent
        self.assertEqual(no1, no2)
        self.assertEqual(self.c.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"], 1)

    def test_customer_data_isolation(self):
        bid = create_booking(self.c, self.est, self.cid, "Crane", "load")
        cid2 = create_customer(self.c, self.admin, "Other Co")
        create_user(self.c, "b@other", "pw", "customer", "B", customer_id=cid2)
        other = actor_for(self.c, login(self.c, "b@other", "pw"))
        with self.assertRaises(ForbiddenError):
            get_booking(self.c, other, bid)                 # cannot read Acme's booking

    def _confirmable(self):
        bid = create_booking(self.c, self.est, self.cid, "Crane", "load"); self.to_ready(bid)
        qid = create_quotation(self.c, self.est, bid, big_lines())
        submit_quotation(self.c, self.est, qid); approve_quotation(self.c, self.appr, qid)
        send_quotation(self.c, self.est, qid); accept_quotation(self.c, self.cust, qid, "J. Roe")
        prid = create_payment_request(self.c, self.fin, bid, self.wise)
        due = self.c.execute("SELECT amount_due FROM payment_requests WHERE id=?", (prid,)).fetchone()["amount_due"]
        submit_payment_evidence(self.c, self.cust, prid, "receipt.pdf")
        verify_payment(self.c, self.fin, prid, due, "TXN", fees=200)
        return bid


class TestSoftDeleteAudit(Base):
    def test_soft_delete_restore(self):
        bid = create_booking(self.c, self.admin, self.cid, "Crane", "load")
        soft_delete(self.c, self.admin, "bookings", bid, "duplicate")
        self.assertEqual(self.c.execute("SELECT status FROM bookings WHERE id=?", (bid,)).fetchone()["status"], "DELETED")
        restore(self.c, self.admin, "bookings", bid)
        self.assertEqual(self.c.execute("SELECT status FROM bookings WHERE id=?", (bid,)).fetchone()["status"], "ACTIVE")


class TestEndToEnd(Base):
    def test_full_scenario(self):
        c = self.c
        bid = create_booking(c, self.cust, self.cid, "Machinery Relocation", "Transformer 42t")  # customer self-books
        review_booking(c, self.est, bid)
        ready_for_quotation(c, self.est, bid)
        qid = create_quotation(c, self.est, bid, big_lines(), est_cost=420000)
        st = submit_quotation(c, self.est, qid); self.assertEqual(st, "pending_approval")
        approve_quotation(c, self.appr, qid)                # different user -> SoD ok
        send_quotation(c, self.est, qid)
        request_revision(c, self.cust, qid, "trim standby")
        qid2 = create_quotation(c, self.est, bid, big_lines())
        submit_quotation(c, self.est, qid2); approve_quotation(c, self.appr, qid2)
        send_quotation(c, self.est, qid2)
        accept_quotation(c, self.cust, qid2, "J. Roe", "CFO")
        prid = create_payment_request(c, self.fin, bid, self.wise)
        register_payment_link(c, self.fin, prid, self.wise)
        submit_payment_evidence(c, self.cust, prid, "wise_receipt.pdf")
        due = c.execute("SELECT amount_due FROM payment_requests WHERE id=?", (prid,)).fetchone()["amount_due"]
        verify_payment(c, self.fin, prid, due, "WISE-TXN-1", fees=due * 0.007)
        job_no = confirm_job(c, self.admin, bid)
        self.assertTrue(job_no.startswith("JO-"))
        b = c.execute("SELECT stage, job_id FROM bookings WHERE id=?", (bid,)).fetchone()
        self.assertEqual(b["stage"], "CONFIRMED")
        self.assertIsNotNone(b["job_id"])
        # audit recorded the confirmation
        self.assertTrue(any(a["action"] == "job.confirm" for a in list_audit(c)))


if __name__ == "__main__":
    import json  # noqa
    unittest.main(verbosity=2)
