#!/usr/bin/env python3
"""LiftHaul hosted regional-booking acceptance for the controlled MVP.

Creates exactly three synthetic, no-live-funds bookings: one each for Luzon,
Visayas, and Mindanao. It proves every exact booking can be tracked publicly and
is visible in the authenticated canonical public-intake queue. It never accepts
a quote, opens checkout, captures funds, assigns a provider, or starts a payout.

The check fails before its first write unless the backend reports the exact
expected release SHA. Production credentials have no demo defaults.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8787").rstrip("/")
EXPECTED_SHA = os.environ.get("EXPECTED_SHA") or os.environ.get("RELEASE_SHA")
EXPECTED_DEPLOYMENT_ID = os.environ.get("EXPECTED_DEPLOYMENT_ID", "").strip()
ADMIN_EMAIL = os.environ.get("LH_ADMIN_EMAIL")
ADMIN_PW = os.environ.get("LH_ADMIN_PASSWORD")
EVIDENCE_PATH = Path(os.environ.get(
    "LH_MVP_EVIDENCE_PATH", "evidence/regional-booking-acceptance.json"
))

if not EXPECTED_SHA or len(EXPECTED_SHA) != 40:
    raise SystemExit("EXPECTED_SHA (or RELEASE_SHA) must be the exact 40-character deployed commit")
if not ADMIN_EMAIL or not ADMIN_PW:
    raise SystemExit("LH_ADMIN_EMAIL and LH_ADMIN_PASSWORD are required; production validation has no demo defaults")

REGIONAL_CASES = (
    ("LUZON", "Makati", "Quezon City", "moto"),
    ("VISAYAS", "Cebu City", "Mandaue City", "sedan"),
    ("MINDANAO", "Davao City", "Tagum City", "pickup"),
)

_ok = 0
_fail = 0


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward login payloads or authorization headers to a redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect())


def _req(method, path, body=None, token=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", "LiftHaul-Regional-MVP-Acceptance/1.0")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with OPENER.open(request, timeout=20) as response:
            code, payload = response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        code, payload = error.code, error.read().decode()
    except Exception as error:
        return 0, {"error": str(error)}
    try:
        parsed = json.loads(payload) if payload else {}
    except Exception:
        parsed = {"raw": payload[:200]}
    if isinstance(parsed, dict) and "data" in parsed:
        parsed = parsed["data"]
    return code, parsed


def step(name, condition, detail=""):
    global _ok, _fail
    if condition:
        _ok += 1
        print(f"  [PASS] {name}")
    else:
        _fail += 1
        print(f"  [FAIL] {name}" + (f"  {detail}" if detail else ""))
    return condition


def _fingerprint(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _write_evidence(evidence):
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(json.dumps(evidence, indent=2), encoding="utf-8")


def main():
    global _ok, _fail
    _ok = _fail = 0
    evidence = {
        "acceptance": "regional_public_booking",
        "base_url": BASE,
        "expected_release_sha": EXPECTED_SHA,
        "expected_deployment_id": EXPECTED_DEPLOYMENT_ID or None,
        "live_funds_exercised": False,
        "regions": [],
        "result": "INCOMPLETE",
    }
    print(f"LiftHaul regional booking acceptance  ->  {BASE}")
    print("-" * 72)

    # Fail closed before any write. A healthy but different release is not evidence.
    code, ready = _req("GET", "/readyz")
    identity_ok = (
        code == 200
        and ready.get("status") == "ready"
        and ready.get("schema_version") is not None
        and ready.get("release_sha") == EXPECTED_SHA
        and (not EXPECTED_DEPLOYMENT_ID
             or ready.get("deployment_id") == EXPECTED_DEPLOYMENT_ID)
    )
    step("exact deployed release is ready", identity_ok, f"got HTTP {code}")
    evidence["observed_release_sha"] = ready.get("release_sha") if isinstance(ready, dict) else None
    evidence["schema_version"] = ready.get("schema_version") if isinstance(ready, dict) else None
    if not identity_ok:
        evidence["result"] = "BLOCKED_RELEASE_IDENTITY_MISMATCH"
        evidence["passed"] = _ok
        evidence["failed"] = _fail
        _write_evidence(evidence)
        return 1

    code, health = _req("GET", "/healthz")
    step("health endpoint reports ok", code == 200 and health.get("status") == "ok", f"got HTTP {code}")

    code, _ = _req("GET", "/admin/marketplace/public-booking-queue")
    step("public user cannot read the intake queue", code in (401, 403), f"got HTTP {code}")

    # Nothing that creates a booking may run before both environment identity
    # and the *separate* least-privilege production QA principal are verified.
    # Local PostgreSQL CI uses a throwaway admin in an isolated localhost database.
    # That exception is prohibited on all hosted environments.
    if _fail:
        evidence["result"] = "BLOCKED_PUBLIC_SAFETY_CHECKS"
        evidence.update(passed=_ok, failed=_fail)
        _write_evidence(evidence)
        return 1

    code, login = _req("POST", "/login", {"email": ADMIN_EMAIL, "password": ADMIN_PW})
    bearer = login.get("token")
    login_ok = code == 200 and isinstance(bearer, str) and bool(bearer.strip())
    step("dedicated acceptance identity can sign in", login_ok, f"got HTTP {code}")
    if not login_ok:
        evidence["result"] = "BLOCKED_QA_AUTHENTICATION"
        evidence.update(passed=_ok, failed=_fail)
        _write_evidence(evidence)
        return 1

    code, principal = _req("GET", "/me/permissions", token=bearer)
    required = {"marketplace.booking.view", "marketplace.public_intake.view"}
    perms = set(principal.get("permissions") or [])
    isolated_ci_admin = (
        BASE in ("http://localhost:8787", "http://127.0.0.1:8787")
        and os.environ.get("LIFTHAUL_CI", "").lower() == "true"
        and principal.get("role") == "admin"
    )
    least_privilege_qa = (
        principal.get("role") == "public_intake_qa"
        and perms == required
        and not principal.get("is_super")
        and not any((principal.get("financial") or {}).values())
    )
    authorized = code == 200 and (least_privilege_qa or isolated_ci_admin)
    step("QA is least privilege (or isolated localhost CI admin)", authorized,
         f"permissions HTTP {code}")
    if not authorized:
        evidence["result"] = "BLOCKED_QA_AUTHORIZATION"
        evidence.update(passed=_ok, failed=_fail)
        _write_evidence(evidence)
        return 1

    code, queue = _req("GET", "/admin/marketplace/public-booking-queue", token=bearer)
    queue_ready = (
        code == 200 and isinstance(queue, dict)
        and queue.get("source") == "PUBLIC_MARKETPLACE"
        and isinstance(queue.get("requests"), list)
    )
    step("QA has read-only access to governed intake", queue_ready, f"got HTTP {code}")
    if not queue_ready:
        evidence["result"] = "BLOCKED_QA_QUEUE_ACCESS"
        evidence.update(passed=_ok, failed=_fail)
        _write_evidence(evidence)
        return 1

    stamp = int(time.time())
    created = []
    for index, (island, origin, destination, vehicle) in enumerate(REGIONAL_CASES, start=1):
        key = f"mvp-{EXPECTED_SHA[:12]}-{stamp}-{island.lower()}"
        booking = {
            "contact_name": f"MVP Synthetic {island}",
            "contact_email": f"mvp-{island.lower()}-{stamp}@example.invalid",
            "origin_island": island,
            "dest_island": island,
            "origin_city": origin,
            "dest_city": destination,
            "vehicle": vehicle,
            "km": 12 + index,
            "cargo_category": "BOXES_GENERAL",
            "cargo": "Synthetic QA parcels — no commercial shipment",
            "weight_kg": 10,
            "package_count": 1,
            "package_length_cm": 40,
            "package_width_cm": 30,
            "package_height_cm": 30,
            "payment": "protected",
            "excluded_charges_ack": True,
            "idempotency_key": key,
        }
        code, result = _req("POST", "/public/bookings", booking)
        token = result.get("tracking_token")
        ref = result.get("ref")
        accepted = (
            code == 200 and bool(token) and bool(ref)
            and result.get("status") == "REQUEST_RECEIVED"
            and result.get("protected_payment", {}).get("live_funds_enabled") is False
            and result.get("payment_preference", {}).get("charged") is False
        )
        step(f"{island} synthetic booking accepted with live funds off", accepted, f"got HTTP {code}")
        if not accepted:
            continue

        replay_code, replay = _req("POST", "/public/bookings", booking)
        step(
            f"{island} retry is idempotent",
            replay_code == 200 and replay.get("ref") == ref,
            f"got HTTP {replay_code}",
        )

        track_code, tracked = _req("GET", f"/public/bookings/track/{token}")
        step(
            f"{island} exact booking is publicly trackable",
            track_code == 200 and tracked.get("ref") == ref
            and tracked.get("customer_status") == "Request Received"
            and tracked.get("protected_payment", {}).get("live_funds_enabled") is False,
            f"got HTTP {track_code}",
        )
        created.append({"island": island, "ref": ref, "token": token})
        evidence["regions"].append({
            "island": island,
            "reference": ref,
            "tracking_token_sha256_prefix": _fingerprint(token),
            "idempotency_key_sha256_prefix": _fingerprint(key),
        })

    step("all three island-group bookings were created", len(created) == 3, f"created {len(created)}")

    if created:
        code, queue = _req("GET", "/admin/marketplace/public-booking-queue", token=bearer)
        requests = queue.get("requests", []) if isinstance(queue, dict) else []
        queue_tokens = {item.get("tracking_token") for item in requests if isinstance(item, dict)}
        for item in created:
            step(
                f"{item['island']} exact booking is present in canonical intake queue",
                code == 200 and item["token"] in queue_tokens,
                f"queue HTTP {code}; exact token absent",
            )

        # Independent reads prove hosted persistence across request boundaries. Process-restart
        # proof remains part of the separate recovery gate and is not overclaimed here.
        for item in created:
            track_code, tracked = _req("GET", f"/public/bookings/track/{item['token']}")
            step(
                f"{item['island']} booking persists across independent HTTP reads",
                track_code == 200 and tracked.get("ref") == item["ref"],
                f"got HTTP {track_code}",
            )

    evidence["passed"] = _ok
    evidence["failed"] = _fail
    evidence["result"] = "PASS" if _fail == 0 else "FAIL"
    _write_evidence(evidence)
    print("-" * 72)
    print(f"REGIONAL ACCEPTANCE: {evidence['result']} — {_ok} passed, {_fail} failed")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
