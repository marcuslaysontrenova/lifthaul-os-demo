"""Prove observation and actual enforcement agree without provider calls or writes."""
import json
import os
import unittest
from unittest.mock import patch, Mock

import core
import operational_safety as safety
import payment_gateway
import platform_fee_settlement
import marketplace_matching
import marketplace_trips
import marketplace_payments
import ops

IDENTITY = {"release_sha": "a" * 40, "deployment_id": "deployment-test"}
ADMIN = {"id": 1, "role": "super_admin", "perms": {"*"}}


class OperationalSafetyTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"APP_ENV": "production", "LH_LIVE_OPERATIONS": "off"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_all_eight_off_at_verified_release(self):
        result = safety.status(ADMIN, IDENTITY)
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(len(result["capabilities"]), 8)
        self.assertTrue(all(v is False for v in result["capabilities"].values()))
        for capability in safety.FIELDS:
            with self.assertRaises(core.ForbiddenError):
                safety.require(capability)

    def test_enabled_policy_never_passes_and_existing_gates_still_apply(self):
        os.environ["LH_LIVE_OPERATIONS"] = "on"
        self.assertEqual(safety.status(ADMIN, IDENTITY)["result"], "FAIL")
        for capability in safety.FIELDS:
            safety.require(capability)
        # Lifting the interlock does not activate an unsupported payment provider.
        with self.assertRaises(Exception):
            marketplace_payments.provider("LICENSED_PARTNER").submit_payout({})

    def test_missing_invalid_and_wrong_type_unknown_and_denied(self):
        for value in (None, "false", "true", "OFF", "", "0", False, {}, []):
            env = {"APP_ENV": "production"}
            if value is not None:
                env["LH_LIVE_OPERATIONS"] = value
            with self.subTest(value=value):
                self.assertIsNone(safety.state(env))
        os.environ.pop("LH_LIVE_OPERATIONS")
        self.assertEqual(safety.status(ADMIN, IDENTITY)["result"], "BLOCKED")
        with self.assertRaises(core.ForbiddenError):
            safety.require("live_dispatch_enabled")

    def test_local_or_missing_environment_cannot_certify_off(self):
        for mode in ("development", "test", "local"):
            os.environ["APP_ENV"] = mode
            self.assertEqual(safety.status(ADMIN, IDENTITY)["result"], "BLOCKED")
        os.environ.pop("APP_ENV")
        self.assertEqual(safety.status(ADMIN, IDENTITY)["result"], "BLOCKED")

    def test_role_denials_and_admin_security_access(self):
        for role in (None, "finance", "booker", "operations_manager", "driver", "carrier"):
            with self.subTest(role=role), self.assertRaises(core.ForbiddenError):
                safety.status(None if role is None else {"role": role, "perms": {"*"}}, IDENTITY)
        for role in safety.ADMIN_ROLES:
            self.assertEqual(safety.status({"role": role}, IDENTITY)["result"], "PASS")

    def test_missing_or_unrecognised_environment_denies_synthetic_dispatch_before_io(self):
        for mode in (None, "", "unexpected"):
            with self.subTest(mode=mode), patch.dict(os.environ, {}, clear=False):
                if mode is None:
                    os.environ.pop("APP_ENV", None)
                else:
                    os.environ["APP_ENV"] = mode
                conn = Mock()
                self.assertEqual(safety.status(ADMIN, IDENTITY)["result"], "BLOCKED")
                calls = (
                    lambda: marketplace_matching.create_assignment(conn, ADMIN, 1),
                    lambda: marketplace_matching.request_substitution(conn, ADMIN, 1, new_driver_id=2),
                    lambda: marketplace_trips.activate_trip(conn, ADMIN, 1),
                    lambda: marketplace_trips.advance_trip(conn, ADMIN, 1, "EN_ROUTE_PICKUP"),
                )
                for call in calls:
                    with self.assertRaises(core.ForbiddenError):
                        call()
                with patch.object(ops, "_job", return_value={"status": "DISPATCHED"}):
                    with self.assertRaises(core.ForbiddenError):
                        ops.transition_job(conn, ADMIN, 1, "IN_TRANSIT", evidence="synthetic")
                conn.execute.assert_not_called()

    def test_only_explicit_local_environment_permits_synthetic_execution(self):
        for mode in safety.LOCAL_ENVS:
            with self.subTest(mode=mode), patch.dict(os.environ, {"APP_ENV": mode}):
                for field in ("live_dispatch_enabled", "automatic_assignment_enabled"):
                    safety.require(field, synthetic_local=True)
                    with self.assertRaises(core.ForbiddenError):
                        safety.require(field)
                self.assertEqual(safety.status(ADMIN, IDENTITY)["result"], "BLOCKED")

    def test_each_reported_capability_guards_its_execution_path(self):
        # Independently deny each guard while allowing the other guards, proving
        # that an earlier shared guard cannot hide a missing capability check.
        xendit = payment_gateway.XenditClient("synthetic")
        wise = platform_fee_settlement.WiseAdminFeeClient(cfg={
            "api_token": "synthetic", "api_base": "https://api.wise.com"})
        conn = Mock()
        paths = {
            "live_collection_enabled": lambda: xendit.create_session({}),
            "live_refunds_enabled": lambda: xendit.create_refund({}),
            "live_payouts_enabled": lambda: marketplace_payments.provider("LICENSED_PARTNER"),
            "live_settlement_enabled": lambda: wise._request("POST", "transfers", {}),
            "live_escrow_disbursement_enabled": lambda: marketplace_payments.provider("LICENSED_PARTNER"),
            "live_dispatch_enabled": lambda: marketplace_trips.activate_trip(conn, ADMIN, 1),
            "automatic_assignment_enabled": lambda: marketplace_matching.create_assignment(conn, ADMIN, 1),
            "external_financial_provider_enabled": lambda: xendit.get_payment("synthetic"),
        }
        self.assertEqual(set(paths), set(safety.FIELDS))
        original = safety.require
        os.environ["LH_LIVE_OPERATIONS"] = "on"
        for field, execute in paths.items():
            def deny_selected(capability, **kwargs):
                if capability == field:
                    raise core.ForbiddenError("selected capability denied")
                return original(capability, **kwargs)
            with self.subTest(field=field), patch.object(safety, "require", side_effect=deny_selected), \
                    patch("urllib.request.urlopen") as network:
                with self.assertRaisesRegex(core.ForbiddenError, "selected capability denied"):
                    execute()
                network.assert_not_called()
                conn.execute.assert_not_called()

    def test_missing_release_or_deployment_blocks(self):
        for key, value in (("release_sha", "unknown"), ("release_sha", "a" * 7),
                           ("deployment_id", "unknown"), ("deployment_id", "")):
            self.assertEqual(safety.status(ADMIN, {**IDENTITY, key: value})["result"], "BLOCKED")

    def test_secret_values_and_unknown_config_never_appear(self):
        os.environ["XENDIT_SECRET_KEY"] = "sensitive-test-value"
        self.addCleanup(os.environ.pop, "XENDIT_SECRET_KEY", None)
        os.environ["LH_LIVE_OPERATIONS"] = "secret-in-wrong-variable"
        body = json.dumps(safety.status(ADMIN, IDENTITY))
        self.assertNotIn("sensitive-test-value", body)
        self.assertNotIn("secret-in-wrong-variable", body)

    def test_real_provider_network_denied_before_io_even_in_local_mode(self):
        for app_env in ("production", "test"):
            os.environ["APP_ENV"] = app_env
            with patch("urllib.request.urlopen") as network:
                xendit = payment_gateway.XenditClient("synthetic")
                wise = platform_fee_settlement.WiseAdminFeeClient(cfg={"api_token": "synthetic", "api_base": "https://api.wise.com"})
                for call in (lambda: xendit.create_session({}), lambda: xendit.create_refund({}),
                             lambda: xendit.get_payment("synthetic"),
                             lambda: wise._request("POST", "transfers", {})):
                    with self.assertRaises(core.ForbiddenError):
                        call()
                network.assert_not_called()

    def test_dispatch_and_assignment_denied_before_database_access(self):
        conn = Mock()
        for call in (lambda: marketplace_matching.create_assignment(conn, ADMIN, 1),
                     lambda: marketplace_matching.request_substitution(conn, ADMIN, 1, new_driver_id=2),
                     lambda: marketplace_trips.activate_trip(conn, ADMIN, 1),
                     lambda: marketplace_trips.advance_trip(conn, ADMIN, 1, "EN_ROUTE_PICKUP")):
            with self.assertRaises(core.ForbiddenError):
                call()
        conn.execute.assert_not_called()
        with patch.object(ops, "_job", return_value={"status": "READY_FOR_DISPATCH"}), \
                patch.object(ops, "_payment_verified", return_value=True):
            with self.assertRaises(core.ForbiddenError):
                ops.transition_job(conn, ADMIN, 1, "DISPATCHED", evidence="synthetic")
        conn.execute.assert_not_called()

    def test_existing_dispatch_cannot_progress_with_interlock_off(self):
        conn = Mock()
        for old, new in (("DISPATCHED", "IN_TRANSIT"), ("IN_TRANSIT", "ON_SITE"),
                         ("ON_SITE", "IN_PROGRESS")):
            with self.subTest(old=old), patch.object(ops, "_job", return_value={"status": old}):
                with self.assertRaises(core.ForbiddenError):
                    ops.transition_job(conn, ADMIN, 1, new, evidence="synthetic")
        conn.execute.assert_not_called()

    def test_database_config_cannot_bypass_external_interlock(self):
        with patch("admin_platform.resolve_config", return_value=(True, {})), \
                patch("urllib.request.urlopen") as network:
            with self.assertRaises(core.ForbiddenError):
                marketplace_payments.provider("LICENSED_PARTNER")
            network.assert_not_called()
