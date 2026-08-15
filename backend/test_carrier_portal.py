"""Carrier / Fleet Owner Portal — secure self-service over the EXISTING carrier ecosystem.

Proves: identity-scoped principal binding (a carrier only ever reaches its OWN data, spoofed
carrier_id ignored); the operational-eligibility summary panel (ACTIVE vs BLOCKED with reasons);
self-service writes land as DRAFT/APPLICATION/SUBMITTED and NEVER self-verify; a carrier principal
cannot call any verify/activate/approve path; the portal never elevates into a forbidden permission;
cross-carrier isolation on reads and writes; operator-only principal administration; and that the
portal composes existing domains rather than duplicating them.
"""
import unittest

import db
import core
import admin_platform as ap
import marketplace_onboarding as mo
import marketplace_trust as tr
import ltfrb
import carrier_portal as cp


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.rgo = ap.get_tenant(self.c, "RGO")["id"]
        self.op = self._a(10)   # operator / reviewer (full perms)

    def _a(self, id, perms=("*",)):
        return {"id": id, "role": "ops", "perms": set(perms), "tenant_id": self.rgo}

    def _principal(self, user_id):
        return {"id": user_id, "role": "carrier_principal",
                "perms": set(core.PERMISSIONS["carrier_principal"]), "tenant_id": self.rgo}

    def _carrier(self, reg="S1", name="Haulers", kyb=True, ltfrb_ok=True):
        cid = mo.create_carrier_application(self.c, self.op, "FLEET_OPERATOR", name,
                                            registration_type="SEC", registration_number=reg,
                                            operating_address="Manila")
        mo.submit_carrier(self.c, self.op, cid)
        mo.verify_carrier(self.c, self._a(11), cid)
        for dt in ("BUSINESS_REGISTRATION", "TAX_REGISTRATION", "AUTHORITY_TO_OPERATE", "INSURANCE"):
            d = mo.upload_document(self.c, self.op, dt, "CARRIER", cid, expiry_date="2027-01-01")
            mo.verify_document(self.c, self._a(11), d)
        mo.activate_carrier(self.c, self._a(12), cid)
        if kyb:
            k = tr.submit_kyb(self.c, self.op, "CARRIER", cid, "SEC", reg, name)
            tr.verify_kyb(self.c, self._a(11), k, "VERIFIED", "manual")
        if ltfrb_ok:
            au = ltfrb.record_authority(self.c, self.op, cid, cpc_number="CPC" + reg, expiry_date="2027-01-01")
            ltfrb.verify_authority(self.c, self._a(11), au, "VERIFIED", "manual")
        return cid

    def _bind(self, user_id, cid):
        cp.bind_principal(self.c, self.op, user_id, cid)
        return self._principal(user_id)


# --------------------------------------------------------------------------- #
class Binding(Base):
    def test_resolve_bound_carrier(self):
        cid = self._carrier()
        p = self._bind(90, cid)
        self.assertEqual(cp.resolve_carrier(self.c, p), cid)

    def test_spoofed_carrier_id_ignored(self):
        cid = self._carrier()
        p = self._bind(90, cid)
        # a client-supplied carrier_id can never redirect a bound principal
        self.assertEqual(cp.resolve_carrier(self.c, p, requested=99999), cid)

    def test_unbound_user_denied(self):
        with self.assertRaises(core.ForbiddenError):
            cp.resolve_carrier(self.c, self._principal(77))

    def test_write_requires_binding(self):
        with self.assertRaises(core.ForbiddenError):
            cp.add_vehicle(self.c, self._principal(77), "truck_6w", "X-1")

    def test_binding_requires_operator_authority(self):
        cid = self._carrier()
        weak = {"id": 5, "role": "x", "perms": {"carrier.portal.view"}, "tenant_id": self.rgo}
        with self.assertRaises(core.ForbiddenError):
            cp.bind_principal(self.c, weak, 90, cid)

    def test_revoke_blocks_access(self):
        cid = self._carrier()
        p = self._bind(90, cid)
        pid = cp._binding(self.c, p)["id"]
        cp.revoke_principal(self.c, self.op, pid)
        with self.assertRaises(core.ForbiddenError):
            cp.resolve_carrier(self.c, p)


# --------------------------------------------------------------------------- #
class Overview(Base):
    def test_active_when_verified(self):
        cid = self._carrier()
        ov = cp.overview(self.c, self._bind(90, cid))
        self.assertTrue(ov["company"]["kyb_verified"])
        self.assertTrue(ov["company"]["ltfrb_authority_valid"])
        self.assertEqual(ov["marketplace_status"], "ACTIVE")

    def test_blocked_without_kyb(self):
        cid = self._carrier(reg="S2", kyb=False)
        ov = cp.overview(self.c, self._bind(91, cid))
        self.assertEqual(ov["marketplace_status"], "BLOCKED")
        self.assertTrue(ov["marketplace_reasons"])

    def test_fleet_and_driver_counts(self):
        cid = self._carrier()
        p = self._bind(90, cid)
        cp.add_vehicle(self.c, p, "truck_6w", "PLT-1")   # DRAFT -> not eligible
        cp.add_driver(self.c, p, "Juan", licence_expiry="2027-01-01")
        ov = cp.overview(self.c, p)
        self.assertEqual(ov["fleet"]["total"], 1)
        self.assertEqual(ov["fleet"]["eligible"], 0)     # newly registered, not yet activated
        self.assertEqual(ov["drivers"]["total"], 1)


# --------------------------------------------------------------------------- #
class SelfService(Base):
    def test_add_vehicle_lands_draft(self):
        cid = self._carrier()
        r = cp.add_vehicle(self.c, self._bind(90, cid), "truck_6w", "PLT-1")
        self.assertEqual(r["status"], "DRAFT")
        row = self.c.execute("SELECT status,carrier_id FROM mkt_vehicles WHERE id=?", (r["vehicle_id"],)).fetchone()
        self.assertEqual(row["status"], "DRAFT")
        self.assertEqual(row["carrier_id"], cid)

    def test_add_driver_lands_application(self):
        cid = self._carrier()
        r = cp.add_driver(self.c, self._bind(90, cid), "Juan", licence_expiry="2027-01-01")
        self.assertEqual(r["status"], "APPLICATION")

    def test_upload_document_not_verified(self):
        cid = self._carrier()
        r = cp.upload_document(self.c, self._bind(90, cid), "AUTHORITY_TO_OPERATE", "CARRIER", cid,
                               expiry_date="2027-06-01")
        self.assertEqual(r["status"], "UPLOADED")
        row = self.c.execute("SELECT status FROM mkt_documents WHERE id=?", (r["document_id"],)).fetchone()
        self.assertNotEqual(row["status"], "VERIFIED")

    def test_payout_submitted_not_approved(self):
        cid = self._carrier()
        r = cp.submit_payout_account(self.c, self._bind(90, cid), "Juan Cruz", "Haulers Inc",
                                     "prov_ref", "1234567890")
        self.assertEqual(r["status"], "SUBMITTED")
        row = self.c.execute("SELECT status FROM mkt_payout_accounts WHERE id=?", (r["payout_account_id"],)).fetchone()
        self.assertNotIn(row["status"], ("APPROVED", "ACTIVE"))

    def test_maintenance_hold_toggle(self):
        cid = self._carrier()
        p = self._bind(90, cid)
        vid = cp.add_vehicle(self.c, p, "truck_6w", "PLT-1")["vehicle_id"]
        cp.set_vehicle_maintenance(self.c, p, vid, on=True)
        self.assertEqual(self.c.execute("SELECT status FROM mkt_vehicles WHERE id=?", (vid,)).fetchone()["status"],
                         "MAINTENANCE")


# --------------------------------------------------------------------------- #
class NoSelfVerification(Base):
    def test_principal_cannot_verify_vehicle(self):
        cid = self._carrier()
        p = self._bind(90, cid)
        vid = cp.add_vehicle(self.c, p, "truck_6w", "PLT-1")["vehicle_id"]
        with self.assertRaises(core.ForbiddenError):
            mo.verify_vehicle(self.c, p, vid)     # role lacks marketplace.vehicle.verify

    def test_principal_cannot_verify_document(self):
        cid = self._carrier()
        p = self._bind(90, cid)
        did = cp.upload_document(self.c, p, "AUTHORITY_TO_OPERATE", "CARRIER", cid,
                                 expiry_date="2027-06-01")["document_id"]
        with self.assertRaises(core.ForbiddenError):
            mo.verify_document(self.c, p, did)

    def test_principal_cannot_approve_payout(self):
        import marketplace_trust_closure as tc
        cid = self._carrier()
        p = self._bind(90, cid)
        pid = cp.submit_payout_account(self.c, p, "Juan", "Haulers", "prov_ref", "1234567890")["payout_account_id"]
        with self.assertRaises(core.ForbiddenError):
            tc.approve_payout_account(self.c, p, pid)

    def test_portal_never_elevates_into_verification(self):
        cid = self._carrier()
        p = self._bind(90, cid)
        for forbidden in ("marketplace.vehicle.verify", "marketplace.compliance.verify",
                          "marketplace.payout.approve", "marketplace.carrier.activate"):
            with self.assertRaises(core.ForbiddenError):
                cp._svc(p, forbidden)


# --------------------------------------------------------------------------- #
class CrossCarrierIsolation(Base):
    def test_cannot_upload_for_another_carrier(self):
        a = self._carrier(reg="A1", name="Alpha")
        b = self._carrier(reg="B1", name="Bravo")
        pa = self._bind(90, a)
        with self.assertRaises(core.ForbiddenError):
            cp.upload_document(self.c, pa, "AUTHORITY_TO_OPERATE", "CARRIER", b, expiry_date="2027-06-01")

    def test_fleet_scoped_to_own_carrier(self):
        a = self._carrier(reg="A1", name="Alpha")
        b = self._carrier(reg="B1", name="Bravo")
        pa, pb = self._bind(90, a), self._bind(91, b)
        cp.add_vehicle(self.c, pa, "truck_6w", "A-PLATE")
        cp.add_vehicle(self.c, pb, "truck_6w", "B-PLATE")
        plates_a = {v["plate_number"] for v in cp.fleet(self.c, pa)["vehicles"]}
        self.assertIn("A-PLATE", plates_a)
        self.assertNotIn("B-PLATE", plates_a)

    def test_cannot_hold_another_carriers_vehicle(self):
        a = self._carrier(reg="A1", name="Alpha")
        b = self._carrier(reg="B1", name="Bravo")
        pa = self._bind(90, a)
        vb = cp.add_vehicle(self.c, self._bind(91, b), "truck_6w", "B-PLATE")["vehicle_id"]
        with self.assertRaises(core.ForbiddenError):
            cp.set_vehicle_maintenance(self.c, pa, vb, on=True)


# --------------------------------------------------------------------------- #
class OperatorSupportView(Base):
    def test_operator_overview_by_carrier_id(self):
        cid = self._carrier()
        ov = cp.overview(self.c, self.op, requested=cid)   # operator support read
        self.assertEqual(ov["carrier_id"], cid)

    def test_operator_read_only_no_write_without_binding(self):
        cid = self._carrier()
        with self.assertRaises(core.ForbiddenError):
            cp.add_vehicle(self.c, self.op, "truck_6w", "X-1")   # write path needs a binding


# --------------------------------------------------------------------------- #
class Finance(Base):
    def test_finance_projection_shape(self):
        cid = self._carrier()
        f = cp.finance(self.c, self._bind(90, cid))
        self.assertIn("totals", f)
        self.assertEqual(set(f["totals"].keys()), {"earned", "released", "held"})
        self.assertIn("payout_accounts", f)


# --------------------------------------------------------------------------- #
class Integrity(Base):
    def test_integrity_clean(self):
        cid = self._carrier()
        self._bind(90, cid)
        self.assertTrue(cp.run_integrity(self.c, self.op)["ok"])


if __name__ == "__main__":
    unittest.main()
