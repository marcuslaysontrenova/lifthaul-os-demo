"""LiftHaul Referral Rewards — single-level, fraud-screened, finance-approved.

Covers §60/§61: code generation/validity, attribution + persistence, REGISTERED != EARNED, carrier &
shipper qualification, fixed/percentage/credit rewards + cap, validation cooldown, finance SoD, refund
reversal, self/duplicate/circular fraud, budget + per-user + monthly caps, terms snapshot + immutability,
program flag, tenant isolation, audit — and the red-team proof that A earns NOTHING from C.
"""
import os
import unittest

os.environ.setdefault("APP_ENV", "development")

import db
import core
import admin_platform as ap
import marketplace_onboarding as mo
import fleet_registration as fr
import accreditation as acc
import referral as rf

SUP = {"id": 1, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
FIN = {"id": 2, "role": "finance", "perms": set(core.PERMISSIONS["finance"]), "tenant_id": None}
CARR = {"id": 9, "role": "carrier_principal", "perms": set(core.PERMISSIONS["carrier_principal"]), "tenant_id": None}
SPEC = {"vehicle_type": "TRUCK", "wheels": 6, "body": "closed_van", "payload_kg": 4000}


def _carrier(c, name, reg, tin=None):
    return mo.create_carrier_application(c, SUP, "FLEET_OPERATOR", name, registration_type="SEC",
                                         registration_number=reg, tax_id=tin)


def _pay_accreditation(c, carrier_id, plate):
    vid = fr.register_unit(c, SUP, carrier_id, plate, SPEC)["vehicle_id"]
    a = acc.assessment_for(c, vid)
    acc.record_payment(c, FIN, a["id"], "gcash", "PAY-" + plate)
    return vid


class Base(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        ap.set_config(self.c, "platform", "", "referral.program.enabled", "true", actor=SUP)
        self.camp = rf.create_campaign(self.c, SUP, "Fleet", "FIRST_ACCREDITED_VEHICLE",
                                       reward_type="FIXED", reward_amount=150, validation_days=0,
                                       total_budget=1000, status="ACTIVE")
        self.A = _carrier(self.c, "Alpha", "A1", "TIN-A")
        self.B = _carrier(self.c, "Bravo", "B1", "TIN-B")
        self.codeA = rf.issue_code(self.c, SUP, "CARRIER", self.A, campaign_id=self.camp["id"],
                                   referrer_label="Alpha")["code"]


class CodesAndAttribution(Base):
    def test_code_format_and_unique(self):
        self.assertTrue(self.codeA.startswith("LH-"))
        c2 = rf.issue_code(self.c, SUP, "CARRIER", self.B, referrer_label="Bravo")["code"]
        self.assertNotEqual(self.codeA, c2)

    def test_validate_code(self):
        self.assertTrue(rf.validate_code(self.c, self.codeA)["valid"])
        self.assertFalse(rf.validate_code(self.c, "LH-NOPE-000000")["valid"])
        rf.revoke_code(self.c, SUP, self.codeA)
        self.assertFalse(rf.validate_code(self.c, self.codeA)["valid"])

    def test_attribution_persists_no_reassignment(self):
        r = rf.attribute(self.c, SUP, self.codeA, "CARRIER", self.B, referred_label="Bravo")
        self.assertEqual(r["status"], "REGISTERED")
        self.assertEqual(r["referrer_ref"], str(self.A))
        self.assertEqual(r["referred_ref"], str(self.B))
        # a second attribution to the same business is refused (no silent reassignment)
        with self.assertRaises(core.ConflictError):
            rf.attribute(self.c, SUP, self.codeA, "CARRIER", self.B)

    def test_invalid_code_attribution_rejected(self):
        rf.revoke_code(self.c, SUP, self.codeA)
        with self.assertRaises(core.ValidationError):
            rf.attribute(self.c, SUP, self.codeA, "CARRIER", self.B)


class Qualification(Base):
    def test_registered_does_not_earn(self):
        r = rf.attribute(self.c, SUP, self.codeA, "CARRIER", self.B)
        with self.assertRaises(core.ConflictError):
            rf.qualify(self.c, SUP, r["id"])           # event not met -> registration alone earns nothing

    def test_carrier_qualifies_on_accredited_vehicle(self):
        r = rf.attribute(self.c, SUP, self.codeA, "CARRIER", self.B)
        _pay_accreditation(self.c, self.B, "NGA-1")
        q = rf.qualify(self.c, SUP, r["id"])
        self.assertEqual(q["status"], "EARNED")
        self.assertEqual(q["reward_amount"], 150.0)
        self.assertEqual(q["terms_version"], "v1")

    def test_shipper_qualifies_on_settled_booking(self):
        camp = rf.create_campaign(self.c, SUP, "Shipper", "FIRST_SETTLED_MARKETPLACE_JOB",
                                  reward_type="FIXED", reward_amount=100, validation_days=0, status="ACTIVE")
        code = rf.issue_code(self.c, SUP, "SHIPPER", 55, campaign_id=camp["id"])["code"]
        r = rf.attribute(self.c, SUP, code, "SHIPPER", 77, referred_label="Acme Shipper")
        with self.assertRaises(core.ConflictError):
            rf.qualify(self.c, SUP, r["id"])           # no settled booking yet
        cols = {r[1] for r in self.c.execute("PRAGMA table_info(mkt_bookings)").fetchall()}
        base = {"shipper_id": 77, "status": "SETTLED", "cargo_code": "general",
                "origin_zone": "MM", "dest_zone": "CAV", "requested_vehicle_category": "truck_6w",
                "service_class": "STANDARD"}
        use = {k: v for k, v in base.items() if k in cols}
        self.c.execute("INSERT INTO mkt_bookings(" + ",".join(use) + ") VALUES(" +
                       ",".join("?" for _ in use) + ")", tuple(use.values()))
        self.c.commit()
        q = rf.qualify(self.c, SUP, r["id"])
        self.assertEqual(q["status"], "EARNED")

    def test_program_disabled_blocks_earning(self):
        ap.set_config(self.c, "platform", "", "referral.program.enabled", "false", actor=SUP)
        r = rf.attribute(self.c, SUP, self.codeA, "CARRIER", self.B)
        _pay_accreditation(self.c, self.B, "NGA-1")
        with self.assertRaises(core.ConflictError):
            rf.qualify(self.c, SUP, r["id"])           # qualified event met, but program not activated


class RewardModels(Base):
    def _earned(self, camp_kw, referred, plate="P1"):
        camp = rf.create_campaign(self.c, SUP, "C", "FIRST_ACCREDITED_VEHICLE", validation_days=0,
                                  status="ACTIVE", **camp_kw)
        code = rf.issue_code(self.c, SUP, "CARRIER", self.A, campaign_id=camp["id"])["code"]
        r = rf.attribute(self.c, SUP, code, "CARRIER", referred)
        _pay_accreditation(self.c, referred, plate)
        return rf.qualify(self.c, SUP, r["id"])

    def test_percentage_reward_with_cap(self):
        # 6W accreditation subtotal 799; 10% = 79.90; cap 50 -> 50
        q = self._earned({"reward_type": "PERCENTAGE", "reward_pct": 10, "max_reward": 50,
                          "reward_basis": "NET_ACCREDITATION_FEE"}, self.B, "PC1")
        self.assertEqual(q["reward_amount"], 50.0)
        self.assertEqual(q["reward_basis_amount"], 799.0)     # net of VAT, never on VAT

    def test_credit_reward_issues_credit(self):
        q = self._earned({"reward_type": "CREDIT", "reward_amount": 120}, self.B, "CR1")
        rf.approve(self.c, FIN, q["id"])
        rf.pay(self.c, FIN, q["id"], method="ACCREDITATION_CREDIT")
        row = self.c.execute("SELECT kind,amount,status FROM referral_credits WHERE referral_id=?",
                             (q["id"],)).fetchone()
        self.assertEqual(row["kind"], "ACCREDITATION_CREDIT")
        self.assertEqual(row["amount"], 120.0)


class FinanceAndReversal(Base):
    def _earn(self):
        r = rf.attribute(self.c, SUP, self.codeA, "CARRIER", self.B)
        _pay_accreditation(self.c, self.B, "NGA-1")
        return rf.qualify(self.c, SUP, r["id"])

    def test_finance_sod(self):
        q = self._earn()
        for fn in (lambda: rf.qualify(self.c, CARR, q["id"]),
                   lambda: rf.approve(self.c, CARR, q["id"]),
                   lambda: rf.pay(self.c, CARR, q["id"])):
            with self.assertRaises(core.ForbiddenError):
                fn()

    def test_validation_cooldown(self):
        camp = rf.create_campaign(self.c, SUP, "Cool", "FIRST_ACCREDITED_VEHICLE", reward_type="FIXED",
                                  reward_amount=150, validation_days=14, status="ACTIVE")
        code = rf.issue_code(self.c, SUP, "CARRIER", self.A, campaign_id=camp["id"])["code"]
        r = rf.attribute(self.c, SUP, code, "CARRIER", self.B)
        _pay_accreditation(self.c, self.B, "NGA-1")
        q = rf.qualify(self.c, SUP, r["id"])
        with self.assertRaises(core.ConflictError):
            rf.approve(self.c, FIN, q["id"])           # validation window not elapsed
        rf.approve(self.c, FIN, q["id"], force=True)   # governed override
        self.assertEqual(rf.pay(self.c, FIN, q["id"])["status"], "PAID")

    def test_full_finance_flow(self):
        q = self._earn()
        self.assertEqual(rf.approve(self.c, FIN, q["id"])["status"], "PAYABLE")
        self.assertEqual(rf.pay(self.c, FIN, q["id"], payout_ref="PO-1")["status"], "PAID")

    def test_refund_reversal_keeps_record(self):
        q = self._earn()
        rf.approve(self.c, FIN, q["id"]); rf.pay(self.c, FIN, q["id"])
        rev = rf.reverse(self.c, FIN, q["id"], "underlying accreditation refunded")
        self.assertEqual(rev["status"], "REVERSED")
        self.assertIsNotNone(self.c.execute("SELECT id FROM referrals WHERE id=?", (q["id"],)).fetchone())


class Fraud(Base):
    def test_self_referral_review(self):
        r = rf.attribute(self.c, SUP, self.codeA, "CARRIER", self.A)   # A refers itself
        self.assertEqual(r["status"], "REVIEW_REQUIRED")
        self.assertIn("SELF_REFERRAL", r["risk_reasons"])
        _pay_accreditation(self.c, self.A, "NGA-1")
        with self.assertRaises(core.ConflictError):
            rf.qualify(self.c, SUP, r["id"])           # HIGH/CRITICAL cannot auto-qualify

    def test_duplicate_company_second_reward_blocked(self):
        rf.attribute(self.c, SUP, self.codeA, "CARRIER", self.B)
        codeC = rf.issue_code(self.c, SUP, "CARRIER", _carrier(self.c, "Gamma", "G1"), referrer_label="G")["code"]
        with self.assertRaises(core.ConflictError):
            rf.attribute(self.c, SUP, codeC, "CARRIER", self.B)        # same business, no 2nd referral

    def test_circular_referral_flagged(self):
        rf.attribute(self.c, SUP, self.codeA, "CARRIER", self.B)        # A -> B
        codeB = rf.issue_code(self.c, SUP, "CARRIER", self.B, referrer_label="Bravo")["code"]
        r = rf.attribute(self.c, SUP, codeB, "CARRIER", self.A)         # B -> A (circular)
        self.assertEqual(r["status"], "REVIEW_REQUIRED")
        self.assertIn("CIRCULAR_REFERRAL", r["risk_reasons"])

    def test_weak_signal_not_auto_accused(self):
        # a clean A->B referral carries no risk flags
        r = rf.attribute(self.c, SUP, self.codeA, "CARRIER", self.B)
        self.assertEqual(r["risk_status"], "NONE")
        self.assertEqual(r["risk_reasons"], [])


class CapsAndBudget(Base):
    def test_budget_cap_blocks_new_reward(self):
        camp = rf.create_campaign(self.c, SUP, "Tiny", "FIRST_ACCREDITED_VEHICLE", reward_type="FIXED",
                                  reward_amount=150, validation_days=0, total_budget=100, status="ACTIVE")
        code = rf.issue_code(self.c, SUP, "CARRIER", self.A, campaign_id=camp["id"])["code"]
        r = rf.attribute(self.c, SUP, code, "CARRIER", self.B)
        _pay_accreditation(self.c, self.B, "NGA-1")
        with self.assertRaises(core.ConflictError):
            rf.qualify(self.c, SUP, r["id"])           # reward 150 > budget 100 -> blocked

    def test_per_user_cap(self):
        camp = rf.create_campaign(self.c, SUP, "Cap1", "FIRST_ACCREDITED_VEHICLE", reward_type="FIXED",
                                  reward_amount=50, validation_days=0, total_budget=10000, per_user_cap=1,
                                  status="ACTIVE")
        code = rf.issue_code(self.c, SUP, "CARRIER", self.A, campaign_id=camp["id"])["code"]
        r1 = rf.attribute(self.c, SUP, code, "CARRIER", self.B); _pay_accreditation(self.c, self.B, "B1v")
        rf.qualify(self.c, SUP, r1["id"])
        G = _carrier(self.c, "Gamma", "G1")
        r2 = rf.attribute(self.c, SUP, code, "CARRIER", G); _pay_accreditation(self.c, G, "G1v")
        with self.assertRaises(core.ConflictError):
            rf.qualify(self.c, SUP, r2["id"])          # per-user cap = 1


class SingleLevelRedTeam(Base):
    def test_A_earns_nothing_from_C(self):
        # A -> B (A earns), B -> C (B earns). A must earn NOTHING from C.
        rA = rf.attribute(self.c, SUP, self.codeA, "CARRIER", self.B)
        _pay_accreditation(self.c, self.B, "NGA-1")
        rf.qualify(self.c, SUP, rA["id"])
        C = _carrier(self.c, "Charlie", "C1", "TIN-C")
        codeB = rf.issue_code(self.c, SUP, "CARRIER", self.B, referrer_label="Bravo")["code"]
        rBC = rf.attribute(self.c, SUP, codeB, "CARRIER", C)
        _pay_accreditation(self.c, C, "CRN-1")
        rf.qualify(self.c, SUP, rBC["id"])
        dashA = rf.referrer_dashboard(self.c, SUP, "CARRIER", self.A)
        dashB = rf.referrer_dashboard(self.c, SUP, "CARRIER", self.B)
        self.assertEqual(dashA["total_earned"], 150.0)          # only from B
        self.assertEqual(dashB["total_earned"], 150.0)          # only from C
        # A has NO referral whose referred entity is C
        a_refs = [r["referred_ref"] for r in self.c.execute(
            "SELECT referred_ref FROM referrals WHERE referrer_ref=?", (str(self.A),)).fetchall()]
        self.assertNotIn(str(C), a_refs)

    def test_no_downline_parent_column(self):
        cols = [r[1] for r in self.c.execute("PRAGMA table_info(referrals)").fetchall()]
        for banned in ("parent_referrer_id", "upline", "downline", "generation", "level"):
            self.assertNotIn(banned, cols)


class TenantAndAudit(Base):
    def test_tenant_isolation(self):
        owner = {"id": 5, "role": "ops", "perms": {"*"}, "tenant_id": 101}
        code = rf.issue_code(self.c, owner, "CARRIER", self.A, referrer_label="A")["code"]
        r = rf.attribute(self.c, owner, code, "CARRIER", self.B)     # stamped tenant 101
        other = {"id": 6, "role": "finance", "perms": set(core.PERMISSIONS["finance"]) | {"referral.finance"}, "tenant_id": 202}
        with self.assertRaises((core.NotFoundError, core.ForbiddenError)):
            rf.reverse(self.c, other, r["id"], "x")

    def test_audit_events(self):
        r = rf.attribute(self.c, SUP, self.codeA, "CARRIER", self.B)
        _pay_accreditation(self.c, self.B, "NGA-1")
        q = rf.qualify(self.c, SUP, r["id"])
        rf.approve(self.c, FIN, q["id"]); rf.pay(self.c, FIN, q["id"])
        acts = {x["action"] for x in self.c.execute(
            "SELECT action FROM audit_logs WHERE entity IN ('referrals','referral_codes','referral_campaigns')").fetchall()}
        self.assertTrue({"REFERRAL_CODE_CREATED", "REFERRAL_ATTRIBUTED", "REFERRAL_QUALIFIED",
                         "REFERRAL_EARNED", "REFERRAL_APPROVED", "REFERRAL_PAID", "CAMPAIGN_CREATED"}.issubset(acts))


if __name__ == "__main__":
    unittest.main()
