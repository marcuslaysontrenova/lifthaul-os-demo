#!/usr/bin/env python3
"""Offline LiftHaul agent handover validator. No provider execution or network.

A valid handover is evidence, not permission to run a paid provider.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
from fallback_policy import select_next, ORDER

SHA = re.compile(r"^[0-9a-f]{40}$")
SAFE_TASK = re.compile(r"^#[0-9]{1,7}$")
FAILURES = frozenset(("quota_exhausted", "rate_limit_exhausted"))

def validate_handover(record):
    if not isinstance(record, dict):
        raise ValueError("handover must be an object")
    required = ("issue", "base_sha", "branch", "last_provider", "failure",
                "completed", "remaining", "evidence_urls", "no_production_writes")
    if any(k not in record for k in required):
        raise ValueError("handover fields missing")
    if not SAFE_TASK.fullmatch(str(record["issue"])):
        raise ValueError("issue must be a repository issue number")
    if not SHA.fullmatch(str(record["base_sha"])):
        raise ValueError("base_sha must be exact 40-character hexadecimal")
    branch = str(record["branch"])
    if not branch.startswith(("chore/", "fix/", "codex/", "ai/")) or not re.fullmatch(r"[a-zA-Z0-9_./-]{5,100}", branch) or ".." in branch:
        raise ValueError("unapproved task branch")
    if record["last_provider"] not in ORDER or record["failure"] not in FAILURES:
        raise ValueError("handover requires classified quota failure")
    if record["no_production_writes"] is not True:
        raise ValueError("production mutations not permitted")
    for field in ("completed", "remaining", "evidence_urls"):
        if not isinstance(record[field], list) or len(record[field]) > 30 or any(not isinstance(x, str) or len(x) > 300 for x in record[field]):
            raise ValueError("invalid " + field)
    if not record["remaining"] or not record["evidence_urls"]:
        raise ValueError("missing actionable context/evidence")
    for url in record["evidence_urls"]:
        if not url.startswith("https://github.com/marcuslaysontrenova/lifthaul-os-demo/"):
            raise ValueError("external or untrusted evidence source")
    return True

def evaluate(record, enabled=(), budget_approved=False, runner_verified=False,
             owner_approved=False, kill_switch_off=False):
    validate_handover(record)
    decision = select_next(record["last_provider"], record["failure"], set(enabled))
    reason = "NO_VERIFIED_PROVIDER"
    selected = None
    if decision["decision"] == "handover":
        selected = decision["provider"]
        if not (budget_approved and runner_verified and owner_approved and kill_switch_off):
            reason = "BLOCKED_GOVERNANCE"
        else:
            reason = "READY_FOR_SEPARATE_AUTHORIZED_RUN"
    return {
        "issue": record["issue"], "base_sha": record["base_sha"],
        "selected_provider": selected, "status": reason,
        "provider_invoked": False, "production_changed": False,
        "handover_fingerprint": hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16],
    }

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--handover", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    record = json.loads(Path(args.handover).read_text(encoding="utf-8"))
    # Offline CI never accepts environment secrets or approval flags.
    result = evaluate(record)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0  # dry-run evidence is valid even when the actual connection is blocked

if __name__ == "__main__":
    raise SystemExit(main())
