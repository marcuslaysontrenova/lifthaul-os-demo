"""LiftHaul OS — two-tenant isolation (Phase 1 Item 4, HTTP + persistent DB).

Drives the real HTTP router (server._match) -> real authorization -> persistent SQLite
file DB. Two synthetic hauling companies with OVERLAPPING names/values prove isolation
is by tenant, not by string. Covers read isolation, relationship isolation, tenant
stamping, forged-tenant rejection, and restart persistence.

NOTE: this is end-to-end through every backend layer and a persistent database. The
LITERAL browser E2E and a PostgreSQL container restart require a Docker/PG/browser host
and are reported separately as environment-blocked — they are NOT claimed here.
"""
import os
import unittest

DBFILE = "rgo_tenant_test.sqlite"
if os.path.exists(DBFILE):
    os.remove(DBFILE)
os.environ["DATABASE_URL"] = "sqlite:///" + DBFILE

import server   # noqa: E402
import core      # noqa: E402
import db        # noqa: E402
import admin_platform as ap   # noqa: E402
import tenant    # noqa: E402


def call(method, path, body=None, actor=None):
    fn, params = server._match(method, path)
    assert fn, f"no route for {method} {path}"
    return fn(actor, body or {}, params or {})


def _mkactor(email):
    tok = call("POST", "/login", {"email": email, "password": "demo1234"})["token"]
    a = core.actor_for(server._conn, tok)
    ap.apply_rbac(server._conn, a)
    return a


class TestTwoTenantIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.path.exists(DBFILE):
            os.remove(DBFILE)
        server._conn = db.connect("sqlite:///" + DBFILE)   # own fresh DB, import-order independent
        c = server._conn
        cls.tA = ap.create_tenant(c, "HAULA", "Synthetic Hauling Company A")
        cls.tB = ap.create_tenant(c, "HAULB", "Synthetic Hauling Company B")
        uA = core.create_user(c, "admin@haula", "demo1234", "operations_manager", "A Admin")
        uB = core.create_user(c, "admin@haulb", "demo1234", "operations_manager", "B Admin")
        tenant.bind_user_tenant(c, None, uA, cls.tA)      # authoritative tenant membership
        tenant.bind_user_tenant(c, None, uB, cls.tB)
        cls.aA, cls.aB = _mkactor("admin@haula"), _mkactor("admin@haulb")
        # overlapping data (same names) in each tenant
        cls.custA = call("POST", "/customers", {"name": "Acme Hauling", "email": "x@acme"}, cls.aA)["id"]
        cls.custB = call("POST", "/customers", {"name": "Acme Hauling", "email": "x@acme"}, cls.aB)["id"]
        cls.bkA = call("POST", "/bookings", {"customer_id": cls.custA, "service": "Crane", "cargo": "Transformer", "weight": 40}, cls.aA)["id"]
        cls.bkB = call("POST", "/bookings", {"customer_id": cls.custB, "service": "Crane", "cargo": "Transformer", "weight": 40}, cls.aB)["id"]

    # ---- tenant context is server-derived, not client-supplied ------------
    def test_actor_carries_authoritative_tenant(self):
        self.assertEqual(self.aA["tenant_id"], self.tA)
        self.assertEqual(self.aB["tenant_id"], self.tB)

    def test_create_stamps_actor_tenant_ignoring_forged_body(self):
        # a forged tenant_id in the body must be ignored — ownership comes from the actor
        cid = call("POST", "/customers", {"name": "Forged", "tenant_id": self.tB}, self.aA)["id"]
        row = server._conn.execute("SELECT tenant_id FROM customers WHERE id=?", (cid,)).fetchone()
        self.assertEqual(row["tenant_id"], self.tA)        # actor's tenant, not the forged one

    # ---- read isolation ---------------------------------------------------
    def test_own_records_readable(self):
        self.assertEqual(call("GET", f"/bookings/{self.bkA}", {}, self.aA)["id"], self.bkA)
        self.assertEqual(call("GET", f"/bookings/{self.bkB}", {}, self.aB)["id"], self.bkB)

    def test_cross_tenant_read_denied_as_not_found(self):
        with self.assertRaises(core.NotFoundError):           # 404 — no existence leak
            call("GET", f"/bookings/{self.bkB}", {}, self.aA)
        with self.assertRaises(core.NotFoundError):
            call("GET", f"/bookings/{self.bkA}", {}, self.aB)

    # ---- relationship isolation ------------------------------------------
    def test_cross_tenant_relationship_denied(self):
        with self.assertRaises(core.ForbiddenError):          # A booking a B customer
            call("POST", "/bookings", {"customer_id": self.custB, "service": "Crane", "cargo": "X", "weight": 1}, self.aA)

    # ---- restart persistence ---------------------------------------------
    def test_restart_persistence_and_isolation(self):
        server._conn.close()
        server._conn = db.connect("sqlite:///" + DBFILE)      # reconnect to the same file
        # both tenants' records survive
        self.assertEqual(call("GET", f"/bookings/{self.bkA}", {}, self.aA)["id"], self.bkA)
        self.assertEqual(call("GET", f"/bookings/{self.bkB}", {}, self.aB)["id"], self.bkB)
        # isolation still enforced after restart
        with self.assertRaises(core.NotFoundError):
            call("GET", f"/bookings/{self.bkB}", {}, self.aA)

    @classmethod
    def tearDownClass(cls):
        try:
            server._conn.close()
        except Exception:
            pass
        for f in (DBFILE,):
            try: os.remove(f)
            except OSError: pass
        os.environ.pop("DATABASE_URL", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
