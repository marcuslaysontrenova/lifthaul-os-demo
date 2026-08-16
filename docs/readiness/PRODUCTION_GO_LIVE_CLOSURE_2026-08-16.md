# LiftHaul Enterprise — Production Go-Live Closure Review

**Date:** 2026-08-16 · **Release under review:** `fef8d69` · **Regression:** 1,176 passed / 0 failed
**Trigger:** Orchestrator decision — **FEATURE FREEZE**. LiftHaul moves from Product Development to
**Production Activation & Controlled Launch**. This review supersedes the 2026-08-15 readiness review
([`GO_LIVE_READINESS_REVIEW_2026-08-15.md`](GO_LIVE_READINESS_REVIEW_2026-08-15.md)) at the frozen build.
**Reviewer roles (single orchestrator):** Release Manager · SRE · Security Architect · Compliance
Architect · QA/Release Lead · Solution Architect.

> **A word on what this document is.** This is an evidence-based readiness certification. The build
> environment has **no Docker, no PostgreSQL, no hosting account, no production domain, and no production
> credentials** — so the runtime gates (hosted deploy, TLS, production browser E2E, PostgreSQL
> backup/restore, real CPC data, live providers, named on-call humans) cannot be *executed* here; they
> must be executed on the owner's production environment. For each gate this review therefore separates
> **APPLICATION-READY (verified in-repo now)** from **EXECUTE-ON-PROD (owner runbook step)**, and never
> claims a runtime pass that was not run. Code-side green is not launch-done — the same discipline the
> platform applies to email delivery it applies to itself.

---

## 1. Three independent go/no-go decisions (per the Orchestrator directive)

A single "ready/not-ready" label would be misleading. The launch is three separable decisions:

| Decision | Status now | Unlocks when |
|---|---|---|
| **APPLICATION** — is the software technically ready to run production? | 🟡 **CONDITIONAL** — application-ready and fully green in-repo (incl. the PostgreSQL/HTTP-E2E code paths); **runtime PASS pending** the hosted PostgreSQL deploy + production browser E2E (Gates 1–3, 5). | Gates 1, 2, 3, 5 executed on prod. |
| **MARKETPLACE** — may carriers be matched to real for-hire jobs? | 🔴 **DISABLED** — LTFRB enforcement is config-gated **OFF** by default; the hard CPC assignment gate is built and tested but inert until real CPC/unit/area data is loaded, independently verified, and enforcement switched on. | Gate 6 executed (real CPCs verified + enforcement ON). |
| **LIVE PROTECTED PAYMENT** — may real custody of funds move? | 🔴 **DISABLED (by design, correctly)** — `live_funds_enabled()` returns **False**; it requires **three** independent flags true (`live_protected_funds_enabled ∧ legal_operating_model_approved ∧ licensed_provider_active`), all `false`. Technology enforces the boundary; it does not decide the law. | Gate 8 satisfied (legal model approved + licensed provider certified + explicit flags). |

**Bottom line:** the platform can go live for **non-funds, non-for-hire-enforced operation** as soon as
Gates 1–5 + 9 are executed on a real host; **for-hire matching** unlocks at Gate 6; **live money** stays
off until Gate 8's external legal/provider conditions are independently met.

---

## 2. The launch rule (Orchestrator)

```
FEATURES COMPLETE ✓ (fef8d69, 1176/0)
     ↓
POSTGRESQL PASS            (Gate 1 — EXECUTE-ON-PROD)
     ↓
PRODUCTION E2E PASS        (Gate 3 — EXECUTE-ON-PROD)
     ↓
TENANT ISOLATION PASS      (Gate 4 — APPLICATION-READY ✓, re-verify on prod)
     ↓
BACKUP/RESTORE PASS        (Gate 5 — EXECUTE-ON-PROD)
     ↓
SECURITY PASS              (Gate 9 — APPLICATION-READY ✓, prod-secret hardening on prod)
     ↓
REGULATORY/PROVIDER FLAGS CORRECT  (Gates 6/7/8 — FAIL-CLOSED CORRECT ✓; activation is owner-gated)
     ↓
CONTROLLED GO-LIVE
```

---

## 3. Ten-gate scorecard (evidence-based)

Legend: 🟢 APPLICATION-READY (verified in-repo) · 🟠 EXECUTE-ON-PROD (owner runtime step) · 🔵 FAIL-CLOSED CORRECT (deliberately off).

### Gate 1 — Hosted PostgreSQL 🟢→🟠
- **In-repo (verified):** SQLite + PostgreSQL dual backend (`db.py` selects by `DATABASE_URL`); `dbconn.py`
  dialect-translating adapter; `migrate.py`; `docker-compose.yml` (bundles postgres:16) + `Dockerfile`.
  **`test_pg_portability` + `test_pgadapter` + `test_http_e2e` + `test_ops` = 34 passed** (full lifecycle
  incl. the PG-adapter code paths).
- **Execute on prod:** `docker compose up --build` (or managed Postgres + container host) → `migrate.py` →
  `GET /readyz` 200 with `schema_version` → run a lifecycle → `docker compose restart web` → confirm data
  persists. **This runtime PASS has not been run here** (no Docker/PG in this environment).

### Gate 2 — Production HTTP / TLS 🟢→🟠
- **In-repo (verified):** `validate_config()` **`sys.exit(2)`** in `APP_ENV=production` if `APP_SECRET`,
  `DATABASE_URL` or `CORS_ORIGINS` is missing (fail-fast-safe). `/healthz` (liveness) + `/readyz` (DB ping
  + schema_version → 503 when down). Payload caps (32 KB public / 256 KB api). Per-IP rate limiting.
  CORS returns only configured origins.
- **Execute on prod:** real backend origin behind TLS; set `APP_SECRET`/`DATABASE_URL`/`CORS_ORIGINS`
  (do **not** include `*` in `CORS_ORIGINS`); point the front-ends' `localStorage.lifthaul_api_base` at it;
  confirm probes over HTTPS. **Owner infra.**

### Gate 3 — Production browser E2E 🟢→🟠
- **In-repo (verified):** `test_http_e2e` drives the HTTP surface; `test_ops::test_booking_to_closure_and_profit`
  proves the full lifecycle; the public flow (book→track), operator console, carrier portal, driver app all
  parse and are unit-covered.
- **Execute on prod:** one real synthetic transaction end-to-end: customer booking → operator review → quote
  → payment state → assignment → carrier portal → trip → secure delivery OTP → POD → settlement, in a real
  browser against the hosted backend. **Owner infra.**

### Gate 4 — Tenant isolation 🟢 (re-verify on prod)
- **In-repo (verified):** `tenant.predicate/guard`; deny only when both tenants set and differ; **404-no-leak**
  on cross-tenant reads; 403 on cross-tenant writes. Dedicated **`test_tenant_isolation.py` green** (part of the
  55-pass tenant/admin batch). Carrier-portal + driver-app + fleet + availability all enforce
  own-resource-only on top of tenant scope.
- **Execute on prod:** stand up two synthetic tenants and prove neither can cross-read/cross-write customers,
  bookings, fleet, payments, claims, documents, or API resources against the real DB. (The logic is proven; the
  prod re-run is the sign-off.)

### Gate 5 — Backup / restore 🟢→🟠
- **In-repo (verified):** backup/restore cycle is exercised in-suite (SQLite online-backup path;
  `test_admin_platform` / `test_security` cover the create→backup→destroy→restore→verify cycle).
- **Execute on prod:** `pg_dump` → destructive synthetic test → `pg_restore` → reconciliation → record
  **RTO/RPO** evidence. **Owner infra (PostgreSQL host).**

### Gate 6 — LTFRB activation 🔵 (marketplace decision)
- **In-repo (verified):** `ltfrb.assignment_authority_gate` is a HARD assignment gate; wired into
  `create_assignment` **config-gated by `marketplace.ltfrb_enforcement_enabled` (default `false`)** so it is
  inert until switched on; never fabricates authority — a missing CPC blocks. `test_ltfrb` green.
- **Owner activation:** load + independently verify real carrier CPC / authorized-unit / area-of-operation
  data, then set `marketplace.ltfrb_enforcement_enabled=true`. **Until then, MARKETPLACE decision = DISABLED.**

### Gate 7 — External providers 🔵
- **In-repo (verified):** every provider is **fail-closed** and **never fakes success**: messaging/OTP
  (`delivery.messaging_provider_active=false`), notifications (`notify.*.provider_active=false`), insurance
  (`insurance.provider_active=false`). No live rail engages by default.
- **Owner activation:** connect only the actually-selected providers (messaging, maps/tracking, insurance if
  available, payment when authorized) and flip the corresponding `*_active` flags. Honest-failure semantics are
  already proven.

### Gate 8 — Protected Payment (live custody) 🔵 (correctly OFF)
- **Verified now:** `live_funds_enabled() == False`; requires **all three** of `live_protected_funds_enabled`,
  `legal_operating_model_approved`, `licensed_provider_active` (all `false`). `_assert_live_allowed` refuses any
  non-MOCK rail. No accidental custody by config drift.
- **Owner gate:** legal operating model approved **and** licensed provider certified **and** explicit
  production flags — independently. **LIVE PROTECTED PAYMENT decision stays DISABLED until then.**

### Gate 9 — Security closure 🟢 (prod-secret hardening on prod)
- **In-repo (verified):** RBAC least-privilege + SoD (no self-verify/activate/select-own-offer); API-key
  auth with **hashed** secrets + scopes; **HMAC-SHA256 webhook** signing + verify; per-IP rate limits;
  idempotency keys; append-only `audit_logs` with correlation ids; guarded login + lockout + MFA-sensitive
  actions + session governance; no hardcoded secrets; payload caps. **`test_security` + `test_tenant_isolation`
  + `test_api_platform` + `test_delivery_verification` = 76 passed.**
- **Execute on prod:** rotate/inject production secrets; confirm MFA on sensitive actions with real users;
  external vulnerability review of the deployed surface. (Application controls proven; prod hardening is the
  sign-off.)

### Gate 10 — Hypercare 🟠 (owner assignment)
- **To assign (owner):** named operational/support owners; incident **severity matrix** (S1–S4) + escalation
  contacts; monitoring on `/healthz`, `/readyz`, and the audit ledger; support hours; **first-30-day review
  cadence**. A template is in §5. **Requires real humans + tooling — cannot be self-assigned.**

---

## 4. Launch-defect gate

**Zero Critical / High defects** at `fef8d69`: 1,176 automated tests green; 9 module integrity self-checks
green; no hardcoded secrets; every revenue/regulated capability fail-closed. No open Critical/High from this
review. (Prod-runtime defects, if any, surface during Gates 1–3 execution and must return to zero before
Controlled Go-Live.)

---

## 5. Cutover runbook (ordered — the Production Go-Live Closure Sprint)

1. **Provision (Gate 1/2):** managed PostgreSQL + container host + domain + TLS. Set `APP_ENV=production`,
   `APP_SECRET`, `DATABASE_URL`, `CORS_ORIGINS` (no `*`). `docker compose up --build` → `migrate.py`.
2. **Smoke (Gate 2):** `/healthz` 200, `/readyz` 200 (schema_version), login, an authenticated call.
3. **Production E2E (Gate 3):** the full synthetic transaction in a real browser; **zero Critical/High**.
4. **Tenant isolation (Gate 4):** two synthetic tenants; prove no cross-read/write across all resources.
5. **Backup/restore (Gate 5):** `pg_dump` → destructive test → restore → reconcile → record RTO/RPO.
6. **Security hardening (Gate 9):** production secrets, MFA on sensitive actions, external vuln review.
7. **Regulatory (Gate 6):** load + verify real CPCs → `marketplace.ltfrb_enforcement_enabled=true` → **MARKETPLACE = READY**.
8. **Providers (Gate 7):** connect messaging (then OTP/notifications), maps/tracking, insurance if available.
9. **Funds (Gate 8):** ONLY after legal model + licensed provider — flip the three `payments.*` flags; run one
   low-value controlled live transaction first → **LIVE PROTECTED PAYMENT = READY**.
10. **Hypercare (Gate 10):** owners, severity matrix, monitoring, support hours, 30-day review → **CONTROLLED GO-LIVE**.

**Hypercare template (fill on prod):** S1 = data loss / funds / security breach (15-min page, owner + eng);
S2 = booking/matching/payment-state broken (1-hr); S3 = single-tenant/feature degraded (next business day);
S4 = cosmetic. On-call: [name/number]. Monitoring: [APM] on `/readyz` + audit ledger. Support hours: [tz].
First-30-day review: weekly.

---

## 6. Definitive decision

**STOP building. Execute the Production Go-Live Closure Sprint.** There is no remaining *feature* work that
materially improves launch readiness; the risk is now entirely in unproven production operations. The
software is application-ready and green (1,176/0), fail-closed on every revenue/regulated surface, and the
exact executable path to a controlled launch is §5.

The target is not another test-count increase. It is: **one real production environment, one complete
synthetic transaction, one restored backup, one verified second tenant, zero Critical/High launch defects,
and a controlled first-customer onboarding path.**

| Role sign-off | Position |
|---|---|
| QA / Release Lead | Application GREEN in-repo (1176/0); prod E2E is the outstanding runtime sign-off |
| Security | Controls proven; prod-secret hardening + external vuln review outstanding |
| SRE | Postgres/Docker-ready + probes; **must execute Gates 1–3, 5 on a real host** |
| Compliance | MARKETPLACE disabled until verified CPCs; LIVE FUNDS disabled until legal+provider — both correctly fail-closed |
| Release Manager | **CONDITIONAL GO** — ship to a hosted environment now; the three decisions unlock independently per §1 |
