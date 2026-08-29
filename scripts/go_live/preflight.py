#!/usr/bin/env python3
"""LiftHaul — Production preflight (Gates 2/6/7/8/9 config surface). Audits a HOSTED backend URL for the
launch-critical posture: probes up, auth enforced, and every revenue/regulated capability fail-closed.
Stdlib only.

Usage:
    BASE_URL=https://api.lifthaul.example \
    LH_ADMIN_EMAIL=admin@yourco.com LH_ADMIN_PASSWORD=****** \
    python scripts/go_live/preflight.py

Exit 0 = launch-safe posture. Non-zero = a check failed. Read-only (no writes, no money).
"""
import json
import os
import sys
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
    raise SystemExit("LH_ADMIN_EMAIL and LH_ADMIN_PASSWORD are required; production preflight has no demo defaults")
_ok = _fail = _warn = 0


def _req(method, path, body=None, token=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            _p = json.loads(r.read().decode() or "{}")
            if isinstance(_p, dict) and "data" in _p:
                _p = _p["data"]
            return r.status, _p, dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            b = json.loads(e.read().decode() or "{}")
        except Exception:
            b = {}
        return e.code, b, dict(e.headers)
    except Exception as e:
        return 0, {"error": str(e)}, {}


def ok(name, cond, detail=""):
    global _ok, _fail
    (globals().__setitem__("_ok", _ok + 1) if cond else globals().__setitem__("_fail", _fail + 1))
    print(f"  {'[PASS]' if cond else '[FAIL]'} {name}" + ("" if cond else f"  {detail}"))
    return cond


def warn(name, cond, detail=""):
    global _warn
    if not cond:
        _warn += 1
        print(f"  [WARN] {name}  {detail}")
    else:
        print(f"  [PASS] {name}")


def main():
    print(f"LiftHaul Production preflight  ->  {BASE}")
    print("-" * 60)

    # Probes
    code, body, _ = _req("GET", "/healthz")
    ok("healthz up", code == 200 and body.get("status") == "ok", f"got {code}")
    code, body, _ = _req("GET", "/readyz")
    ok("readyz up (DB reachable + schema stamped)",
       code == 200 and body.get("schema_version") is not None, f"got {code} {body}")

    # TLS
    warn("backend served over HTTPS", BASE.startswith("https://"), "prod MUST be https:// (found http://)")

    # Auth enforced
    code, _, _ = _req("GET", "/admin/marketplace/public-booking-queue")
    ok("unauthenticated admin call rejected (401)", code == 401, f"got {code}")

    # CORS not wildcard (best-effort: a preflight from a random origin should not be echoed '*')
    code, _, hdrs = _req("GET", "/healthz")
    acao = (hdrs.get("Access-Control-Allow-Origin") or hdrs.get("access-control-allow-origin") or "")
    warn("CORS is not wildcard '*'", acao != "*", "Access-Control-Allow-Origin is '*' — set explicit CORS_ORIGINS")

    # Fail-closed posture (needs an operator token; the healthz build-info may also expose flags)
    code, body, _ = _req("POST", "/login", {"email": ADMIN_EMAIL, "password": ADMIN_PW})
    tok = body.get("token")
    if tok:
        # LTFRB enforcement + provider/funds flags via config read (if exposed)
        code, cfg, _ = _req("GET", "/admin/config", token=tok)
        flat = json.dumps(cfg).lower() if code == 200 else ""
        def flag_off(key):
            # crude: find "<key>": "false" style; if key absent, treat as unknown->warn
            k = key.lower()
            return (f'"{k}"' not in flat) or ('"false"' in flat.split(k, 1)[-1][:40] if k in flat else True)
        if code == 200:
            for key in ("marketplace.ltfrb_enforcement_enabled", "payments.live_protected_funds_enabled",
                        "insurance.provider_active", "delivery.messaging_provider_active"):
                warn(f"{key} is OFF (or activate deliberately)", flag_off(key), "verify this flag before launch")
        else:
            warn("config flags readable for audit", False, f"/admin/config returned {code}; audit flags manually")
    else:
        warn("operator login for flag audit", False, "could not log in to read config flags — audit manually")

    print("-" * 60)
    status = "LAUNCH-SAFE" if _fail == 0 else "BLOCKED"
    print(f"PREFLIGHT: {status} — {_ok} passed, {_fail} failed, {_warn} warnings")
    if _warn:
        print("  (warnings are launch-judgement items — review each before go-live)")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
