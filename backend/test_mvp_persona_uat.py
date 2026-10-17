"""Controlled MVP persona UAT matrix.

This suite exercises complete synthetic journeys against an isolated in-memory
database.  It deliberately does not call production, dispatch a provider, send a
message, or move money.  Passing this suite is the precondition for (not a
substitute for) witnessed production UAT with dedicated persona accounts.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("APP_ENV", "test")

import admin_platform as ap
import carrier_portal
import client_portal
import core
import db
import fleet_registration
import marketplace_onboarding
import protected_payment
import public_booking
import public_driver
import public_provider


PASSWORD = "Synthetic-UAT-2026!"


class MvpPersonaUat(unittest.TestCase):
    """One independently reportable acceptance test for every required persona."""

    def setUp(self):
        self.conn = db.connect(":memory:")
        self.tenant_id = ap.get_tenant(self.conn, "RGO")["id"]
        self.supervisor = {
            "id": 9000,
            "role": "super_admin",
            "perms": {"*"},
            "tenant_id": self.tenant_id,
        }

    def tearDown(self):
        self.conn.close()

    def authenticated_actor(self, role: str, name: str):
        email = f"{name}@persona.invalid"
        uid = ap.create_user(
            self.conn, self.supervisor, email, PASSWORD, role,
            f"Synthetic {role} UAT",
        )
        token = core.login(self.conn, email, PASSWORD)
        actor = core.actor_for(self.conn, token)
        ap.apply_rbac(self.conn, actor)
        self.assertEqual(actor["id"], uid)
        self.assertEqual(actor["tenant_id"], self.tenant_id)
        return actor

    def provider_payload(self, suffix: str, **overrides):
        payload = {
            "provider_type": "OWNER_OPERATOR",
            "legal_name": f"Synthetic Provider {suffix}",
            "trade_name": f"Synthetic {suffix}",
            "email": f"provider-{suffix}@persona.invalid",
            "mobile": "09170000001",
            "username": f"provider-{suffix}@persona.invalid",
            "password": PASSWORD,
            "island_group": "LUZON",
        }
        payload.update(overrides)
        return payload

    def verified_provider(self, suffix: str, **overrides):
        application = public_provider.submit(
            self.conn, self.provider_payload(suffix, **overrides)
        )
        verified = public_provider.verify(
            self.conn,
            {
                "challenge_id": application["challenge_id"],
                "code": application["dev_code"],
            },
        )
        actor = core.actor_for(self.conn, verified["token"])
        ap.apply_rbac(self.conn, actor)
        return application, actor

    def driver_payload(self, suffix: str, **overrides):
        payload = {
            "full_name": f"Synthetic Driver {suffix}",
            "email": f"driver-{suffix}@persona.invalid",
            "mobile": "09170000002",
            "username": f"driver-{suffix}@persona.invalid",
            "password": PASSWORD,
            "base_location": "Manila",
            "licence_number": f"N01-26-{suffix.zfill(6)[-6:]}",
            "licence_class": "C, CE",
            "licence_expiry": "2030-12-31",
            "authorized_categories": ["TRUCK", "TRAILER"],
            "years_experience": 5,
            "route_experience": "Luzon synthetic UAT routes",
            "safety_qualifications": "Synthetic defensive-driving record",
            "emergency_contact": "Synthetic Contact 09170000009",
            "employment_mode": "SEEKING_FLEET",
            "consent": True,
        }
        payload.update(overrides)
        return payload

    def test_01_booker_authenticated_workspace_is_owned_and_trackable(self):
        booker = self.authenticated_actor("customer", "booker")
        other = self.authenticated_actor("customer", "other-booker")
        cur = self.conn.execute(
            "INSERT INTO mkt_shippers(applicant_type,legal_name,status,created_by,created_at) "
            "VALUES('INDIVIDUAL','Synthetic Booker','ACTIVE',?,?)",
            (booker["id"], core.now()),
        )
        shipper_id = cur.lastrowid
        other_shipper = self.conn.execute(
            "INSERT INTO mkt_shippers(applicant_type,legal_name,status,created_by,created_at) "
            "VALUES('INDIVIDUAL','Other Booker','ACTIVE',?,?)",
            (other["id"], core.now()),
        ).lastrowid
        client_portal.bind_principal(self.conn, self.supervisor, booker["id"], shipper_id)
        client_portal.bind_principal(self.conn, self.supervisor, other["id"], other_shipper)
        booking = self.conn.execute(
            "INSERT INTO mkt_bookings(shipper_id,cargo_code,cargo_description,origin_zone,dest_zone,"
            "status,created_by,created_at) VALUES(?,'GENERAL','Synthetic parcels','NCR','CAVITE',"
            "'DRAFT',?,?)",
            (shipper_id, booker["id"], core.now()),
        ).lastrowid
        self.conn.commit()
        mine = client_portal.bookings(self.conn, booker)["bookings"]
        self.assertEqual([item["id"] for item in mine], [booking])
        self.assertEqual(client_portal.bookings(self.conn, other)["bookings"], [])
        self.assertEqual(client_portal.booking_detail(self.conn, booker, booking)["cargo_description"],
                         "Synthetic parcels")

    def test_02_driver_applicant_verifies_contact_but_cannot_self_activate(self):
        payload = self.driver_payload("100001")
        application = public_driver.submit(self.conn, payload)
        verified = public_driver.verify(
            self.conn,
            {"challenge_id": application["challenge_id"], "code": application["dev_code"]},
        )
        self.assertEqual(verified["status"], "CONTACT_VERIFIED")
        self.assertFalse(verified["login_active"])
        with self.assertRaises(core.AuthError):
            core.login(self.conn, payload["email"], PASSWORD)

    def test_03_accredited_driver_is_sponsored_then_independently_activated(self):
        payload = self.driver_payload("100002")
        application = public_driver.submit(self.conn, payload)
        public_driver.verify(
            self.conn,
            {"challenge_id": application["challenge_id"], "code": application["dev_code"]},
        )
        carrier_id = marketplace_onboarding.create_carrier_application(
            self.conn, self.supervisor, "FLEET_OPERATOR", "Synthetic Accredited Fleet",
            registration_type="SEC", registration_number="SYN-DRV-1", operating_address="Manila",
        )
        # Public applications are platform-owned until sponsorship, so the governed
        # compliance actors intentionally use global scope here.
        global_supervisor = dict(self.supervisor, tenant_id=None)
        sponsored = public_driver.sponsor(
            self.conn, global_supervisor, application["application_id"], carrier_id
        )
        verifier = dict(global_supervisor, id=9001)
        activator = dict(global_supervisor, id=9002)
        marketplace_onboarding.verify_driver(self.conn, verifier, sponsored["driver_id"])
        marketplace_onboarding.activate_driver(self.conn, activator, sponsored["driver_id"])
        token = core.login(self.conn, payload["email"], PASSWORD)
        actor = core.actor_for(self.conn, token)
        row = self.conn.execute(
            "SELECT status FROM mkt_drivers WHERE id=?", (sponsored["driver_id"],)
        ).fetchone()
        self.assertEqual(row["status"], "ACTIVE")
        self.assertEqual(actor["role"], "driver_principal")
        self.assertFalse(core.can(actor, "marketplace.driver.verify"))

    def test_04_motorcycle_owner_registers_canonical_unit_without_self_approval(self):
        application, owner = self.verified_provider(
            "motorcycle",
            capabilities=["vehicle:motorcycle", "cargo_delivery"],
            initial_vehicle_category="MOTORCYCLE",
        )
        unit = carrier_portal.add_vehicle(self.conn, owner, "motorcycle", "SYN-MOTO-01")
        self.assertEqual(unit["status"], "DRAFT")
        self.assertEqual(
            self.conn.execute(
                "SELECT category_code FROM mkt_vehicles WHERE id=?", (unit["vehicle_id"],)
            ).fetchone()["category_code"],
            "motorcycle",
        )
        self.assertEqual(carrier_portal.resolve_carrier(self.conn, owner), application["carrier_id"])
        with self.assertRaises(core.ForbiddenError):
            marketplace_onboarding.verify_vehicle(self.conn, owner, unit["vehicle_id"])

    def test_05_light_vehicle_owner_registers_mini_van_as_distinct_category(self):
        _, owner = self.verified_provider(
            "minivan",
            capabilities=["vehicle:mini_van", "cargo_delivery"],
            initial_vehicle_category="MINI_VAN",
        )
        unit = carrier_portal.add_vehicle(self.conn, owner, "mini_van", "SYN-MINI-01")
        row = self.conn.execute(
            "SELECT category_code,status FROM mkt_vehicles WHERE id=?", (unit["vehicle_id"],)
        ).fetchone()
        self.assertEqual((row["category_code"], row["status"]), ("mini_van", "DRAFT"))

    def test_06_truck_owner_registers_specification_classified_unit(self):
        _, owner = self.verified_provider("truck")
        unit = carrier_portal.register_unit(
            self.conn,
            owner,
            "SYN-TRK-01",
            {"vehicle_type": "TRUCK", "wheels": 6, "body": "closed_van", "payload_kg": 4000},
        )
        self.assertEqual(unit["status"], "DRAFT")
        spec = carrier_portal.classify_unit(
            self.conn,
            owner,
            {"vehicle_type": "TRUCK", "wheels": 6, "body": "closed_van", "payload_kg": 4000},
        )
        self.assertEqual(spec["category_code"], "truck_6w")

    def test_07_fleet_owner_adds_multiple_units_and_drivers_with_owned_scope(self):
        _, fleet_owner = self.verified_provider(
            "fleet", provider_type="FLEET_OPERATOR", capabilities=["fleet_operations"]
        )
        for plate, category in (("SYN-FLT-01", "mini_van"), ("SYN-FLT-02", "truck_6w")):
            carrier_portal.add_vehicle(self.conn, fleet_owner, category, plate)
        carrier_portal.add_driver(
            self.conn, fleet_owner, "Synthetic Fleet Driver A",
            licence_expiry="2030-12-31", authorized_categories=["mini_van"],
        )
        carrier_portal.add_driver(
            self.conn, fleet_owner, "Synthetic Fleet Driver B",
            licence_expiry="2030-12-31", authorized_categories=["truck_6w"],
        )
        overview = carrier_portal.overview(self.conn, fleet_owner)
        self.assertEqual(overview["fleet"]["total"], 2)
        self.assertEqual(overview["drivers"]["total"], 2)
        self.assertEqual(overview["fleet"]["eligible"], 0)

    def test_08_specialized_provider_captures_crane_capability_and_engineering_specs(self):
        application, provider = self.verified_provider(
            "crane",
            provider_type="CRANE_COMPANY",
            capabilities=["heavy_haulage", "crane_heavy_lift", "rigging"],
        )
        carrier_portal.set_capability(self.conn, provider, "crane_heavy_lift")
        unit = carrier_portal.register_unit(
            self.conn,
            provider,
            "SYN-CRANE-01",
            {
                "vehicle_type": "CRANE", "lifting": True, "subtype": "mobile",
                "lifting_capacity_kg": 25000, "boom_length_m": 40,
            },
        )
        spec = fleet_registration.unit_spec(
            self.conn, self.supervisor, unit["vehicle_id"]
        )["spec"]
        self.assertEqual(spec["provider_specs"]["lifting_capacity_kg"], 25000)
        self.assertEqual(spec["provider_specs"]["boom_length_m"], 40)
        saved = self.conn.execute(
            "SELECT cargo_capabilities FROM mkt_carriers WHERE id=?", (application["carrier_id"],)
        ).fetchone()
        self.assertIn("crane_heavy_lift", saved["cargo_capabilities"])

    def test_09_operations_reviews_public_intake_but_finance_cannot(self):
        operations = self.authenticated_actor("public_intake_operator", "operations")
        finance = self.authenticated_actor("finance", "finance-no-review")
        booking = public_booking.submit(
            self.conn,
            {
                "contact_name": "Synthetic Operations UAT",
                "contact_phone": "09170000003",
                "origin_island": "Luzon", "dest_island": "Luzon",
                "vehicle": "6w", "km": 20, "excluded_charges_ack": True,
            },
        )
        queue = public_booking.admin_queue(self.conn, operations)
        self.assertIn(booking["booking_id"], [item["id"] for item in queue["requests"]])
        self.assertEqual(
            public_booking.review(self.conn, operations, booking["booking_id"], "REVIEW")["status"],
            "REVIEWED",
        )
        with self.assertRaises(core.ForbiddenError):
            public_booking.review(self.conn, finance, booking["booking_id"], "APPROVE")

    def test_10_finance_reads_protected_payment_queue_while_live_funds_remain_off(self):
        finance = self.authenticated_actor("finance", "finance")
        tx_id = protected_payment.create_transaction(
            self.conn,
            self.supervisor,
            booking_id=1001,
            carrier_id=2001,
            contract_amount=10000,
            protected_amount=10000,
            platform_fee=1000,
            provider_fee=150,
            tax=0,
            provider_name="MOCK",
        )
        queue = protected_payment.finance_queues(self.conn, finance)
        self.assertIn(tx_id, [item["id"] for item in queue["transactions"]])
        self.assertTrue(core.can(finance, "marketplace.payment.view"))
        self.assertFalse(core.can(finance, "marketplace.payment.manage"))
        self.assertFalse(core.can(finance, "marketplace.release.approve"))
        self.assertFalse(core.can(finance, "marketplace.payout.approve"))
        self.assertFalse(queue["live_funds_enabled"])
        self.assertEqual(queue["legal_escrow"], "NOT_YET_AUTHORIZED")
        with self.assertRaises(core.ForbiddenError):
            protected_payment.assert_live_allowed(self.conn, moving_real_funds=True)

    def test_11_administrator_creates_scoped_user_and_revokes_session_on_suspend(self):
        administrator = self.authenticated_actor("super_admin", "administrator")
        target_id = ap.create_user(
            self.conn, administrator, "synthetic-ops@persona.invalid", PASSWORD,
            "operations_manager", "Synthetic Operations User",
        )
        session = core.login(self.conn, "synthetic-ops@persona.invalid", PASSWORD)
        target = core.actor_for(self.conn, session)
        self.assertEqual(target["tenant_id"], self.tenant_id)
        ap.suspend_user(self.conn, administrator, target_id)
        with self.assertRaises(core.AuthError):
            core.actor_for(self.conn, session)
        ap.activate_user(self.conn, administrator, target_id)
        self.assertTrue(core.login(self.conn, "synthetic-ops@persona.invalid", PASSWORD))


if __name__ == "__main__":
    unittest.main(verbosity=2)
