#!/usr/bin/env python3
"""Interactive hosted Gate 5 UAT without a GitHub credential-manifest secret.

Uses the same persona contract as production-persona-uat.yml, but enters 11
existing approved synthetic account credentials privately on the operator's
own terminal. The session lives only in process memory. This is NOT an account
provisioner, impersonation mechanism, production seeder, or release approver.

No real customers, dispatches, payments, refunds, or payouts are exercised.
Only run with explicit operator authorization for production UAT.
"""
from __future__ import annotations

import argparse
import getpass
import json
import pathlib
import re
import sys
import subprocess

try:
    from . import prod_persona_uat as gate5
    from . import supplemental_persona_uat as supplement
except ImportError:
    import prod_persona_uat as gate5
    import supplemental_persona_uat as supplement


APPROVAL_PHRASE = "I AUTHORIZE READ-ONLY HOSTED PERSONA UAT"


def collect_existing_accounts(secret_fn):
    """Private prompt for real existing synthetic identities; no disk or logs."""
    manifest = {}
    for persona in gate5.PERSONAS:
        print("Approved synthetic role: " + persona)
        manifest[persona] = {
            "email": secret_fn("Existing account email (hidden): ").strip(),
            "password": secret_fn("Existing account password (hidden): "),
            "marker": secret_fn("Existing owned fixture marker (hidden): ").strip(),
        }
    # Four pre-approved, existing objects are required for supplementary IDOR tests.
    # Never print paths: IDs may disclose fixture relationships.
    for persona in supplement.OBJECT_PATTERNS:
        manifest[persona]["object_path"] = secret_fn("Existing approved object path (hidden): ").strip()
    validated = gate5.load_manifest(json.dumps(manifest))
    for persona, pattern in supplement.OBJECT_PATTERNS.items():
        if not re.fullmatch(pattern, manifest[persona]["object_path"]):
            raise supplement.Blocked("BLOCKED_APPROVED_SYNTHETIC_OBJECT_FIXTURES")
        validated[persona]["object_path"] = manifest[persona]["object_path"]
    return validated


def run(args, *, confirm=input, secret_prompt=getpass.getpass, call=None,
        terminal_ok=None, output_path=None):
    if not args.execute:
        print("SAFE MODE: No credentials collected, no production requests.")
        print("Use --execute --expected-sha <40-hex> --approval-reference <ticket>")
        print("only after a named operator has authorized the hosted test.")
        return 0
    if terminal_ok is None:
        terminal_ok = sys.stdin.isatty() and sys.stdout.isatty()
    if not terminal_ok:
        print("Refused: production credentials require a private interactive terminal.", file=sys.stderr)
        return 2
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_sha or ""):
        print("Refused: specify exact 40-character deployed commit SHA.", file=sys.stderr)
        return 2
    if not args.approval_reference or len(args.approval_reference.strip()) < 5:
        print("Refused: a specific approved UAT change/ticket reference is mandatory.", file=sys.stderr)
        return 2

    effective_call = call if call is not None else gate5.http_call
    status, observed = effective_call("GET", "/readyz")
    if status != 200 or observed.get("status") != "ready":
        print("Refused: canonical production health is not ready.", file=sys.stderr)
        return 2
    if observed.get("release_sha") != args.expected_sha:
        print("Refused: observed deployed SHA does not match approved SHA.", file=sys.stderr)
        return 2
    if not observed.get("deployment_id") or not observed.get("schema_version"):
        print("Refused: deployment identity is incomplete.", file=sys.stderr)
        return 2
    print("Verified deployed SHA: " + args.expected_sha)
    print("Approval reference: " + args.approval_reference.strip())
    print("This will log in and log out 11 approved synthetic accounts.")
    print("A pending driver must be denied access; live funds stay disabled.")
    if confirm("Type 'I AUTHORIZE READ-ONLY HOSTED PERSONA UAT': ").strip() != APPROVAL_PHRASE:
        print("Cancelled; no persona credentials collected.")
        return 2

    manifest = {}
    try:
        manifest = collect_existing_accounts(secret_prompt)
        # Existing protected runner performs actual permission/fixture verification,
        # denial checks, server-side logout and ten stale-bearer denials.
        evidence = gate5.execute(manifest, args.expected_sha, call=effective_call)
        if (evidence.get("result") != "PASS" or evidence.get("personas_passed") != 11
                or evidence.get("sessions_revoked") != 10
                or evidence.get("live_funds_enabled") is not False
                or evidence.get("external_persona_fixture_isolation") != "PASS"):
            raise AssertionError("full Gate 5 hosted acceptance contract was not satisfied")
        supplemental = supplement.execute(manifest, args.expected_sha, call=effective_call)
        if supplemental.get("result") != "PASS":
            raise supplement.Blocked(supplemental.get("blocker") or "BLOCKED_GATE5_SUPPLEMENTARY_EVIDENCE")
        if supplemental.get("deployment_id") != observed["deployment_id"]:
            raise supplement.Blocked("BLOCKED_RELEASE_IDENTITY_MISMATCH")
        evidence["security_supplement"] = supplemental
        evidence["browser_journeys"] = "BLOCKED_WITNESSED_BROWSER_EVIDENCE"
        if getattr(args, "browser", False):
            # Credentials are supplied to the local child process through a
            # private pipe, never argv, environment, logs or temporary files.
            runner = pathlib.Path(__file__).resolve().parents[2] / "ci/browser/operator-hosted-acceptance.js"
            try:
                child = subprocess.run(
                    ["node", str(runner), "--execute"],
                    input=json.dumps({"expected_sha": args.expected_sha,
                                      "approval_reference": args.approval_reference,
                                      "manifest": manifest}),
                    text=True, capture_output=True, timeout=240, check=False)
                data = json.loads(child.stdout)
                if data.get("release_sha") == args.expected_sha and data.get("deployment_id") == observed["deployment_id"]:
                    # Runner deliberately does not self-certify browser navigation.
                    evidence["browser_journeys"] = data.get("result", "BLOCKED")
                else:
                    evidence["browser_journeys"] = "BLOCKED_RELEASE_IDENTITY_MISMATCH"
            except Exception:
                evidence["browser_journeys"] = "BLOCKED_BROWSER_RUNTIME_OR_DEPENDENCY"

        evidence["administrator_tenant_scope"] = "BLOCKED_WITNESSED_ADMIN_SCOPE"
        evidence["audit_qa_identity_attestation"] = "PENDING"
        evidence["authentication_email_evidence_or_approved_limitation"] = "PENDING"
        evidence["security_signoff"] = "PENDING"
        evidence["release_owner_signoff"] = "PENDING"
        evidence["gate5_closure"] = "BLOCKED"
        evidence["execution_channel"] = "authorized_private_interactive_operator"
        evidence["approval_reference"] = args.approval_reference.strip()
        # No approved QA review is claimed merely because an operator ran tests.
        evidence["independent_qa_signoff"] = "PENDING"
    except Exception:
        # Fail closed; don't expose a response body, token, account, fixture or
        # unexpected exception text from the production server.
        print("BLOCKED_OR_FAILED: hosted persona contract or credential validation failed.",
              file=sys.stderr)
        return 1
    finally:
        manifest.clear()

    output = pathlib.Path(output_path if output_path is not None else args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    try:
        output.chmod(0o600)
    except OSError:
        pass
    print("Primary and supplementary API checks PASS; full Gate 5 remains BLOCKED pending browser, admin scope, auth-email and human approvals.")
    print("This is NOT automatic Gate 5 closure or production release approval.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true",
                        help="Run optional local private-input Playwright browser checks; missing dependencies BLOCKED")
    parser.add_argument("--execute", action="store_true",
                        help="Run authorized hosted persona UAT (default is safe mode)")
    parser.add_argument("--expected-sha", default="")
    parser.add_argument("--approval-reference", default="")
    parser.add_argument("--output", default="evidence/gate5-interactive-hosted-uat.json")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
