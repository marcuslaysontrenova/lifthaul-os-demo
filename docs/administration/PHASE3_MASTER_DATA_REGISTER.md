# PHASE 3 — Master-Data Audit Register

**Product:** LiftHaul OS · **Tenant Zero:** RGO Machine Rigging · **Date:** 2026-08-02
**Scope:** governed administration layer for CRM + shared operational master data.
**Method:** full repository inspection of `backend/*.py` (schemas, service layer, seeds,
literal `IN (...)` checks, free-text columns, hard-coded formats).

> Guiding rule (from the directive): **system workflow states are NOT master data.**
> Booking stages, quotation/job/invoice/payment/reservation/change-order statuses are
> controlled by state machines (`core._BOOKING_FLOW`, `ops` transitions) and are classified
> **SYSTEM CONTROLLED** — they are governed by workflow logic, not by an editable list.

## Classification legend

`VERIFIED GOVERNED` already admin-owned/data-driven · `HARDCODED` literal in code ·
`PARTIALLY GOVERNED` some governance, gaps remain · `LEGACY` free-text column, no reference list ·
`DUPLICATED` same concept in >1 place · `UNUSED` declared but not consumed ·
`SYSTEM CONTROLLED` workflow/state-machine owned · `NOT APPLICABLE` concept absent today.

---

## A. CRM — customer classifications

| Domain | Code location | Current source | Entity | State | Tenant | Org | Eff-dated | Ref'd by txns | Safe deactivate | Safe replace | Migration | UI | API | Permission | Audit | Recommendation | Class |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| customer.category | — (absent) | none | customers | new | yes | opt | yes | no (new field) | yes | yes | seed defaults | new | new | crm.admin.classification.* | yes | governed master data | NOT APPLICABLE→governed |
| customer.type | — (absent) | none | customers | new | yes | opt | yes | no | yes | yes | seed | new | new | crm.admin.classification.* | yes | governed master data | NOT APPLICABLE→governed |
| customer.industry | — (absent) | none | customers | new | yes | opt | yes | no | yes | yes | seed | new | new | crm.admin.classification.* | yes | governed master data | NOT APPLICABLE→governed |
| customer.group | — (absent) | none | customers | new | yes | opt | yes | no | yes | yes | seed | new | new | crm.admin.classification.* | yes | governed master data | NOT APPLICABLE→governed |
| customer.account_status | — (absent) | none | customers | new | yes | opt | yes | no | yes | yes | seed | new | new | crm.admin.classification.* | yes | governed master data | NOT APPLICABLE→governed |
| customer.account_rating | — (absent) | none | customers | new | yes | opt | yes | no | yes | yes | seed | new | new | crm.admin.classification.* | yes | governed master data | NOT APPLICABLE→governed |
| customer.credit_rating | `core.customers.credit_status DEFAULT 'Good'` | free-text column | customers | legacy | yes | opt | yes | yes (credit path) | yes | yes | preserve existing labels; add governed list | new | new | crm.admin.classification.* | yes | governed master data; keep existing values intact | LEGACY |
| customer.risk_class | — (absent) | none | customers | new | yes | opt | yes | no | yes | yes | seed | new | new | crm.admin.classification.* | yes | governed master data | NOT APPLICABLE→governed |
| customer.lifecycle | — (absent) | none | customers | new | yes | opt | yes | no | yes | yes | seed | new | new | crm.admin.classification.* | yes | governed master data | NOT APPLICABLE→governed |
| customer.strategic_indicator | — (absent) | none | customers | new | yes | opt | yes | no | yes | yes | seed | new | new | crm.admin.classification.* | yes | governed master data | NOT APPLICABLE→governed |

## B. Lead & sales administration

| Domain | Current source | Class | Recommendation |
|---|---|---|---|
| lead.source | absent | NOT APPLICABLE→governed | master data `lead.source` |
| sales.territory | absent | NOT APPLICABLE→governed | master data `sales.territory` (hierarchical via parent) |
| account_manager (ownership) | `users` + role | PARTIALLY GOVERNED | ownership = a **user assignment**, not master data; expose via territory/ownership screen (users already governed by C-006) |
| sales.team | `org_units kind=team` (C-004) | VERIFIED GOVERNED | reuse org hierarchy; no new master data |
| opportunity.type | absent | NOT APPLICABLE→governed | master data `opportunity.type` |
| opportunity.source | absent | NOT APPLICABLE→governed | master data `opportunity.source` |
| opportunity.lost_reason | absent | NOT APPLICABLE→governed | master data `opportunity.lost_reason` |
| lead.qualification | absent | NOT APPLICABLE→governed | master data `lead.qualification` |
| campaign.source | absent | NOT APPLICABLE→governed | master data `campaign.source` |

## C. Commercial policy administration

| Domain | Current source | Class | Recommendation |
|---|---|---|---|
| commercial.payment_term | absent (invoices compute from downpayment cfg) | NOT APPLICABLE→governed | master data `commercial.payment_term` |
| commercial.credit_policy | `core.CONFIG` / `customers.credit_status` | PARTIALLY GOVERNED | **dedicated** `credit_policies` table (limit/terms/deposit/restrictions, effective-dated) + persisted evidence |
| commercial.pricing_policy | absent | NOT APPLICABLE→governed | master data `commercial.pricing_policy` (descriptive; does not alter quote math) |
| commercial.discount_policy | `quotation.approval.discount_threshold_pct` (Phase 2 cfg) | VERIFIED GOVERNED | keep in config cascade; reference list only |
| portal.access_policy | absent | NOT APPLICABLE→governed | master data `portal.access_policy` |
| customer retention rules | absent | NOT APPLICABLE→governed | master data `customer.retention_rule` (descriptive) |

## D. Customer-data governance

| Domain | Current source | Class | Recommendation |
|---|---|---|---|
| customer numbering | `customers.id` integer PK only (no formatted number) | HARDCODED | **dedicated** governed numbering (prefix/year/branch/seq/padding, concurrency-safe) + `customer_number` column |
| duplicate-detection rules | absent | NOT APPLICABLE→governed | **dedicated** `customer_duplicate_rules` (dimension/match_type/weight/threshold) |
| merge rules | absent | NOT APPLICABLE→governed | **dedicated** governed merge (survivor, reference redirect, cross-tenant block, audit) |
| required fields | absent | NOT APPLICABLE→governed | via custom-field `required` flag |
| archival / retention policy | absent | NOT APPLICABLE→governed | master data `customer.retention_rule` + status lifecycle |
| attachment categories | absent | NOT APPLICABLE→governed | master data `customer.attachment_category` |
| contact classifications | `catalog.contacts.role` free text | LEGACY | master data `contact.classification` |
| address types | `catalog.addresses.kind` free text | LEGACY | master data `address.type` |
| portal-user eligibility | absent | NOT APPLICABLE→governed | master data `portal.eligibility` |
| data-quality rules | absent | NOT APPLICABLE→governed | custom-field validation (declarative) |

## E. Shared master data — Operations

| Domain | Code location | Current source | Class | Recommendation |
|---|---|---|---|---|
| ops.service_type | `core.bookings.service` free text; `quotation_lines.kind` | LEGACY | master data `ops.service_type` |
| ops.job_category | absent | NOT APPLICABLE→governed | master data `ops.job_category` |
| ops.equipment_type | `catalog.equipment.etype` free text | LEGACY | master data `ops.equipment_type` |
| ops.vehicle_type | `catalog.vehicles.vtype` free text | LEGACY | master data `ops.vehicle_type` |
| ops.trailer_type | absent | NOT APPLICABLE→governed | master data `ops.trailer_type` |
| ops.lifting_equipment_category | absent | NOT APPLICABLE→governed | master data `ops.lifting_equipment_category` |
| ops.rigging_equipment_category | absent | NOT APPLICABLE→governed | master data `ops.rigging_equipment_category` |
| ops.crew_role | `catalog.employees.role` free text | LEGACY | master data `ops.crew_role` |
| ops.maintenance_category | `catalog.maintenance_work_orders.mtype` free text | LEGACY | master data `ops.maintenance_category` |
| ops.inspection_category | `catalog.inspections.itype` free text | LEGACY | master data `ops.inspection_category` |
| ops.incident_category | absent | NOT APPLICABLE→governed | master data `ops.incident_category` |
| ops.safety_category | absent | NOT APPLICABLE→governed | master data `ops.safety_category` |
| dispatch status | `ops.reservations.status`, job stage | SYSTEM CONTROLLED | **do not convert** — state machine |

## F. Shared master data — Finance

| Domain | Code location | Class | Recommendation |
|---|---|---|---|
| finance.expense_category | `ops.expenses.category` free text | LEGACY | master data `finance.expense_category` |
| finance.payment_method | `core` payment provider literal | LEGACY | master data `finance.payment_method` (descriptive) |
| finance.payment_term | absent | NOT APPLICABLE→governed | master data `finance.payment_term` |
| finance.currency | `'PHP'` hard-coded default (`payment_requests`, `expenses`) | HARDCODED | master data `finance.currency`; **default stays PHP** (no financial change) |
| finance.tax_code | `tax.default.code` (Phase 2 cfg) governs the ACTIVE code | PARTIALLY GOVERNED | master data `finance.tax_code` is **descriptive reference only**; the effective **rate** stays in the Phase-2 config cascade → **financials unchanged** |
| finance.uom | absent (quotation_lines use qty/days/rate) | NOT APPLICABLE→governed | master data `finance.uom` (descriptive) |
| finance.invoice_category | absent | NOT APPLICABLE→governed | master data `finance.invoice_category` |
| finance.change_order_reason | `ops.change_orders.reason` free text | LEGACY | master data `finance.change_order_reason` |
| finance.refund_reason | absent (`payment.refund` exists) | NOT APPLICABLE→governed | master data `finance.refund_reason` |
| finance.adjustment_reason | absent | NOT APPLICABLE→governed | master data `finance.adjustment_reason` |

## G. Shared master data — Geography (hierarchical, `parent_id`)

| Domain | Class | Recommendation |
|---|---|---|
| geo.country | NOT APPLICABLE→governed | master data `geo.country` (seed PH) |
| geo.region | NOT APPLICABLE→governed | `geo.region` parent=country |
| geo.province | NOT APPLICABLE→governed | `geo.province` parent=region |
| geo.city / geo.municipality | NOT APPLICABLE→governed | parent=province |
| geo.barangay | NOT APPLICABLE→governed | parent=city |
| geo.service_area | absent | NOT APPLICABLE→governed | `geo.service_area` |
| postal-code definitions | NOT APPLICABLE→governed | `geo.postal_code` (metadata on city) |

## H. Shared master data — Documents & communication

| Domain | Code location | Class | Recommendation |
|---|---|---|---|
| doc.type | `documents` table exists, type free text | LEGACY | master data `doc.type` |
| doc.attachment_type | absent | NOT APPLICABLE→governed | master data `doc.attachment_type` |
| comms.email_template | absent | NOT APPLICABLE→governed | master data `comms.email_template` (body in metadata) |
| comms.sms_template | absent | NOT APPLICABLE→governed | master data `comms.sms_template` |
| comms.notification_template | absent | NOT APPLICABLE→governed | master data `comms.notification_template` |
| doc.template | absent | NOT APPLICABLE→governed | master data `doc.template` |
| ops.cancellation_reason | `_BOOKING_FLOW` CANCELLED transition (no reason list) | PARTIALLY GOVERNED | master data `ops.cancellation_reason` (reason list; the *state* stays system-controlled) |
| ops.status_reason | absent | NOT APPLICABLE→governed | master data `ops.status_reason` |

## I. Explicitly SYSTEM CONTROLLED (NOT converted)

| Item | Location | Why not master data |
|---|---|---|
| booking stage flow | `core._BOOKING_FLOW` | finite-state machine with transition guards |
| quotation status | `core.quotations.status` | lifecycle governed by submit/approve/send/accept |
| job / invoice / payment / reservation / change-order / expense status | `ops`, `core` | state machines; converting to editable lists would break control invariants |
| booking_messages.visibility `IN ('internal','customer')` | `catalog.post_message` | binary system enum guarding customer-visible vs internal threads |
| `iam.rbac_source`, auth policy, tax/downpayment/approval | Phase 2 config cascade | already VERIFIED GOVERNED (do not duplicate as master data) |

---

## Summary counts

- **LEGACY (free-text → governed master data):** 11 (equipment/vehicle/crew/maintenance/inspection type,
  service type, contact classification, address type, expense category, change-order reason, doc type,
  credit rating).
- **HARDCODED → governed:** 2 (customer numbering, currency default).
- **PARTIALLY GOVERNED → tightened:** 4 (credit policy, tax code reference, cancellation reason, discount policy).
- **NOT APPLICABLE → newly governed:** ~40 CRM/sales/geo/finance/document domains introduced as governed
  master data (additive; no existing behavior depends on them).
- **SYSTEM CONTROLLED (untouched):** all workflow states + binary system enums.
- **DUPLICATED / UNUSED:** none material found (workflow-state literals appear in multiple queries but are
  a single system-controlled concept, not duplicated master data).

## Financial & operational safety commitments

1. Master-data **tax codes are descriptive reference only.** The effective VAT rate/mode/type remains in the
   Phase-2 config cascade. `policy.evaluate_tax` is **not** changed → **UNEXPECTED FINANCIAL DIFFERENCES = 0**.
2. Currency default remains **PHP**; adding a governed currency list changes no stored amount.
3. Credit-policy **enforcement default = `evidence_only`** (records the evidence that would apply; never blocks
   or mutates historical documents) → **UNEXPECTED OPERATIONAL STATUS CHANGES = 0**. Blocking is opt-in per tenant.
4. Customer numbering is **additive** (`customer_number` column); it never alters the integer PK, existing rows,
   or any financial field.
5. Deactivating a master value **never hard-deletes**; historical records keep the exact code/name they used
   (transaction snapshots + never-deleted rows).
