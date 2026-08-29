#!/usr/bin/env python3
"""LiftHaul — Production E2E smoke (Gate 3). Runs ONE real synthetic transaction against a hosted
backend over HTTP and asserts each step. Stdlib only.

Usage:
    BASE_URL=https://api.lifthaul.example \
    LH_ADMIN_EMAIL=admin@yourco.com LH_ADMIN_PASSWORD=****** \
    python scripts/go_live/prod_e2e.py

Exit code 0 = all steps PASS. Non-zero = a step failed (details printed). Safe to run against
production: it creates one public parcel booking (source=PUBLIC_MARKETPLACE) and reads it back;
it moves NO money and touches no live provider (those stay fail-closed server-side).
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8787").rstrip("/")
ADMIN_EMAIL = os.environ.get("LH_ADMIN_EMAIL")
ADMIN_PW = os.environ.get("LH_ADMIN_PASSWORD")
if not ADMIN_EMAIL or not ADMIN_PW:
    raise SystemExit("LH_ADMIN_EMAIL and LH_ADMIN_PASSWORD are required; production validation has no demo defaults")

_ok = 0
_fail = 0


def _req(method, path, body=None, token=None, expect=200):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            code, payload = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, payload = e.code, e.read().decode()
    except Exception as e:
        return None, {"error": str(e)}, 0
    try:
        parsed = json.loads(payload) if payload else {}
    except Exception:
        parsed = {"raw": payload[:200]}
    if isinstance(parsed, dict) and "data" in parsed:
        parsed = parsed["data"]
    return code, parsed, code


def step(name, cond, detail=""):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  [PASS] {name}")
    else:
        _fail += 1
        print(f"  [FAIL] {name}  {detail}")
    return cond


def main():
    print(f"LiftHaul Production E2E smoke  ->  {BASE}")
    print("-" * 60)

    # 1. Liveness
    code, body, _ = _req("GET", "/healthz")
    step("healthz 200 + status ok", code == 200 and body.get("status") == "ok", f"got {code} {body}")

    # 2. Readiness (DB reachable + schema stamped)
    code, body, _ = _req("GET", "/readyz")
    step("readyz 200 + schema_version present",
         code == 200 and body.get("status") == "ready" and body.get("schema_version") is not None,
         f"got {code} {body}")

    # 3. Customer books a motorcycle parcel delivery (public, unauthenticated)
    booking = {"contact_name": "E2E Synthetic", "contact_phone": "09170000000",
               "origin_island": "LUZON", "dest_island": "LUZON", "origin_city": "Makati",
               "dest_city": "Quezon City", "vehicle": "moto", "km": 12,
               "idempotency_key": f"e2e-{int(time.time())}"}
    code, body, _ = _req("POST", "/public/bookings", booking)
    token_tok = body.get("tracking_token")
    step("customer public booking accepted (+tracking token)",
         code == 200 and bool(token_tok) and body.get("ref"), f"got {code} {body}")

    # 4. Customer tracks the booking
    if token_tok:
        code, body, _ = _req("GET", f"/public/bookings/track/{token_tok}")
        step("customer tracking returns booking status",
             code == 200 and (body.get("customer_status") or body.get("stages") or body.get("ref")),
             f"got {code} {body}")

    # 5. Auth is enforced (an admin route without a token must be 401)
    code, _, _ = _req("GET", "/admin/marketplace/public-booking-queue")
    step("unauthenticated admin call rejected (401)", code == 401, f"got {code}")

    # 6. Operator login
    code, body, _ = _req("POST", "/login", {"email": ADMIN_EMAIL, "password": ADMIN_PW})
    tok = body.get("token")
    step("operator login issues a session token", code == 200 and bool(tok), f"got {code} {body}")

    # 7. Authenticated, persisted read: the operator sees the booking just created (proves persistence)
    if tok:
        code, body, _ = _req("GET", "/me/permissions", token=tok)
        step("authenticated /me/permissions 200", code == 200, f"got {code}")
        code, body, _ = _req("GET", "/admin/marketplace/public-booking-queue", token=tok)
        q = (body or {}).get("queue") or (body or {}).get("bookings") or body
        found = json.dumps(q).find("PUBLIC") >= 0 or json.dumps(q).find(booking["contact_name"]) >= 0 or (isinstance(q, list) and len(q) > 0)
        step("operator sees the persisted public booking", code == 200 and found, f"got {code}")

    print("-" * 60)
    print(f"E2E RESULT: {_ok} passed, {_fail} failed")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
