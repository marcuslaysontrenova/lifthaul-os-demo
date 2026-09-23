"""P0 industrial-project controls: governed survey and atomic resource package."""
import datetime
import unittest

import core
import db
import industrial_projects as industrial
import public_booking as public


SUP = {"id": 900, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
ESTIMATOR = {"id": 901, "role": "estimator", "perms": core.PERMISSIONS["estimator"], "tenant_id": None}
SAFETY = {"id": 902, "role": "safety_officer", "perms": core.PERMISSIONS["safety_officer"], "tenant_id": None}
DISPATCHER = {"id": 903, "role": "dispatcher", "perms": core.PERMISSIONS["dispatcher"], "tenant_id": None}


def _payload(**overrides):
    data = {
        "contact_name": "Plant Engineer", "contact_email": "plant@example.test",
        "origin_island": "Luzon", "dest_island": "Luzon", "vehicle": "manual", "km": 25,
        "excluded_charges_ack": True, "booking_mode": "MANAGED_PROJECT",
        "service_line": "INDUSTRIAL", "cargo_category": "MACHINERY_EQUIPMENT",
        "cargo": "Production machine", "weight_kg": 5000, "package_count": 1,
        "package_length_cm": 300, "package_width_cm": 180, "package_height_cm": 200,
        "site_access_confirmed": True, "cargo_photo_count": 2,
        "requested_resources": ["TRANSPORT", "CRANE", "RIGGING_CREW", "SAFETY_OFFICER"],
    }
    data.update(overrides)
    return data


class IndustrialProjectTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def managed_booking(self, survey=False, **overrides):
        if survey:
            overrides.update({"technical_specs_unknown": True, "site_access_confirmed": False,
                              "weight_kg": None, "package_length_cm": None,
                              "package_width_cm": None, "package_height_cm": None})
        return public.submit(self.conn, _payload(**overrides))

    def specialized(self, code, resource_type, **kwargs):
        rid = industrial.register_specialized_resource(
            self.conn, SUP, code=code, name=code, resource_type=resource_type, **kwargs)
        industrial.verify_specialized_resource(self.conn, SUP, rid, "VERIFIED", "official record")
        return rid

    def vehicle(self, plate="TEST-001", capacity=8000):
        cur = self.conn.execute(
            "INSERT INTO mkt_vehicles(carrier_id,category_code,plate_number,payload_kg,status,verified_by,"
            "verified_at,created_by,created_at) VALUES(1,'truck_6w',?,?,'ACTIVE',1,?,1,?)",
            (plate, capacity, "2026-01-01", "2026-01-01"))
        vid = cur.lastrowid
        self.conn.execute(
            "INSERT INTO mkt_vehicle_legality(vehicle_id,or_number,cr_number,plate,capacity_kg,"
            "registration_expiry,insurance_expiry,inspection_valid_until,maintenance_status,"
            "verification_status,source,verified_by,verified_at,created_at,updated_at) "
            "VALUES(?,?,?,?,?,'2999-01-01','2999-01-01','2999-01-01','SAFE','VERIFIED','LTO evidence',1,?,?,?)",
            (vid, "OR-1", "CR-1", plate, capacity, "2026-01-01", "2026-01-01", "2026-01-01"))
        self.conn.commit()
        return vid


class GovernedSurvey(IndustrialProjectTestCase):
    def test_free_text_note_cannot_replace_structured_survey(self):
        booking = self.managed_booking(survey=True)
        with self.assertRaises(core.ConflictError):
            public.review(self.conn, SUP, booking["booking_id"], "COMPLETE_SURVEY",
                          note="looks fine")

    def test_survey_requires_measurements_findings_evidence_and_independent_approval(self):
        booking = self.managed_booking(survey=True)
        scheduled = industrial.schedule_survey(
            self.conn, ESTIMATOR, booking["booking_id"], "2026-10-01T09:00:00+08:00", ESTIMATOR["id"])
        with self.assertRaises(core.ValidationError):
            industrial.complete_survey(self.conn, ESTIMATOR, scheduled["survey_id"],
                                       measurements={}, findings="safe", evidence_refs=["evidence://survey/1"])
        industrial.complete_survey(
            self.conn, ESTIMATOR, scheduled["survey_id"],
            measurements={"cargo_length_m": 3.2, "door_clearance_m": 4.0, "floor_capacity_kpa": 90},
            findings="Access and slab are suitable subject to the approved lift plan.",
            evidence_refs=["evidence://survey/1", "evidence://survey/2"],
            access_conditions="6 m gate", ground_conditions="reinforced slab",
            clearances="4 m", required_resources=["CRANE", "RIGGING_CREW"])
        with self.assertRaises(core.ForbiddenError):
            industrial.approve_survey(self.conn, ESTIMATOR, scheduled["survey_id"], "APPROVED")
        approved = industrial.approve_survey(
            self.conn, SAFETY, scheduled["survey_id"], "APPROVED", "Evidence and capacity reviewed")
        self.assertEqual(approved["status"], "APPROVED")
        row = self.conn.execute("SELECT site_survey_required,project_stage,assessment_status FROM mkt_bookings WHERE id=?",
                                (booking["booking_id"],)).fetchone()
        self.assertEqual(row["site_survey_required"], 0)
        self.assertEqual(row["project_stage"], "ESTIMATION")
        self.assertEqual(row["assessment_status"], "SURVEY_APPROVED")

    def test_customer_projection_never_leaks_survey_findings_or_evidence_references(self):
        booking = self.managed_booking(survey=True)
        survey = industrial.schedule_survey(
            self.conn, ESTIMATOR, booking["booking_id"], "2026-10-01T09:00:00+08:00")
        industrial.complete_survey(
            self.conn, ESTIMATOR, survey["survey_id"], measurements={"length_m": 3},
            findings="PRIVATE FINDING", evidence_refs=["PRIVATE-EVIDENCE-REF"])
        tracked = public.track(self.conn, booking["tracking_token"])
        blob = str(tracked)
        self.assertEqual(tracked["industrial_control"]["survey"]["status"], "COMPLETED")
        self.assertNotIn("PRIVATE FINDING", blob)
        self.assertNotIn("PRIVATE-EVIDENCE-REF", blob)


class AtomicResourcePackage(IndustrialProjectTestCase):
    def approve_required_survey(self, booking):
        survey = industrial.schedule_survey(
            self.conn, ESTIMATOR, booking["booking_id"], "2026-10-01T09:00:00+08:00")
        industrial.complete_survey(
            self.conn, ESTIMATOR, survey["survey_id"],
            measurements={"cargo_length_m": 3, "clearance_m": 4},
            findings="Site suitable for the proposed controlled lift.",
            evidence_refs=["evidence://survey/resource-plan"])
        industrial.approve_survey(self.conn, SAFETY, survey["survey_id"], "APPROVED", "Reviewed")

    def resources(self):
        return {
            "vehicle": self.vehicle(),
            "crane": self.specialized("CR-50", "CRANE", capacity_kg=50000,
                                       certification_expiry="2999-01-01", inspection_valid_until="2999-01-01"),
            "crew": self.specialized("CREW-A", "RIGGING_CREW", certification_expiry="2999-01-01"),
            "safety": self.specialized("SO-A", "SAFETY_OFFICER", certification_expiry="2999-01-01"),
        }

    @staticmethod
    def items(resources):
        return [
            {"resource_type": "VEHICLE", "resource_id": resources["vehicle"],
             "requirement_code": "TRANSPORT"},
            {"resource_type": "CRANE", "resource_id": resources["crane"],
             "requirement_code": "CRANE", "required_capacity_kg": 5500},
            {"resource_type": "RIGGING_CREW", "resource_id": resources["crew"],
             "requirement_code": "RIGGING_CREW"},
            {"resource_type": "SAFETY_OFFICER", "resource_id": resources["safety"],
             "requirement_code": "SAFETY_OFFICER"},
        ]

    def test_complete_package_is_reserved_and_requires_independent_approval(self):
        booking = self.managed_booking()
        self.approve_required_survey(booking)
        resources = self.resources()
        plan = industrial.reserve_resource_package(
            self.conn, DISPATCHER, booking["booking_id"],
            start_at="2026-11-01T08:00:00+08:00", end_at="2026-11-01T18:00:00+08:00",
            items=self.items(resources))
        self.assertEqual(plan["status"], "RESERVED")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) c FROM mkt_project_resource_reservations WHERE plan_id=?",
            (plan["plan_id"],)).fetchone()["c"], 4)
        approved = industrial.approve_resource_plan(self.conn, SAFETY, plan["plan_id"], "Package verified")
        self.assertEqual(approved["status"], "APPROVED")
        stage = self.conn.execute("SELECT project_stage FROM mkt_bookings WHERE id=?",
                                  (booking["booking_id"],)).fetchone()["project_stage"]
        self.assertEqual(stage, "SAFETY_REVIEW")

    def test_failed_item_rolls_back_the_entire_package(self):
        booking = self.managed_booking()
        self.approve_required_survey(booking)
        resources = self.resources()
        self.conn.execute("UPDATE mkt_specialized_resources SET maintenance_status='GROUNDED' WHERE id=?",
                          (resources["crane"],))
        self.conn.commit()
        with self.assertRaises(core.ConflictError):
            industrial.reserve_resource_package(
                self.conn, DISPATCHER, booking["booking_id"],
                start_at="2026-11-01T08:00:00+08:00", end_at="2026-11-01T18:00:00+08:00",
                items=self.items(resources))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM mkt_project_resource_plans").fetchone()["c"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM mkt_project_resource_reservations").fetchone()["c"], 0)

    def test_overlapping_package_cannot_double_book_a_resource(self):
        first = self.managed_booking()
        second = self.managed_booking(contact_email="other@example.test")
        self.approve_required_survey(first)
        self.approve_required_survey(second)
        resources = self.resources()
        industrial.reserve_resource_package(
            self.conn, DISPATCHER, first["booking_id"],
            start_at="2026-11-01T08:00:00+08:00", end_at="2026-11-01T18:00:00+08:00",
            items=self.items(resources))
        with self.assertRaises(core.ConflictError):
            industrial.reserve_resource_package(
                self.conn, DISPATCHER, second["booking_id"],
                start_at="2026-11-01T12:00:00+08:00", end_at="2026-11-01T20:00:00+08:00",
                items=self.items(resources))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM mkt_project_resource_plans").fetchone()["c"], 1)

    def test_missing_requested_resource_is_rejected_before_any_reservation(self):
        booking = self.managed_booking()
        self.approve_required_survey(booking)
        resources = self.resources()
        incomplete = self.items(resources)[:-1]
        with self.assertRaises(core.ValidationError):
            industrial.reserve_resource_package(
                self.conn, DISPATCHER, booking["booking_id"],
                start_at="2026-11-01T08:00:00+08:00", end_at="2026-11-01T18:00:00+08:00",
                items=incomplete)


if __name__ == "__main__":
    unittest.main()
