"""Samantha — AI Business Development Manager (native LiftHaul module).

Verifies the governed BD pipeline across BOTH marketplace sides: deterministic qualification, tailored
outreach, human-approved sends with separation of duties, honest (fail-closed) delivery, tenant
isolation, RBAC and audit.
"""
import os
import unittest

os.environ.setdefault("APP_ENV", "development")

import db
import core
import samantha_bd as sb


def _actor(conn, email, role="bd_manager"):
    uid = core.create_user(conn, email, "Str0ngPass!", role, email.split("@")[0])
    return core.actor_for(conn, core.login(conn, email, "Str0ngPass!"))


DEMAND = {"side": "DEMAND", "company": "Apex Mining Corp", "contact_name": "Maria Cruz",
          "contact_title": "VP Operations", "sector": "mining", "region": "Mindanao",
          "profile": "oversized plant equipment, Q4 expansion"}
SUPPLY = {"side": "SUPPLY", "company": "FastHaul Fleet", "contact_title": "Owner",
          "sector": "trucking", "region": "Luzon", "profile": "25 units, prime mover, CPC"}


class SamanthaPipeline(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect("sqlite:///:memory:")
        self.drafter = _actor(self.conn, "sam.drafter@lh.test")
        self.approver = _actor(self.conn, "sam.approver@lh.test")

    # --- both sides ---
    def test_add_both_sides(self):
        d = sb.add_prospect(self.conn, self.drafter, DEMAND)
        s = sb.add_prospect(self.conn, self.drafter, SUPPLY)
        self.assertEqual(d["side"], "DEMAND")
        self.assertEqual(s["side"], "SUPPLY")
        sides = {p["side"] for p in sb.list_prospects(self.conn, self.drafter)}
        self.assertEqual(sides, {"DEMAND", "SUPPLY"})

    def test_side_validation(self):
        with self.assertRaises(core.ValidationError):
            sb.add_prospect(self.conn, self.drafter, {"side": "BOGUS", "company": "X"})
        with self.assertRaises(core.ValidationError):
            sb.add_prospect(self.conn, self.drafter, {"side": "DEMAND", "company": ""})

    # --- qualification is deterministic + explainable ---
    def test_qualify_scores_and_reasons(self):
        d = sb.add_prospect(self.conn, self.drafter, DEMAND)
        q = sb.qualify(self.conn, self.drafter, d["id"])
        self.assertEqual(q["status"], "QUALIFIED")
        self.assertGreaterEqual(q["score"], 70)          # VP + mining + region + signal + urgency -> HOT
        self.assertEqual(q["tier"], "HOT")
        self.assertTrue(q["reasons"])                    # coded, human-readable reasons
        # a weak prospect scores lower
        w = sb.add_prospect(self.conn, self.drafter, {"side": "DEMAND", "company": "Tiny Retail",
                                                      "contact_title": "Coordinator", "sector": "retail"})
        qw = sb.qualify(self.conn, self.drafter, w["id"])
        self.assertLess(qw["score"], q["score"])

    def test_qualify_is_repeatable(self):
        d = sb.add_prospect(self.conn, self.drafter, DEMAND)
        s1 = sb.qualify(self.conn, self.drafter, d["id"])["score"]
        s2 = sb.qualify(self.conn, self.drafter, d["id"])["score"]
        self.assertEqual(s1, s2)                          # no LLM, deterministic

    # --- outreach draft is tailored + inert ---
    def test_draft_is_pending_and_tailored(self):
        d = sb.add_prospect(self.conn, self.drafter, DEMAND)
        o = sb.draft_outreach(self.conn, self.drafter, d["id"])
        self.assertEqual(o["status"], "PENDING_APPROVAL")
        self.assertIn("Apex Mining Corp", o["subject"])
        self.assertIn("Maria", o["body"])                 # personalized to the contact
        # supply draft uses the supply angle
        s = sb.add_prospect(self.conn, self.drafter, SUPPLY)
        os_ = sb.draft_outreach(self.conn, self.drafter, s["id"])
        self.assertIn("fleet", os_["body"].lower())

    # --- human approval with separation of duties ---
    def test_drafter_cannot_approve_own(self):
        d = sb.add_prospect(self.conn, self.drafter, DEMAND)
        o = sb.draft_outreach(self.conn, self.drafter, d["id"])
        with self.assertRaises(core.ForbiddenError):
            sb.approve_outreach(self.conn, self.drafter, o["id"])   # SoD
        ap = sb.approve_outreach(self.conn, self.approver, o["id"])
        self.assertEqual(ap["status"], "APPROVED")

    def test_cannot_send_unapproved(self):
        d = sb.add_prospect(self.conn, self.drafter, DEMAND)
        o = sb.draft_outreach(self.conn, self.drafter, d["id"])
        with self.assertRaises(core.ConflictError):
            sb.send_outreach(self.conn, self.approver, o["id"])     # not APPROVED yet

    # --- honest send: fails closed with no provider ---
    def test_send_fails_closed_without_provider(self):
        d = sb.add_prospect(self.conn, self.drafter, DEMAND)
        o = sb.draft_outreach(self.conn, self.drafter, d["id"])
        sb.approve_outreach(self.conn, self.approver, o["id"])
        r = sb.send_outreach(self.conn, self.approver, o["id"])
        self.assertFalse(r["delivered"])
        self.assertEqual(r["status"], "APPROVED")         # NOT marked SENT
        self.assertIn("provider", r["note"].lower())
        self.assertIsNotNone(r["ready_to_send_copy"])     # the human-send copy is surfaced
        # prospect stays QUALIFIED (not CONTACTED) since nothing was delivered
        row = self.conn.execute("SELECT status FROM bd_prospects WHERE id=?", (d["id"],)).fetchone()
        self.assertIn(row["status"], ("NEW", "QUALIFIED"))

    # --- RBAC: a non-BD role is refused ---
    def test_rbac_enforced(self):
        outsider = _actor(self.conn, "driver@lh.test", role="driver_principal")
        with self.assertRaises(core.ForbiddenError):
            sb.add_prospect(self.conn, outsider, DEMAND)
        with self.assertRaises(core.ForbiddenError):
            sb.list_prospects(self.conn, outsider)

    # --- audit trail ---
    def test_audit_trail(self):
        d = sb.add_prospect(self.conn, self.drafter, DEMAND)
        sb.qualify(self.conn, self.drafter, d["id"])
        o = sb.draft_outreach(self.conn, self.drafter, d["id"])
        sb.approve_outreach(self.conn, self.approver, o["id"])
        sb.send_outreach(self.conn, self.approver, o["id"])
        acts = {r["action"] for r in self.conn.execute(
            "SELECT action FROM audit_logs WHERE entity IN ('bd_prospects','bd_outreach')").fetchall()}
        self.assertTrue({"BD_PROSPECT_ADDED", "BD_PROSPECT_QUALIFIED", "BD_OUTREACH_DRAFTED",
                         "BD_OUTREACH_APPROVED", "BD_OUTREACH_SEND_UNAVAILABLE"}.issubset(acts))

    # --- playbooks + summary read models ---
    def test_playbooks_cover_both_sides(self):
        pb = sb.playbooks()
        self.assertEqual(set(pb["sides"]), {"DEMAND", "SUPPLY"})
        self.assertTrue(pb["playbooks"]["DEMAND"] and pb["playbooks"]["SUPPLY"])

    def test_pipeline_summary_shape(self):
        sb.qualify(self.conn, self.drafter, sb.add_prospect(self.conn, self.drafter, DEMAND)["id"])
        sb.add_prospect(self.conn, self.drafter, SUPPLY)
        s = sb.pipeline_summary(self.conn, self.drafter)
        self.assertEqual(s["total_prospects"], 2)
        self.assertIn("DEMAND", s["by_side"])
        self.assertIn("SUPPLY", s["by_side"])


class SamanthaTenantIsolation(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect("sqlite:///:memory:")

    def _tenant_actor(self, email, tenant_id):
        core.create_user(self.conn, email, "Str0ngPass!", "bd_manager", email.split("@")[0])
        actor = core.actor_for(self.conn, core.login(self.conn, email, "Str0ngPass!"))
        actor["tenant_id"] = tenant_id            # explicit tenant for the isolation check
        return actor

    def test_tenant_scoped_lists(self):
        a = self._tenant_actor("bdA@lh.test", 101)
        b = self._tenant_actor("bdB@lh.test", 202)
        self.assertEqual(a["tenant_id"], 101)
        self.assertEqual(b["tenant_id"], 202)
        sb.add_prospect(self.conn, a, DEMAND)                # stamped to tenant 101
        self.assertEqual(len(sb.list_prospects(self.conn, a)), 1)   # owner sees it
        self.assertEqual(sb.list_prospects(self.conn, b), [])       # other tenant does NOT


if __name__ == "__main__":
    unittest.main()
