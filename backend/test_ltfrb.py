"""LTFRB carrier transport-authority compliance (regulatory closure C/D).

Asserts: authorities record as SUBMITTED (not verified); the adapter never fabricates a verification
(returns MANUAL_VERIFICATION_REQUIRED); human verification requires a recorded source; the carrier
gate hard-blocks without a VERIFIED unexpired CPC; the assignment gate hard-blocks unauthorized units
and out-of-area work; expiry sweeps VERIFIED→EXPIRED; the regulatory summary surfaces exceptions and
keeps BSP status at application-preparation with live protected funds OFF.
"""
import unittest

import core
import db
import ltfrb


def _staff(c, e):
    core.create_user(c, e, "pw", "admin", "S")
    return core.actor_for(c, core.login(c, e, "pw"))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        self.sup = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
        self.ops = _staff(self.c, "ltfrb-ops@e2e")
        self.cid = 7

    def _record(self, **kw):
        d = dict(cpc_number="CPC-2026-001", case_reference="2026-CFC-0001",
                 area_of_operation=["LUZON"], authorized_units=["ABC1234", "XYZ5678"],
                 expiry_date="2027-01-01")
        d.update(kw)
        return ltfrb.record_authority(self.c, self.ops, self.cid, **d)


class RecordAndVerify(Fixture):
    def test_records_as_submitted_not_verified(self):
        aid = self._record()
        a = self.c.execute("SELECT status FROM mkt_ltfrb_authority WHERE id=?", (aid,)).fetchone()
        self.assertEqual(a["status"], "SUBMITTED")

    def test_adapter_never_fabricates_verification(self):
        aid = self._record()
        res = ltfrb.check_authority(self.c, self.ops, aid)
        self.assertEqual(res["adapter_result"]["status"], "MANUAL_VERIFICATION_REQUIRED")
        a = self.c.execute("SELECT status FROM mkt_ltfrb_authority WHERE id=?", (aid,)).fetchone()
        self.assertEqual(a["status"], "MANUAL_VERIFICATION_REQUIRED")  # never auto-VERIFIED

    def test_human_verify_requires_recorded_source(self):
        aid = self._record()
        with self.assertRaises(core.ValidationError):
            ltfrb.verify_authority(self.c, self.ops, aid, "VERIFIED", source=None)

    def test_human_verify_with_source_marks_verified(self):
        aid = self._record()
        ltfrb.verify_authority(self.c, self.ops, aid, "VERIFIED",
                               source="LTFRB CPC decision, verified at RO office", evidence={"doc": "cpc.pdf"})
        a = self.c.execute("SELECT status,verification_source,verified_by FROM mkt_ltfrb_authority WHERE id=?", (aid,)).fetchone()
        self.assertEqual(a["status"], "VERIFIED")
        self.assertTrue(a["verification_source"])
        self.assertEqual(a["verified_by"], self.ops["id"])


class CarrierGate(Fixture):
    def test_no_authority_blocks(self):
        g = ltfrb.carrier_authority_gate(self.c, 999)
        self.assertFalse(g["ok"])
        self.assertIn("no_ltfrb_authority_on_file", g["reasons"])

    def test_unverified_blocks(self):
        self._record()
        g = ltfrb.carrier_authority_gate(self.c, self.cid)
        self.assertFalse(g["ok"])
        self.assertTrue(any("cpc_not_verified" in r for r in g["reasons"]))

    def test_verified_passes(self):
        aid = self._record()
        ltfrb.verify_authority(self.c, self.ops, aid, "VERIFIED", source="LTFRB")
        g = ltfrb.carrier_authority_gate(self.c, self.cid)
        self.assertTrue(g["ok"])

    def test_expired_cpc_blocks(self):
        aid = self._record(expiry_date="2020-01-01")
        ltfrb.verify_authority(self.c, self.ops, aid, "VERIFIED", source="LTFRB")
        g = ltfrb.carrier_authority_gate(self.c, self.cid)
        self.assertFalse(g["ok"])
        self.assertIn("cpc_expired", g["reasons"])


class AssignmentGate(Fixture):
    def _verified(self, **kw):
        aid = self._record(**kw)
        ltfrb.verify_authority(self.c, self.ops, aid, "VERIFIED", source="LTFRB")
        return aid

    def test_authorized_unit_in_area_passes(self):
        self._verified()
        g = ltfrb.assignment_authority_gate(self.c, self.cid, vehicle_plate="ABC1234", area="LUZON")
        self.assertTrue(g["ok"], g)

    def test_unauthorized_unit_blocks(self):
        self._verified()
        g = ltfrb.assignment_authority_gate(self.c, self.cid, vehicle_plate="NOTMINE9", area="LUZON")
        self.assertFalse(g["ok"])
        self.assertIn("vehicle_not_authorized_unit", g["reasons"])

    def test_out_of_area_blocks(self):
        self._verified()
        g = ltfrb.assignment_authority_gate(self.c, self.cid, vehicle_plate="ABC1234", area="MINDANAO")
        self.assertFalse(g["ok"])
        self.assertIn("area_outside_authority", g["reasons"])

    def test_unverified_carrier_blocks_assignment(self):
        self._record()  # submitted, not verified
        g = ltfrb.assignment_authority_gate(self.c, self.cid, vehicle_plate="ABC1234", area="LUZON")
        self.assertFalse(g["ok"])


class ExpiryAndDashboard(Fixture):
    def test_expire_due_sweeps_verified(self):
        aid = self._record(expiry_date="2020-06-01")
        ltfrb.verify_authority(self.c, self.ops, aid, "VERIFIED", source="LTFRB")
        r = ltfrb.expire_due(self.c)
        self.assertEqual(r["expired"], 1)
        a = self.c.execute("SELECT status FROM mkt_ltfrb_authority WHERE id=?", (aid,)).fetchone()
        self.assertEqual(a["status"], "EXPIRED")

    def test_regulatory_summary_keeps_bsp_prep_and_funds_off(self):
        self._record()  # a pending-verification exception
        s = ltfrb.regulatory_summary(self.c, self.sup)
        self.assertEqual(s["bsp"]["status"], "REGULATORY CLASSIFICATION / APPLICATION PREPARATION")
        self.assertFalse(s["bsp"]["registered"])
        self.assertFalse(s["live_protected_funds_enabled"])
        self.assertGreaterEqual(s["ltfrb"]["pending_manual_verification"], 1)

    def test_pending_verification_lists_submitted(self):
        self._record()
        p = ltfrb.pending_verification(self.c, self.sup)
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0]["status"], "SUBMITTED")


if __name__ == "__main__":
    unittest.main()
