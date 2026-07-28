"""RGO OS backend — operations & finance tests (extends the spine to job closure)."""
import unittest
import core
import ops
from core import (create_user, login, actor_for, create_customer, create_booking,
                  review_booking, ready_for_quotation, create_quotation, submit_quotation,
                  approve_quotation, send_quotation, accept_quotation, create_payment_request,
                  verify_payment, confirm_job, MockWiseProvider, ForbiddenError, ConflictError,
                  ValidationError)
from ops import (connect_full, create_site_assessment, assessment_ok, reserve_resource,
                 confirm_reservations, release_expired_holds, transition_job, create_change_order,
                 approve_change_order, approved_change_total, add_expense, approve_expense,
                 actual_cost, job_profitability, generate_final_invoice, allocate_payment,
                 mark_overdue, cancel_and_refund, approve_refund, report_quotation_conversion,
                 report_receivables)


def lines():
    return [{"kind": "crane", "description": "350t", "qty": 1, "days": 3, "rate": 200000}]


class Base(unittest.TestCase):
    def setUp(self):
        self.c = connect_full(":memory:")
        for e, r in [("admin@r", "admin"), ("est@r", "estimator"), ("appr@r", "approver"),
                     ("fin@r", "finance"), ("ops@r", "operations_manager"), ("disp@r", "dispatcher"),
                     ("drv@r", "driver")]:
            create_user(self.c, e, "pw", r, r)
        A = lambda e: actor_for(self.c, login(self.c, e, "pw"))
        self.admin, self.est, self.appr, self.fin, self.ops, self.disp, self.drv = \
            A("admin@r"), A("est@r"), A("appr@r"), A("fin@r"), A("ops@r"), A("disp@r"), A("drv@r")
        self.cid = create_customer(self.c, self.admin, "Acme")
        create_user(self.c, "cust@a", "pw", "customer", "J", customer_id=self.cid)
        self.cust = A("cust@a")
        self.wise = MockWiseProvider()

    def confirmed(self):
        """Run the spine to a CONFIRMED job; return (booking_id, job_no, job_id)."""
        c = self.c
        bid = create_booking(c, self.est, self.cid, "Crane", "load")
        review_booking(c, self.est, bid); ready_for_quotation(c, self.est, bid)
        qid = create_quotation(c, self.est, bid, lines(), est_cost=400000)
        submit_quotation(c, self.est, qid); approve_quotation(c, self.appr, qid)
        send_quotation(c, self.est, qid); accept_quotation(c, self.cust, qid, "J", "CFO")
        prid = create_payment_request(c, self.fin, bid, self.wise)
        due = c.execute("SELECT amount_due FROM payment_requests WHERE id=?", (prid,)).fetchone()["amount_due"]
        verify_payment(c, self.fin, prid, due, "T", fees=100)
        job_no = confirm_job(c, self.admin, bid)
        jid = c.execute("SELECT id FROM jobs WHERE no=?", (job_no,)).fetchone()["id"]
        return bid, job_no, jid


class TestSiteAssessment(Base):
    def test_gate(self):
        bid = create_booking(self.c, self.est, self.cid, "Crane", "load")
        self.assertTrue(assessment_ok(self.c, bid))  # none yet -> ok
        create_site_assessment(self.c, self.est, bid, "NOT_READY", hazards="power lines")
        self.assertFalse(assessment_ok(self.c, bid))
        create_site_assessment(self.c, self.est, bid, "READY_WITH_CONDITIONS")
        self.assertTrue(assessment_ok(self.c, bid))


class TestReservation(Base):
    def test_double_book_prevented(self):
        b1 = create_booking(self.c, self.est, self.cid, "Crane", "l1")
        b2 = create_booking(self.c, self.est, self.cid, "Crane", "l2")
        reserve_resource(self.c, self.ops, b1, "crane", "CC-250", confirmed=True)
        with self.assertRaises(ConflictError):
            reserve_resource(self.c, self.ops, b2, "crane", "CC-250")

    def test_temp_hold_expiry_release(self):
        b1 = create_booking(self.c, self.est, self.cid, "Crane", "l1")
        reserve_resource(self.c, self.ops, b1, "crane", "AT-100", hold_hours=48)
        # nothing expired yet
        self.assertEqual(release_expired_holds(self.c, self.ops, as_of="2000-01-01T00:00:00+00:00"), [])
        # far-future as_of -> the hold is past expiry -> released
        released = release_expired_holds(self.c, self.ops, as_of="2999-01-01T00:00:00+00:00")
        self.assertEqual(len(released), 1)
        # released resource can be re-booked by another booking
        b2 = create_booking(self.c, self.est, self.cid, "Crane", "l2")
        reserve_resource(self.c, self.ops, b2, "crane", "AT-100")


class TestJobLifecycle(Base):
    def test_dispatch_requires_confirmed_reservation(self):
        bid, _, jid = self.confirmed()
        transition_job(self.c, self.ops, jid, "PLANNING")
        transition_job(self.c, self.ops, jid, "RESOURCES_RESERVED")
        transition_job(self.c, self.ops, jid, "SAFETY_REVIEW")
        with self.assertRaises(ConflictError):        # no confirmed reservation
            transition_job(self.c, self.ops, jid, "READY_FOR_DISPATCH")
        reserve_resource(self.c, self.ops, bid, "crane", "CC-250", confirmed=True)
        transition_job(self.c, self.ops, jid, "READY_FOR_DISPATCH")

    def test_dispatch_requires_verified_payment(self):
        bid, _, jid = self.confirmed()
        reserve_resource(self.c, self.ops, bid, "crane", "CC-250", confirmed=True)
        for s in ("PLANNING", "RESOURCES_RESERVED", "SAFETY_REVIEW", "READY_FOR_DISPATCH"):
            transition_job(self.c, self.ops, jid, s)
        # break the payment to prove the gate
        self.c.execute("UPDATE payment_requests SET status='SUBMITTED' WHERE booking_id=?", (bid,))
        with self.assertRaises(ConflictError):
            transition_job(self.c, self.ops, jid, "DISPATCHED", evidence="departed 06:00")

    def test_evidence_required(self):
        bid, _, jid = self.confirmed()
        reserve_resource(self.c, self.ops, bid, "crane", "CC-250", confirmed=True)
        for s in ("PLANNING", "RESOURCES_RESERVED", "SAFETY_REVIEW", "READY_FOR_DISPATCH"):
            transition_job(self.c, self.ops, jid, s)
        with self.assertRaises(ValidationError):      # DISPATCHED needs evidence
            transition_job(self.c, self.ops, jid, "DISPATCHED")

    def test_illegal_transition(self):
        _, _, jid = self.confirmed()
        with self.assertRaises(ConflictError):
            transition_job(self.c, self.ops, jid, "CLOSED")


class TestChangeOrders(Base):
    def test_only_approved_change_orders_count(self):
        _, _, jid = self.confirmed()
        co = create_change_order(self.c, self.ops, jid, "extra crane day", 85000, tax=10200)
        self.assertEqual(approved_change_total(self.c, jid), 0)     # not approved yet -> not billable
        approve_change_order(self.c, self.ops, co)
        self.assertEqual(approved_change_total(self.c, jid), 95200)


class TestExpensesCosting(Base):
    def test_actual_cost_and_profitability(self):
        _, _, jid = self.confirmed()
        e1 = add_expense(self.c, self.ops, jid, "fuel", 30000)
        add_expense(self.c, self.ops, jid, "tolls", 5000)         # submitted, not approved
        self.assertEqual(actual_cost(self.c, jid), 0)              # none approved yet
        approve_expense(self.c, self.ops, e1)
        self.assertEqual(actual_cost(self.c, jid), 30000)
        p = job_profitability(self.c, jid)
        self.assertEqual(p["actual_cost"], 30000)
        self.assertEqual(p["gross_profit"], p["final_revenue"] - 30000)


class TestBilling(Base):
    def _to_accepted_job(self):
        bid, _, jid = self.confirmed()
        reserve_resource(self.c, self.ops, bid, "crane", "CC-250", confirmed=True)
        for s in ("PLANNING", "RESOURCES_RESERVED", "SAFETY_REVIEW", "READY_FOR_DISPATCH"):
            transition_job(self.c, self.ops, jid, s)
        transition_job(self.c, self.ops, jid, "DISPATCHED", evidence="departed")
        transition_job(self.c, self.ops, jid, "ON_SITE", evidence="arrived")
        transition_job(self.c, self.ops, jid, "IN_PROGRESS")
        transition_job(self.c, self.ops, jid, "COMPLETED", evidence="lift complete")
        transition_job(self.c, self.ops, jid, "CUSTOMER_ACCEPTANCE_PENDING")
        transition_job(self.c, self.ops, jid, "ACCEPTED", evidence="client signoff")
        return bid, jid

    def test_final_invoice_deducts_downpayment_and_partials(self):
        bid, jid = self._to_accepted_job()
        iid = generate_final_invoice(self.c, self.fin, jid, due_date="2999-01-01")
        inv = dict(self.c.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone())
        self.assertGreater(inv["downpayment_applied"], 0)
        self.assertEqual(inv["balance"], inv["total"] - inv["downpayment_applied"])
        half = inv["balance"] / 2
        r1 = allocate_payment(self.c, self.fin, iid, half, "PAY-1")
        self.assertEqual(r1["status"], "PARTIALLY_PAID")
        r2 = allocate_payment(self.c, self.fin, iid, half, "PAY-2")
        self.assertEqual(r2["status"], "PAID")
        self.assertAlmostEqual(r2["balance"], 0, places=2)

    def test_invoice_before_completion_blocked(self):
        _, _, jid = self.confirmed()   # job still CONFIRMED
        with self.assertRaises(ConflictError):
            generate_final_invoice(self.c, self.fin, jid)

    def test_double_invoice_blocked(self):
        _, jid = self._to_accepted_job()
        generate_final_invoice(self.c, self.fin, jid)
        with self.assertRaises(ConflictError):
            generate_final_invoice(self.c, self.fin, jid)

    def test_mark_overdue(self):
        _, jid = self._to_accepted_job()
        generate_final_invoice(self.c, self.fin, jid, due_date="2000-01-01")
        mark_overdue(self.c, self.fin, as_of="2999-01-01")
        st = self.c.execute("SELECT status FROM invoices WHERE job_id=?", (jid,)).fetchone()["status"]
        self.assertEqual(st, "OVERDUE")


class TestCancellationRefund(Base):
    def test_refund(self):
        bid, _, _ = self.confirmed()
        rid = cancel_and_refund(self.c, self.fin, bid, "customer request", "customer", 100000, 50000)
        approve_refund(self.c, self.fin, rid, "REF-1")
        r = self.c.execute("SELECT status,ref FROM refunds WHERE id=?", (rid,)).fetchone()
        self.assertEqual(r["status"], "APPROVED")


class TestSecurity(Base):
    def test_driver_cannot_invoice(self):
        _, _, jid = self.confirmed()
        with self.assertRaises(ForbiddenError):
            generate_final_invoice(self.c, self.drv, jid)

    def test_driver_cannot_transition_job(self):
        _, _, jid = self.confirmed()
        with self.assertRaises(ForbiddenError):
            transition_job(self.c, self.drv, jid, "PLANNING")


class TestReporting(Base):
    def test_reports_from_stored_data(self):
        self.confirmed()
        conv = report_quotation_conversion(self.c)
        self.assertGreaterEqual(conv["accepted"], 1)
        self.assertEqual(report_receivables(self.c), {})   # no invoices issued yet


class TestFullE2E(Base):
    def test_booking_to_closure_and_profit(self):
        c = self.c
        bid, _, jid = self.confirmed()                       # steps 1-17 (spine)
        reserve_resource(c, self.ops, bid, "crane", "CC-250", confirmed=True)   # 16 resources
        for s in ("PLANNING", "RESOURCES_RESERVED", "SAFETY_REVIEW", "READY_FOR_DISPATCH"):  # 18-19
            transition_job(c, self.ops, jid, s)
        transition_job(c, self.ops, jid, "DISPATCHED", evidence="departed 06:00")  # 20
        transition_job(c, self.ops, jid, "ON_SITE", evidence="arrived 07:30")
        transition_job(c, self.ops, jid, "IN_PROGRESS")
        co = create_change_order(c, self.ops, jid, "standby 2h", 24000)          # 21
        approve_change_order(c, self.ops, co)                                    # 22
        transition_job(c, self.ops, jid, "COMPLETED", evidence="lift done")      # 23
        transition_job(c, self.ops, jid, "CUSTOMER_ACCEPTANCE_PENDING")
        transition_job(c, self.ops, jid, "ACCEPTED", evidence="client accepts")  # 24
        e = add_expense(c, self.ops, jid, "fuel", 28000); approve_expense(c, self.ops, e)  # 25
        iid = generate_final_invoice(c, self.fin, jid, due_date="2999-01-01")     # 26
        inv = dict(c.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone())
        allocate_payment(c, self.fin, iid, inv["balance"], "FINAL")              # 27
        transition_job(c, self.ops, jid, "CLOSED")                               # 28
        prof = job_profitability(c, jid)                                         # 29
        self.assertEqual(c.execute("SELECT status FROM invoices WHERE id=?", (iid,)).fetchone()["status"], "PAID")
        self.assertEqual(c.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"], "CLOSED")
        self.assertEqual(prof["approved_variations"], 24000)
        self.assertEqual(prof["actual_cost"], 28000)
        self.assertEqual(prof["gross_profit"], prof["final_revenue"] - 28000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
