# LiftHaul master directive — evidence-backed MVP audit

Audit date: 2026-10-12 (Asia/Manila)  
Repository: `marcuslaysontrenova/lifthaul-os-demo`  
Audited branch: `codex/fix-exact-release-gate`  
Branch baseline: `eac8f3745be4812f3346dfd9825ba1ca17a1964f`  
Approved UI candidate: `a076aedfd978b65999be04b494622e0dd80f1fbb`  
Merged `main` commit: `ca467dd4919d1e5f5f60006875588ecd94f18c0a`  
Public URL: <https://www.lifthaul.com.ph>  

## Executive decision

**MVP status: NO-GO for public commercial launch.** The repository contains a substantial, integrated
marketplace foundation and the approved public UI has passed automated regression in CI. The canonical
public domain is nevertheless still serving release `3919680f25f2f9cd3d1c2325923dc1f9a43fb70b`.
Two Railway deployments of the merged PR #18 release reached the container stage but failed the
`/readyz` health check after approximately five minutes. The last known public result also returned 404
for `/data/psgc/manifest.json`, which prevents the production booking geography controls from operating.

Live payments, settlement, provider payout and any escrow-like fund custody remain correctly fail-closed.
The code includes payment interfaces, ledgers, idempotency and certification gates, but there is no
evidence of an executed regulated-provider agreement, production credentials, legal approval, live
reconciliation, disaster-recovery certification or founder-approved live pilot.

## Audit method and classification

This audit compared the master directive with repository code, public pages, tests, workflows, current
Git history, open PRs, release records, Railway deployment state and live HTTP probes. A source file by
itself is not treated as proof that a feature works.

- **VERIFIED WORKING** — integrated behavior with passing automated or live evidence.
- **IMPLEMENTED BUT UNVERIFIED** — code exists, but accountable UAT or production evidence is absent.
- **PARTIAL** — only part of the requirement is implemented or connected.
- **MISSING** — no sufficient implementation evidence.
- **DEFECTIVE** — implementation exists but currently fails its intended behavior.
- **BLOCKED** — requires credentials, a contract, legal/commercial approval or an external decision.
- **NOT APPLICABLE** — outside the controlled MVP scope and not a launch gate.

## Current system and deployment baseline

| Item | Classification | Evidence and finding |
|---|---|---|
| Default branch and approved UI | VERIFIED WORKING in repository | PR #18 is merged at `ca467dd`; its tested candidate is `a076aed`. `audit/2026-10-11/MASTER_FINALIZATION_CYCLE_1.md` records 1,491/1,491 Python tests and 48/48 three-browser checks. |
| Exact-release ancestry gate | VERIFIED WORKING in development | PR #19, `eac8f37`, preserves merge ancestry so the approved PR head is not falsely rejected after merge. Its SQLite and PostgreSQL checks are green. |
| Canonical production deployment | DEFECTIVE (P1) | `www.lifthaul.com.ph/readyz` still reports `3919680...`; two deployments of PR #18 failed Railway health checking. The approved UI is therefore not live on the canonical domain. |
| Booking geography in production | DEFECTIVE (P1) | The stale public release returns 404 for `/data/psgc/manifest.json`. PR #18 packages and serves the governed PSGC JSON, but the fix is not live. |
| Production monitoring | PARTIAL → FIXED IN DEVELOPMENT | The prior monitor checked GitHub Pages and a different Railway hostname, allowing a false green state. This branch now checks the canonical domain, release ancestry, PSGC data, release copy, health and readiness. |
| Local complete regression | IMPLEMENTED BUT ENVIRONMENT-BLOCKED | 1,491 tests executed locally: five `TemporaryDirectory` permission errors in `test_static_frontend`, no assertion failures. The authoritative Linux CI run for PR #18 passed 1,491/1,491. |
| Production database | IMPLEMENTED BUT UNVERIFIED | `/readyz` exposes schema identity and Railway has a persistent PostgreSQL-backed service, but no current hosted backup/restore artifact proves RPO/RTO for this release. |
| Live payment capability | BLOCKED (P1 for live-money launch; not a no-funds MVP blocker) | `backend/payment_gateway.py`, payment tests and certification workflows exist. External certification gates remain open in `docs/readiness/PAYMENT_PRODUCTION_CERTIFICATION_REGISTER_2026-10-02.md`. |

## Master directive traceability

### 1–3. Governance, baseline and UI/UX

| Requirement | Classification | Evidence / remaining gap |
|---|---|---|
| Unified CTO/BA/QA/security/commercial review | PARTIAL | Architecture, UX, payment, performance, go-live and marketplace records exist under `docs/`; this document consolidates the current evidence. Accountable finance, legal and business-owner approvals are still external. |
| Inspect repository, branches, PRs and deployments | VERIFIED WORKING | `main=ca467dd`; PR #19 remains open; Railway and live-domain states were checked on 2026-10-12. |
| Consolidated requirements traceability | VERIFIED WORKING | This audit is the canonical current-cycle traceability record. |
| Remove public demo/prototype/sample claims | VERIFIED WORKING in candidate; DEFECTIVE in production | `backend/test_public_release_content.py` and PR #18 remove the prohibited copy. The stale live release still presents the old interface. |
| Shared design system and public navigation | VERIFIED WORKING in candidate | `theme.css`, `public-nav.js`, `backend/test_navigation_integrity.py`, `backend/test_theme_consistency.py`, and 48/48 cross-browser tests. |
| Public responsive behavior | VERIFIED WORKING in candidate | Playwright coverage passed Chromium, Firefox and WebKit at desktop, tablet and mobile sizes. |
| Authenticated workspace visual consistency | PARTIAL (P2) | Authenticated pages exist, but the current cycle has not produced complete screenshot comparison evidence for every workspace and state. Some internal synthetic/demo guidance remains to be isolated. |
| Every public link/control works | VERIFIED WORKING in candidate | Static target, fragment, home-route and placeholder-link checks are in `backend/test_navigation_integrity.py`. Accountable manual UAT remains open. |

### 4–5. Identity, role workspaces and Founder command center

| Requirement | Classification | Evidence / remaining gap |
|---|---|---|
| Server-side authentication, sessions and role checks | VERIFIED WORKING | `backend/security.py`, `backend/core.py`, `backend/admin_platform.py`; covered by `test_security`, `test_booking_access_model`, `test_tenant_isolation`, `test_org`, `test_client_auth`. |
| Organization boundaries / tenant isolation | VERIFIED WORKING | Cross-tenant denial tests and PostgreSQL lifecycle checks exist in `test_tenant_isolation.py` and `scripts/go_live/pg_lifecycle.py`. |
| Multiple roles and secure workspace switcher | PARTIAL (P2) | Role/permission data model exists; no complete public evidence proves a user can securely switch every required workspace without privilege leakage. |
| Customer/booker workspace | IMPLEMENTED BUT UNVERIFIED | `client.html`, `backend/client_portal.py`, `test_client_portal.py`; full persona UAT and every linked counter/filter remain unsigned. |
| Driver mobile workspace | IMPLEMENTED BUT UNVERIFIED | `driver.html`, `backend/driver_app.py`, `test_driver_app.py`; field-operability UAT and production notification/tracking evidence remain open. |
| Fleet-owner/dispatcher workspace | IMPLEMENTED BUT UNVERIFIED | `portal.html`, `backend/carrier_portal.py`, `backend/fleet_registration.py`, related tests; multi-vehicle business-owner UAT remains open. |
| Founder-only authorization boundary | MISSING / P1 for directive acceptance | No dedicated Founder role/workspace or MFA-protected founder route was found in the public HTML or current UI evidence. Ordinary admin must not be treated as Founder. |
| Founder P&L and financial command center | MISSING / P1 for directive acceptance | Reporting and revenue modules exist, but no dedicated screen evidences GBV-versus-revenue, liabilities, P&L, EBITDA, cash flow, budget/actual, drill-down and founder-only export as one reconciled dashboard. |

### 6–8. Booking, matching and pricing

| Requirement | Classification | Evidence / remaining gap |
|---|---|---|
| Canonical booking record and status history | VERIFIED WORKING in code/CI | `backend/public_booking.py`, `backend/core.py`, `test_public_booking.py`, `test_booking_experience.py`, 31-step `test_http_e2e.py`. |
| Cargo weight/dimensions/handling before recommendation | VERIFIED WORKING in candidate | Server-side suitability and public flow tests cover weight, dimensions, body, refrigeration and manual-assessment conditions. |
| Vehicle recommendation blocks unsuitable units | VERIFIED WORKING | Canonical catalogue plus suitability tests in `test_vehicle_catalogue_qa.py`, `test_quotation_pricing.py`, `test_public_booking.py`. |
| Multi-stop, helpers and special handling | PARTIAL | Data/rule support exists in booking and pricing modules; complete persona and exception UAT is not signed. |
| Payment-gated operational advancement | VERIFIED WORKING for synthetic records | `backend/core.py`, `backend/marketplace_payments.py`, `test_protected_payment_e2e.py`; live funds remain blocked. |
| Trip/POD/review/closure lifecycle | VERIFIED WORKING in synthetic CI | `backend/marketplace_trips.py`, delivery-verification modules and 31-step HTTP E2E. Production role UAT remains open. |
| Deterministic eligibility before ranking | VERIFIED WORKING | `backend/marketplace_matching.py`, `test_marketplace_matching.py`, onboarding/trust tests. |
| Dispatch waves, load board and managed RFQ | IMPLEMENTED BUT UNVERIFIED | Marketplace matching and quotation modes exist; operational UAT with real dispatch roles is missing. |
| Atomic/idempotent assignment | VERIFIED WORKING in code/CI | Unique/idempotency guards and concurrent acceptance tests exist. No hosted multi-instance production-parity artifact is current. |
| Fare calculator uses booking pricing authority | VERIFIED WORKING in candidate | `fare-calculator.html` calls canonical public quote behavior; benchmark record states `public_booking.quote` is the only authority. |
| Immutable fare snapshot/version/effective date | VERIFIED WORKING | `backend/public_booking.py`, `backend/rates.py`, `test_quotation_pricing.py`, `docs/marketplace/COMPETITIVE_FARE_BENCHMARK_2026-10-09.md`. |
| Transparent breakdown and deterministic rounding | VERIFIED WORKING | Pricing tests cover components, historical snapshots and server authority. |
| Commercially approved Philippine rate matrix | BLOCKED (P1 for paid launch) | Current rates are benchmark-informed planning values. Founder/Finance approval, contribution-margin review and tax advice are not recorded. |
| 10% administration fee activation | BLOCKED | Logic exists, but the master directive explicitly requires founder approval. It must not silently become a new live policy. |
| Route/distance production evidence | PARTIAL | Geography hierarchy is implemented; governed production mapping/distance-provider evidence is not complete. No fabricated distance should be treated as a final fare. |

### 9–13. Membership, registration, ledgers, payments and exceptions

| Requirement | Classification | Evidence / remaining gap |
|---|---|---|
| Free registration separated from paid membership | VERIFIED WORKING as policy/control | Pages advertise free registration; SaaS/membership code separates entitlement and billing. Proposed tier prices remain inactive. |
| Subscription lifecycle/entitlements | IMPLEMENTED BUT UNVERIFIED | `backend/saas.py`, `test_phase10_saas.py`; no approved live billing provider or end-user UAT. |
| Driver registration | IMPLEMENTED BUT UNVERIFIED | `driver-register.html`, `test_driver_registration_page.py`; includes identity/licence/service/document fields, but accountable applicant UAT and production document review are open. |
| Individual owner and vehicle registration | IMPLEMENTED BUT UNVERIFIED | `provider.html`, canonical catalogue, onboarding/accreditation tests; owner persona UAT remains open. |
| Fleet registration and driver/vehicle assignment | VERIFIED WORKING in CI | `backend/fleet_registration.py`, `test_fleet_registration.py`, `test_carrier_portal.py`; business UAT remains open. |
| Progressive disclosure | PARTIAL (P2) | Public forms are segmented, but no complete cognitive-load/accessibility sign-off exists for every role. |
| Separate financial ledgers | VERIFIED WORKING in code/CI | Billing, protected payment, payouts, deposits and SaaS use distinct governed records; financial invariant and payment tests cover isolation/idempotency. |
| Refundable security deposit | VERIFIED FAIL-CLOSED | `backend/provider_security_deposit.py`; seeded amount is zero/inactive. Founder/legal/PSP approval is required before collection. |
| Canonical internal Payment API | VERIFIED WORKING in sandbox/synthetic mode | `backend/payment_gateway.py`, `backend/integrations.py`, gateway/payment/red-team tests. |
| GCash/Maya/QR Ph/cards/bank/OTC live channels | BLOCKED | No production provider certification/credentials/reconciliation evidence. UI must not imply live availability. |
| Live provider payouts/refunds | BLOCKED | Governed request/ledger controls exist; no approved live provider reference and reconciliation pilot. |
| Protected Payment terminology/control | VERIFIED WORKING | Fail-closed provider-managed model and legal/payment records exist; no unsupported escrow approval claim. |
| Payment webhook/replay/idempotency/security | VERIFIED WORKING in synthetic CI | Gateway, trust-closure, integration and red-team tests. Independent provider/live callback certification remains open. |
| Exception ownership and state handling | PARTIAL | Cancellation, reassignment, dispute, damage, failed payment/payout and trip exceptions have modules/tests; a single complete operational ownership matrix and all-persona UAT are not closed. |

### 14–17. Competition, security, performance and UAT

| Requirement | Classification | Evidence / remaining gap |
|---|---|---|
| Ethical competitor benchmark | VERIFIED WORKING as point-in-time research | `docs/marketplace/COMPETITIVE_FARE_BENCHMARK_2026-10-09.md` cites official Lalamove/Transportify pages and distinguishes evidence from proposals. |
| Payout/deposit benchmark | VERIFIED WORKING as research | `docs/PROVIDER_PAYOUT_BENCHMARK_AND_POLICY.md`; explicitly records that Lalamove publishes a deposit model while Transportify publishes no initial cash deposit. |
| Product differentiation decisions | PARTIAL | Backhaul, corporate, heavy haul and fleet tooling exist in architecture/modules, but commercial validation and adoption evidence are absent. |
| Privacy/security control implementation | VERIFIED WORKING in code/CI | Authorization, tenant isolation, rate limiting, session controls, CORS/startup guards, audit logging and security tests exist. |
| Regulatory/legal readiness | BLOCKED | Registers exist under `docs/regulatory/` and `docs/legal-payment/`; there is no verified BSP/LTFRB approval or licensed-counsel sign-off. No approval claim is permitted. |
| Secrets and production configuration fail closed | VERIFIED WORKING | `_production_config_errors` and `backend/test_deploy.py` block unsafe secrets, origins, databases and payment activation. |
| Accessibility/browser tests | VERIFIED WORKING for approved public candidate | 48/48 Playwright checks across Chromium, Firefox and WebKit. Screen-reader/manual assistive-tech sign-off is still open. |
| Performance and concurrency correctness | PARTIAL (P1 before scaled pilot) | Automated smoke/idempotency workflows and plans exist. Current hosted load, connection-pool saturation, failover and long-running transaction evidence is not complete. |
| Backup/restore/restart persistence | IMPLEMENTED BUT UNVERIFIED (P1) | Scripts and workflows exist (`scripts/go_live/backup_restore.py`, PostgreSQL lifecycle workflow); no current production-like restore artifact and approved RPO/RTO record for the release. |
| Persona UAT for all 12 roles | BLOCKED / P1 | Synthetic automated tests exist, but accountable UAT sign-off for customer, driver, owner, fleet, operations, finance, admin and Founder is absent. |
| Defect register | VERIFIED WORKING | This audit plus `audit/2026-10-12/MVP_DEFECT_REGISTER.csv` is the current register; older performance/readiness registers remain historical evidence, not current truth. |

### 18–21. Execution, deliverables and release gates

| Requirement | Classification | Evidence / remaining gap |
|---|---|---|
| Dedicated development branch and reviewable PR | VERIFIED WORKING | Work is isolated on `codex/fix-exact-release-gate`; PR #19 is open with green SQLite and PostgreSQL checks. |
| Automated regressions for material changes | VERIFIED WORKING | Deployment/monitor corrections include `backend/test_deploy.py` coverage. |
| PostgreSQL validation | VERIFIED WORKING in CI | PR #19 PostgreSQL runtime + production E2E check is green. Current hosted restore/load evidence remains separate and open. |
| Executive system audit | VERIFIED WORKING | This document. |
| Master business requirements | VERIFIED WORKING as directive; PARTIAL as implementable policy | The directive is preserved; commercial/legal decisions remain unapproved. |
| UI/UX finalization evidence | PARTIAL | Candidate code and browser tests exist; live before/after evidence is blocked by failed deployment. |
| Booking/dispatch architecture | VERIFIED WORKING in code/CI | Marketplace foundation, matching, protected-payment and trip execution records/tests. |
| Commercial/pricing package | PARTIAL / BLOCKED | Benchmark and configurable engine exist; final tariffs, tax, membership, cancellation and provider-share approvals do not. |
| Founder financial package | MISSING / P1 | No dedicated, reconciled Founder dashboard/P&L evidence. |
| Payments/compliance register | VERIFIED WORKING | Current certification and legal-payment documents explicitly retain open gates. |
| Quality/production evidence | PARTIAL | CI evidence is strong; live deployment, business UAT, hosted restore/load and rollback drill remain open. |
| Development handover | PARTIAL | Current branch/PR and audit identify changes and blockers. Final exact release/deployment manifest awaits a successful canonical deployment. |

## MVP boundary and acceptance gates

For this programme, MVP means a controlled no-live-funds marketplace that can truthfully demonstrate:

1. approved public discovery, vehicle catalogue, fare estimate and booking request;
2. role-specific customer, driver, owner and fleet registration/login;
3. server-enforced cargo/vehicle suitability and canonical fare snapshots;
4. eligible provider matching, single assignment, trip state and POD;
5. synthetic protected-payment, fee, settlement and payout records without moving money;
6. operational staff/admin controls, tenant isolation and auditable actions;
7. founder-only reconciled financial visibility sufficient to distinguish GBV, revenue, liabilities and cash;
8. canonical-domain monitoring, persistent PostgreSQL, recovery evidence and rollback;
9. signed persona UAT with no open P0/P1 or understanding/safety-related P2 defect.

Live collection, disbursement and escrow-like custody are **not** MVP capabilities until the external
payment and legal gates pass; the MVP must visibly remain in no-live-funds mode.

## Outstanding launch defects and blockers

| ID | Severity | Status | Owner / required closure |
|---|---|---|---|
| MVP-001 | P1 | OPEN | Railway/Engineering: diagnose container startup/health failure and deploy the exact approved release to the custom-domain service. |
| MVP-002 | P1 | OPEN | Engineering/QA: prove `www.lifthaul.com.ph/data/psgc/manifest.json` and the booking geography journey after deployment. |
| MVP-003 | P1 | OPEN | Product/Finance/Engineering: implement and reconcile a Founder-only dashboard; add MFA/authorization tests. |
| MVP-004 | P1 | OPEN | QA/UAT/Founder: execute and sign the 12-persona acceptance pack. |
| MVP-005 | P1 | OPEN | DevOps/DB owner: execute PostgreSQL backup/restore, restart persistence and rollback drill with RPO/RTO evidence. |
| MVP-006 | P1 | OPEN for paid launch | Founder/Finance/Legal: approve tariffs, tax, membership, cancellation, provider share and 10% fee policy. |
| MVP-007 | P1 | BLOCKED for live money | Founder/Legal/Finance/PSP/Security: provider contract, credentials, safeguarded-funds model, callback tests, reconciliation, DR and pilot approval. |
| MVP-008 | P2 | OPEN | UX/QA: authenticated-workspace navigation, synthetic-data isolation and responsive state-by-state screenshot audit. |
| MVP-009 | P2 | OPEN | Operations/Product: consolidate exception ownership, SLA and communication rules and UAT each path. |
| MVP-010 | P2 | OPEN | Performance/Engineering: hosted concurrency, slow-network, retry, connection-pool and geographic-search evidence. |

## Work completed in this audit cycle

- Verified Git/PR/CI/live-domain/Railway state without treating source existence as production proof.
- Retested deployment configuration locally; 15/15 deployment/startup-guard tests passed.
- Ran a 20-module MVP regression pack covering deployment, navigation, release copy, theme, vehicle
  catalogue, booking, pricing, matching, protected payment, gateway, payout, deposit, security, tenant
  isolation, registration and role workspaces: **379/379 passed**. The run emitted pre-existing
  unclosed-fixture `ResourceWarning` messages but no test failure.
- Corrected the production health monitor to probe the canonical public domain, the live release SHA,
  PSGC manifest, public release copy, `/healthz` and `/readyz`.
- Corrected the exact-release workflow so wait, preflight and synthetic E2E target the canonical domain
  rather than a different Railway hostname.
- Added a regression test that prevents either workflow from drifting back to the unrelated endpoints.
- Retried the authorized PR #18 Railway deployment; the second attempt again failed the health check,
  establishing an infrastructure/startup blocker rather than declaring a false success.
- Removed a duplicate container-start schema bootstrap: `server.py` already applies the complete
  idempotent schema before listening, so running `migrate.py` immediately beforehand repeated the same
  work inside Railway's health window. Added flushed migration logs plus server-side database bootstrap
  start/completion telemetry. The focused deployment/server/PostgreSQL compatibility pack passed 38/38.

## Next three executable actions

1. Push the tested startup/monitoring correction through PR review and obtain exact founder approval for
   its resulting release SHA; do not deploy the new SHA before that approval.
2. Implement the minimum Founder-only financial read model and dashboard with GBV/revenue/liability
   separation, MFA-sensitive authorization and deterministic tests.
3. Run the automated persona pack plus a production-like PostgreSQL backup/restore/concurrency exercise,
   then issue the business UAT checklist for accountable sign-off.

