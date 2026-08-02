# PHASE 5 — Form & Field Audit Register

**Product:** LiftHaul OS · **Tenant Zero:** RGO Machine Rigging · **Date:** 2026-08-02
**Scope:** governed, tenant/role-aware Form & Custom-Field Administration over the existing
entity schemas and dialogs.
**Method:** full inspection of `backend/core.py`, `ops.py`, `catalog.py`, `admin.py`,
`crm_admin.py` (Phase-3 CRM custom fields) and `admin-console.html` (Dialog Standard screens).

> Guiding rule (directive): **system identifiers, financial totals, workflow state, tenant
> ownership, and security fields are NOT convertible into ordinary user-configurable fields.**
> The Phase-5 engine adds governed sections/fields/layout/validation/visibility/sensitivity ABOVE
> the fixed columns; it never overrides an authoritative column. The Phase-3 `crm_admin`
> custom-field foundation is PRESERVED; Phase-5 `forms` generalizes it to all entities.

## Classification legend

`SYSTEM FIELD` engine/identity · `BUSINESS FIELD` fixed business column · `CONFIGURABLE FIELD`
label/required/visibility governable · `CUSTOM FIELD` admin-added · `DERIVED FIELD` computed ·
`WORKFLOW CONTROLLED` state machine · `FINANCIAL CONTROLLED` money/tax/status · `SECURITY CONTROLLED`
tenant/auth · `LEGACY` · `UNUSED`.

---

## A. CRM entities

| Entity | Field | Code location | DB field | Hardcoded? | Required | Validation | Sensitivity | Class | Recommended action |
|---|---|---|---|---|---|---|---|---|---|
| customer | name | core.customers | name | fixed | yes | non-empty | INTERNAL | BUSINESS | keep; governable label/help |
| customer | customer_number | crm_admin | customer_number | governed (P3) | — | unique | INTERNAL | SYSTEM | keep (numbering-owned) |
| customer | credit_status | core | credit_status | fixed | no | master-data ref | CONFIDENTIAL | FINANCIAL CONTROLLED | do NOT convert; read-only in forms |
| customer | tenant_id | tenant.stamp | tenant_id | server-derived | — | — | RESTRICTED | SECURITY CONTROLLED | never user-configurable |
| customer | (extension) | crm_admin.custom_field_defs | custom_field_values | governed (P3) | configurable | declarative | configurable | CUSTOM | superseded by `forms` engine |
| contact | name/email/phone/role | catalog.contacts | — | fixed | partial | email/phone | PERSONAL DATA | BUSINESS + CUSTOM | governable; add custom fields |
| address | kind/line/city | catalog.addresses | — | fixed (free text) | no | master-data ref | INTERNAL | CONFIGURABLE | governed via forms + master data |
| lead / opportunity | (none as columns) | — | — | absent | — | — | — | CUSTOM (new) | governed via forms custom fields |

## B. Commercial entities

| Entity | Field | DB field | Class | Recommended action |
|---|---|---|---|---|
| booking | ref | bookings.ref | SYSTEM | keep (identifier) |
| booking | service / cargo / weight / from / to / date | bookings.* | BUSINESS/CONFIGURABLE | governable labels/required/visibility + custom fields |
| booking | stage | bookings.stage | WORKFLOW CONTROLLED | never a form field |
| site assessment | (absent) | — | CUSTOM (new) | governed section on booking form |
| quotation | subtotal/tax/total/discount/dp_amount | quotations.* | FINANCIAL CONTROLLED | render read-only; never override |
| quotation | status/approval_snapshot | quotations.* | WORKFLOW/FINANCIAL | system-controlled |
| quotation_line | kind/description/qty/days/rate/amount | quotation_lines.* | BUSINESS + FINANCIAL (amount) | line fields governable; `amount` derived read-only |
| payment_request | amount_due/status/provider_ref | payment_requests.* | FINANCIAL CONTROLLED | system-controlled; metadata custom fields only |

## C. Operations entities

| Entity | Field | DB field | Class | Recommended action |
|---|---|---|---|---|
| job | no/status/stage | jobs.* | SYSTEM/WORKFLOW | keep |
| dispatch / reservation | resource_type/status | reservations.* | WORKFLOW CONTROLLED | metadata custom fields only |
| equipment | code/name/etype/capacity/status | equipment.* | BUSINESS/CONFIGURABLE | governable; etype→master data; custom fields (crane capacity etc.) |
| vehicle | plate/vtype/status | vehicles.* | BUSINESS/CONFIGURABLE | governable; vtype→master data |
| employee/driver/operator | name/role/status | employees.* | BUSINESS + PERSONAL | governable; driver-license custom field (required for activation) |
| supplier/subcontractor | company/contact/category/coverage/insurance_expiry | admin.* | BUSINESS/CONFIGURABLE | governable; insurance-expiry required rule for subcontractors |

## D. Maintenance & safety

| Entity | Field | Class | Recommended action |
|---|---|---|---|
| maintenance_work_order | no/mtype/status/cost | SYSTEM/BUSINESS | mtype→master data; custom fields |
| inspection | itype/result | BUSINESS/CONFIGURABLE | itype→master data; governed fields |
| safety_record / incident | result/severity | BUSINESS + SAFETY DATA | governed incident section; root-cause required before closure |

## E. Finance (low-risk metadata custom fields only)

| Entity | Field | Class | Recommended action |
|---|---|---|---|
| expense | category/amount/status | FINANCIAL CONTROLLED (amount/status) | only low-risk metadata custom fields (e.g. cost-center note); never touch amount/status |
| invoice | total/balance/status | FINANCIAL CONTROLLED | metadata custom fields only; totals system-controlled |
| change_order | reason/amount/status | FINANCIAL CONTROLLED (amount/status) | reason→master data; metadata custom fields only |
| document metadata | documents.* | CONFIGURABLE | doc-type→master data; governed attachment metadata |

## F. Explicitly NOT convertible (protected)

| Category | Examples | Reason |
|---|---|---|
| System identifiers | `id`, `ref`, `no`, `customer_number` | identity/sequence-owned |
| Financial totals | `subtotal`, `tax`, `total`, `dp_amount`, `balance`, `amount` | authoritative money; Phase-2 policy owns them |
| Workflow state | `stage`, `status` (booking/quotation/job/invoice/payment) | state machines (Phase 4) |
| Tenant ownership | `tenant_id` | server-derived (Phase 1) |
| Security | auth policy, roles, permissions | governed elsewhere |

## Summary counts

- **SYSTEM / FINANCIAL / WORKFLOW / SECURITY CONTROLLED (protected, NOT convertible):** ~28 fields.
- **BUSINESS / CONFIGURABLE (governable labels/required/visibility, columns retained):** ~40 fields.
- **CUSTOM FIELD candidates (governed extensions, additive):** all 25 audited entities.
- **Existing CRM custom-field foundation (Phase 3):** PRESERVED; generalized by `forms`.

## Safety commitments

1. Financial/workflow/security/identity columns are **never** exposed as editable form fields;
   the `forms` engine rejects any field whose code collides with a protected system field for that
   entity → **UNEXPECTED FINANCIAL DIFFERENCES = 0**, **UNEXPECTED OPERATIONAL STATUS CHANGES = 0**.
2. Custom values are stored **additively** (new `form_values` table); no existing column is removed
   → **UNEXPECTED FIELD-VALUE LOSSES = 0**.
3. Published form/field versions are **immutable** (checksum); existing records keep the field
   version they were captured under.
4. Runtime submission is **server-validated** against the effective definition; browser-submitted
   unknown/inactive/cross-tenant/unauthorized fields are rejected.
5. Sensitivity classification governs view/edit/export/masking; sensitive values never appear in
   generic audit/logs/exports.
