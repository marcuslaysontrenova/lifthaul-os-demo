import importlib.util
import json
import pathlib
import unittest


PATH = pathlib.Path(__file__).with_name("prod_persona_uat.py")
SPEC = importlib.util.spec_from_file_location("prod_persona_uat", PATH)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
SHA = "a" * 40


def manifest():
    return {p: {"email": f"{p}@uat.invalid", "password": "Gate5Password!", "marker": f"UAT-{p}-fixture"}
            for p in MOD.PERSONAS}


class FakeApi:
    def __init__(self, data):
        self.data = data
        self.tokens = {f"tok-{p}": p for p in MOD.PERSONAS if p != "driver_applicant"}
        self.revoked = set()
        self.logout_status = 200
        self.keep_token_active = False

    def __call__(self, method, path, payload=None, bearer=None):
        if path == "/readyz":
            return 200, {"status": "ready", "release_sha": SHA, "deployment_id": "dep-1", "schema_version": 27}
        if path == "/login":
            persona = next(p for p, row in self.data.items() if row["email"] == payload["email"])
            if persona == "driver_applicant":
                return 403, {"error": "pending"}
            return 200, {"token": f"tok-{persona}"}
        if path == "/logout":
            if self.logout_status == 200 and not self.keep_token_active:
                self.revoked.add(bearer)
            return self.logout_status, {"ok": self.logout_status == 200}
        if path == "/me/permissions" and bearer in self.revoked:
            return 401, {"error": "session_revoked"}
        persona = self.tokens[bearer]
        if path == "/me/permissions":
            return 200, {"role": sorted(MOD.ROLE_RULES[persona])[0], "permissions": []}
        if path == "/admin/users" and persona != "administrator":
            return 403, {"error": "forbidden"}
        if path == "/admin/marketplace/protected-payment-live-status":
            if persona not in ("finance", "administrator"):
                return 403, {"error": "forbidden"}
            return 200, {"LIVE_PROTECTED_FUNDS_ENABLED": False, "live_protected_payment": "BLOCKED"}
        if path == "/admin/marketplace/public-driver-applications":
            return 200, {"applications": [self.data["driver_applicant"]["marker"],
                                            self.data[persona]["marker"]]}
        return 200, {"fixture": self.data[persona]["marker"]}


class ProductionPersonaContract(unittest.TestCase):
    def test_manifest_requires_exact_unique_personas(self):
        data = manifest()
        self.assertEqual(set(MOD.load_manifest(json.dumps(data))), set(MOD.PERSONAS))
        data["finance"]["email"] = data["operations"]["email"]
        with self.assertRaisesRegex(AssertionError, "distinct"):
            MOD.load_manifest(json.dumps(data))

    def test_all_personas_role_scope_fixture_and_funds_off(self):
        data = manifest()
        result = MOD.execute(data, SHA, call=FakeApi(data))
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["personas_passed"], 11)
        self.assertEqual(result["sessions_revoked"], 10)
        self.assertEqual(result["revocation_control"], "logout_http_200_or_204_and_stale_bearer_401_or_403")
        self.assertFalse(result["live_funds_enabled"])
        serialized = json.dumps(result)
        self.assertNotIn("Gate5Password", serialized)
        self.assertNotIn("@uat.invalid", serialized)

    def test_revocation_required_after_every_authenticated_persona(self):
        data = manifest()
        api = FakeApi(data)
        self.assertEqual(MOD.execute(data, SHA, call=api)["personas_passed"], 11)
        self.assertEqual(len(api.revoked), 10)

    def test_logout_error_blocks_gate_five(self):
        data = manifest()
        api = FakeApi(data)
        api.logout_status = 503
        with self.assertRaisesRegex(AssertionError, "session revocation failed"):
            MOD.execute(data, SHA, call=api)

    def test_valid_stale_token_blocks_gate_five(self):
        data = manifest()
        api = FakeApi(data)
        api.keep_token_active = True
        with self.assertRaisesRegex(AssertionError, "session revocation failed"):
            MOD.execute(data, SHA, call=api)

    def test_external_persona_cannot_read_finance_status(self):
        data = manifest()
        class LeakyApi(FakeApi):
            def __call__(self, method, path, payload=None, bearer=None):
                if path == "/admin/marketplace/protected-payment-live-status" and bearer == "tok-booker":
                    return 200, {"LIVE_PROTECTED_FUNDS_ENABLED": False}
                return super().__call__(method, path, payload, bearer)
        with self.assertRaisesRegex(AssertionError, "restricted finance status"):
            MOD.execute(data, SHA, call=LeakyApi(data))

    def test_wrong_live_sha_fails_closed(self):
        data = manifest()
        with self.assertRaisesRegex(AssertionError, "differs"):
            MOD.execute(data, "b" * 40, call=FakeApi(data))


if __name__ == "__main__":
    unittest.main()
