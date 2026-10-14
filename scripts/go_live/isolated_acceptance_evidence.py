#!/usr/bin/env python3
"""Fail-closed, no-secret summary of isolated LiftHaul MVP acceptance jobs.

This script ONLY records results of GitHub-hosted, disposable-data test suites.
It cannot certify live Railway identity, human persona UAT, email delivery,
production recovery, compliance signoff, or real-money operations.
"""
import json
import os
from pathlib import Path
import re
import sys

SUITES = (
    "client_identity_and_workspace",
    "carrier_and_driver_access",
    "staff_finance_founder_authority",
    "booking_tracking_and_live_funds_off",
    "qa_permission_and_email_contract",
)
STATUS = {"success", "failure", "cancelled", "skipped", "timed_out", "neutral"}


def evidence_for(results, sha):
    if not re.fullmatch(r"[0-9a-fA-F]{40}", str(sha)):
        raise ValueError("Exact 40-character test commit SHA required")
    if set(results) != set(SUITES) or any(value not in STATUS for value in results.values()):
        raise ValueError("Missing or unsupported job outcomes")
    passed = all(results[key] == "success" for key in SUITES)
    return {
        "schema": "lifthaul-isolated-mvp-evidence-v1",
        "candidate_sha": sha.lower(),
        "environment": "ephemeral_github_ci_only",
        "result": "PASS_ISOLATED_SUITES_ONLY" if passed else "FAILED_OR_INCOMPLETE",
        "suites": {key: results[key] for key in SUITES},
        "synthetic_data_only": True,
        "hosted_production_qa_verified": False,
        "hosted_database_recovery_verified": False,
        "authentication_email_delivery_verified": False,
        "independent_review_signed": False,
        "founder_commercial_policy_signed": False,
        "external_payment_certification": False,
        "live_funds_exercised": False,
        "mvp_no_live_funds_go": False,
        "production_go": False,
    }


def main():
    results = {key: os.environ.get("LH_SUITE_" + key.upper(), "") for key in SUITES}
    try:
        record = evidence_for(results, os.environ.get("LH_CANDIDATE_SHA", ""))
    except ValueError:
        # Invalid/untrusted CI identity must produce no artifact or false PASS.
        print("FAIL: incomplete acceptance job status or candidate identity")
        return 2
    path = Path(os.environ.get("LH_ISOLATED_EVIDENCE_PATH", "evidence/isolated-mvp-acceptance.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, sort_keys=True))
    return 0 if record["result"] == "PASS_ISOLATED_SUITES_ONLY" else 1


if __name__ == "__main__":
    sys.exit(main())
