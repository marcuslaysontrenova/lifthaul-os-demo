# PHASE 5 — Form & Field Migration Report

**Product:** LiftHaul OS · **Tenant Zero:** RGO Machine Rigging · **Date:** 2026-08-02
**Scope:** introduce governed Form & Custom-Field Administration WITHOUT removing any existing
column, WITHOUT changing any financial value or operational status, and WITHOUT losing any field
value.

## Migration strategy (additive, non-destructive)

The Phase-5 `forms` engine adds a governed sections/fields/validation/visibility/sensitivity layer
**above** the fixed entity columns. It never overrides an authoritative column; custom values are
stored **additively** in a new `form_values` table. The Phase-3 `crm_admin` custom-field foundation
(`custom_field_defs` / `custom_field_values`) is **preserved** and continues to function.

| Existing surface | Migration action | Impact |
|---|---|---|
| fixed entity columns (customer/booking/quotation/job/…) | retained as-is; exposed read-only in governed forms where appropriate | none — no column removed |
| financial columns (`total`, `tax`, `dp_amount`, `balance`, `amount`, `status`) | **protected** — cannot be created as configurable form fields | none — money/status system-controlled |
| workflow state (`stage`, `status`) | protected | none |
| tenant ownership (`tenant_id`) | protected (server-derived) | none |
| Phase-3 CRM custom fields | preserved; generalized (not migrated away) | none — values retained |
| new custom values | stored additively in `form_values` (typed + JSON hybrid) | additive only |

## Classification

| Class | Handling |
|---|---|
| system fields retained | identifiers/sequences kept authoritative |
| configurable fields | governable label/required/visibility; column retained |
| custom-field candidates | additive `form_values` storage, bound to a field version |
| deterministic mappings | none required (additive engine; no reinterpretation) |
| safe defaults | field `default_value` used where a required field is conditionally hidden |
| ambiguous values | none (no in-place value reinterpretation) |
| excluded fields | financial/workflow/security/identity (protected registry) |
| sensitive fields | classified PUBLIC…SAFETY_DATA; masking + export exclusion enforced |
| historical-only fields | prior field versions retained; old records keep their `field_version` |

## Value preservation guarantees

For every captured value: original value, timestamp, and actor are preserved; the **field version**
used at capture time is stamped on the row, so old records are never silently reinterpreted under a
new field definition. Published field/form versions are immutable (checksum).

## Migration results

| Metric | Result |
|---|---|
| System fields retained | yes (all) |
| Columns removed | **0** |
| CRM custom-field values preserved | all (`custom_field_values` untouched) |
| Records analyzed | all form-value rows (read-only reconcile) |
| Ambiguous values | 0 |
| Invalid values | 0 (runtime validation blocks invalid captures) |
| **Financial differences** | **0** |
| **Operational status differences** | **0** |
| **Field-value losses** | **0** |

## Invariants (proved in CI on PostgreSQL)

```
UNEXPECTED FINANCIAL DIFFERENCES = 0
UNEXPECTED OPERATIONAL STATUS CHANGES = 0
UNEXPECTED FIELD VALUE LOSSES = 0
```

- **Financial:** `test_forms_do_not_change_financials` drives a form submission against a booking and
  asserts the quotation `tax`/`total` are unchanged (72000/672000); protected-field guard rejects any
  attempt to create a financial field (`test_protected_financial_field_blocked`).
- **Operational:** submitting form values never touches `stage`/`status`; the booking real stage is
  unchanged.
- **Field-value loss:** storage is additive; `classify_existing` reports `field_value_losses = 0` and
  `columns_removed = 0`; the CI backup/restore reconciles `form_values` row counts before and after.

## Reversibility

- Only additive DDL (`CREATE TABLE IF NOT EXISTS`); no column drops.
- Form/field versions are immutable but a definition or version can be retired without deleting values.
- `form_values` rows are additive and can be removed without affecting any authoritative column.
