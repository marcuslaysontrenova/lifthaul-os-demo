# LiftHaul Enterprise — Go-Live Readiness Review

**Date:** 2026-08-15 · **Release under review:** `9cff7cd` (public `lifthaul-os-demo` main)
**Trigger:** CTO checkpoint — reassess production readiness after the Carrier Portal + Driver
Reassignment increments, before building further revenue/expansion features.
**Reviewer roles (single orchestrator, multiple hats):** Release Manager · QA · Security · SRE ·
Regulatory/Compliance · Product.

> **VERDICT: CONDITIONAL GO.** The software is production-grade and the marketplace-operability core is
> closed and green (**1065 automated tests, 0 failed**). Every remaining gate to a revenue-bearing
> launch is an **owner-controlled external activation** (a hosted deployment cutover, an approved PH
> funds legal model + licensed provider, live comms/insurance/AI adapters, and LTFRB CPC enforcement) —
> **none is a code defect**, and each is deliberately fail-closed in code today. Recommendation:
> **stop feature-building and execute the deployment cutover + external activations**; treat the
> remaining backlog (Rental, Corporate Billing, Preferred Carriers, Dynamic Surcharge, Driver Mobile
> App) as **post-launch expansion**.

This review builds on and supersedes the engineering baselines in
[`docs/GO_LIVE_RUNBOOK.md`](../GO_LIVE_RUNBOOK.md) (P0 gate runbook, then 755 tests) and
[`docs/administration/PRODUCT_READINESS_AND_MARKETPLACE_GAP_ASSESSMENT.md`](../administration/PRODUCT_READINESS_AND_MARKETPLACE_GAP_ASSESSMENT.md)
(2026-08-03, then 532 tests). It does not duplicate them; it re-scores at `9cff7cd`.

---

## 1. Scope closed since the last assessment

| Increment | Release | Governance property |
|---|---|---|
| Cargo Insurance / Goods Protection | `7af4aa4` | LiftHaul is not the insurer; provider fail-closed |
| Secure Delivery OTP & Recipient Verification | `163b8aa` | OTP hashed/single-use; fail-closed release gate |
| Automated Customer & Operational Notifications | `c4af74a` | Event-driven; **never fakes delivery**; OTP never in a notification |
| Carrier / Fleet Owner Portal | `232021d` | Self-service over existing domains; carriers **never self-verify** |
| Driver Reassignment / Re-matching | `9cff7cd` | Reuses matching; **protected funds never moved** |

The end-to-end marketplace lifecycle is now continuous and governed:
booking → validation → pricing → candidates → broadcast → offers → assignment → Protected Payment →
trip execution → GPS/geofence → POD → Secure Delivery OTP → release gate → settlement → claims/Goods
Protection → notifications, with carrier self-service and reassignment/re-matching across the top.

---

## 2. Readiness scorecard (evidence-based)

| Domain | Verdict | Evidence |
|---|---|---|
| **Automated QA** | 🟢 GO | 1065 passed / 0 failed (`python -m pytest backend/`); 50 test suites; 9 module `run_integrity()` self-checks |
| **RBAC / least privilege** | 🟢 GO | Server-side `core.require`; wildcard grammar; new `carrier_principal` role holds only `carrier.portal.*` (no `marketplace.*`) → carrier tokens are 403 on `/admin/*`; portal elevation is a closed allow-list that can never include a verify/approve/activate perm (`_FORBIDDEN_ELEVATION`, hard-asserted) |
| **Tenant isolation** | 🟢 GO | `tenant.predicate/guard`; identity-derived only; 404-no-leak on cross-tenant; carrier-portal binding is identity-scoped and ignores client-supplied `carrier_id` |
| **Audit trail** | 🟢 GO | `audit_logs` on every governed write; per-request correlation id; reassignment/portal/notification actions all audited |
| **Secrets hygiene** | 🟢 GO | No hardcoded credentials/keys/tokens found in `backend/*.py` (demo logins excepted); `_reject_raw_secret` blocks raw secrets into records; payout accounts stored masked |
| **Protected funds safety** | 🟢 GO (gated) | `live_funds_enabled()` requires **three** flags true (`live_protected_funds_enabled` ∧ `legal_operating_model_approved` ∧ `licensed_provider_active`); all default **false**; `_assert_live_allowed` refuses any non-MOCK rail; reassignment refuses once release/settlement is under way and never mutates the ledger |
| **Input hardening** | 🟢 GO | Public/API payload caps (32 KB / 256 KB) + per-IP rate limit; API-key auth + idempotency on `/api/v1/*` |
| **Health / readiness probes** | 🟢 GO | `GET /healthz` (liveness) + `GET /readyz` (DB ping + schema_version → 503 when not ready) |
| **DB portability** | 🟢 GO (code) | SQLite (dev) + PostgreSQL (`psycopg2`, `dbconn` dialect translation); `test_pg_portability`, `test_pgadapter`, `test_http_e2e`; idempotent per-module `init/seed`; `schema_version` stamping |
| **CI** | 🟢 GO | 13 GitHub Actions validation workflows (per-phase + marketplace + items) |
| **Live deployment (PostgreSQL + HTTP host)** | 🔴 **NO-GO (owner infra)** | Backend has **not** been exercised against a hosted PostgreSQL/HTTP origin from this environment (no Docker/DB here). Dockerfile + `docker-compose.yml` (bundles postgres) are ready. **This is the #1 gate.** |
| **Regulatory — LTFRB enforcement** | 🟡 CONDITIONAL | Hard assignment gate implemented and **config-gated** (`marketplace.ltfrb_enforcement_enabled=false`); inert until CPCs are recorded/verified and enforcement is switched on at go-live. Never fabricates authority. |
| **Comms / insurance / AI providers** | 🟡 CONDITIONAL | email/SMS/push/whatsapp/insurance/messaging all `provider_active=false`; adapters fail **honestly** (no fabricated delivery). Live launch needs real adapters configured. |
| **Observability (metrics/tracing/alerting)** | 🟡 CONDITIONAL | Structured logging + health/ready probes + audit ledger present; no external metrics/APM/alerting wired (expected to be host-provided). |
| **Business continuity** | 🟢 GO (code) | Backup/restore cycle proven in-repo (SQLite online backup); Postgres backup is a host responsibility documented in the runbook. |

Legend: 🟢 GO · 🟡 CONDITIONAL (owner activation, not code) · 🔴 NO-GO (must clear before revenue).

---

## 3. Fail-closed inventory (technology enforces the boundary; it does not decide the law)

Every revenue/regulated capability is **off by default** and refuses rather than fakes:

| Switch | Default | Effect while off |
|---|---|---|
| `payments.live_protected_funds_enabled` (+ legal + licensed provider) | false | no live rail engages; MOCK only |
| `marketplace.ltfrb_enforcement_enabled` | false | CPC gate inert until CPCs verified |
| `insurance.provider_active` | false | Goods Protection never binds a real policy |
| `delivery.messaging_provider_active` | false | OTP delivery path honest-fails |
| `notify.{email,sms,push,whatsapp}.provider_active` | false | notifications queue + honestly report no-provider |

No accidental live custody or fabricated delivery is possible by config drift.

---

## 4. Go-live blockers — all owner-controlled, none a code gap

| # | Blocker | Owner action | Type |
|---|---|---|---|
| G1 | **Hosted deployment** | Stand up the stack on a Docker/PostgreSQL host (or managed Postgres + container host), point the front-end `localStorage.lifthaul_api_base` at it, run the runbook's P0-4/P0-5 live E2E + restart-persistence. | Infra cutover |
| G2 | **PH funds legal model + licensed provider** | Approve the operating model and activate a licensed provider, then set the three `payments.*` flags. | Legal + provider |
| G3 | **LTFRB CPC enforcement** | Record + verify carriers' CPCs, then switch on `marketplace.ltfrb_enforcement_enabled` at go-live. | Regulatory |
| G4 | **Live comms/insurance/AI adapters** | Configure real provider adapters and flip the `*_active` flags. | Provider |

Prior owner blockers B1 (live Wise) and B2 (live AI provider) from the 2026-08-03 assessment remain
open and are subsumed under G2/G4 — still mock-proven, adapters complete and fail-safe.

---

## 5. Recommended cutover sequence (no new feature work required)

1. **Deploy (G1):** `docker compose up --build` on a real host → verify `/healthz` 200, `/readyz` 200,
   login, tenant isolation, a full booking→settlement E2E, and data survival across restart.
2. **Regulatory (G3):** load + verify CPCs; enable LTFRB enforcement.
3. **Providers (G4):** activate comms first (notifications/OTP delivery), then insurance/AI as needed.
4. **Funds (G2):** only after legal + licensed provider — flip the three `payments.*` flags; re-run the
   Protected Payment E2E against the live rail in a controlled, low-value transaction first.
5. **Observability:** attach the host's metrics/APM/alerting to `/healthz`, `/readyz`, and the audit
   ledger before onboarding the first paying tenant.

---

## 6. Product decision — remaining backlog is post-launch

The marketplace-**operability** core is complete. The remaining sequence — Hourly/Daily/Project Rental,
Corporate Billing & Statements, Preferred Carriers/Dedicated Capacity, Dynamic Surcharge, Driver Mobile
App — is **revenue/expansion**, not an operability gap. Recommendation: **freeze feature scope for the
first client**, execute §5, and schedule the backlog as post-launch expansion. (Explicitly excluded
from the release path, unchanged: rewards, coupons, COD, ride-hailing, lifestyle-benefit programs.)

---

## 7. Sign-off

| Hat | Position |
|---|---|
| QA | GO — 1065/0, integrity checks green, deterministic suite |
| Security | GO — RBAC/tenant/audit enforced server-side; no secrets; funds/providers fail-closed |
| Regulatory | CONDITIONAL — LTFRB gate ready, off until CPCs verified; funds off until legal + provider |
| SRE | CONDITIONAL — Postgres/Docker-ready + health/ready probes; **must complete the hosted cutover (G1)** and attach observability |
| Release Manager | **CONDITIONAL GO** — ship to a hosted environment now; revenue-bearing launch unlocks as G1→G4 clear, in that order |
| Product | GO — freeze scope for client #1; remaining backlog is post-launch |

**Bottom line:** there is no remaining *engineering* work required to be production-ready. The path to a
live, revenue-bearing launch is a deployment cutover plus owner-controlled legal/provider/regulatory
activations that the codebase already gates safely.
