"""RGO OS backend — Phase 2 tests: quotation PDF (+immutability), invoice lines,
dispatch calendar, quotation-line validation."""
import unittest
import core, ops, admin, catalog, pdfgen
from core import (create_user, login, actor_for, create_customer, create_booking,
                  review_booking, ready_for_quotation, create_quotation, submit_quotation,
                  approve_quotation, send_quotation, accept_quotation, request_revision,
                  create_payment_request, verify_payment, confirm_job, MockWiseProvider,
                  ValidationError, ForbiddenError)
from ops import (reserve_resource, transition_job, create_change_order, approve_change_order,
                 generate_final_invoice, invoice_lines, calendar)
from pdfgen import generate_quotation_pdf, get_quotation_pdf, DbStore, MemStore, render_pdf


def lines(rate=200000):
    return [{"kind": "crane", "description": "350t crane", "qty": 1, "days": 3, "rate": rate}]


class Base(unittest.TestCase):
    def setUp(self):
        self.c = catalog.connect_full(":memory:")
        for e, r in [("admin@r", "admin"), ("est@r", "estimator"), ("appr@r", "approver"),
                     ("fin@r", "finance"), ("ops@r", "operations_manager")]:
            create_user(self.c, e, "pw", r, r)
        A = lambda e: actor_for(self.c, login(self.c, e, "pw"))
        self.admin, self.est, self.appr, self.fin, self.ops = A("admin@r"), A("est@r"), A("appr@r"), A("fin@r"), A("ops@r")
        self.cid = create_customer(self.c, self.admin, "Acme")
        create_user(self.c, "cust@a", "pw", "customer", "J", customer_id=self.cid)
        self.cust = A("cust@a")
        self.wise = MockWiseProvider()
        self.store = MemStore()

    def sent_quote(self):
        c = self.c
        bid = create_booking(c, self.est, self.cid, "Crane", "load")
        review_booking(c, self.est, bid); ready_for_quotation(c, self.est, bid)
        qid = create_quotation(c, self.est, bid, lines())
        submit_quotation(c, self.est, qid); approve_quotation(c, self.appr, qid); send_quotation(c, self.est, qid)
        return bid, qid


class TestQuotationLineValidation(Base):
    def test_negative_rejected(self):
        bid = create_booking(self.c, self.est, self.cid, "Crane", "load")
        review_booking(self.c, self.est, bid); ready_for_quotation(self.c, self.est, bid)
        with self.assertRaises(ValidationError):
            create_quotation(self.c, self.est, bid, [{"rate": -5, "qty": 1, "days": 1}])


class TestQuotationPDF(Base):
    def test_render_is_valid_pdf(self):
        pdf = render_pdf(["Hello", "World"])
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))

    def test_generate_stores_and_contains_number(self):
        bid, qid = self.sent_quote()
        res = generate_quotation_pdf(self.c, self.est, qid, self.store)
        no = self.c.execute("SELECT no FROM quotations WHERE id=?", (qid,)).fetchone()["no"]
        self.assertTrue(res["bytes"].startswith(b"%PDF"))
        self.assertIn(no.encode(), res["bytes"])                 # quote no printed in PDF
        self.assertEqual(self.store.get(res["ref"]), res["bytes"])  # stored
        # document row recorded
        d = self.c.execute("SELECT content_type,scan_status FROM documents WHERE storage_ref=?", (res["ref"],)).fetchone()
        self.assertEqual(d["content_type"], "application/pdf")

    def test_revision_does_not_alter_historical_pdf(self):
        bid, qid = self.sent_quote()
        r1 = generate_quotation_pdf(self.c, self.est, qid, self.store)
        v1_bytes = bytes(r1["bytes"])
        request_revision(self.c, self.est, qid, "cheaper")
        qid2 = create_quotation(self.c, self.est, bid, lines(rate=150000))   # version 2
        submit_quotation(self.c, self.est, qid2); approve_quotation(self.c, self.appr, qid2); send_quotation(self.c, self.est, qid2)
        r2 = generate_quotation_pdf(self.c, self.est, qid2, self.store)
        self.assertNotEqual(r1["ref"], r2["ref"])                # different version -> different doc
        self.assertEqual(self.store.get(r1["ref"]), v1_bytes)    # v1 PDF unchanged

    def test_customer_isolation_on_retrieve(self):
        bid, qid = self.sent_quote()
        generate_quotation_pdf(self.c, self.est, qid, self.store)
        # own customer can read
        self.assertTrue(get_quotation_pdf(self.c, self.cust, qid, self.store).startswith(b"%PDF"))
        # another customer cannot
        cid2 = create_customer(self.c, self.admin, "Other")
        create_user(self.c, "b@o", "pw", "customer", "B", customer_id=cid2)
        other = actor_for(self.c, login(self.c, "b@o", "pw"))
        with self.assertRaises(ForbiddenError):
            get_quotation_pdf(self.c, other, qid, self.store)

    def test_database_store_survives_store_recreation_and_is_immutable(self):
        _, qid = self.sent_quote()
        first_store = DbStore(self.c)
        generated = generate_quotation_pdf(self.c, self.est, qid, first_store)
        restarted_store = DbStore(self.c)  # equivalent to rebuilding the web-process store
        self.assertEqual(restarted_store.get(generated["ref"]), generated["bytes"])
        with self.assertRaises(ValueError):
            restarted_store.put(generated["ref"], b"different bytes")


class TestInvoiceLines(Base):
    def _accepted_job(self):
        c = self.c
        bid, qid = self.sent_quote()
        accept_quotation(c, self.cust, qid, "J")
        prid = create_payment_request(c, self.fin, bid, self.wise)
        due = c.execute("SELECT amount_due FROM payment_requests WHERE id=?", (prid,)).fetchone()["amount_due"]
        verify_payment(c, self.fin, prid, due, "T", 100)
        jn = confirm_job(c, self.admin, bid)
        jid = c.execute("SELECT id FROM jobs WHERE no=?", (jn,)).fetchone()["id"]
        reserve_resource(c, self.ops, bid, "crane", "CC-250", confirmed=True)
        for s in ("PLANNING", "RESOURCES_RESERVED", "SAFETY_REVIEW", "READY_FOR_DISPATCH"):
            transition_job(c, self.ops, jid, s)
        transition_job(c, self.ops, jid, "DISPATCHED", evidence="go")
        for s in (("ON_SITE", "x"), ("IN_PROGRESS", None), ("COMPLETED", "done"),
                  ("CUSTOMER_ACCEPTANCE_PENDING", None), ("ACCEPTED", "ok")):
            transition_job(c, self.ops, jid, s[0], evidence=s[1])
        return bid, jid

    def test_invoice_lines_generated(self):
        c = self.c
        bid, jid = self._accepted_job()
        co = create_change_order(c, self.ops, jid, "standby 2h", 24000)
        approve_change_order(c, self.ops, co)
        iid = generate_final_invoice(c, self.fin, jid, due_date="2999-01-01")
        il = invoice_lines(c, iid)
        kinds = [l["kind"] for l in il]
        self.assertIn("quoted", kinds)
        self.assertIn("change_order", kinds)
        self.assertIn("downpayment", kinds)                       # deduction line present
        dp = next(l for l in il if l["kind"] == "downpayment")
        self.assertLess(dp["amount"], 0)                          # downpayment is a negative line
        inv = dict(c.execute("SELECT total,balance,downpayment_applied FROM invoices WHERE id=?", (iid,)).fetchone())
        # sum of lines == balance (quoted + change orders - downpayment)
        self.assertAlmostEqual(sum(l["amount"] for l in il), inv["balance"], places=2)


class TestCalendar(Base):
    def test_calendar_shows_jobs_blocks_and_conflicts(self):
        c = self.c
        # confirm one job
        bid, qid = self.sent_quote()
        accept_quotation(c, self.cust, qid, "J")
        prid = create_payment_request(c, self.fin, bid, self.wise)
        due = c.execute("SELECT amount_due FROM payment_requests WHERE id=?", (prid,)).fetchone()["amount_due"]
        verify_payment(c, self.fin, prid, due, "T", 100)
        confirm_job(c, self.admin, bid)
        cal = calendar(c, self.admin)
        self.assertEqual(len(cal["jobs"]), 1)
        self.assertFalse(cal["jobs"][0]["blocks"]["payment"])     # payment verified -> not blocked
        # create a resource conflict: two bookings hold the same crane
        b2 = create_booking(c, self.ops, self.cid, "Crane", "l2")
        reserve_resource(c, self.ops, bid, "crane", "CC-250", confirmed=True)
        # second reservation on same resource by another booking is refused at reserve;
        # simulate a conflict by inserting a second TEMP hold directly (data-level) then detect
        c.execute("INSERT INTO reservations(booking_id,resource_type,resource_ref,status,created_at) VALUES(?,?,?,?,?)",
                  (b2, "crane", "CC-250", "TEMP", "now"))
        cal2 = calendar(c, self.admin)
        self.assertTrue(any(x["resource_ref"] == "CC-250" for x in cal2["conflicts"]))

    def test_range_filter(self):
        c = self.c
        bid, qid = self.sent_quote()
        accept_quotation(c, self.cust, qid, "J")
        prid = create_payment_request(c, self.fin, bid, self.wise)
        due = c.execute("SELECT amount_due FROM payment_requests WHERE id=?", (prid,)).fetchone()["amount_due"]
        verify_payment(c, self.fin, prid, due, "T", 100)
        confirm_job(c, self.admin, bid)
        self.assertEqual(len(calendar(c, self.admin, end="2000-01-01T00:00:00+00:00")["jobs"]), 0)  # scheduled now > 2000
        self.assertEqual(len(calendar(c, self.admin, start="2000-01-01T00:00:00+00:00")["jobs"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
