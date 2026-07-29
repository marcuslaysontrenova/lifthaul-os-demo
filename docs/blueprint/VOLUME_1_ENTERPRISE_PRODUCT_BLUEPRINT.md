# Volume 1 — Enterprise Product Blueprint

> The complete architecture and capability model. This volume defines the nine
> platforms, the multi-tenancy model, the capability map, and — most importantly —
> an honest validation of the **current implementation** against the target, with a
> ranked gap register. Version 0.1 (DRAFT).

---

## 1. Product principles (derived from ED-001…005)

1. **Capability over feature.** We build capabilities that belong to a platform. A
   "screen" is an expression of a capability, never the unit of planning.
2. **Multi-tenant by construction.** Every table, API, and permission is scoped to a
   tenant (organization) from day one of Phase 2. Single-tenant assumptions are debt.
3. **Configuration over code (ED-004).** Limits, states, templates, fields, catalogs,
   and rules are data an administrator owns — not constants a developer changes.
4. **Governed change.** Every mutating action emits an audit event; every sensitive
   action is permissioned and, where required, separated (maker ≠ checker).
5. **Delivery success ≠ code success.** A capability is "done" only with tests and,
   for external effects, verified real-world outcome (carried over from the email/send
   lessons — see backend `CLAUDE.md` legacy note).

## 2. The nine platforms (capability model)

Each platform is an **ownership boundary**. A capability lives in exactly one platform;
cross-platform needs are contracts, not shared tables.

| # | Platform | Mandate | Revenue? | Owns (canonical) |
|---|---|---|---|---|
| 1 | **Enterprise Platform Administration** | The operating system of LiftHaul — identity, org, config, governance | No (enabler) | Tenants, users, roles, permissions, modules, workflows, master data, config, branding, licensing, audit, security, integrations registry, AI admin |
| 2 | **Commercial** | Everything before operations — win and contract the work | Yes | Customers, CRM, leads, opportunities, bookings, quotations, contracts, pricing, credit, invoices, collections, customer portal |
| 3 | **Operations** | Execute the job safely and on time | Indirect | Dispatch, fleet, equipment, scheduling, maintenance, safety, incidents, jobs, GPS, crew, operators, drivers |
| 4 | **Financial** | Independent finance system of record | Yes | Chart of accounts, expenses, revenue, profitability, billing, collections, taxes, forecasting, cash flow, budget |
| 5 | **Analytics** | Business intelligence and decision support | No (enabler) | KPIs, dashboards, forecasting, AI analytics, executive/operational reporting, data warehouse |
| 6 | **AI** | Centralized, governed intelligence | No (enabler) | AI admin, prompt library, model registry, confidence rules, recommendation engine, learning logs, human override |
| 7 | **Integration** | The product's edges | No (enabler) | API gateway, webhooks, SAP/Oracle/QuickBooks/Wise/Google/Microsoft connectors, Open API, SDK |
| 8 | **Mobile** | Field and customer surfaces | Indirect | Driver/operator/customer/approver apps, offline sync, GPS, camera, QR, signatures |
| 9 | **DevOps** | Keeps the SaaS alive | No (enabler) | Environments, feature flags, deployment, monitoring, logs, alerts, backups, recovery, secrets, performance |

**Platform dependency order (build/enable sequence):** 1 → (9 in parallel) → 2 → 3 → 4
→ 5 → 6 → 7 → 8. Administration and DevOps are prerequisites for everything;
Commercial precedes Operations precedes Financial close-out; Analytics/AI sit above a
populated data model; Integration and Mobile are surfaces over stable domains.

## 3. Multi-tenancy model

- **Tenant = Organization.** Top-level isolation boundary. RGO Machine Rigging is
  `tenant_id = 0/RGO`. Branding, licensing, config, and master data are per-tenant.
- **Isolation strategy (target):** row-level tenant scoping (`tenant_id` on every
  domain row) with a mandatory tenant filter in the data-access layer, plus
  per-tenant encryption of secrets. Schema-per-tenant is a future option for large
  accounts; row-level is the Phase-2 default.
- **Identity is tenant-scoped.** A user belongs to one tenant (cross-tenant support
  staff are a separate, explicitly-elevated construct in Volume 2 §Support Access).
- **Config cascade:** Platform defaults → Tenant config → Business-unit/branch
  override → User preference. Later, more specific wins (Volume 2 §Configuration Cascade).

> **Current reality:** the system is **single-tenant** (one database, no `tenant_id`,
> roles hard-coded in `core.PERMISSIONS`). Introducing the tenant dimension is the
> #1 architectural gap (§6).

## 4. Capability → data-domain map (grounded in today's schema)

The existing backend already implements a substantial slice of Platforms 2–4 and part
of 1/9. Mapping the **real ~37 tables** to platforms:

| Platform | Implemented today | Partial | Missing |
|---|---|---|---|
| 1 Administration | `users`, `sessions`, `audit_logs`, `system_config`, `master_data`, `notification_templates`, `documents` | RBAC (code-only `PERMISSIONS`, not data) | **tenants/orgs, branches, departments, business units, cost centers, holidays, user_groups, MFA, password_policy, login_history, modules, workflow definitions, licensing, branding, integration registry, AI admin** |
| 2 Commercial | `customers`, `contacts`, `addresses`, `bookings`, `booking_messages`, `quotations`, `quotation_lines`, `payment_requests`, `invoices`, `invoice_lines`, `payment_allocations`, `refunds` | Pricing (in-code), credit (none) | leads, opportunities, contracts, credit policy, customer portal, pricing catalog/policy |
| 3 Operations | `jobs`, `job_stage_history`, `site_assessments`, `reservations`, `equipment`, `vehicles`, `employees`, `maintenance_work_orders`, `inspections`, `safety_records`, `incidents`, `subcontractors` | Dispatch (calendar only), GPS (demo only) | crew rostering, operator/driver licensing, real GPS telematics, scheduling optimizer |
| 4 Financial | `expenses`, `change_orders`, `suppliers`, `purchase_orders`, `supplier_invoices`, `inventory_items`, `inventory_movements` | Billing/collections (via invoices), profitability (per-job report) | chart of accounts, taxes engine, forecasting, cash flow, budget, GL |
| 5 Analytics | `/reports` (profitability rollup) | — | KPIs, dashboards, warehouse, exec reporting, forecasting |
| 6 AI | — | Front-end "AI Quotation Assistant" (demo, not backed) | entire platform |
| 7 Integration | `PaymentProvider`/`MockWiseProvider`, notification sender interface | — | API gateway, webhooks, ERP/accounting connectors, Open API, SDK |
| 8 Mobile | Responsive web only | — | native/PWA apps, offline sync, device capture |
| 9 DevOps | Docker, `migrate.py`, `schema_version`, `/health`, `/ready`, CORS, config validation, backup/restore, structured logging | Secrets (`security.SecretManager` interface) | feature flags, monitoring/alerting, environment mgmt, recovery runbooks |

## 5. Current-state validation — what is genuinely solid

Not everything is a gap. The following are **blueprint-compliant today** and should be
preserved, not rebuilt:

- **Commercial spine with governance.** customer→booking→quotation(+versioning)→
  approval with **separation of duties**→acceptance→payment-request→finance
  verification→**gated confirmed job**→job lifecycle→change orders→expenses→final
  invoice→allocation→close→profitability. This is a correct enterprise control chain.
- **Auditability.** `audit_logs` append-only trail; soft-delete instead of hard delete.
- **AuthN/Z primitives.** pbkdf2 password hashing, token sessions, server-side RBAC
  enforcement (`require(actor, permission)`).
- **Data-layer portability.** SQLite (dev/test) ↔ PostgreSQL (prod) via `dbconn.py`,
  statically proven complete (`test_pg_portability.py`; see backend `PG_PORTABILITY_AUDIT.md`).
- **Deployment discipline.** Config validation refuses unsafe prod start; `/health`,
  `/ready`; Docker Compose full stack; backup/restore round-trip verified.
- **Test posture.** 96 automated tests incl. a real threaded HTTP E2E + restart
  persistence.

These map cleanly onto Platforms 2/3/4/9 and are the load-bearing assets we extend.

## 6. Gap register (ranked)

Severity: **P0** blocks the enterprise/SaaS thesis · **P1** blocks a platform ·
**P2** quality/scale.

| ID | Gap | Platform | Sev | Why it matters |
|---|---|---|---|---|
| G-01 | **No tenant/organization dimension** | 1 | P0 | Without it LiftHaul OS is an app, not a SaaS product. Blocks ED-001 thesis. |
| G-02 | **RBAC is code, not data** (`PERMISSIONS` dict) | 1 | P0 | Violates ED-004. Roles/permissions must be admin-configurable. |
| G-03 | **No configuration platform** (limits, states, templates, fields hard-coded) | 1 | P0 | ED-004 core. Approval limits, quote expiry, workflow states must be data. |
| G-04 | **No workflow engine** (states/transitions in code) | 1/3 | P0 | Booking/approval/dispatch/finance/SLA rules must be configurable (ED-002 tree). |
| G-05 | **UI has no Dialog Standard** | 3 | P1 | ED-003. Informational vs edit vs action dialogs are indistinguishable today. |
| G-06 | **Front-end is a localStorage prototype** | 2/3/8 | P1 | Not wired to the real backend; not a system of record. |
| G-07 | **No integration platform** (gateway/webhooks/connectors) | 7 | P1 | Enterprise buyers require SAP/Oracle/QuickBooks/Wise/SSO. |
| G-08 | **No analytics/warehouse** | 5 | P1 | Only a single profitability rollup exists. |
| G-09 | **AI not centralized/governed** | 6 | P1 | Demo-only; needs model registry, prompt library, confidence + human override. |
| G-10 | **No feature flags / monitoring / alerting** | 9 | P1 | Required to operate multi-tenant SaaS safely. |
| G-11 | **No IAM depth** (MFA, password policy, groups, login history, sessions admin) | 1 | P1 | Security-governance table stakes. |
| G-12 | **Finance lacks GL/tax/forecast** | 4 | P2 | Needed for finance independence (Platform 4 mandate). |
| G-13 | **No mobile/offline surfaces** | 8 | P2 | Field capture, signatures, GPS depend on it. |
| G-14 | **Live PostgreSQL runtime proof not executed** | 9 | P1 | Carried from prior gate; needs Docker/PG host validation. |

## 7. Reprioritized product thesis (the "why now" order)

1. **Make it a product, not an app** (G-01, G-02, G-03, G-04, G-11): stand up
   Platform 1 (Administration) + the tenant dimension. Everything else inherits from it.
2. **Make the UI trustworthy** (G-05): ratify and apply the Dialog Standard so the
   admin ERP and operational screens are unambiguous.
3. **Make the real backend the system of record** (G-06): replace localStorage with
   authenticated API calls; the demo becomes the reference UX, not the data store.
4. **Open the edges** (G-07) and **light up insight** (G-08, G-09).
5. **Operate it safely at scale** (G-10, G-14) and **deepen finance/mobile** (G-12, G-13).

Volume 5 turns this into phases with dependencies, tests, and acceptance criteria.

## 8. Non-negotiable invariants (apply to every future build)

- Every domain row carries `tenant_id`; every query is tenant-filtered.
- Every mutating endpoint: permission check → maker/checker where required →
  `audit_logs` event → (optional) workflow transition.
- No business limit/state/template/field is a code constant if an admin could own it.
- Every capability ships with tests; external effects require verified outcomes.
- Every dialog declares its mode (Volume 3, ED-003).
