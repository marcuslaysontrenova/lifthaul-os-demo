#!/usr/bin/env python3
"""LiftHaul production preflight: independent public and privileged audits.

Only GET requests, an unauthenticated access probe, and session login/logout are
made. Never books, dispatches, changes configuration, or touches funds.

  python scripts/go_live/preflight.py --scope public
  LH_ADMIN_EMAIL=... LH_ADMIN_PASSWORD=... python scripts/go_live/preflight.py --scope admin
  python scripts/go_live/preflight.py                  # both (fail closed)

Each scope writes non-secret JSON evidence when LH_PREFLIGHT_EVIDENCE_PATH is set.
A scope-specific exit 0 is NOT complete MVP or full-preflight acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8787").rstrip("/")
EXPECTED_SHA = os.environ.get("EXPECTED_SHA", "").strip()
EXPECTED_DEPLOYMENT_ID = os.environ.get("EXPECTED_DEPLOYMENT_ID", "").strip()
ADMIN_EMAIL = os.environ.get("LH_ADMIN_EMAIL", "").strip()
ADMIN_PW = os.environ.get("LH_ADMIN_PASSWORD", "")
EVIDENCE_PATH = os.environ.get("LH_PREFLIGHT_EVIDENCE_PATH", "").strip()
UNTRUSTED_ORIGIN = "https://untrusted-preflight.invalid"
CONTROLLED_FLAGS = (
    "marketplace.ltfrb_enforcement_enabled",
    "payments.live_protected_funds_enabled",
    "payments.licensed_provider_active",
    "insurance.provider_active",
    "delivery.messaging_provider_active",
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward credentials or bearer headers to any redirect destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect())


def _req(method, path, body=None, token=None, origin=None):
    headers = {"Accept": "application/json", "User-Agent": "LiftHaul-Preflight/2.0"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    if origin is not None:
        headers["Origin"] = origin
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with OPENER.open(req, timeout=20) as response:
            status = response.status
            raw = response.read(512 * 1024)
            response_headers = dict(response.headers)
    except urllib.error.HTTPError as error:
        status = error.code
        raw = error.read(4096)
        response_headers = dict(error.headers)
    except Exception:
        return 0, {}, {}
    try:
        payload = json.loads(raw) if raw else {}
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            payload = payload["data"]
    except (ValueError, UnicodeDecodeError):
        payload = {}
    return status, payload if isinstance(payload, dict) else {}, response_headers


def _check(evidence, name, passed):
    outcome = "PASS" if passed else "FAIL"
    evidence["checks"].append({"name": name, "result": outcome})
    print(f"  [{outcome}] {name}")
    return passed


def _identity_checks(evidence, ready_status, ready):
    valid = _check(
        evidence, "readyz reports ready and schema",
        ready_status == 200 and ready.get("status") == "ready"
        and ready.get("schema_version") is not None,
    )
    evidence["observed_release_sha"] = ready.get("release_sha")
    evidence["observed_deployment_id"] = ready.get("deployment_id")
    evidence["schema_version"] = ready.get("schema_version")
    isolated_local_ci = (
        BASE in ("http://localhost:8787", "http://127.0.0.1:8787")
        and os.environ.get("LIFTHAUL_CI", "").lower() == "true"
    )
    valid = _check(
        evidence, "hosted release SHA and deployment ID pinned",
        isolated_local_ci or bool(EXPECTED_SHA and EXPECTED_DEPLOYMENT_ID),
    ) and valid
    if EXPECTED_SHA:
        valid = _check(evidence, "exact release SHA", ready.get("release_sha") == EXPECTED_SHA) and valid
    if EXPECTED_DEPLOYMENT_ID:
        valid = _check(
            evidence, "exact deployment ID", ready.get("deployment_id") == EXPECTED_DEPLOYMENT_ID
        ) and valid
    return valid


def _public(evidence):
    status, health, _ = _req("GET", "/healthz")
    _check(evidence, "healthz reports ok", status == 200 and health.get("status") == "ok")

    status, ready, _ = _req("GET", "/readyz")
    _identity_checks(evidence, status, ready)

    local_ci = os.environ.get("LIFTHAUL_CI", "").lower() == "true" and BASE in (
        "http://localhost:8787", "http://127.0.0.1:8787",
    )
    _check(evidence, "HTTPS endpoint (local CI exception only)", BASE.startswith("https://") or local_ci)

    status, _, _ = _req("GET", "/admin/marketplace/public-booking-queue")
    _check(evidence, "anonymous intake-queue access denied", status in (401, 403))

    cors_status, _, headers = _req("GET", "/healthz", origin=UNTRUSTED_ORIGIN)
    acao = next((str(v) for k, v in headers.items()
                 if k.lower() == "access-control-allow-origin"), "")
    _check(evidence, "CORS does not trust arbitrary origins",
           cors_status == 200 and acao not in ("*", UNTRUSTED_ORIGIN))


def _config_flag_is_off(rows, key):
    """Every scope must be explicitly OFF; platform default must exist once.

    Even expired activated overrides require review before a release is accepted.
    An absent value must never be mistaken for a disabled regulated capability.
    """
    matching = [row for row in rows
                if isinstance(row, dict) and row.get("key") == key]
    defaults = [row for row in matching
                if row.get("scope") == "platform" and row.get("scope_ref", "") == ""]
    def explicitly_off(row):
        value = row.get("value")
        return value is False or (isinstance(value, str) and value.strip().lower() == "false")
    return len(defaults) == 1 and all(explicitly_off(row) for row in matching)


def _admin(evidence):
    # Re-check the deployment before ever transmitting privileged credentials.
    status, ready, _ = _req("GET", "/readyz")
    if not _identity_checks(evidence, status, ready):
        evidence["blocker"] = "ADMIN_IDENTITY_NOT_VERIFIED"
        return
    if not ADMIN_EMAIL or not ADMIN_PW:
        evidence["blocker"] = "ADMIN_CREDENTIALS_MISSING"
        print("  [BLOCKED] administrator credentials not configured")
        return
    status, login, _ = _req("POST", "/login", {"email": ADMIN_EMAIL, "password": ADMIN_PW})
    token = login.get("token")
    if not _check(evidence, "privileged login succeeded",
                  status == 200 and isinstance(token, str) and bool(token.strip())):
        return
    try:
        status, me, _ = _req("GET", "/me/permissions", token=token)
        if not _check(evidence, "administrator identity (never QA)",
                      status == 200 and me.get("role") in ("admin", "founder", "super_admin")):
            return
        status, config, _ = _req("GET", "/admin/config", token=token)
        rows = config.get("config")
        if not _check(evidence, "governed configuration readable",
                      status == 200 and isinstance(rows, list)):
            return
        for key in CONTROLLED_FLAGS:
            _check(evidence, f"regulated flag OFF across scopes: {key}", _config_flag_is_off(rows, key))
    finally:
        # A logout response alone is not revocation evidence. If a stale
        # administrator bearer still authorizes requests, fail the release gate.
        try:
            logout_status, _, _ = _req("POST", "/logout", {}, token=token)
        except Exception:
            logout_status = 0
        revoked_status = 0
        if logout_status in (200, 204):
            try:
                revoked_status, _, _ = _req("GET", "/me/permissions", token=token)
            except Exception:
                revoked_status = 0
        evidence["admin_logout_http_status"] = logout_status
        evidence["admin_post_logout_http_status"] = revoked_status
        _check(
            evidence, "administrator audit session revoked server-side",
            logout_status in (200, 204) and revoked_status in (401, 403)
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("public", "admin", "all"), default="all")
    args = parser.parse_args(argv)
    evidence = {
        "audit": "production_preflight",
        "scope": args.scope,
        "base_url": BASE,
        "expected_release_sha": EXPECTED_SHA or None,
        "expected_deployment_id": EXPECTED_DEPLOYMENT_ID or None,
        "identity_bound": bool(EXPECTED_SHA and EXPECTED_DEPLOYMENT_ID),
        "checks": [],
        "acceptance_complete": False,
    }
    print(f"LiftHaul production preflight [{args.scope}] -> {BASE}")
    if args.scope in ("public", "all"):
        _public(evidence)
    if args.scope in ("admin", "all"):
        _admin(evidence)

    failed = sum(item["result"] == "FAIL" for item in evidence["checks"])
    blocker = evidence.get("blocker")
    if failed:
        status, exit_code = "FAILED", 1
    elif blocker:
        status, exit_code = blocker, 2
    elif args.scope == "public":
        status, exit_code = "PUBLIC_PASS_ADMIN_UNVERIFIED", 0
    elif args.scope == "admin":
        status, exit_code = "ADMIN_PASS_PUBLIC_UNVERIFIED", 0
    else:
        status, exit_code = "PREFLIGHT_COMPLETE", 0
        evidence["acceptance_complete"] = True
    evidence.update(result=status, passed=len(evidence["checks"]) - failed, failed=failed)
    if EVIDENCE_PATH:
        path = Path(EVIDENCE_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"PREFLIGHT: {status} -- {evidence['passed']} passed, {failed} failed. "
          "Not an MVP GO decision.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
