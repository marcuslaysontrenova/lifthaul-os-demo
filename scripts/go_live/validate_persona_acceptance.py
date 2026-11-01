#!/usr/bin/env python3
"""Fail-closed verifier for exact-release, no-live-funds Gate 5 UAT artifacts.

This file must exist in the CHECKED-OUT RELEASE. An older deployed commit
that lacks the hardened acceptance implementation cannot produce Gate 5 PASS.
Never print credentials, response bodies, synthetic markers or fingerprints.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PERSONAS = (
    "booker", "driver_applicant", "accredited_driver", "motorcycle_owner",
    "light_vehicle_owner", "truck_owner", "fleet_owner", "specialized_provider",
    "operations", "finance", "administrator",
)
REVOCATION_CONTROL = "logout_http_200_or_204_and_stale_bearer_401_or_403"


def valid(evidence: object, release_sha: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
        return False
    if not isinstance(evidence, dict):
        return False
    if (evidence.get("schema") != "lifthaul.production-persona-uat.v1"
            or evidence.get("result") != "PASS"
            or evidence.get("expected_release_sha") != release_sha
            or evidence.get("observed_release_sha") != release_sha
            or not isinstance(evidence.get("deployment_id"), str)
            or not evidence["deployment_id"].strip()
            or evidence.get("schema_version") in (None, "", 0)
            or evidence.get("personas_required") != 11
            or evidence.get("personas_passed") != 11
            or evidence.get("sessions_revoked") != 10
            or evidence.get("revocation_control") != REVOCATION_CONTROL
            or evidence.get("external_persona_fixture_isolation") != "PASS"
            or evidence.get("live_funds_enabled") is not False):
        return False
    results = evidence.get("results")
    if not isinstance(results, list) or len(results) != 11:
        return False
    result_by_persona = {}
    for entry in results:
        if not isinstance(entry, dict):
            return False
        persona = entry.get("persona")
        if persona not in PERSONAS or persona in result_by_persona:
            return False
        if entry.get("result") != "PASS":
            return False
        expected_control = ("login_pending" if persona == "driver_applicant"
                            else "login_role_scope_fixture")
        if entry.get("control") != expected_control:
            return False
        result_by_persona[persona] = entry
    if set(result_by_persona) != set(PERSONAS):
        return False
    accounts = evidence.get("accounts")
    if not isinstance(accounts, list) or len(accounts) != 11:
        return False
    fingerprints = {}
    for entry in accounts:
        if not isinstance(entry, dict):
            return False
        persona = entry.get("persona")
        fp = entry.get("account_fingerprint")
        if persona not in PERSONAS or persona in fingerprints:
            return False
        if not isinstance(fp, str) or not re.fullmatch(r"[0-9a-f]{12}", fp):
            return False
        fingerprints[persona] = fp
    return set(fingerprints) == set(PERSONAS) and len(set(fingerprints.values())) == 11


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    args = parser.parse_args()
    try:
        proof = json.loads(args.evidence.read_text(encoding="utf-8"))
        accepted = valid(proof, args.release_sha)
    except (OSError, UnicodeError, ValueError):
        accepted = False
    print("Gate 5 exact-release evidence " + ("ACCEPTED" if accepted else "REJECTED"))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
