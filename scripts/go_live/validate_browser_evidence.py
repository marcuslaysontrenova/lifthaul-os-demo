"""Browser acceptance evidence must bind to a witnessed authenticated session.

This offline verifier NEVER calls the network, reads credentials, or considers
editable observations a human attestation. Run an independently approved
Playwright browser session using existing repository conventions.
"""
import re
SHA = re.compile(r"^[a-f0-9]{40}$")
CHECKS = ("authenticated_landing", "role_navigation", "direct_route_denied",
          "tenant_boundary", "session_cleanup")
def validate(record, sha, deployment_id):
    if not SHA.fullmatch(sha) or not deployment_id:
        return {"result":"FAIL","reason":"INVALID_EXPECTED_IDENTITY"}
    if not isinstance(record,dict):
        return {"result":"BLOCKED","reason":"MISSING_BROWSER_EVIDENCE"}
    if any(record.get(k)!=v for k,v in (
        ("release_sha",sha),("deployment_id",deployment_id),
        ("release_sha_after",sha),("deployment_id_after",deployment_id))):
        return {"result":"BLOCKED","reason":"RELEASE_IDENTITY_UNSTABLE"}
    checks=record.get("checks")
    checks=checks if isinstance(checks,dict) else {}
    results={}
    for key in CHECKS:
        item=checks.get(key)
        state=item.get("status") if isinstance(item,dict) else None
        # A browser-generated result must be linked to an independently
        # inspected artifact; typed flags alone cannot pass.
        ref=item.get("artifact_reference") if isinstance(item,dict) else None
        if state=="FAIL":
            results[key]="FAIL"
        elif state=="PASS" and isinstance(ref,str) and ref.startswith("github-actions-run:") and ref[19:].isdigit():
            results[key]="BLOCKED"  # requires source and artifact verification
        else:
            results[key]="BLOCKED"
    return {"result":"FAIL" if "FAIL" in results.values() else "BLOCKED",
            "checks":results, "witness_verification":"PENDING"}
