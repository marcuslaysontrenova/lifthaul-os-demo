#!/usr/bin/env python3
"""Fail-closed Gate 5/6 evidence index checker. No human signature is self-certified.

This checker assesses supplied, sanitized evidence references and exact identity.
Operator/witness/review attestation references must be verified independently by
the accountable release controller; user-editable text is NEVER sufficient.
"""
import argparse
import json
import re
from pathlib import Path

SHA = re.compile(r"^[a-f0-9]{40}$")
G5 = (
    "independent_human_review", "exact_release_authorization", "deployed_identity",
    "protected_personas_attested", "primary_personas_11", "pending_applicant_denial",
    "browser_authenticated_navigation", "browser_direct_route_denial",
    "rbac_cross_role", "carrier_tenant_isolation", "admin_tenant_scope",
    "direct_object_denial", "finance_admin_boundary", "ten_session_revocations",
    "supplemental_session_cleanup", "eight_enforced_capabilities_off",
    "no_live_payment_dispatch", "audit_admin_qa_attestation", "auth_email_evidence_or_approved_exception",
    "qa_human_signoff", "security_human_signoff", "release_human_signoff",
)
G6 = (
    "approved_isolated_target", "approved_backup", "data_handling_approved",
    "provider_restore_evidence", "schema_tables_indexes_integrity",
    "recovered_application_readiness", "recovery_auth_tenant_isolation",
    "callbacks_payments_dispatch_off", "measured_rpo_rto_with_provenance",
    "rpo_rto_within_objectives", "alert_delivered", "incident_human_acknowledged",
    "incident_escalation_resolution", "rollback_evidence", "rollforward_evidence",
    "operations_human_signoff", "independent_recovery_witness_signoff",
)
HUMAN = {x for x in G5 + G6 if any(k in x for k in ("human_", "approved", "authorization", "attestation", "independent_"))}
# Evidence is a machine-readable index of reviewed artifacts, not a signatory.
# All HUMAN items remain BLOCKED until independently checked by the operator.
def validate(data, release_sha):
    if not isinstance(data, dict) or not SHA.fullmatch(release_sha):
        return {"result": "FAIL", "reason": "INVALID_INPUT"}
    identity = data.get("identity") or {}
    if identity.get("release_sha") != release_sha or not identity.get("deployment_id"):
        return {"result": "BLOCKED", "reason": "BLOCKED_RELEASE_IDENTITY_MISMATCH"}
    items = data.get("criteria") or {}
    output = {}
    for gate, required in (("gate5", G5), ("gate6", G6)):
        results = {}
        for key in required:
            entry = items.get(key)
            status = entry.get("status") if isinstance(entry, dict) else None
            ref = entry.get("evidence_ref") if isinstance(entry, dict) else None
            bound = entry.get("release_sha") if isinstance(entry, dict) else None
            if status == "FAIL":
                results[key] = "FAIL"
            elif status == "PASS" and isinstance(ref, str) and ref.strip() and bound == release_sha:
                # A submitted approval reference is unverified until an independent
                # controller checks it against GitHub/provider/attestation source.
                if key in HUMAN or key.endswith("_signoff") or "acknowledged" in key:
                    results[key] = "BLOCKED"
                else:
                    results[key] = "PASS"
            else:
                results[key] = "BLOCKED"
        output[gate] = {"result": "FAIL" if "FAIL" in results.values() else
                                  "PASS" if all(x == "PASS" for x in results.values()) else "BLOCKED",
                        "criteria": results}
    return {"schema": "lifthaul.gate-closure-index.v1", "release_sha": release_sha,
            "deployment_id": identity["deployment_id"], "gates": output,
            "note": "Human approvals require external verification; index cannot authorize release or gate closure."}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--release-sha", required=True)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    try:
        result = validate(json.loads(args.evidence.read_text()), args.release_sha)
    except (OSError, ValueError, UnicodeError):
        result = {"result": "BLOCKED", "reason": "MISSING_OR_INVALID_EVIDENCE"}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 1 if result.get("result") in ("FAIL", "BLOCKED") or any(g["result"] != "PASS" for g in result.get("gates", {}).values()) else 0

if __name__ == "__main__":
    raise SystemExit(main())
