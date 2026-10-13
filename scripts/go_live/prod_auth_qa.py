#!/usr/bin/env python3
"""Read-only authenticated LiftHaul production UAT for an approved QA principal.

Requires a dedicated public_intake_qa account, stored only in GitHub's protected
production environment. Never generates bookings, dispatches, collects funds,
changes status, or prints credentials or bearer tokens.
"""
import json
import os
import re
import urllib.error
import urllib.request

BASE = os.environ["BASE_URL"].rstrip("/")
EXPECTED_SHA = os.environ["EXPECTED_SHA"]
EXPECTED_BOOKING_REF = os.environ["LH_TEST_BOOKING_REF"]
EMAIL = os.environ["LH_ADMIN_EMAIL"]
PASSWORD = os.environ["LH_ADMIN_PASSWORD"]
assert BASE == "https://www.lifthaul.com.ph", "Must use the canonical production domain"
assert re.fullmatch(r"[0-9a-f]{40}", EXPECTED_SHA), "Expected a pinned 40-character merge commit SHA"
assert EXPECTED_BOOKING_REF.startswith("LH-"), "Expected an existing synthetic reference"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward credentials or bearer headers to a redirected endpoint."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect())


def call(method, path, payload=None, bearer=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "User-Agent": "LiftHaul-Prod-QA-ReadOnly/1.0"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if bearer is not None:
        headers["Authorization"] = "Bearer " + bearer
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with OPENER.open(req, timeout=25) as response:
            code, body = response.status, response.read(512 * 1024)
    except urllib.error.HTTPError as err:
        code, body = err.code, err.read(4096)
    try:
        out = json.loads(body)
        if isinstance(out, dict) and isinstance(out.get("data"), dict):
            out = out["data"]
    except (ValueError, UnicodeDecodeError):
        out = {}
    return code, out if isinstance(out, dict) else {}


def main():
    code, ready = call("GET", "/readyz")
    assert code == 200 and ready.get("status") == "ready", "Production readiness failed"
    assert ready.get("release_sha") == EXPECTED_SHA, "The live SHA differs from the approved release"
    assert ready.get("schema_version"), "Production schema version missing"
    print("PASS: canonical deployment identity and database ready")

    code, login = call("POST", "/login", {"email": EMAIL, "password": PASSWORD})
    assert code == 200 and isinstance(login.get("token"), str) and login["token"].strip(), "Test account login failed"
    token = login["token"]  # Never write to logs or artifacts.
    try:
        code, me = call("GET", "/me/permissions", bearer=token)
        assert code == 200 and me.get("role") == "public_intake_qa", "Not a least-privilege QA account"
        perms = set(me.get("permissions") or [])
        required = {"marketplace.booking.view", "marketplace.public_intake.view"}
        prohibited = {"marketplace.booking.manage", "marketplace.public_intake.manage",
                      "user_admin.manage", "role_admin.manage", "payment.read",
                      "payment.verify", "founder.dashboard.view", "marketplace.payout.manage"}
        assert perms == required, "QA account must have exactly the two approved read-only permissions"
        assert not prohibited.intersection(perms), "QA account has prohibited elevated authority"
        assert not me.get("is_super"), "QA account unexpectedly has wildcard access"
        assert not any((me.get("financial") or {}).values()), "QA account exposed financial permissions"
        print("PASS: authenticated QA identity and effective least-privilege RBAC")

        code, queue = call("GET", "/admin/marketplace/public-booking-queue", bearer=token)
        assert code == 200 and queue.get("source") == "PUBLIC_MARKETPLACE", "Guest queue not available"
        rows = queue.get("requests")
        assert isinstance(rows, list), "Guest queue has no requests array"
        refs = []
        for row in rows:
            opaque = str(row.get("tracking_token") or "")
            if opaque.startswith("pbk_"):
                refs.append(("LH-II" if row.get("inter_island") else "LH-") + opaque[-6:].upper())
        assert refs.count(EXPECTED_BOOKING_REF) == 1, "Exact prior synthetic booking missing from accessible queue"
        print("PASS: exact stored synthetic booking visible to authorized QA (no PII printed)")

        # Every write probe points to an impossible id; even accidental authority
        # cannot mutate a real booking. A correctly scoped QA principal must get 403.
        forbidden = [
            ("POST", "/admin/marketplace/public-bookings/0/review", {"action": "REVIEW"}),
            ("GET", "/admin/users", None),
            ("POST", "/founder/dashboard/summary", {"period": "monthly"}),
        ]
        for method, path, body in forbidden:
            status, _ = call(method, path, body, bearer=token)
            assert status == 403, "QA account was not rejected for prohibited privileged action"
        print("PASS: review, user administration and Founder permissions denied")
        print("AUTHENTICATED PRODUCTION QA ACCEPTANCE PASSED: read-only, no funds moved")
    finally:
        try:
            call("POST", "/logout", {}, bearer=token)
        except Exception:
            pass


if __name__ == "__main__":
    main()
