"""RGO OS backend — admin/support domain tests (master data, inventory, documents,
notifications, subcontractors/suppliers, safety gate)."""
import unittest
import core, ops, admin
from core import (create_user, login, actor_for, create_customer, create_booking,
                  review_booking, ready_for_quotation, create_quotation, submit_quotation,
                  approve_quotation, send_quotation, accept_quotation, create_payment_request,
                  verify_payment, confirm_job, MockWiseProvider, ForbiddenError, ConflictError,
                  ValidationError)
from ops import reserve_resource, transition_job
from admin import (md_create, md_mark_used, md_deactivate, md_delete, inv_create, inv_move,
                   low_stock, doc_upload, nt_template, notify, MockSender, MockScanner,
                   sc_create, sup_create, po_create, safety_record, report_incident)


def qlines():
    return [{"kind": "crane", "qty": 1, "days": 3, "rate": 200000}]


class Base(unittest.TestCase):
    def setUp(self):
        self.c = admin.connect_full(":memory:")
        MockSender.sent = []
        for e, r in [("admin@r", "admin"), ("est@r", "estimator"), ("appr@r", "approver"),
                     ("fin@r", "finance"), ("ops@r", "operations_manager"),
                     ("safe@r", "safety_officer"), ("drv@r", "driver")]:
            create_user(self.c, e, "pw", r, r)
        A = lambda e: actor_for(self.c, login(self.c, e, "pw"))
        self.admin, self.est, self.appr, self.fin, self.ops, self.safe, self.drv = \
            A("admin@r"), A("est@r"), A("appr@r"), A("fin@r"), A("ops@r"), A("safe@r"), A("drv@r")
        self.cid = create_customer(self.c, self.admin, "Acme")
        create_user(self.c, "cust@a", "pw", "customer", "J", customer_id=self.cid)
        self.cust = A("cust@a")
        self.wise = MockWiseProvider()

    def confirmed(self):
        c = self.c
        bid = create_booking(c, self.est, self.cid, "Crane", "load")
        review_booking(c, self.est, bid); ready_for_quotation(c, self.est, bid)
        qid = create_quotation(c, self.est, bid, qlines())
        submit_quotation(c, self.est, qid); approve_quotation(c, self.appr, qid)
        send_quotation(c, self.est, qid); accept_quotation(c, self.cust, qid, "J")
        prid = create_payment_request(c, self.fin, bid, self.wise)
        due = c.execute("SELECT amount_due FROM payment_requests WHERE id=?", (prid,)).fetchone()["amount_due"]
        verify_payment(c, self.fin, prid, due, "T", 100)
        jn = confirm_job(c, self.admin, bid)
        jid = c.execute("SELECT id FROM jobs WHERE no=?", (jn,)).fetchone()["id"]
        return bid, jid


class TestMasterData(Base):
    def test_delete_guard_and_deactivate(self):
        mid = md_create(self.c, self.admin, "service_type", "CRANE_HIRE", "Crane Hire")
        md_mark_used(self.c, mid)                       # referenced by a transaction
        with self.assertRaises(ConflictError):
            md_delete(self.c, self.admin, mid)          # cannot delete in-use value
        md_deactivate(self.c, self.admin, mid)          # deactivate instead
        self.assertEqual(self.c.execute("SELECT active FROM master_data WHERE id=?", (mid,)).fetchone()["active"], 0)
        mid2 = md_create(self.c, self.admin, "service_type", "RIGGING", "Rigging")
        md_delete(self.c, self.admin, mid2)             # unused -> deletable
        self.assertIsNone(self.c.execute("SELECT 1 FROM master_data WHERE id=?", (mid2,)).fetchone())


class TestInventory(Base):
    def test_stock_control_and_low_stock(self):
        it = inv_create(self.c, self.ops, "SLING-10T", "10t Sling", reorder_point=5)
        self.assertEqual(inv_move(self.c, self.ops, it, "IN", 20), 20)
        self.assertEqual(inv_move(self.c, self.ops, it, "OUT", 16), 4)
        self.assertTrue(any(x["sku"] == "SLING-10T" for x in low_stock(self.c)))  # 4 <= 5
        with self.assertRaises(ValidationError):
            inv_move(self.c, self.ops, it, "OUT", 100)  # cannot go negative

    def test_driver_cannot_move_stock(self):
        it = inv_create(self.c, self.ops, "SHACKLE", "Shackle")
        with self.assertRaises(ForbiddenError):
            inv_move(self.c, self.drv, it, "IN", 5)


class TestDocuments(Base):
    def test_valid_upload(self):
        did = doc_upload(self.c, self.ops, "booking", 1, "quote.pdf", "application/pdf", 20000, "s3://x")
        self.assertTrue(did)

    def test_rejects_bad_type_size_and_malware(self):
        with self.assertRaises(ValidationError):
            doc_upload(self.c, self.ops, "booking", 1, "x.exe", "application/x-msdownload", 100, "s3://x")
        with self.assertRaises(ValidationError):
            doc_upload(self.c, self.ops, "booking", 1, "big.pdf", "application/pdf", 99 * 1024 * 1024, "s3://x")
        with self.assertRaises(ValidationError):
            doc_upload(self.c, self.ops, "booking", 1, "bad.pdf", "application/pdf", 100, "s3://virus-file")


class TestNotifications(Base):
    def test_template_render_and_send(self):
        nt_template(self.c, self.admin, "quote_sent", "Quotation {no}", "Hi {name}, quote {no} total {total}.")
        nid = notify(self.c, self.admin, "quote_sent", "jroe@acme.demo",
                     {"no": "QN-3001", "name": "J", "total": "600,000"})
        row = self.c.execute("SELECT status,subject FROM notifications WHERE id=?", (nid,)).fetchone()
        self.assertEqual(row["status"], "SENT")
        self.assertEqual(row["subject"], "Quotation QN-3001")
        self.assertEqual(len(MockSender.sent), 1)


class TestVendors(Base):
    def test_subcontractor_supplier_po(self):
        sc_create(self.c, self.ops, "Heavy Rig Partners", coverage="Luzon")
        sup = sup_create(self.c, self.ops, "FuelCo", "fuel")
        po = po_create(self.c, self.ops, sup, "Diesel 2000L", 120000)
        self.assertTrue(self.c.execute("SELECT no FROM purchase_orders WHERE id=?", (po,)).fetchone()["no"].startswith("PO-"))

    def test_po_missing_supplier(self):
        from core import NotFoundError
        with self.assertRaises(NotFoundError):
            po_create(self.c, self.ops, 999, "x", 1)


class TestSafetyGate(Base):
    def test_failed_safety_blocks_dispatch_pass_allows(self):
        bid, jid = self.confirmed()
        reserve_resource(self.c, self.ops, bid, "crane", "CC-250", confirmed=True)
        transition_job(self.c, self.ops, jid, "PLANNING")
        transition_job(self.c, self.ops, jid, "RESOURCES_RESERVED")
        transition_job(self.c, self.ops, jid, "SAFETY_REVIEW")
        safety_record(self.c, self.safe, jid, "FAIL", notes="ground unstable")
        with self.assertRaises(ConflictError):
            transition_job(self.c, self.ops, jid, "READY_FOR_DISPATCH")
        safety_record(self.c, self.safe, jid, "PASS", notes="ground matted")   # re-inspection passes
        transition_job(self.c, self.ops, jid, "READY_FOR_DISPATCH")            # now allowed

    def test_incident_report(self):
        _, jid = self.confirmed()
        iid = report_incident(self.c, self.safe, jid, "MINOR", "near miss")
        self.assertTrue(iid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
