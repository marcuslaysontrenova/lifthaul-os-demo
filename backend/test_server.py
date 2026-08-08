"""RGO OS backend — API-level test: drive the full lifecycle through the HTTP
router (server._match handlers), proving the transactional operations work over
the API surface, not just the service layer. Also checks authorization mapping."""
import os
import unittest

# fresh DB before importing the server (it opens a file DB + seeds users on import)
if os.path.exists("rgo_os.sqlite"):
    os.remove("rgo_os.sqlite")

import server  # noqa: E402
import core     # noqa: E402
import db       # noqa: E402


def call(method, path, body=None, actor=None):
    fn, params = server._match(method, path)
    assert fn, f"no route for {method} {path}"
    return fn(actor, body or {}, params or {})


class TestApiLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Isolation: server._conn is a module global shared across test modules; the login
        # lockout counts consecutive failed login_history rows per email, so another module's
        # failed logins on the shared connection could leave a seeded account locked here.
        # Rebinding to a fresh, fully-seeded connection makes this suite order-independent
        # WITHOUT changing production behavior (the lockout policy itself is untouched).
        cls._orig_conn = server._conn
        server._conn = db.connect(":memory:")
        server._seed_users()                               # admin@rgo.demo, est@, appr@, fin@
        c = server._conn
        for e, r in [("ops2@r", "operations_manager"), ("safe2@r", "safety_officer")]:
            try:
                core.create_user(c, e, "demo1234", r, r)
            except core.ConflictError:
                pass
        cls.tok = lambda self, e: call("POST", "/login", {"email": e, "password": "demo1234"})["token"]

    @classmethod
    def tearDownClass(cls):
        try:
            server._conn.close()
        except Exception:
            pass
        server._conn = cls._orig_conn                      # restore the shared connection

    def _actor(self, email):
        return core.actor_for(server._conn, call("POST", "/login", {"email": email, "password": "demo1234"})["token"])

    def test_full_lifecycle_over_api(self):
        c = server._conn
        admin = self._actor("admin@rgo.demo")
        est = self._actor("est@rgo.demo")
        appr = self._actor("appr@rgo.demo")
        fin = self._actor("fin@rgo.demo")
        opsm = self._actor("ops2@r")
        safe = self._actor("safe2@r")
        cid = call("POST", "/customers", {"name": "API Co"}, admin)["id"]
        # customer user
        try:
            core.create_user(c, "apicust@r", "demo1234", "customer", "C", customer_id=cid)
        except core.ConflictError:
            pass
        cust = self._actor("apicust@r")

        bid = call("POST", "/bookings", {"customer_id": cid, "service": "Crane", "cargo": "load"}, est)["id"]
        call("POST", f"/bookings/{bid}/review", {}, est)
        call("POST", f"/bookings/{bid}/ready", {}, est)
        qid = call("POST", f"/bookings/{bid}/quotation", {"lines": [{"rate": 200000, "days": 3}]}, est)["id"]
        self.assertEqual(call("POST", f"/quotations/{qid}/submit", {}, est)["status"], "pending_approval")
        call("POST", f"/quotations/{qid}/approve", {}, appr)
        call("POST", f"/quotations/{qid}/send", {}, est)
        call("POST", f"/quotations/{qid}/accept", {"accepted_by": "C", "position": "CFO"}, cust)
        prid = call("POST", f"/bookings/{bid}/payment-request", {}, fin)["id"]
        call("POST", f"/payments/{prid}/link", {}, fin)
        call("POST", f"/payments/{prid}/evidence", {"proof": "r.pdf"}, cust)
        due = c.execute("SELECT amount_due FROM payment_requests WHERE id=?", (prid,)).fetchone()["amount_due"]
        self.assertEqual(call("POST", f"/payments/{prid}/verify",
                              {"amount_received": due, "txn_ref": "T", "fees": 100}, fin)["status"], "VERIFIED")
        job_no = call("POST", f"/bookings/{bid}/confirm", {}, admin)["job_no"]
        jid = c.execute("SELECT id FROM jobs WHERE no=?", (job_no,)).fetchone()["id"]

        # ops over API
        call("POST", f"/bookings/{bid}/reserve", {"resource_type": "crane", "resource_ref": "CC-250", "confirmed": True}, opsm)
        for s in ("PLANNING", "RESOURCES_RESERVED", "SAFETY_REVIEW"):
            call("POST", f"/jobs/{jid}/transition", {"to_status": s}, opsm)
        call("POST", f"/jobs/{jid}/safety", {"result": "PASS", "notes": "ok"}, safe)
        call("POST", f"/jobs/{jid}/transition", {"to_status": "READY_FOR_DISPATCH"}, opsm)
        call("POST", f"/jobs/{jid}/transition", {"to_status": "DISPATCHED", "evidence": "departed"}, opsm)
        for s in ("ON_SITE", "IN_PROGRESS", "COMPLETED", "CUSTOMER_ACCEPTANCE_PENDING", "ACCEPTED"):
            ev = {"evidence": "x"} if s in ("ON_SITE", "COMPLETED", "ACCEPTED") else {}
            call("POST", f"/jobs/{jid}/transition", dict({"to_status": s}, **ev), opsm)
        call("POST", f"/jobs/{jid}/expense", {"category": "fuel", "amount": 28000}, opsm)
        iid = call("POST", f"/jobs/{jid}/invoice", {"due_date": "2999-01-01"}, fin)["id"]
        bal = c.execute("SELECT balance FROM invoices WHERE id=?", (iid,)).fetchone()["balance"]
        self.assertEqual(call("POST", f"/invoices/{iid}/allocate", {"amount": bal, "ref": "FINAL"}, fin)["status"], "PAID")
        prof = call("GET", f"/jobs/{jid}/profitability", {}, admin)
        self.assertGreater(prof["final_revenue"], 0)
        reports = call("GET", "/reports", {}, admin)
        self.assertGreaterEqual(reports["confirmed_jobs"], 1)

    def test_api_authorization_enforced(self):
        # customer cannot verify a payment via the API
        cust = None
        try:
            core.create_user(server._conn, "apicust2@r", "demo1234", "customer", "C")
        except core.ConflictError:
            pass
        cust = self._actor("apicust2@r")
        with self.assertRaises(core.ForbiddenError):
            call("POST", "/payments/1/verify", {"amount_received": 1, "txn_ref": "x"}, cust)


if __name__ == "__main__":
    unittest.main(verbosity=2)
