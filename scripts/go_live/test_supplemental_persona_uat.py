import json
import unittest
from scripts.go_live import supplemental_persona_uat as supplement
from scripts.go_live.test_prod_persona_uat_contract import FakeApi, manifest, SHA


def fixtures():
    data = manifest()
    for p, path in {"booker": "/portal/client/bookings/101",
                    "accredited_driver": "/driver/trips/102",
                    "motorcycle_owner": "/admin/carrier-portal/overview/103",
                    "truck_owner": "/admin/carrier-portal/overview/104"}.items():
        data[p]["object_path"] = path
    return data


class SupplementApi(FakeApi):
    def __init__(self, data):
        super().__init__(data)
        self.calls = []
        self.leak = False
        self.objects_exist = True
        self.safety = {field: False for field in supplement.SAFETY_FIELDS}
        self.change_deployment = False
        self.readies = 0

    def __call__(self, method, path, payload=None, bearer=None):
        self.calls.append((method, path))
        if path == "/readyz":
            self.readies += 1
            status, body = super().__call__(method, path, payload, bearer)
            if self.change_deployment and self.readies > 1:
                body["deployment_id"] = "different"
            return status, body
        if path == "/admin/users" and bearer is None:
            return 401, {}
        if path in [self.data[p]["object_path"] for p in supplement.OBJECT_PATTERNS]:
            actor = self.tokens[bearer]
            owner = next(p for p in supplement.OBJECT_PATTERNS if self.data[p]["object_path"] == path)
            if not self.objects_exist:
                return 404, {}
            if actor == owner:
                return 200, {"fixture": self.data[owner]["marker"]}
            if actor in ("truck_owner", "motorcycle_owner") and owner in ("truck_owner", "motorcycle_owner"):
                return 200, {"fixture": self.data[owner if self.leak else actor]["marker"]}
            return (200, {"fixture": self.data[owner]["marker"]}) if self.leak else (403, {})
        if path == "/admin/security/operational-safety":
            if self.tokens[bearer] != "administrator":
                return 403, {}
            return 200, {"schema": "lifthaul.operational-safety.v1", "result": "PASS",
                         "enforcement": "shared_process_interlock_v1", "release_sha": SHA,
                         "deployment_id": "dep-1", "observed_at": "2026-10-24T15:00:00+00:00",
                         "capabilities": self.safety}
        status, body = super().__call__(method, path, payload, bearer)
        if path.endswith("protected-payment-live-status") and status == 200:
            body.update(self.safety)
        return status, body


class SupplementTests(unittest.TestCase):
    def test_full_evidence_redacted_and_only_login_logout_write(self):
        data = fixtures()
        api = SupplementApi(data)
        result = supplement.execute(data, SHA, api)
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(len(api.revoked), 6)
        self.assertTrue(all(method == "GET" or path in ("/login", "/logout") for method, path in api.calls))
        serialized = json.dumps(result)
        for row in data.values():
            for field in ("email", "password", "marker", "object_path"):
                if field in row:
                    self.assertNotIn(row[field], serialized)

    def test_missing_safety_signals_are_blocked(self):
        data = fixtures()
        api = SupplementApi(data)
        api.safety = {}
        result = supplement.execute(data, SHA, api)
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(result["blocker"], "BLOCKED_GATE5_SAFETY_OBSERVABILITY")
        self.assertEqual(result["checks"]["live_dispatch_enabled"], "BLOCKED")

    def test_live_or_nonboolean_safety_signal_fails(self):
        for value in (True, "false", 0):
            data = fixtures()
            api = SupplementApi(data)
            api.safety["live_dispatch_enabled"] = value
            with self.subTest(value=value), self.assertRaises(AssertionError):
                supplement.execute(data, SHA, api)

    def test_unknown_capability_blocks(self):
        data = fixtures()
        api = SupplementApi(data)
        api.safety["automatic_assignment_enabled"] = None
        self.assertEqual(supplement.execute(data, SHA, api)["result"], "BLOCKED")

    def test_status_identity_mismatch_blocks_and_revokes(self):
        data = fixtures()
        class WrongIdentity(SupplementApi):
            def __call__(self, method, path, payload=None, bearer=None):
                code, body = super().__call__(method, path, payload, bearer)
                if path == "/admin/security/operational-safety" and code == 200:
                    body["release_sha"] = "b" * 40
                return code, body
        api = WrongIdentity(data)
        with self.assertRaisesRegex(supplement.Blocked, "RELEASE_IDENTITY"):
            supplement.execute(data, SHA, api)
        self.assertEqual(len(api.revoked), 6)

    def test_each_enabled_capability_fails(self):
        for field in supplement.SAFETY_FIELDS:
            data = fixtures()
            api = SupplementApi(data)
            api.safety[field] = True
            with self.subTest(field=field), self.assertRaises(AssertionError):
                supplement.execute(data, SHA, api)

    def test_cross_role_leak_fails_and_sessions_are_revoked(self):
        data = fixtures()
        api = SupplementApi(data)
        api.leak = True
        with self.assertRaises(AssertionError):
            supplement.execute(data, SHA, api)
        self.assertEqual(len(api.revoked), 6)

    def test_same_role_tenant_leak_fails(self):
        data = fixtures()
        class CarrierLeakApi(SupplementApi):
            def __call__(self, method, path, payload=None, bearer=None):
                if path == data["motorcycle_owner"]["object_path"] and bearer == "tok-truck_owner":
                    return 200, {"fixture": data["motorcycle_owner"]["marker"]}
                return super().__call__(method, path, payload, bearer)
        with self.assertRaisesRegex(AssertionError, "CROSS_TENANT"):
            supplement.execute(data, SHA, CarrierLeakApi(data))

    def test_nonexistent_objects_do_not_count_as_denials(self):
        data = fixtures()
        api = SupplementApi(data)
        api.objects_exist = False
        with self.assertRaises(supplement.Blocked):
            supplement.execute(data, SHA, api)

    def test_missing_or_unsafe_paths_refused_before_network(self):
        for path in ("https://foreign.invalid/", "/admin/settings/secrets", "/portal/client/bookings/1?x=2", ""):
            data = fixtures()
            data["booker"]["object_path"] = path
            api = SupplementApi(data)
            with self.subTest(path=path), self.assertRaises(supplement.Blocked):
                supplement.execute(data, SHA, api)
            self.assertEqual(api.calls, [])

    def test_deployment_change_during_tests_blocks_result(self):
        data = fixtures()
        api = SupplementApi(data)
        api.change_deployment = True
        with self.assertRaises(supplement.Blocked):
            supplement.execute(data, SHA, api)
        self.assertEqual(len(api.revoked), 6)

    def test_stale_session_prevents_pass(self):
        data = fixtures()
        api = SupplementApi(data)
        api.keep_token_active = True
        with self.assertRaisesRegex(AssertionError, "REVOCATION"):
            supplement.execute(data, SHA, api)
