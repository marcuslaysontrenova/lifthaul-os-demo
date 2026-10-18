#!/usr/bin/env python3
"""Read-only, exact-release production acceptance for every MVP persona.

The credential/fixture manifest is supplied as one protected GitHub environment
secret. The script never prints emails, passwords, tokens, or production response
bodies. Each persona must use a distinct account and a distinct synthetic marker.
No dispatch, status mutation, payment, refund, or payout endpoint is called.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request


BASE_URL = "https://www.lifthaul.com.ph"
PERSONAS = (
    "booker",
    "driver_applicant",
    "accredited_driver",
    "motorcycle_owner",
    "light_vehicle_owner",
    "truck_owner",
    "fleet_owner",
    "specialized_provider",
    "operations",
    "finance",
    "administrator",
)
ROLE_RULES = {
    "booker": {"customer"},
    "accredited_driver": {"driver_principal"},
    "motorcycle_owner": {"carrier_principal"},
    "light_vehicle_owner": {"carrier_principal"},
    "truck_owner": {"carrier_principal"},
    "fleet_owner": {"carrier_principal"},
    "specialized_provider": {"carrier_principal"},
    "operations": {"public_intake_operator", "operations_manager"},
    "finance": {"finance", "finance_admin"},
    "administrator": {"super_admin", "user_administrator"},
}
JOURNEYS = {
    "booker": ("/portal/client/overview", "/portal/client/bookings"),
    "accredited_driver": ("/driver/profile", "/driver/trips"),
    "motorcycle_owner": ("/portal/carrier/overview", "/portal/carrier/fleet"),
    "light_vehicle_owner": ("/portal/carrier/overview", "/portal/carrier/fleet"),
    "truck_owner": ("/portal/carrier/overview", "/portal/carrier/fleet"),
    "fleet_owner": ("/portal/carrier/fleet", "/portal/carrier/drivers"),
    "specialized_provider": ("/portal/carrier/overview", "/portal/carrier/dashboard"),
    "operations": ("/admin/marketplace/public-booking-queue",),
    "finance": ("/admin/marketplace/protected-payments-queues",),
    "administrator": ("/admin/users", "/admin/marketplace/public-driver-applications"),
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_call(method, path, payload=None, bearer=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "User-Agent": "LiftHaul-Prod-Persona-UAT/1.0"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    req = urllib.request.Request(BASE_URL + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=25) as response:
            status, raw = response.status, response.read(1024 * 1024)
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read(8192)
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        body = {}
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        body = body["data"]
    return status, body if isinstance(body, dict) else {}


def _fingerprint(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _contains(value, marker):
    return marker.casefold() in json.dumps(value, sort_keys=True, ensure_ascii=True).casefold()


def load_manifest(raw):
    try:
        manifest = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise AssertionError("persona credential manifest is absent or invalid JSON") from exc
    assert isinstance(manifest, dict), "persona credential manifest must be an object"
    assert set(manifest) == set(PERSONAS), "persona manifest must contain exactly the 11 required personas"
    seen_emails, seen_markers = set(), set()
    for persona in PERSONAS:
        row = manifest[persona]
        assert isinstance(row, dict), f"{persona}: configuration must be an object"
        email = str(row.get("email") or "").strip().lower()
        password = str(row.get("password") or "")
        marker = str(row.get("marker") or "").strip()
        assert "@" in email and len(password) >= 8, f"{persona}: dedicated credentials are incomplete"
        assert len(marker) >= 6, f"{persona}: a unique synthetic fixture marker is required"
        assert email not in seen_emails, "every persona must use a distinct production account"
        assert marker.casefold() not in seen_markers, "every persona must use a distinct synthetic marker"
        seen_emails.add(email)
        seen_markers.add(marker.casefold())
    return manifest


def execute(manifest, expected_sha, call=http_call):
    assert re.fullmatch(r"[0-9a-f]{40}", expected_sha), "expected release SHA must be a full commit"
    status, ready = call("GET", "/readyz")
    assert status == 200 and ready.get("status") == "ready", "canonical production is not ready"
    assert ready.get("release_sha") == expected_sha, "live production SHA differs from approved release"
    assert ready.get("deployment_id") and ready.get("schema_version"), "deployment identity is incomplete"

    tokens, results = {}, []
    try:
        for persona in PERSONAS:
            row = manifest[persona]
            status, login = call("POST", "/login", {"email": row["email"], "password": row["password"]})
            if persona == "driver_applicant":
                assert status in (401, 403), "pending driver applicant unexpectedly received an active session"
                assert not login.get("token"), "pending driver applicant unexpectedly received a token"
                results.append({"persona": persona, "result": "PASS", "control": "login_pending"})
                continue
            assert status == 200 and login.get("token"), f"{persona}: production login failed"
            token = login["token"]
            tokens[persona] = token
            status, me = call("GET", "/me/permissions", bearer=token)
            assert status == 200 and me.get("role") in ROLE_RULES[persona], f"{persona}: incorrect role"
            if persona != "administrator":
                denied, _ = call("GET", "/admin/users", bearer=token)
                assert denied == 403, f"{persona}: user administration was not denied"

            journey_payloads = []
            for path in JOURNEYS[persona]:
                code, payload = call("GET", path, bearer=token)
                assert code == 200, f"{persona}: journey endpoint failed"
                journey_payloads.append(payload)
            assert _contains(journey_payloads, row["marker"]), f"{persona}: dedicated synthetic fixture missing"
            results.append({"persona": persona, "result": "PASS", "control": "login_role_scope_fixture"})

        admin_token = tokens["administrator"]
        code, driver_queue = call("GET", "/admin/marketplace/public-driver-applications", bearer=admin_token)
        assert code == 200 and _contains(driver_queue, manifest["driver_applicant"]["marker"]), (
            "driver_applicant: pending application fixture missing from governed queue"
        )

        finance_token = tokens["finance"]
        code, live = call("GET", "/admin/marketplace/protected-payment-live-status", bearer=finance_token)
        assert code == 200, "finance: protected-payment status unavailable"
        assert live.get("LIVE_PROTECTED_FUNDS_ENABLED") is False, "live protected funds unexpectedly enabled"
        assert live.get("live_protected_payment") == "BLOCKED", "live-funds boundary is not fail-closed"
        return {
            "schema": "lifthaul.production-persona-uat.v1",
            "result": "PASS",
            "expected_release_sha": expected_sha,
            "observed_release_sha": ready.get("release_sha"),
            "deployment_id": ready.get("deployment_id"),
            "schema_version": ready.get("schema_version"),
            "personas_required": len(PERSONAS),
            "personas_passed": len(results),
            "live_funds_enabled": False,
            "accounts": [{"persona": p, "account_fingerprint": _fingerprint(manifest[p]["email"])} for p in PERSONAS],
            "results": results,
        }
    finally:
        for token in tokens.values():
            try:
                call("POST", "/logout", {}, bearer=token)
            except Exception:
                pass


def main():
    output = pathlib.Path(os.environ.get("OUTPUT_PATH", "production-persona-uat.json"))
    try:
        manifest = load_manifest(os.environ.get("LH_PROD_PERSONA_CREDENTIALS_JSON", ""))
        evidence = execute(manifest, os.environ.get("EXPECTED_SHA", ""))
        exit_code = 0
    except Exception as exc:  # keep a non-secret fail-closed artifact
        evidence = {
            "schema": "lifthaul.production-persona-uat.v1",
            "result": "BLOCKED_OR_FAILED",
            "reason": str(exc),
            "live_funds_enabled": False,
        }
        exit_code = 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"Production persona evidence result: {evidence['result']}")
    if exit_code:
        print(evidence["reason"], file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
