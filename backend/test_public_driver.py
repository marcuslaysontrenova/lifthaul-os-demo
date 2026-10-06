import json
import os
import unittest

os.environ.setdefault("APP_ENV", "development")

import core
import db
import driver_app
import marketplace_onboarding as mo
import public_driver as pd


def payload(**over):
    data = {
        "full_name": "Juan Driver", "email": "juan.driver@example.test", "mobile": "09171234567",
        "username": "juan.driver@example.test", "password": "Str0ngPass!", "base_location": "Manila",
        "licence_number": "N01-23-456789", "licence_class": "C, CE", "licence_expiry": "2030-12-31",
        "authorized_categories": ["TRUCK", "TRAILER"], "years_experience": 7,
        "route_experience": "Luzon trunk routes", "safety_qualifications": "Defensive driving",
        "emergency_contact": "Ana Driver 09170000000", "employment_mode": "SEEKING_FLEET",
        "consent": True,
    }
    data.update(over)
    return data


class PublicDriverFlow(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect("sqlite:///:memory:")
        self.admin = {"id": 901, "role": "super_admin", "perms": {"*"}, "tenant_id": None}

    def test_application_is_separate_and_contact_verification_does_not_activate(self):
        r = pd.submit(self.conn, payload())
        self.assertEqual(r["status"], "VERIFY_CONTACT")
        self.assertTrue(r["ref"].startswith("DRV-"))
        self.assertIn("dev_code", r)
        self.assertIsNone(self.conn.execute("SELECT id FROM mkt_drivers").fetchone())
        user = self.conn.execute("SELECT role,status FROM users WHERE id=?", (r["application_id"],)).fetchone()
        # application id and user id happen to be 1 in a new DB; assert the actual row defensively
        user = self.conn.execute("SELECT role,status FROM users WHERE email=?", ("juan.driver@example.test",)).fetchone()
        self.assertEqual(user["role"], "driver_principal")
        self.assertEqual(user["status"], "PENDING_DRIVER_REVIEW")

        v = pd.verify(self.conn, {"challenge_id": r["challenge_id"], "code": r["dev_code"]})
        self.assertEqual(v["status"], "CONTACT_VERIFIED")
        self.assertFalse(v["login_active"])
        with self.assertRaises(core.AuthError):
            core.login(self.conn, "juan.driver@example.test", "Str0ngPass!")

    def test_sponsorship_creates_canonical_driver_and_binding_but_not_compliance_approval(self):
        r = pd.submit(self.conn, payload())
        pd.verify(self.conn, {"challenge_id": r["challenge_id"], "code": r["dev_code"]})
        carrier_id = mo.create_carrier_application(self.conn, self.admin, "CORPORATION", "Verified Fleet Candidate")
        result = pd.sponsor(self.conn, self.admin, r["application_id"], carrier_id)
        self.assertEqual(result["status"], "SPONSORED")
        self.assertEqual(result["compliance_status"], "APPLICATION")
        driver = self.conn.execute("SELECT * FROM mkt_drivers WHERE id=?", (result["driver_id"],)).fetchone()
        self.assertEqual(driver["carrier_id"], carrier_id)
        self.assertEqual(driver["status"], "APPLICATION")
        user = self.conn.execute("SELECT * FROM users WHERE email=?", ("juan.driver@example.test",)).fetchone()
        binding = self.conn.execute("SELECT * FROM driver_principals WHERE user_id=?", (user["id"],)).fetchone()
        self.assertEqual(binding["driver_id"], result["driver_id"])
        token = core.login(self.conn, "juan.driver@example.test", "Str0ngPass!")
        self.assertEqual(driver_app.resolve_driver(self.conn, core.actor_for(self.conn, token)), result["driver_id"])
        again = pd.sponsor(self.conn, self.admin, r["application_id"], carrier_id)
        self.assertEqual(again["driver_id"], result["driver_id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM mkt_drivers").fetchone()["c"], 1)

    def test_unverified_application_cannot_be_sponsored(self):
        r = pd.submit(self.conn, payload())
        carrier_id = mo.create_carrier_application(self.conn, self.admin, "CORPORATION", "Fleet")
        with self.assertRaises(core.ValidationError):
            pd.sponsor(self.conn, self.admin, r["application_id"], carrier_id)

    def test_validation_and_duplicate_controls(self):
        for update in ({"consent": False}, {"licence_number": ""}, {"password": "weak"},
                       {"years_experience": -1}, {"licence_expiry": "2020-01-01"},
                       {"licence_expiry": "not-a-date"}, {"partner_track": "UNSUPPORTED"}):
            with self.subTest(update=update), self.assertRaises(core.ValidationError):
                pd.submit(self.conn, payload(**update))
        pd.submit(self.conn, payload())
        with self.assertRaises(core.ConflictError):
            pd.submit(self.conn, payload(full_name="Duplicate"))
        with self.assertRaises(core.ConflictError):
            pd.submit(self.conn, payload(email="other@example.test", username="other@example.test",
                                         licence_number="n01-23-456789"))

    def test_heavy_equipment_operator_track_is_persisted_separately_from_provider_ownership(self):
        r = pd.submit(self.conn, payload(
            partner_track="HEAVY_EQUIPMENT_OPERATOR",
            authorized_categories=["HEAVY_EQUIPMENT"],
        ))
        app = self.conn.execute(
            "SELECT partner_track,authorized_categories FROM public_driver_applications WHERE id=?",
            (r["application_id"],),
        ).fetchone()
        self.assertEqual(app["partner_track"], "HEAVY_EQUIPMENT_OPERATOR")
        self.assertEqual(json.loads(app["authorized_categories"]), ["HEAVY_EQUIPMENT"])

    def test_canonical_vehicle_code_is_validated_and_persisted(self):
        r = pd.submit(self.conn, payload(
            partner_track="LIGHT_VEHICLE_DRIVER",
            vehicle_category_code="mini_van",
            authorized_categories=["VAN"],
        ))
        app = self.conn.execute(
            "SELECT partner_track,vehicle_category_code FROM public_driver_applications WHERE id=?",
            (r["application_id"],),
        ).fetchone()
        self.assertEqual(app["vehicle_category_code"], "mini_van")
        with self.assertRaises(core.ValidationError):
            pd.submit(self.conn, payload(
                email="wrong-track@example.test", username="wrong-track@example.test",
                licence_number="N01-23-456788", partner_track="MOTORCYCLE_RIDER",
                vehicle_category_code="mini_van",
            ))
        with self.assertRaises(core.ValidationError):
            pd.submit(self.conn, payload(
                email="unknown@example.test", username="unknown@example.test",
                licence_number="N01-23-456787", partner_track="LIGHT_VEHICLE_DRIVER",
                vehicle_category_code="not_a_vehicle",
            ))

    def test_audit_trail_and_ui_route(self):
        r = pd.submit(self.conn, payload())
        pd.verify(self.conn, {"challenge_id": r["challenge_id"], "code": r["dev_code"]})
        actions = {x["action"] for x in self.conn.execute("SELECT action FROM audit_logs").fetchall()}
        self.assertTrue({"PUBLIC_DRIVER_APPLIED", "DRIVER_SIGNUP_CODE_ISSUED", "DRIVER_CONTACT_VERIFIED"} <= actions)
        with open(os.path.join(os.path.dirname(__file__), "..", "index.html"), encoding="utf-8") as f:
            home = f.read()
        self.assertIn("driver-register.html", home)
        self.assertNotIn("provider.html?type=DRIVER", home)

    def test_application_queue_is_tenant_scoped(self):
        pd.submit(self.conn, payload())
        outsider = {"id": 902, "role": "operations_manager",
                    "perms": {"marketplace.driver.manage"}, "tenant_id": 999999}
        self.assertEqual(pd.list_applications(self.conn, outsider), [])


class DriverOtpProductionBoundary(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect("sqlite:///:memory:")
        self.previous = os.environ.get("APP_ENV")

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("APP_ENV", None)
        else:
            os.environ["APP_ENV"] = self.previous

    def test_production_does_not_expose_code(self):
        os.environ["APP_ENV"] = "production"
        r = pd.submit(self.conn, payload())
        self.assertNotIn("dev_code", r)
        self.assertFalse(r["delivered"])
        self.assertIn("VERIFICATION DELIVERY UNAVAILABLE", r["delivery_note"])


if __name__ == "__main__":
    unittest.main()
