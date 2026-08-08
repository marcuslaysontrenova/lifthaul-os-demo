# LiftHaul Enterprise — RC1 Go / No-Go Dossier

One-day final marketplace & go-live closure. Decision-grade evidence. Live protected funds remain
OFF; only genuinely-external items (legal model, licensed provider, official-verification credentials,
production infrastructure) are outstanding.

## Release candidate
- Tag: **lifthaul-enterprise-rc1**
- Closure trail: `e3683b8 → 47e4dd5 → b36ec09 → 2051069 → b598067 → c52bdc9 → <rc1 commit>`
- Schema version: 20
- Backend: Python stdlib + psycopg2 (PostgreSQL) — dialect-portable via `dbconn`/`pgcompat`.

## Gate results

| Gate | Result | Evidence |
|---|---|---|
| Full regression | **PASS — deterministic** | `python -m pytest backend/` |
| Trust/compliance | **PASS** | test_marketplace_trust*.py (KYB, adapters, fraud, trust score, driver/vehicle legality, payout, disputes, claims) |
| Protected-payment red-team | **PASS — 0 Critical open** | `PROTECTED_TRANSACTION_ATTACK_MATRIX.md` (29 attacks, all CLOSED) |
| Release controls | **PASS** | composed `release_gate` denies on any unmet condition |
| Fraud / disputes / claims | **PASS** | closure tests |
| Ledger reconciliation | **PASS** | `reconcile_ledger` balances; imbalance flagged |
| Nationwide flows | **PASS** | test_nationwide_marketplace.py — Luzon / Visayas / Mindanao / inter-island sea-leg |
| Tenant isolation | **PASS** | test_tenant_isolation.py + per-module isolation tests |
| RBAC / SoD | **PASS** | booking-access, trust SoD (no self-verify / self-approve / self-resolve) |
| Restart persistence | **PASS** | file-DB reconnect tests |
| Backup / restore | **PASS (SQLite)** | real create→backup→destroy→restore→verify |
| Security smoke | **PASS** | 401 unauth, 403 wrong-role, tampered total recomputed, no secret leak |
| Prod config fail-closed | **PASS** | `server.validate_config()` exits(2) on missing APP_SECRET/DATABASE_URL/CORS_ORIGINS |
| LIVE_PROTECTED_FUNDS_ENABLED | **OFF (enforced)** | central `live_funds_enabled()` requires flag + legal + licensed provider |

## Browser E2E — EXECUTED (application level, live backend)

`BROWSER_E2E_EVIDENCE.md`: the SPA was served over real HTTP and driven in an automated browser
against a live backend. Confirmed live: JS + CORS + cross-origin fetch; admin login (441 perms);
**Finance Administrator** persona (Bookings hidden, Finance/Quotations/Invoices visible);
**Admin → Trust & Compliance** screens render live (KYB, payout approvals, disputes, claims,
integrity); pricing preview server-authoritative (tampered value ignored, margin 32.76%); rate
catalog; marketplace trust gate denies an unverified carrier. **Browser E2E = PASS.**

## Still requires a PostgreSQL server (owner infrastructure)

This host has **psycopg2 installed** and the code is PostgreSQL-portable (`test_pg_portability.py`
green), but **no PostgreSQL server, no Docker**, and a system-wide install needs admin/UAC (not
performed — a durable change to the owner's machine). Packaged and ready (`Dockerfile`,
`docker-compose.yml`, `requirements.txt`, `.env.example`, `GO_LIVE_RUNBOOK.md`):

- **PostgreSQL live E2E** (W4) — `docker compose up --build`, migrate, /health+/ready, run the spine.
- **PostgreSQL-backed browser E2E** (W5) — the same (now-passing) browser flows with `DATABASE_URL`
  pointed at PostgreSQL.
- **PostgreSQL backup/restore** (W6) — the SQLite cycle is proven; repeat against the real database.

One command closes all three on any host with PostgreSQL: point `DATABASE_URL` at it and re-run.

## Owner-controlled remaining actions (genuinely external)

1. **Legal:** qualified counsel approves the PH marketplace operating model + payment-custody structure.
2. **Provider:** engage a licensed protected-payment/safeguarding partner; provision credentials.
3. **Official verification:** DTI/SEC/BIR/LTFRB/LTO/LGU/insurance API agreements (until then:
   `MANUAL_VERIFICATION_REQUIRED`, never fabricated).
4. **Infrastructure:** Docker/PostgreSQL host + hosting account + production domain + TLS + secrets;
   run W4/W5/W6.

## Classification

**GO-LIVE READY WITH OWNER-CONTROLLED CONDITIONS.** Engineering is green across every locally-executable
gate with zero open Critical/High defects. Live protected funds stay OFF until legal + licensed provider
are confirmed. The platform can launch operationally with `LIVE_PROTECTED_FUNDS_ENABLED=false` (payment
operator-verified/external) so a single regulatory dependency does not hold the product hostage.
