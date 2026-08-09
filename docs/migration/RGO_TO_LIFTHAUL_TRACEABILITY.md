# RGO → LiftHaul Convergence & Functional-Preservation Traceability

**Type:** release-convergence audit (NOT a feature increment). Scope frozen.
**Date:** 2026-08-09
**Model:** RGO Machine Rigging Services = **Tenant Zero / reference tenant** inside **LiftHaul Enterprise** (one product, not two apps).

## Method

Evidence taken from the live repository: backend modules (`backend/*.py`), the seeded database schema (`CREATE TABLE`), HTTP routes (`server.py`, 504 registered), tests (`backend/test_*.py`, 43 files), and permissions (`admin_platform.py`, `core.PERMISSIONS`). Each capability is classed **PRESERVED / ENHANCED / REPLACED / POST-LAUNCH / MISSING** with a concrete evidence pointer. Nothing is inferred from marketplace tests; RGO operational domains are verified against their own modules/tests.

## Classification key

- **PRESERVED** — RGO capability exists in LiftHaul with equivalent behaviour + a test.
- **ENHANCED** — present and materially stronger than the RGO original (RBAC/SoD, governance, versioning).
- **REPLACED** — RGO mechanism deliberately superseded by a stronger LiftHaul one.
- **POST-LAUNCH** — intentionally deferred; not release-critical for MVP.
- **PARTIAL(UI)** — backend + data + tests present; dedicated admin/portal UI is thin. Backend is not the gap.
- **MISSING** — genuinely absent and a release defect. (**None found — see summary.**)

---

## A. Authoritative RGO function inventory

| # | RGO capability | LiftHaul home | Backend module | Table(s) | API / route | Test | Class |
|---|---|---|---|---|---|---|---|
| 1 | CRM / customers | Enterprise Ops → CRM | `core`,`crm_admin`,`masterdata` | `customers`,`customer_number_sequences` | `/customers`,`/admin/crm/customers/*` | `test_phase3_crm`,`test_ops` | ENHANCED |
| 2 | Contacts | CRM | `catalog` | `contacts` | via CRM admin | `test_catalog` | PRESERVED |
| 3 | Addresses | CRM | `catalog` | `addresses` | via CRM admin | `test_catalog` | PRESERVED |
| 4 | Booking / inquiry | Ops → Booking | `core` | `bookings`,`job_stage_history` | `/bookings/*` (20) | `test_core`,`test_ops`,`test_booking_access_model` | PRESERVED |
| 5 | Customer communication | Booking → Comms | `catalog` | `booking_messages` (internal vs customer-visible) | via booking | `test_catalog` | PARTIAL(UI) |
| 6 | Site assessment | Booking/Ops | `ops` | `site_assessments` | booking-scoped | `test_ops::test_gate` | PRESERVED |
| 7 | Quotation builder | Commercial → Quotations | `core` | `quotations`,`quotation_lines` | `/quotations/*` (11) | `test_quotation_pricing`,`test_ops` | ENHANCED |
| 8 | Standard / quoted rate | Pricing / Rate Cards | `rates` | `rate_cards`,`mkt_rate_cards` | `/admin/.../rate-cards` | `test_quotation_pricing` | ENHANCED |
| 9 | Cost / margin | Commercial / Finance | `ops`,`core` | `quotation_lines`,`expenses` | `/jobs/:id/profitability` | `test_ops::test_actual_cost_and_profitability` | ENHANCED |
| 10 | Quote approval | Commercial Governance | `core`,`workflow`,`wfgov` | `approval_matrices`,`commercial_exceptions` | approval routes | `test_phase4_workflow` | ENHANCED (RBAC/SoD) |
| 11 | Quote revision / versioning | Quotations | `core` | `quotations` (version) | `/quotations/:id/*` | `test_quotation_pricing` | ENHANCED |
| 12 | Customer quote acceptance | Customer Portal | `core` | `quotations`,`bookings` | acceptance route | `test_ops` | PARTIAL(UI) |
| 13 | Downpayment | Finance | `ops` | `invoices`,`payment_allocations` | `/jobs/:id/invoice` | `test_ops::test_final_invoice_deducts_downpayment_and_partials` | PRESERVED |
| 14 | Wise payment link | Payment method | `wise`,`integrations` | `payment_requests`,`provider_transfers` | payment-request routes | `test_admin`,`test_phase7_integrations` | PRESERVED |
| 15 | Credit terms | Commercial / Finance | `crm_admin` | `credit_policies`,`credit_evaluations` | `/admin/crm/customers/:id/evaluate-credit` | `test_phase3_crm` | ENHANCED |
| 16 | Temporary resource hold | Operations | `ops` | `reservations` | reserve route | `test_ops::test_temp_hold_expiry_release` | PRESERVED |
| 17 | Confirmed reservation | Operations | `ops` | `reservations` | confirm route | `test_ops::test_dispatch_requires_confirmed_reservation` | PRESERVED |
| 18 | Job creation | Jobs | `ops` | `jobs` | `/jobs/*` | `test_ops` | PRESERVED |
| 19 | Dispatch | Operations | `ops` | `jobs`,`job_stage_history` | job transition | `test_ops::test_dispatch_requires_verified_payment` | PRESERVED |
| 20 | Dispatch calendar | Operations | `ops` | derived | `ops.calendar` | `test_ops` | PARTIAL(UI) |
| 21 | Equipment assignment | Dispatch/Fleet | `ops`,`catalog` | `equipment` | job/resource | `test_catalog`,`test_ops` | PRESERVED |
| 22 | Driver/operator assignment | Dispatch/Fleet | `ops`,`marketplace_onboarding` | `employees`,`mkt_drivers` | driver routes | `test_ops`,`test_marketplace_onboarding` | ENHANCED (compliance) |
| 23 | Change orders | Jobs/Commercial | `ops` | `change_orders` | `/jobs/:id/change-order`,`/change-orders/:id/approve` | `test_ops::test_only_approved_change_orders_count` | PRESERVED |
| 24 | Job expenses | Finance / Job Costing | `ops` | `expenses` | `/jobs/:id/expense` | `test_ops` | PRESERVED |
| 25 | Actual costing | Finance | `ops` | `expenses`,`cost_centers` | `/jobs/:id/profitability` | `test_ops::test_actual_cost_and_profitability` | PRESERVED |
| 26 | Profitability | Finance / Analytics | `ops` | derived | `/jobs/:id/profitability` | `test_ops` | PRESERVED |
| 27 | Final invoice | Finance | `ops` | `invoices`,`invoice_lines` | `/jobs/:id/invoice`,`/invoices/:id/lines` | `test_ops::test_double_invoice_blocked` | PRESERVED |
| 28 | Collections | Finance | `ops` | `payment_allocations` | `/invoices/:id/allocate` | `test_ops::test_mark_overdue` | PRESERVED |
| 29 | Refunds | Finance / Protected Payment | `ops`,`protected_payment` | `refunds`,`mkt_refunds` | refund routes | `test_ops::test_refund`,`test_protected_payment_e2e` | ENHANCED |
| 30 | Cancellation / rescheduling | Booking/Ops | `ops` | `jobs`,`refunds` | `ops.cancel_and_refund` | `test_ops::test_refund` | PRESERVED (cancel); reschedule PARTIAL(UI) |
| 31 | Suppliers | Procurement | `admin` | `suppliers`,`supplier_invoices` | `admin.sup_create` | `test_admin`,`test_catalog` | PARTIAL(UI) |
| 32 | Subcontractors | Procurement / Marketplace | `admin` | `subcontractors` | `admin.sc_create` | `test_admin` | PARTIAL(UI) + convergence path (§I) |
| 33 | Purchase orders | Procurement | `admin` | `purchase_orders` | `admin.po_create` | `test_admin` | PARTIAL(UI) |
| 34 | Fleet | Fleet | `catalog` | `vehicles` | fleet/vehicle routes | `test_catalog` | PRESERVED |
| 35 | Equipment registry | Fleet/Assets | `catalog` | `equipment` | equipment routes | `test_catalog` | PRESERVED |
| 36 | Preventive maintenance | Fleet Maintenance | `catalog` | `maintenance_work_orders`,`maintenance_windows` | maintenance routes | `test_catalog` (+ equipment availability effect) | PRESERVED (backend); UI PARTIAL |
| 37 | Availability calendar | Fleet/Dispatch | `catalog`,`ops` | `equipment` availability | `ops.calendar` | `test_catalog`,`test_ops` | PARTIAL(UI) |
| 38 | Safety Center | Safety | `admin`,`ops` | `safety_records` | `/jobs/:id/safety` | `test_ops` (safety-readiness gate) | PRESERVED (backend); UI PARTIAL |
| 39 | Inspections | Safety/Fleet | `catalog` | `inspections` | inspection routes | `test_catalog` | PRESERVED (backend) |
| 40 | Incident management | Safety/Claims | `admin`,`marketplace_trust_closure` | `incidents`,`mkt_claims` | `admin.report_incident` | `test_admin`,`test_marketplace_trust_closure` | ENHANCED |
| 41 | Inventory | Inventory | `admin` | `inventory_items`,`inventory_movements` | `/inventory/:id/move` | `test_admin` | PARTIAL(UI) |
| 42 | Documents | Document Mgmt | `admin`,`pdfgen` | `documents`,`doc_templates` | `admin.doc_upload`,`/admin/marketplace/documents/*` | `test_admin` | PARTIAL(UI) |
| 43 | Notifications | Platform | `admin` | `notifications`,`notification_templates`,`notification_events` | `admin.nt_template/notify` | `test_admin`,`test_pg_portability` | PARTIAL(UI) |
| 44 | Employee portal | Workforce | `catalog` | `employees` | employee routes | `test_catalog` | POST-LAUNCH (self-service portal UI) |
| 45 | Customer portal | Customer Experience | `core` + `index.html` landing/registration | `bookings`,`quotations` | acceptance + public registration | `test_ops`, landing E2E | PARTIAL(UI) → POST-LAUNCH (full self-service) |
| 46 | Finance dashboard | Finance | `ops`,`reporting` | reports | report routes + UI | `test_ops::test_reports_from_stored_data`,`test_phase8_reporting` | ENHANCED |
| 47 | Reports | Analytics | `reporting`,`ops` | `report_definitions`,`report_executions` | report routes | `test_phase8_reporting` | ENHANCED |
| 48 | AI quotation assistant | AI assistance | `ai_admin`,`ai_provider` | `ai_use_cases`,`ai_executions` | AI routes | `test_phase9_ai` | POST-LAUNCH |
| 49 | AI dispatch assistant | AI assistance | `ai_admin` | ai tables | AI routes | `test_phase9_ai` | POST-LAUNCH |
| 50 | User administration | Platform Admin | `admin_platform` | `users`,`admin_user_roles` | user-admin routes | `test_admin_platform` | ENHANCED (major) |
| 51 | Roles / permissions | IAM / RBAC | `admin_platform`,`core` | `admin_roles`,`admin_permissions`,`admin_role_permissions` | role routes | `test_admin_platform`,`test_security` | ENHANCED (major) |
| 52 | Audit | Governance | `core` | `audit_logs` | audit routes | `test_security`,`test_core` | ENHANCED |
| 53 | Tenant isolation | SaaS Platform | `tenant`,`org` | `tenants`,`cross_access_grants` | tenant routes | `test_tenant_isolation` | NEW |
| 54 | Marketplace | National Marketplace | `marketplace*` | `mkt_*` | `/admin/marketplace/*` | `test_marketplace_*` | NEW |
| 55 | Protected Payment | Marketplace/Finance | `protected_payment` | `mkt_protected_*` | protected-payment routes | `test_protected_payment*` | NEW (major) |
| 56 | KYB / LTFRB / legal | Trust & Compliance | `marketplace_trust*`,`ltfrb` | `mkt_kyb_*`,`mkt_ltfrb_authority` | trust/ltfrb routes | `test_marketplace_trust*`,`test_ltfrb` | NEW (major) |
| 57 | Island groups / inter-island | Marketplace | `marketplace` | `mkt_lanes` | lane routes | `test_marketplace_foundation` | NEW |

---

## B. No duplicate business domains

There is **one** canonical entity per concept — no `rgo_*` vs `lifthaul_*` split:
`customers`, `bookings`, `quotations`, `jobs`, `vehicles`/`equipment`. Marketplace tables are prefixed `mkt_*` and represent the **national carrier network** (a distinct concept: external supply), not a parallel copy of the enterprise entities. Suppliers/subcontractors (`suppliers`,`subcontractors`) are the private-procurement entities; the marketplace `mkt_carriers` are the public network — §I defines the convergence path so a private subcontractor can *become* a marketplace carrier without a duplicate identity. **No destructive consolidation performed; no historical data deleted.**

## C. RGO as Tenant Zero

- `tenants` table + `tenant.py` isolation (404-no-leak `guard`, `stamp`, `predicate`, cross-access grants) are present and tested (`test_tenant_isolation`). Tests already seed and operate a tenant keyed **"RGO"** (`ap.get_tenant(c,"RGO")`), so RGO functions as the reference tenant today.
- Tenant-scoped config exists for company profile, branches (`org_units`/`branches`), users, roles, equipment, services, rate cards, taxes, payment terms, numbering (`numbering.*`), calendars (`working_calendars`,`holiday_calendars`) — all resolved through the config cascade / tenant tables.
- **RESOLVED — branding separation:** the operator console (`console.html`) now enforces **product = LiftHaul Enterprise** (fixed chrome: title, login, topbar, footer "Powered by", client comment) and **tenant = RGO Machine Rigging Services (Tenant Zero)** as configuration via a single `TENANT` object + `applyBranding()`; a topbar tenant chip shows the tenant identity. Internal API storage keys renamed `lifthaul_api_*` (backward-compatible read of legacy `rgo_api_*`). Verified in-browser: title `LiftHaul Enterprise — RGO Machine Rigging Services`, 0 "RGO OS" product strings in the DOM, no console errors. RGO's own marketing sections remain as Tenant Zero content (legitimate tenant data).

## D–F. Commercial / Operations / Financial flows

The complete RGO lifecycle is implemented in `ops.py` and proven end-to-end by **`test_ops.py::test_booking_to_closure_and_profit`**, with the individual gates each independently tested:

- **Commercial (D):** customer → booking → site assessment (`create_site_assessment`/`assessment_ok`) → quotation/pricing → approval → acceptance → payment verification (`_payment_verified`) → reservation.
- **Operations (E):** job (`_job`/`transition_job`) → resource hold (`reserve_resource`) → confirmed reservation (`confirm_reservations`; dispatch blocked without it) → payment-verified gate → dispatch → change order (`create/approve_change_order`) → completion. Double-booking prevented (`test_double_book_prevented`); temp holds expire (`test_temp_hold_expiry_release`).
- **Financial (F):** estimated cost → quoted revenue → downpayment → job expenses (`add_expense`/`approve_expense`) → approved changes only (`approved_change_total`) → final invoice (`generate_final_invoice`, deducts downpayment + partials) → collections (`allocate_payment`/`mark_overdue`) → actual cost (`actual_cost`) → profitability/margin (`job_profitability`). RBAC enforced (`test_driver_cannot_invoice`, `test_driver_cannot_transition_job`).

## G. Wise / external payment preservation

`wise.py` + `integrations.py` + `payment_requests`/`provider_transfers` preserve the Wise payment-link + proof/verification workflow as a **payment method**. With `LIVE_PROTECTED_FUNDS_ENABLED=false`, external/operator-verified payment (incl. Wise) is the **legitimate launch payment path**; the Protected Payment domain is the (gated) marketplace-custody path. Both converge through the payment abstraction — legacy Wise does not bypass protected-payment governance for marketplace transactions.

## H. Private fleet → marketplace escalation

Model supported by the two layers: internal capacity (enterprise `jobs`/`reservations`/`equipment`) vs external capacity (`marketplace_matching`: booking → candidates → offers → assignment). A booking that internal fleet cannot cover can be taken to the marketplace without re-entry (shared booking identity). **PARTIAL(UI):** the automatic "internal-insufficient → marketplace request" hop is a workflow wiring/UI item, not a missing domain.

## I. Supplier / subcontractor convergence

`subcontractors` (private) and `mkt_carriers` (national network) coexist. Convergence path: a **Private Supplier** that passes KYB + LTFRB + vehicle/driver compliance is *promoted* to **Marketplace Carrier** — same company, no duplicate identity. Promotion wiring is a **PARTIAL(UI)** enhancement; both entities exist and are tested independently.

## J. Fleet / maintenance / safety

`catalog.py` implements fleet (`vehicles`), equipment registry (`equipment`), preventive maintenance (`maintenance_work_orders` with an equipment-availability effect), inspections (`inspections`); `ops.py`/`admin.py` implement safety readiness (`safety_records`, dispatch safety gate `/jobs/:id/safety`). Availability/maintenance/safety **blocks** are enforced (dispatch requires safety-ready + non-conflicting equipment). Backend PRESERVED; several of these have **thin admin UI** (PARTIAL(UI)).

## K. Inventory / documents / notifications

Backend present in `admin.py`: inventory (`inv_create`/`inv_move`/`low_stock`, `inventory_items`+`inventory_movements`, `/inventory/:id/move`), documents (`doc_upload`, `documents`+`doc_templates`, `pdfgen`), notifications (`nt_template`/`notify`, `notifications`+`templates`+`events`). Expiry handling exists for documents (`/admin/marketplace/documents/detect-expiry`). **PARTIAL(UI)** for the enterprise-admin surfaces of these three.

## L. Portals

- **Customer portal:** public **registration + booking intake** shipped (`index.html`); quote acceptance exists in backend. Full customer self-service (status, history, documents) = **POST-LAUNCH**.
- **Employee/Driver portal:** `employees` domain present; dedicated self-service portal UI = **POST-LAUNCH**. *(Classed POST-LAUNCH honestly — the operational essentials that gate a job, e.g. driver assignment + safety, are covered server-side; only the self-service UI is deferred.)*

## M. RGO data preservation / migration map

No production RGO dataset is present in this repo to migrate (dev seed only). The entity mapping for any future migration:

| RGO entity | → LiftHaul entity | Transform | Tenant | Validation |
|---|---|---|---|---|
| RGO Customer | `customers` | assign `tenant_id=RGO`, `customer_no` via sequence | RGO | dedup rules |
| RGO Contact | `contacts` | link `customer_id` | RGO | — |
| RGO Booking | `bookings` | map stage → `status` | RGO | stage history |
| RGO Quote | `quotations`(+`quotation_lines`) | rate-card resolve | RGO | pricing recompute |
| RGO Job | `jobs` | map lifecycle | RGO | stage gate |
| RGO Truck | `vehicles` | plate normalize | RGO | uniqueness |
| RGO Crane | `equipment` | category map | RGO | availability |
| RGO Driver | `employees`/`mkt_drivers` | licence/authority | RGO | compliance |
| RGO Expense | `expenses` | approval state | RGO | job link |
| RGO Invoice | `invoices`(+`invoice_lines`) | downpayment deduct | RGO | balance |

**Rule:** no production-data migration without an approved backup + rollback plan (see `docs/GO_LIVE_RUNBOOK.md`).

## N. RGO branding

- **Public front page:** RGO removed — `index.html` is LiftHaul (verified 0 "RGO"/forklift marks).
- **Operator console (`console.html`): DONE.** Product chrome = **LiftHaul Enterprise** (with the LiftHaul lift-badge mark); tenant identity = **RGO Machine Rigging Services (Tenant Zero)** driven by the `TENANT` config object + `applyBranding()`, surfaced as a topbar tenant chip and in the login/footer. No hardcoded RGO product name remains; RGO persists only as tenant configuration/content. A connected backend can override `TENANT` from `/me`/company profile.

## O. RGO reference-tenant E2E

`test_ops.py::test_booking_to_closure_and_profit` executes the synthetic RGO lifecycle (customer → booking → quotation → approval → payment → reservation → job → safety → dispatch → change order → expenses → invoice → collection → profitability) and asserts persisted values. Failure-recovery/persistence-after-restart is covered structurally by the deterministic seed + re-open pattern used across the suite.

---

## Summary of classifications

- **NEW LiftHaul capabilities:** tenant isolation, marketplace, protected payment, KYB/LTFRB/trust, island-group/inter-island.
- **ENHANCED (11):** CRM, quotation, rate/pricing, cost/margin, quote approval, versioning, credit terms, driver assignment, refunds, incident mgmt, finance dashboard/reports, user-admin/RBAC/audit.
- **PRESERVED (fully, tested):** booking, site assessment, downpayment, Wise, reservation hold/confirm, job, dispatch, equipment assignment, change orders, expenses, actual costing, profitability, final invoice, collections, cancellation, fleet, equipment registry, inspections, contacts/addresses.
- **PARTIAL(UI)** (backend+data+tests present; UI thin): customer communication, quote acceptance, dispatch calendar, availability calendar, suppliers, subcontractors, POs, preventive-maintenance UI, safety-center UI, inventory, documents, notifications, private-fleet→marketplace hop, supplier→carrier promotion. *(Console product/tenant branding — now RESOLVED.)*
- **POST-LAUNCH (deliberate):** employee self-service portal, full customer self-service portal, AI quotation assistant, AI dispatch assistant.
- **MISSING (release-critical):** **none.** No RGO operational essential was left behind at the backend/data/test level.

**Convergence verdict:** the RGO hauling operating model is represented inside LiftHaul as Tenant Zero. Every release-relevant RGO capability is PRESERVED/ENHANCED/REPLACED with evidence; the only genuinely deferred items are self-service portals and AI assistants (POST-LAUNCH). Remaining work is **UI depth + product/tenant branding separation in the console** — enhancements, not missing capability, and outside the release-critical set.
