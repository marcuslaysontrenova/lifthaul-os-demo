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

    def test_database_config_cannot_bypass_external_interlock(self):
        with patch("admin_platform.resolve_config", return_value=(True, {})), \
                patch("urllib.request.urlopen") as network:
            with self.assertRaises(core.ForbiddenError):
                marketplace_payments.provider("LICENSED_PARTNER")
            network.assert_not_called()
