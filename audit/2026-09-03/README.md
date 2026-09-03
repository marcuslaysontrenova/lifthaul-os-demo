# LiftHaul hostile product audit — 3 September 2026

## Decision

**NO-GO for a public commercial release.** The current GitHub Pages site is suitable only as a clearly labelled, read-only pre-launch demonstration. It must not accept real bookings, identity documents, live location data, or money.

The release is blocked by **2 Critical, 15 High, 5 Medium** open findings. The most serious are:

1. The client demo's protected-payment breakdown does not add up and contradicts the approved three-line pricing model.
2. The backend accepts missing and nonnumeric cargo weight through a legacy path that bypasses deterministic vehicle eligibility.
3. A concurrent duplicate-submission probe persisted two bookings for one idempotency key in one run; replay responses also violate the original response contract.
4. The deployed frontend has no hosted API, so booking, authentication, provider onboarding, tracking, staff access, notifications, and payments are not operational.

## Evidence-based readiness score

**24% release readiness.** This is a weighted evidence score, not a probability or a claim of production capacity.

| Area | Weight | Earned | Reason |
|---|---:|---:|---|
| Financial and security controls | 25 | 10 | Strong mock/unit controls, but the client ledger is wrong and no live partner or pentest exists. |
| Booking and safe matching | 20 | 8 | Cargo-first UI and deterministic matcher exist; final submission can bypass both. |
| Role workspaces and permissions | 15 | 3 | Backend RBAC tests pass; deployed role logins and real workflows do not operate. |
| Production infrastructure and resilience | 15 | 1 | Static Pages is available; there is no production API, observability, load, recovery, or chaos evidence. |
| Legal, tax, privacy, and regulatory readiness | 10 | 1 | Draft disclosures exist; required approvals, DPO/process evidence, and provider structure are incomplete. |
| Support and incident operations | 5 | 0 | Public support contacts, escalation paths, and service levels are absent. |
| Moderated UAT, accessibility, and device coverage | 10 | 1 | Mobile DOM remains navigable; no representative-user or assistive-technology evidence exists. |
| **Total** | **100** | **24** | **Commercial release blocked.** |

## What was tested

- Deployed GitHub Pages workflows on desktop and a 390 × 844 mobile viewport.
- Landing, booking, client, provider, carrier, driver, staff, tracking, support, and policy pages.
- Client dashboard totals, filters, pagination, protected-payment ledger, and detail view.
- Source-level review of configuration, booking, matching, pricing, authentication, uploads, payment, settlement, and policies.
- Complete backend regression run: **1,361 passed, 3 warnings in 714.22 seconds**.
- Independent controlled hostile probe against temporary local SQLite state.
- Current competitor capabilities using official Transportify, Lalamove, Grab, and inDrive sources.
- Philippine payment, transport, tax-invoice, and privacy readiness against official BSP, LTFRB, BIR, and NPC sources.

The automated suite is evidence of implemented defensive logic, not proof of launch readiness. It currently passes while explicitly testing legacy bookings with no cargo weight, and it does not reconcile the static client-demo ledger.

## Deliverable index

1. [Role-by-role journeys](role-journeys.md)
2. [Competitor feature matrix](competitor-benchmark.md)
3. [Functional, security, performance, mobile, connectivity, and chaos results](functional-security-resilience.md)
4. [Payment, protected-payment, pricing, and tax reconciliation](payments-pricing-tax.md)
5. [Regulatory-readiness gaps](regulatory-readiness.md)
6. [Prioritized defect register](defect-register.csv)
7. [Product-improvement backlog](product-backlog.md)
8. [Release recommendation and Go/No-Go evidence](release-recommendation.md)
9. [Evidence log and reproducible probe](evidence-log.md) / [hostile_probe.py](hostile_probe.py)

Together these files cover the 13 mandated outputs. Security findings, performance results, mobile/connectivity findings, and the final decision are separated into sections in the linked reports instead of being presented as unsupported standalone certifications.

## Audit limits

- No real user, provider, vehicle, document, payment, tax, or location data was used.
- No destructive, aggressive, or unauthorized testing was performed against GitHub Pages, competitors, or third parties.
- Live payment/refund/payout, GPS, SMS, email, push, document malware scanning, disaster recovery, and production-scale load could not be tested because the required production integrations do not exist.
- A passed mock or unit test is recorded as such and never upgraded to a live-system pass.

