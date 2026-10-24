import importlib.util
import pathlib
import sys
import unittest


HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CREDENTIALS = _load("prod_persona_credentials")
PROVISION = _load("prod_persona_provision")
SHA = "b" * 40


class FakeApi:
    def __init__(self):
        self.users = []
        self.shippers = []
        self.carriers = []
        self.drivers = []
        self.driver_apps = []
        self.bookings = []
        self.transactions = []
        self.next_id = 1
        self.write_paths = []

    def _id(self):
        value = self.next_id
        self.next_id += 1
        return value

    def ok(self, method, path, payload=None, token=None, expected=(200,)):
        if method == "POST":
            self.write_paths.append(path)
        if path == "/readyz":
            return {"status": "ready", "release_sha": SHA, "deployment_id": "dep-g5"}
        if path == "/login":
            return {"token": "bootstrap-token"}
        if path == "/logout":
            return {"ok": True}
        if path == "/admin/users" and method == "GET":
            return {"users": self.users}
        if path == "/admin/users" and method == "POST":
            row = {"id": self._id(), "email": payload["email"], "role": payload["role"], "status": "ACTIVE"}
            self.users.append(row)
            return {"id": row["id"]}
        if path.endswith("/reset-password") or path.endswith("/status"):
            return {"ok": True}
        if path == "/public/drivers":
            row = {"id": self._id(), "email": payload["email"], "role": "driver_principal",
                   "status": "PENDING_DRIVER_REVIEW"}
            self.users.append(row)
            self.driver_apps.append({"id": self._id(), "full_name": payload["full_name"]})
            return {"application_id": self.driver_apps[-1]["id"]}
        if path == "/admin/marketplace/shippers" and method == "GET":
            return {"shippers": self.shippers}
        if path == "/admin/marketplace/shippers" and method == "POST":
            row = {"id": self._id(), "legal_name": payload["legal_name"]}
            self.shippers.append(row)
            return {"id": row["id"]}
        if path == "/admin/marketplace/carriers" and method == "GET":
            return {"carriers": self.carriers}
        if path == "/admin/marketplace/carriers" and method == "POST":
            row = {"id": self._id(), "legal_name": payload["legal_name"]}
            self.carriers.append(row)
            return {"id": row["id"]}
        if path == "/admin/marketplace/drivers" and method == "GET":
            return {"drivers": self.drivers}
        if path == "/admin/marketplace/drivers" and method == "POST":
            row = {"id": self._id(), "full_name": payload["full_name"]}
            self.drivers.append(row)
            return {"id": row["id"]}
        if path in ("/admin/client-portal/bind", "/admin/carrier-portal/bind", "/admin/driver-app/bind"):
            return {"id": self._id()}
        if path == "/admin/marketplace/public-booking-queue":
            return {"requests": self.bookings}
        if path == "/public/bookings":
            row = {"id": self._id(), "contact_name": payload["contact_name"]}
            self.bookings.append(row)
            return {"ref": f"LH-{row['id']}", "tracking_token": "synthetic"}
        if path == "/admin/marketplace/protected-payments-queues":
            return {"transactions": self.transactions}
        if path == "/admin/marketplace/protected-payments":
            row = {"id": self._id(), "client_ref": payload["client_ref"]}
            self.transactions.append(row)
            return {"id": row["id"]}
        raise AssertionError((method, path))


class Gate5ProvisionContract(unittest.TestCase):
    def test_hosted_persona_workflow_only_consumes_approved_existing_identities(self):
        workflow = (HERE.parent.parent / ".github" / "workflows" / "production-persona-uat.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("secrets.LH_PROD_PERSONA_CREDENTIALS_JSON", workflow)
        self.assertIn("BLOCKED_HUMAN_IDENTITY_SETUP", workflow)
        self.assertIn("validate_persona_acceptance.py", workflow)
        self.assertIn("ref: ${{ inputs.release_sha }}", workflow)
        self.assertIn('git merge-base --is-ancestor "$RELEASE_SHA" origin/main', workflow)
        for forbidden in (
            "LH_PROD_PERSONA_PROVISION_SEED", "LH_PROD_PERSONA_ADMIN_EMAIL",
            "LH_PROD_PERSONA_ADMIN_PASSWORD", "prod_persona_provision.py",
            "prod_persona_credentials.py", "railway_set_bootstrap_identity.py",
            "variableCollectionUpsert", "RAILWAY_PROJECT_TOKEN",
            "railway variable set", "railway redeploy",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, workflow)

    def test_derivation_is_repeatable_unique_and_strong(self):
        seed = "x" * 48
        first = CREDENTIALS.derive(seed)
        second = CREDENTIALS.derive(seed)
        self.assertEqual(first, second)
        manifest, admin_email, admin_password = first
        self.assertEqual(len({v["email"] for v in manifest.values()}), 11)
        self.assertEqual(len({v["marker"] for v in manifest.values()}), 11)
        self.assertTrue(admin_email.endswith("@qa.lifthaul.com.ph"))
        self.assertGreaterEqual(len(admin_password), 24)
        with self.assertRaises(ValueError):
            CREDENTIALS.derive("too-short")

    def test_provision_is_idempotent_and_never_calls_live_fund_paths(self):
        manifest, admin_email, admin_password = CREDENTIALS.derive("y" * 48)
        api = FakeApi()
        first = PROVISION.provision(api, manifest, admin_email, admin_password, SHA)
        self.assertEqual(first["result"], "PASS")
        self.assertFalse(first["live_funds_enabled"])
        counts = tuple(map(len, (api.users, api.shippers, api.carriers, api.drivers, api.bookings, api.transactions)))
        second = PROVISION.provision(api, manifest, admin_email, admin_password, SHA)
        self.assertEqual(second["result"], "PASS")
        self.assertEqual(counts, tuple(map(len, (api.users, api.shippers, api.carriers, api.drivers, api.bookings, api.transactions))))
        forbidden = ("release", "refund", "payout", "transition", "ledger")
        self.assertFalse(any(any(word in path for word in forbidden) for path in api.write_paths))


if __name__ == "__main__":
    unittest.main()
