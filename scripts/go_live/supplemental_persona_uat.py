#!/usr/bin/env python3
"""GET-only security supplement using separately approved synthetic fixtures.

Uses the existing protected 11-persona manifest. Four rows additionally need an
object_path: booker (/portal/client/bookings/ID), accredited_driver
(/driver/trips/ID), motorcycle_owner and truck_owner
(/admin/carrier-portal/overview/ID). No IDs, bodies or credentials are emitted.
The caller must approve the exact deployed release. Missing runtime safety
signals remain BLOCKED, never inferred from this runner making no mutations.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

try:
    from . import prod_persona_uat as primary
except ImportError:
    import prod_persona_uat as primary

OBJECT_PATTERNS = {
    "booker": r"/portal/client/bookings/[1-9][0-9]*",
    "accredited_driver": r"/driver/trips/[1-9][0-9]*",
    "motorcycle_owner": r"/admin/carrier-portal/overview/[1-9][0-9]*",
    "truck_owner": r"/admin/carrier-portal/overview/[1-9][0-9]*",
}
SAFETY_FIELDS = (
    "live_dispatch_enabled", "live_collection_enabled", "live_refunds_enabled",
    "live_payouts_enabled", "live_settlement_enabled",
    "live_escrow_disbursement_enabled", "automatic_assignment_enabled",
    "external_financial_provider_enabled",
)


class Blocked(Exception):
    """A prerequisite is not available; messages must be fixed non-secret codes."""


def execute(manifest, expected_sha, call=primary.http_call):
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise Blocked("BLOCKED_RELEASE_IDENTITY_MISMATCH")
    # Validate all fixture paths before authentication or any object access.
    paths = {}
    for persona, pattern in OBJECT_PATTERNS.items():
        path = manifest[persona].get("object_path", "")
        if not isinstance(path, str) or not re.fullmatch(pattern, path):
            raise Blocked("BLOCKED_APPROVED_SYNTHETIC_OBJECT_FIXTURES")
        paths[persona] = path
    if paths["motorcycle_owner"] == paths["truck_owner"]:
        raise Blocked("BLOCKED_APPROVED_SYNTHETIC_OBJECT_FIXTURES")
    status, ready = call("GET", "/readyz")
    if (status != 200 or ready.get("status") != "ready"
            or ready.get("release_sha") != expected_sha
            or not ready.get("deployment_id")):
        raise Blocked("BLOCKED_RELEASE_IDENTITY_MISMATCH")
    checks, tokens = {}, {}
    try:
        for persona in (*OBJECT_PATTERNS, "finance", "administrator"):
            row = manifest[persona]
            status, login = call("POST", "/login", {"email": row["email"], "password": row["password"]})
            if status != 200 or not login.get("token"):
                raise Blocked("BLOCKED_HUMAN_IDENTITY_SETUP")
            tokens[persona] = login["token"]
            status, me = call("GET", "/me/permissions", bearer=tokens[persona])
            if status != 200 or me.get("role") not in primary.ROLE_RULES[persona]:
                raise AssertionError("ROLE_BOUNDARY_FAILED")
        # Positive control prevents a nonexistent object / blanket 404 from passing.
        for persona, path in paths.items():
            status, body = call("GET", path, bearer=tokens[persona])
            if status != 200 or not primary._contains(body, manifest[persona]["marker"]):
                raise Blocked("BLOCKED_APPROVED_SYNTHETIC_OBJECT_FIXTURES")
        checks["owned_object_positive_controls"] = "PASS"
        # Cross-role IDOR: existing client booking must be unavailable to a driver,
        # and existing driver trip must be unavailable to a booker.
        for owner, denied in (("booker", "accredited_driver"), ("accredited_driver", "booker")):
            status, _ = call("GET", paths[owner], bearer=tokens[denied])
            if status not in (403, 404):
                raise AssertionError("DIRECT_OBJECT_DENIAL_FAILED")
        checks["direct_object_cross_role_denial"] = "PASS"
        # Carrier resolver deliberately ignores foreign IDs for bound principals.
        # HTTP 200 is acceptable only when it contains OWN data and no foreign marker.
        for owner, denied in (("motorcycle_owner", "truck_owner"), ("truck_owner", "motorcycle_owner")):
            status, body = call("GET", paths[owner], bearer=tokens[denied])
            if status == 200:
                if (not primary._contains(body, manifest[denied]["marker"])
                        or primary._contains(body, manifest[owner]["marker"])):
                    raise AssertionError("CROSS_TENANT_OBJECT_LEAK")
            elif status not in (403, 404):
                raise AssertionError("CROSS_TENANT_OBJECT_DENIAL_FAILED")
        checks["same_role_cross_tenant_object_isolation"] = "PASS"
        # These are direct protected API URLs, not public HTML shell availability.
        status, _ = call("GET", "/admin/users")
        if status not in (401, 403):
            raise AssertionError("ANONYMOUS_DIRECT_URL_DENIAL_FAILED")
        for persona in (*OBJECT_PATTERNS, "finance"):
            status, _ = call("GET", "/admin/users", bearer=tokens[persona])
            if status != 403:
                raise AssertionError("ADMINISTRATOR_BOUNDARY_FAILED")
        status, _ = call("GET", "/admin/users", bearer=tokens["administrator"])
        if status != 200:
            raise AssertionError("ADMINISTRATOR_POSITIVE_CONTROL_FAILED")
        checks["direct_api_url_administrator_boundary"] = "PASS"
        for persona in OBJECT_PATTERNS:
            status, _ = call("GET", "/admin/marketplace/protected-payment-live-status", bearer=tokens[persona])
            if status != 403:
                raise AssertionError("FINANCE_BOUNDARY_FAILED")
        checks["finance_role_restrictions"] = "PASS"
        status, safety = call("GET", "/admin/marketplace/protected-payment-live-status", bearer=tokens["finance"])
        if (status != 200 or safety.get("LIVE_PROTECTED_FUNDS_ENABLED") is not False
                or safety.get("live_protected_payment") != "BLOCKED"):
            raise AssertionError("LIVE_FUNDS_NOT_PROVEN_DISABLED")
        checks["protected_funds_master_switch_off"] = "PASS"
        # Read the shared enforcement interlock as administrator, never finance.
        for persona in (*OBJECT_PATTERNS, "finance"):
            denied, _ = call("GET", "/admin/security/operational-safety", bearer=tokens[persona])
            if denied != 403:
                raise AssertionError("SAFETY_STATUS_ACCESS_DENIAL_FAILED")
        code, observed = call("GET", "/admin/security/operational-safety", bearer=tokens["administrator"])
        if code != 200:
            raise Blocked("BLOCKED_GATE5_SAFETY_OBSERVABILITY")
        if (observed.get("release_sha") != expected_sha
                or observed.get("deployment_id") != ready["deployment_id"]):
            raise Blocked("BLOCKED_RELEASE_IDENTITY_MISMATCH")
        if (observed.get("schema") != "lifthaul.operational-safety.v1"
                or observed.get("enforcement") != "shared_process_interlock_v1"
                or not observed.get("observed_at")):
            raise Blocked("BLOCKED_GATE5_SAFETY_OBSERVABILITY")
        checks["safety_status_role_restrictions"] = "PASS"
        missing = False
        safety = observed.get("capabilities")
        if not isinstance(safety, dict):
            raise Blocked("BLOCKED_GATE5_SAFETY_OBSERVABILITY")
        for field in SAFETY_FIELDS:
            if field not in safety or safety[field] is None:
                checks[field] = "BLOCKED"
                missing = True
            elif safety[field] is not False:
                raise AssertionError("LIVE_ACTION_NOT_PROVEN_DISABLED")
            else:
                checks[field] = "PASS"
        if observed.get("result") != "PASS":
            missing = True
        status, after = call("GET", "/readyz")
        if (status != 200 or after.get("status") != "ready"
                or after.get("release_sha") != expected_sha
                or after.get("deployment_id") != ready["deployment_id"]):
            raise Blocked("BLOCKED_RELEASE_IDENTITY_MISMATCH")
        return {"schema": "lifthaul.gate5.security-supplement.v1",
                "result": "BLOCKED" if missing else "PASS",
                "blocker": "BLOCKED_GATE5_SAFETY_OBSERVABILITY" if missing else None,
                "release_sha": expected_sha, "deployment_id": ready["deployment_id"],
                "checks": checks, "safety_observed_at": observed["observed_at"],
                "independent_signoff": "PENDING"}
    finally:
        failed = False
        for token in tokens.values():
            try:
                logout, _ = call("POST", "/logout", {}, bearer=token)
                stale, _ = call("GET", "/me/permissions", bearer=token)
                failed |= logout not in (200, 204) or stale not in (401, 403)
            except Exception:
                failed = True
        if failed:
            raise AssertionError("SUPPLEMENTAL_SESSION_REVOCATION_FAILED")


def main():
    try:
        try:
            manifest = primary.load_manifest(os.environ.get("LH_PROD_PERSONA_CREDENTIALS_JSON", ""))
        except Exception:
            raise Blocked("BLOCKED_HUMAN_IDENTITY_SETUP") from None
        evidence = execute(manifest, os.environ.get("EXPECTED_SHA", ""))
    except Blocked as exc:
        evidence = {"result": "BLOCKED", "blocker": str(exc)}
    except Exception:
        # Never expose server errors, fixture paths, tokens or credential content.
        evidence = {"result": "FAIL", "reason": "SUPPLEMENTAL_SECURITY_CHECK_FAILED"}
    path = Path(os.environ.get("SUPPLEMENTAL_OUTPUT_PATH", "evidence/production-persona-security.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    print("Gate 5 supplementary result: " + evidence["result"])
    return 0 if evidence["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
