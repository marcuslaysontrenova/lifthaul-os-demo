#!/usr/bin/env python3
"""Offline fallback policy. Never starts provider processes or spends money."""
import argparse
import json

ORDER = ("codex", "claude", "gemini")
QUOTA_CODES = {"quota_exhausted", "rate_limit_exhausted"}
BLOCK_CODES = {"auth_error", "test_failed", "unknown_error", "billing_error", "policy_denied", "ci_failed"}

def select_next(current, failure, enabled):
    if current not in ORDER:
        raise ValueError("unknown current provider")
    if failure not in QUOTA_CODES | BLOCK_CODES:
        return {"decision": "stop", "reason": "unclassified_error"}
    if failure not in QUOTA_CODES:
        return {"decision": "stop", "reason": failure}
    for target in ORDER[ORDER.index(current) + 1:]:
        if target in enabled:
            return {"decision": "handover", "provider": target, "reason": failure}
    return {"decision": "stop", "reason": "no_verified_available_fallback"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--current", choices=ORDER, required=True)
    ap.add_argument("--failure", required=True)
    ap.add_argument("--enabled", nargs="*", default=[])
    args = ap.parse_args()
    # Explicit enabled providers only; never infer credentials from ambient environment.
    if set(args.enabled) - set(ORDER):
        ap.error("unknown enabled provider")
    print(json.dumps(select_next(args.current, args.failure, set(args.enabled)), sort_keys=True))

if __name__ == "__main__":
    main()
