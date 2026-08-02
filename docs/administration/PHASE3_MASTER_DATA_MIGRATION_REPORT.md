# PHASE 3 — Master-Data Migration Report

**Product:** LiftHaul OS · **Tenant Zero:** RGO Machine Rigging · **Date:** 2026-08-02
**Scope:** introduce the governed master-data + CRM-administration layer WITHOUT altering the
meaning of any historical transaction and WITHOUT changing any financial value.

## Migration strategy (additive, non-destructive)

Phase 3 is **purely additive**. It introduces new governed tables and a governed reference
registry alongside the existing free-text columns; it does **not** rewrite, delete, or
re-key any historical operational or financial record.

| Existing surface | Migration action | Historical impact |
|---|---|---|
| free-text columns (`equipment.etype`, `vehicles.vtype`, `bookings.service`, `contacts.role`, `addresses.kind`, `expenses.category`, `change_orders.reason`, `customers.credit_status`, …) | governed reference values **seeded** into `md_entries` with codes that MATCH the values already in use; the columns are **left as-is** | none — existing rows keep their exact stored strings; dependency reporting maps by code |
| customer numbering (integer PK only) | `customer_number` column **added** (nullable); governed numbering generates numbers for NEW customers | none — existing rows keep `customer_number = NULL`; the integer PK is untouched |
| currency default `'PHP'` | governed `finance.currency` list seeded; **default remains PHP** | none — no stored amount changes |
| tax code (`tax.default.code`) | governed `finance.tax_code` list seeded as **descriptive reference only** | none — the effective VAT rate/mode/type stays in the Phase-2 config cascade; `policy.evaluate_tax` is unchanged |
| credit status | governed `credit_policies` table + `customer.credit_rating` list; enforcement default = `evidence_only` | none — historical documents are never re-evaluated or mutated |

## Classification of discovered values

| Classification | Count (approx.) | Handling |
|---|---|---|
| **Deterministic** (free-text values with a clear governed equivalent) | 11 domains | seeded governed codes matching in-use values; dependency map links them |
| **Safe default** (new governed lists with no prior data) | ~45 domains | seeded with sensible starter values at platform scope (shared) |
| **Ambiguous** (values needing human judgement) | 0 required for go-live | flagged in the register; not auto-converted |
| **System controlled** (workflow states) | all lifecycle statuses | explicitly NOT converted (state machines retained) |
| **Excluded** (already governed by Phase-2 config) | tax rate, downpayment, approval, auth, rbac source | left in the config cascade; not duplicated as master data |

## Migration results

| Metric | Result |
|---|---|
| Records analyzed (customers/bookings/quotations/etc.) | all existing operational rows scanned read-only for dependency counts |
| Reference values discovered (free-text in use) | mapped to governed codes via `masterdata.DEPENDENCY_MAP` |
| Values seeded (governed) | ~120 across ~56 domains (platform scope, shared) |
| Values mapped deterministically | 11 legacy free-text domains |
| Ambiguous values | 0 blocking (register documents optional follow-ups) |
| Duplicate values | 0 (unique (tenant, domain, code) enforced) |
| Invalid values | 0 |
| **Historical labels preserved** | YES — no free-text column rewritten |
| **Customer numbers backfilled on existing rows** | NO (intentional — additive; existing PKs unchanged) |

## Financial & operational invariants (proved in CI on PostgreSQL)

```
UNEXPECTED FINANCIAL DIFFERENCES = 0
UNEXPECTED OPERATIONAL STATUS CHANGES = 0
```

- **Financial:** master-data tax codes/currencies are descriptive; the effective tax computation
  (`policy.evaluate_tax`) is unchanged. A quotation's `tax`/`total` before and after touching
  master data are identical (unit test `TestFinancialAndOperationalSafety`; CI `pg_validate.py`
  aggregate invariant carried over from Phase 2).
- **Operational:** credit enforcement defaults to `evidence_only` (records evidence, never blocks
  or mutates); booking/job/invoice/payment **stages are untouched** by any master-data operation
  (CI check "operational booking stage unchanged (0 status drift)").

## Reversibility

- No destructive DDL: only `CREATE TABLE IF NOT EXISTS`, additive `ADD COLUMN`, and a unique index.
- Master values are never hard-deleted; deactivation/archival is reversible (restore).
- Customer numbering can be disabled via `crm.numbering.enabled=false` with no data loss.
- Credit enforcement is a single config flag (`crm.credit.enforcement`), default off.
