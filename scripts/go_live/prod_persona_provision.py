#!/usr/bin/env python3
"""Idempotently provision synthetic Gate 5 production personas and fixtures.

The script deliberately uses only governed HTTPS APIs. It never reads the
production database, emits credentials/response bodies, activates live funds,
approves providers, or changes commercial configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

from prod_persona_uat import PERSONAS, load_manifest


ROLES = {
    "booker": "customer",
    "accredited_driver": "driver_principal",
    "motorcycle_owner": "carrier_principal",
    "light_vehicle_owner": "carrier_principal",
    "truck_owner": "carrier_principal",
    "fleet_owner": "carrier_principal",
    "specialized_provider": "carrier_principal",
    "operations": "public_intake_operator",
    "finance": "finance",
    "administrator": "super_admin",
}


class Api:
    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")

    def call(self, method: str, path: str, payload=None, token=None):
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Accept": "application/json", "User-Agent": "LiftHaul-Gate5-Provisioner/1.0"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status, raw = response.status, response.read(2 * 1024 * 1024)
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read(8192)
        try:
            body = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            body = {}
        if isinstance(body, dict) and "data" in body:
            body = body["data"]
        return status, body

    def ok(self, method, path, payload=None, token=None, expected=(200,)):
        status, body = self.call(method, path, payload, token)
        if status not in expected:
            raise RuntimeError(f"governed API call failed: {method} {path} returned HTTP {status}")
        return body


def _find(rows, field, value):
    return next((row for row in rows if str(row.get(field, "")).casefold() == value.casefold()), None)


def provision(api: Api, manifest: dict, admin_email: str, admin_password: str, expected_sha: str) -> dict:
    ready = api.ok("GET", "/readyz")
    if ready.get("release_sha") != expected_sha or ready.get("status") != "ready":
        raise RuntimeError("production does not report the exact approved release")
    login = api.ok("POST", "/login", {"email": admin_email, "password": admin_password})
    token = login.get("token")
    if not token:
        raise RuntimeError("synthetic provisioning identity could not authenticate")

    created = []
    try:
        users_payload = api.ok("GET", "/admin/users", token=token)
        users = users_payload.get("users", [])
        user_ids = {}
        for persona, role in ROLES.items():
            row = manifest[persona]
            existing = _find(users, "email", row["email"])
            if existing:
                uid = int(existing["id"])
                api.ok("POST", f"/admin/users/{uid}/reset-password", {"password": row["password"]}, token)
                if existing.get("status") != "ACTIVE":
                    api.ok("POST", f"/admin/users/{uid}/status", {"status": "ACTIVE"}, token)
            else:
                result = api.ok("POST", "/admin/users", {
                    "email": row["email"], "password": row["password"], "role": role,
                    "name": row["marker"],
                }, token)
                uid = int(result["id"])
                created.append(f"user:{persona}")
            user_ids[persona] = uid

        # Pending driver applicants are created through the real public intake path.
        users_payload = api.ok("GET", "/admin/users", token=token)
        pending = _find(users_payload.get("users", []), "email", manifest["driver_applicant"]["email"])
        if not pending:
            marker = manifest["driver_applicant"]["marker"]
            api.ok("POST", "/public/drivers", {
                "full_name": marker,
                "email": manifest["driver_applicant"]["email"],
                "username": manifest["driver_applicant"]["email"],
                "password": manifest["driver_applicant"]["password"],
                "licence_number": "G5-" + hashlib.sha256(marker.encode()).hexdigest()[:20].upper(),
                "licence_class": "PROFESSIONAL",
                "licence_expiry": "2099-12-31",
                "partner_track": "TRUCK_DRIVER",
                "employment_mode": "SEEKING_FLEET",
                "consent": True,
            })
            created.append("application:driver_applicant")

        # Booker identity -> synthetic shipper workspace.
        shippers = api.ok("GET", "/admin/marketplace/shippers", token=token).get("shippers", [])
        marker = manifest["booker"]["marker"]
        shipper = _find(shippers, "legal_name", marker)
        if not shipper:
            shipper = {"id": api.ok("POST", "/admin/marketplace/shippers", {
                "applicant_type": "INDIVIDUAL", "legal_name": marker,
                "contract_accepted": True, "privacy_accepted": True,
            }, token)["id"]}
            created.append("workspace:booker")
        api.ok("POST", "/admin/client-portal/bind", {
            "user_id": user_ids["booker"], "shipper_id": int(shipper["id"]),
        }, token)

        # Each carrier persona gets a distinct canonical carrier workspace.
        carriers = api.ok("GET", "/admin/marketplace/carriers", token=token).get("carriers", [])
        carrier_ids = {}
        types = {
            "motorcycle_owner": "OWNER_OPERATOR", "light_vehicle_owner": "OWNER_OPERATOR",
            "truck_owner": "OWNER_OPERATOR", "fleet_owner": "CORPORATION",
            "specialized_provider": "SPECIALIZED_CARRIER",
        }
        for persona, carrier_type in types.items():
            marker = manifest[persona]["marker"]
            carrier = _find(carriers, "legal_name", marker)
            if not carrier:
                carrier = {"id": api.ok("POST", "/admin/marketplace/carriers", {
                    "carrier_type": carrier_type, "legal_name": marker,
                }, token)["id"]}
                created.append(f"workspace:{persona}")
            carrier_ids[persona] = int(carrier["id"])
            api.ok("POST", "/admin/carrier-portal/bind", {
                "user_id": user_ids[persona], "carrier_id": int(carrier["id"]),
            }, token)

        # Accredited driver gets a canonical driver record and principal binding.
        driver_marker = manifest["accredited_driver"]["marker"]
        driver_carrier = _find(carriers, "legal_name", driver_marker + " CARRIER")
        if not driver_carrier:
            driver_carrier = {"id": api.ok("POST", "/admin/marketplace/carriers", {
                "carrier_type": "CORPORATION", "legal_name": driver_marker + " CARRIER",
            }, token)["id"]}
            created.append("workspace:accredited_driver")
        drivers = api.ok("GET", "/admin/marketplace/drivers", token=token).get("drivers", [])
        driver = _find(drivers, "full_name", driver_marker)
        if not driver:
            driver = {"id": api.ok("POST", "/admin/marketplace/drivers", {
                "carrier_id": int(driver_carrier["id"]), "full_name": driver_marker,
                "licence_number": "G5-" + hashlib.sha256(driver_marker.encode()).hexdigest()[:20].upper(),
                "licence_class": "PROFESSIONAL", "licence_expiry": "2099-12-31",
            }, token)["id"]}
            created.append("driver:accredited_driver")
        api.ok("POST", "/admin/driver-app/bind", {
            "user_id": user_ids["accredited_driver"], "driver_id": int(driver["id"]),
        }, token)

        # Fleet-owner's driver list carries that persona's unique fixture marker.
        fleet_marker = manifest["fleet_owner"]["marker"]
        fleet_driver = _find(drivers, "full_name", fleet_marker + " DRIVER")
        if not fleet_driver:
            api.ok("POST", "/admin/marketplace/drivers", {
                "carrier_id": carrier_ids["fleet_owner"], "full_name": fleet_marker + " DRIVER",
                "licence_number": "G5-" + hashlib.sha256((fleet_marker + " DRIVER").encode()).hexdigest()[:20].upper(),
                "licence_class": "PROFESSIONAL", "licence_expiry": "2099-12-31",
            }, token)
            created.append("driver:fleet_owner")

        # Operations fixture is a real synthetic public booking, never dispatched or funded.
        ops_marker = manifest["operations"]["marker"]
        queue = api.ok("GET", "/admin/marketplace/public-booking-queue", token=token)
        requests = queue.get("requests", []) if isinstance(queue, dict) else []
        booking = _find(requests, "contact_name", ops_marker)
        if not booking:
            result = api.ok("POST", "/public/bookings", {
                "contact_name": ops_marker,
                "contact_email": manifest["operations"]["email"],
                "origin_island": "LUZON", "dest_island": "LUZON",
                "origin_city": "Makati", "dest_city": "Quezon City",
                "vehicle": "moto", "km": 12, "cargo_category": "BOXES_GENERAL",
                "cargo": "Synthetic Gate 5 QA parcels — no commercial shipment",
                "weight_kg": 10, "package_count": 1,
                "package_length_cm": 40, "package_width_cm": 30, "package_height_cm": 30,
                "payment": "protected", "excluded_charges_ack": True,
                "idempotency_key": "gate5-" + hashlib.sha256(ops_marker.encode()).hexdigest(),
            })
            created.append("booking:operations")
            queue = api.ok("GET", "/admin/marketplace/public-booking-queue", token=token)
            requests = queue.get("requests", []) if isinstance(queue, dict) else []
            booking = _find(requests, "contact_name", ops_marker)
            if not booking:
                raise RuntimeError("synthetic operations booking is absent from governed queue")

        # Finance fixture uses MOCK only. No provider call or fund movement occurs.
        finance_marker = manifest["finance"]["marker"]
        finance = api.ok("GET", "/admin/marketplace/protected-payments-queues", token=token)
        txs = finance.get("transactions", []) if isinstance(finance, dict) else []
        if not _find(txs, "client_ref", finance_marker):
            api.ok("POST", "/admin/marketplace/protected-payments", {
                "booking_id": int(booking["id"]),
                "carrier_id": carrier_ids["specialized_provider"],
                "contract_amount": 1000, "protected_amount": 1000,
                "platform_fee": 100, "client_ref": finance_marker,
                "currency": "PHP",
            }, token)
            created.append("protected_payment:finance_mock")

        return {
            "schema": "lifthaul.production-persona-provision.v1",
            "result": "PASS",
            "release_sha": expected_sha,
            "deployment_id": ready.get("deployment_id"),
            "personas": len(PERSONAS),
            "created_count": len(created),
            "created_categories": created,
            "live_funds_enabled": False,
        }
    finally:
        try:
            api.call("POST", "/logout", {}, token)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--admin-email", required=True, type=Path)
    parser.add_argument("--admin-password", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output
    try:
        manifest = load_manifest(args.manifest.read_text(encoding="utf-8"))
        evidence = provision(
            Api(os.environ.get("BASE_URL", "https://www.lifthaul.com.ph")),
            manifest,
            args.admin_email.read_text(encoding="utf-8").strip(),
            args.admin_password.read_text(encoding="utf-8").strip(),
            os.environ.get("EXPECTED_SHA", ""),
        )
        code = 0
    except Exception as exc:
        evidence = {
            "schema": "lifthaul.production-persona-provision.v1",
            "result": "BLOCKED_OR_FAILED",
            "reason": str(exc),
            "live_funds_enabled": False,
        }
        code = 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"Production persona provisioning result: {evidence['result']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
