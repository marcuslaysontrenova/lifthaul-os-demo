# LiftHaul OS — Go-Live Runbook (P0)

Status of the P0 release gates, and the exact external steps for the gates that require
owner-controlled infrastructure this build environment does not have (Docker, PostgreSQL,
a hosting account, a production domain, and production credentials).

## Gates verified in-repo (no owner infra needed)

| Gate | Result | Evidence |
|---|---|---|
| Full regression (deterministic) | **PASS — 1,292 passed, 0 failed (2026-08-29)** | `python -m pytest -q -p no:cacheprovider backend` |
| P0-3 test flake fixed | **PASS** | `test_server` rebinds a fresh seeded conn in setUpClass; suite deterministic |
| P0-2 config consumers | **PASS** | numbering (booking/quotation/job/invoice) governed with unchanged defaults; quotation validity persisted + reproducible — `test_quotation_pricing.py::ConfigConsumerTests` |
| Tenant isolation | **PASS** | `test_tenant_isolation.py` (HTTP + persistent DB) + rate-card isolation |
| P0-7 backup / restore | **PASS** | real create → SQLite online backup → destroy → restore → verify cycle |
| P0-8 security smoke | **PASS** | 401 unauth, 403 wrong-role, tampered total server-recomputed (₱999,999→₱165,000), no secret in `/me/permissions` |
| P0-6 prod config fail-closed | **PASS (code)** | `server.validate_config()` exits(2) if production DB/secret/origin/bootstrap-admin values are missing or unsafe |
| Historical reproducibility | **VERIFIED** | tax/dp/approval/validity snapshots persisted per quotation |
| Financial invariants | **UNCHANGED** | `backfill.verify` fingerprint before==after |

## Gates that REQUIRE owner-controlled infrastructure (environment-blocked here)

This container has **no Docker and no PostgreSQL** (`docker: command not found`), no hosting
account, no production domain, and no production credentials. The following are prepared but
cannot be executed here — run them on a Docker/PostgreSQL host:

### P0-4 — PostgreSQL runtime (MANDATORY before "LIVE")
```bash
cp .env.example .env         # fill DB/secret/origin and bootstrap-admin credentials
docker compose up --build
```
Then verify: db starts; `web` runs `migrate.py` (schema) then `server.py`; `GET /health` 200;
`GET /ready` 200; login succeeds; tenant isolation holds; quotation → payment → job flow works;
data survives `docker compose restart web`.

### P0-5 — Real browser E2E
With the stack up and the frontend (`index.html`) pointed at the backend
(`localStorage.rgo_api_base = https://<backend-origin>`), walk:
admin login → create user → customer → booking → quotation → approval → acceptance →
downpayment request → payment verification → job → dispatch → completion → invoice → payment →
close job. Then: customer login sees only own records; a second tenant cannot access the first.

### P0-9 — Production deployment
Deploy the image to the chosen host with TLS, production PostgreSQL, migrations, secrets,
backups, logs, health monitoring.

### P0-10 — Production smoke
One synthetic end-to-end transaction (no real money), verify the audit trail, then archive the
synthetic data.

## Owner action required to lift the block

1. Provide a **Docker + PostgreSQL host** (or a managed PostgreSQL URL) and run P0-4/P0-5.
2. Provide a **hosting account + production domain + TLS** for P0-9.
3. Provide **production secrets** (APP_SECRET, DB credentials, CORS origin, bootstrap admin) and, if enabling
   payments, **Wise production credentials** (a commercial/legal decision).

Until P0-4, P0-5, P0-9, P0-10 pass on that infrastructure, the application status is
**GO-LIVE BLOCKED** — not "almost live". Everything within code/CI control is green.
