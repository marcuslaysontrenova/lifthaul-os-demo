# Volume 5 — Product Roadmap & Traceability Matrix

> Every capability mapped to a platform, business capability, phase, dependency, test
> coverage, and acceptance criteria. This volume is the **execution contract**: no
> work item is built unless it appears here (or is added here through the Governance
> Intake) with its gate answered. Version 0.1 (DRAFT).

---

## 1. Phasing model

Phases are **dependency-ordered**, not date-ordered (dates are the CTO's to set). Each
phase has an exit gate; the next phase does not start until the gate passes.

| Phase | Theme | Exit gate |
|---|---|---|
| **P0 — Foundation hardening** | Close the operate-safely gaps on what exists | Live PostgreSQL E2E (15-check `DEPLOYMENT_VALIDATION.md`) passes; CI secret-scan green |
| **P1 — Make it a product** | Tenant dimension + Platform 1 (Administration) core | 2nd tenant isolated (test); data-driven RBAC enforced; approval threshold configurable — all without code change |
| **P2 — Trustworthy UI** | Dialog Standard + token migration + real backend wiring | Every dialog declares a mode; localStorage retired; console reads/writes the API |
| **P3 — Configurable operations** | Workflow engine + Dispatch/Fleet/Finance admin | Booking/approval/dispatch rules run from config with parity to today |
| **P4 — Open edges & insight** | Integration platform + Analytics baseline | API gateway + 1 real connector (Wise) live; KPI dashboard from warehouse |
| **P5 — Intelligence & scale** | AI platform + finance depth + mobile + observability | Governed AI with human override; monitoring/alerting live; approver mobile flow |

## 2. Traceability matrix (capability-level)

Legend — **Sev:** P0/P1/P2 (from Volume 1 §6). **Cov:** existing test coverage today.

| Cap ID | Capability | Platform | Phase | Depends on | Sev | Cov today | Acceptance (summary) |
|---|---|---|---|---|---|---|---|
| C-001 | Live PostgreSQL runtime proof | 9 | P0 | Docker/PG host | P1 | static only (`test_pg_portability`) | 15/15 `DEPLOYMENT_VALIDATION.md` checks pass |
| C-002 | CI secret-scan | 9 | P0 | — | P1 | no-secrets asserted in tests | pipeline fails on any secret in code/logs/bundle |
| C-003 | Tenant/Organization dimension | 1 | P1 | C-001 | P0 | **✅ foundation (14 tests)** | `tenants` table; tenant-scoped roles/config; isolation proven (test) — *tenant_id backfill on domain rows still pending* |
| C-004 | Company Profile + Org structure | 1 | P1 | C-003 | P0 | none | branches/departments/BU/cost centers CRUD + audit |
| C-005 | Data-driven RBAC (roles/permissions) + enforcement cutover | 1 | P1 | C-003 | P0 | **✅ DELIVERED (19 tests)** | admin creates role→user enforced server-side w/o code change (proven); `server._actor` enriches every request; `iam.rbac_source` flag (legacy/hybrid/db) reversible; backfill maps legacy roles at parity; 115-test suite green |
| C-006 | User lifecycle admin | 1 | P1 | C-005 | P1 | **✅ DELIVERED (8 tests)** | invite→active↔suspend/lock→deactivate(offboard); non-active users blocked at login + mid-session (core gate); status change & password reset revoke sessions; permission review + per-user audit; 123-test suite green |
| C-007 | Sessions/MFA/Password policy/Login history | 1 | P1 | C-005 | P1 | **✅ DELIVERED (16 tests)** | config-driven password policy; persistent login history; consecutive-failure lockout; TOTP MFA (enroll/confirm/verify, policy off/optional/required); session list/revoke; guarded `/login` wired; 133-test suite green |
| C-008 | Configuration cascade + Limits Registry | 1 | P1 | C-003 | P0 | **✅ foundation (14 tests)** | `platform_config` cascade platform→tenant→unit→user with visible source (proven); 5 default limits seeded — *consumers still read code constants* |
| C-009 | Dialog Standard adoption | 3 | P2 | — | P1 | none | every dialog renders correct mode chip; lint flags missing mode |
| C-010 | Token migration (retire inline hex) | 3 | P2 | — | P1 | n/a | no literal color/spacing outside token set |
| C-011 | Console wired to real backend | 2/3 | P2 | C-003,C-005 | P1 | backend tested; UI on localStorage | console CRUD persists via API; refresh/restart safe |
| C-012 | Customer portal | 2 | P2 | C-011 | P2 | none | customer views quotes/invoices scoped to their org |
| C-013 | Workflow engine + seed definitions | 1/3 | P3 | C-008 | P0 | logic in code | booking/job/quotation run from config at parity (tests) |
| C-014 | Approval Matrix (configurable thresholds) | 1 | P3 | C-013 | P0 | ₱500k hard-coded | threshold change reroutes approval w/o code change |
| C-015 | Finance Rules (dp %, tax) configurable | 1/4 | P3 | C-013 | P1 | 30%/12% hard-coded | change dp/tax reflected in new quotes w/o code change |
| C-016 | Dispatch/Fleet admin + rules | 1/3 | P3 | C-013 | P1 | reservation guard in code | double-book policy + assignment from config |
| C-017 | API Gateway + Open API + Webhooks | 7 | P4 | C-005 | P1 | none | external API-key auth, rate limit, signed webhooks, delivery log |
| C-018 | Wise connector (real) | 7 | P4 | C-017 | P1 | mock only | real payment link + verified callback (idempotent) |
| C-019 | Accounting/ERP connector (QuickBooks) | 7 | P4 | C-017 | P2 | none | invoices sync outbound; reconciled |
| C-020 | Analytics warehouse + KPI dashboards | 5 | P4 | C-011 | P1 | `/reports` rollup | KPI set from warehouse; exec + ops dashboards |
| C-021 | AI platform (registry/prompts/confidence/override) | 6 | P5 | C-020 | P1 | demo assistant only | every AI output has model+confidence+human override, logged |
| C-022 | Finance depth (GL/tax/forecast/budget) | 4 | P5 | C-015 | P2 | expenses/invoices only | trial balance + tax report + cash-flow forecast |
| C-023 | Mobile surfaces (approver/driver/customer) | 8 | P5 | C-009,C-011 | P2 | responsive web | approver acts on mobile; driver captures signature/GPS offline |
| C-024 | Monitoring/Alerting/Feature flags | 9 | P5 | C-001 | P1 | logs only | flags gate rollout; alerts fire on SLA/error breach |

## 3. Reprioritized backlog (immediate order)

1. **C-001, C-002** — earn the right to build on the foundation (finish the open gate).
2. **C-003, C-005, C-008** — the tenant + data-driven RBAC + config spine. This is the
   moment LiftHaul OS becomes a product (ED-001).
3. **C-004, C-006, C-007** — the rest of Administration core.
4. **C-009, C-010, C-011** — make the UI trustworthy and put the real backend in charge.
5. **C-013–C-016** — configurable operations (workflow engine + admin).
6. Then P4/P5 per the matrix.

Anything not on this list is **not authorized**. New ideas enter through §4.

## 4. Governance Intake (ED-005) — the required proposal template

Every new work item is proposed by filling this out. If any answer is blank or "TBD,"
the item is **not ready to build**.

```
WORK ITEM: <name>
1. Platform owner:            <1–9>
2. Business capability:       <what capability in Vol 1/2 does this serve?>
3. Users:                     <which personas/roles use it?>
4. Approving roles:           <which roles approve its use/config?>
5. Master data dependencies:  <which catalogs/config does it read?>
6. Workflow changes:          <new/changed states, transitions, guards?>
7. Audit logging:             <which audit events does it emit?>
8. Reporting impact:          <which KPIs/reports change?>
9. Security impact:           <permissions, tenant scope, secrets, PII?>
10. Configuration vs code:    <what part is config (ED-004) vs true business logic?>

Traceability: assign Cap ID, Phase, Dependencies, Test plan, Acceptance criteria.
```

## 5. Definition of Done (every capability)

- Traceable to a Cap ID here and authorized for the current phase.
- Tenant-scoped; permissioned; emits audit events; config-first where applicable.
- Unit tests + (if cross-cutting) HTTP E2E; portability guard stays green.
- Dialogs declare their mode (ED-003); no literal tokens (Volume 3).
- External effects verified against reality, not just code success.
- Docs updated (the blueprint volume it touches + API/OpenAPI if applicable).

## 6. What changes about how we work

- **Before:** "Build feature X." → **Now:** a Governance Intake proposal → Cap ID →
  phase-gated build → traceable acceptance.
- The CTO (Enterprise Product Orchestrator) maintains this matrix; Claude implements
  strictly against authorized Cap IDs and refuses scope that lacks one.
- The blueprint is versioned in `docs/blueprint/`; changes to it are themselves
  governed (a blueprint change is a work item).
