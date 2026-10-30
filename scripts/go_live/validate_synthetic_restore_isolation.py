#!/usr/bin/env python3
"""Offline safety contract for a FUTURE synthetic PostgreSQL recovery drill.

No connections, subprocesses, imports of DB clients, file access or database
modification. A PASS only confirms that supplied endpoints describe separate
allowlisted ephemeral CI databases. It does not authorize or execute a restore,
nor does it provide production backup/RTO/RPO evidence.
"""
import argparse
import json
from urllib.parse import urlsplit

ALLOWED_HOSTS = {"localhost", "127.0.0.1"}
SOURCE_NAME = "lifthaul_ci_source"
TARGET_NAME = "lifthaul_ci_restore"
CI_USER = "lifthaul"


def assess_isolation(source_url, target_url, *, github_actions=False, synthetic_opt_in=False):
    result = {
        "check": "synthetic_pg_restore_isolation",
        "result": "BLOCKED_ENVIRONMENT_ISOLATION",
        "source_label": SOURCE_NAME,
        "target_label": TARGET_NAME,
        "no_network_or_database_operations": True,
        "hosted_backup_verified": False,
        "hosted_restore_verified": False,
        "production_rto_verified": False,
        "production_rpo_verified": False,
        "mvp_recovery_gate_complete": False,
    }
    if not github_actions or not synthetic_opt_in:
        result["reason"] = "CI_CONTEXT_OR_EXPLICIT_OPT_IN_MISSING"
        return result

    def identify(url):
        try:
            parsed = urlsplit(url or "")
            return (
                parsed.scheme in ("postgres", "postgresql")
                and parsed.hostname in ALLOWED_HOSTS
                and parsed.port == 5432
                and parsed.username == CI_USER
                and parsed.path in ("/" + SOURCE_NAME, "/" + TARGET_NAME)
                and parsed.query == ""
                and parsed.fragment == ""
            ), parsed.path[1:]
        except (ValueError, TypeError, AttributeError):
            return False, None

    source_ok, source_name = identify(source_url)
    target_ok, target_name = identify(target_url)
    if not source_ok or not target_ok:
        result["reason"] = "NON_LOCAL_OR_INVALID_DATABASE_ENDPOINT"
    elif source_name != SOURCE_NAME or target_name != TARGET_NAME:
        result["reason"] = "SOURCE_TARGET_IDENTITIES_INVALID"
    elif source_url == target_url:
        result["reason"] = "SOURCE_EQUALS_TARGET"
    else:
        result["result"] = "PASS_OFFLINE_ISOLATION_METADATA_ONLY"
        result["reason"] = "SAFE_ENDPOINT_SHAPES_ONLY"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--ci-context", action="store_true")
    parser.add_argument("--synthetic-opt-in", action="store_true")
    args = parser.parse_args(argv)
    report = assess_isolation(args.source_url, args.target_url,
                              github_actions=args.ci_context,
                              synthetic_opt_in=args.synthetic_opt_in)
    # Only generic diagnostics are printed; never raw URLs or passwords.
    print(json.dumps(report, sort_keys=True))
    return 0 if report["result"] == "PASS_OFFLINE_ISOLATION_METADATA_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
