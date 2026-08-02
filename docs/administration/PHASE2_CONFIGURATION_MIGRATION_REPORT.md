# Phase 2 — Configuration Migration Report

> Migration strategy and results for converting the hardcoded financial rules (approval
> threshold, tax/VAT, downpayment) to governed configuration **without changing any
> existing financial value or historical document total**. Version 0.1 · 2026-08-02.

## Key finding (why this migration is low-risk)
Historical financial totals were **already persisted per document**:
- `quotations` stores `subtotal, discount, tax, total, dp_pct, dp_amount, balance` **per
  version** (each revision is a new immutable version).
- `payment_requests` stores `amount_due, dp_pct` derived from the accepted quotation.
- `invoices` store `quoted, change_orders_total, downpayment_applied, total, balance` +
  normalized `invoice_lines`.

So Phase 2 does **not** recompute any historical total. It adds a *policy snapshot*
(`tax_snapshot`, `dp_snapshot`, `approval_snapshot` on quotations; `dp_snapshot` on payment
requests) recording **which governed policy/scope/version produced** the already-stored
numbers. New documents resolve the policy from the cascade; existing documents are read-only.

## Record classification

| Class | Meaning | Action |
|---|---|---|
| `HAS_STORED_VALUES` | totals already on the row (all legacy quotations/PRs/invoices) | **preserve; no recalculation** |
| `DETERMINISTIC_SNAPSHOT` | a snapshot can be derived from stored `tax`/`dp_pct` without ambiguity | derive snapshot, mark `LEGACY_DERIVED` |
| `SAFE_DEFAULT` | no stored policy metadata, totals present | snapshot = platform default marked `LEGACY_DERIVED`; totals untouched |
| `AMBIGUOUS` | cannot infer policy source safely | leave snapshot NULL; list in remediation; **totals untouched** |
| `EXCLUDED` | non-financial / historical archives | no change |

## Migration mechanics
- Additive columns only (`_ensure_columns` on `quotations`/`payment_requests`); nullable.
- `policy.migrate_legacy_snapshots(conn)` (idempotent) sets a `LEGACY_DERIVED` snapshot for
  rows where the snapshot is NULL, computed from the row's **already-stored** `tax`/`dp_pct`
  — it never writes `tax`, `total`, `dp_amount`, or any financial column.
- No `UPDATE` touches a financial column anywhere in the migration.

## Financial-invariant verification
- The pre-Phase-2 financial regression (`subtotal 600000 → tax 72000 → total 672000 →
  downpayment 201600`) passes unchanged in `test_core`, `test_phase2`, `test_phase2_config`,
  and the PostgreSQL CI (`ci/pg_validate.py`).
- `test_phase2_config.test_config_change_does_not_alter_existing_quotation` proves a later
  tax-rate change leaves an existing quotation's `tax`/`total` identical while a **new**
  quotation uses the new rate.
- `test_phase2_config.test_payment_request_uses_stored_downpayment_snapshot` proves an issued
  payment request's `amount_due` is unchanged by a later downpayment-config change.

## Rollback plan
Fully additive → reversible by dropping the snapshot columns; no historical data is mutated,
so there is nothing to restore. Config definitions/values are governed data (removable).

## Result summary (dev baseline)
| Metric | Value |
|---|---|
| Financial columns modified by migration | **0** |
| Historical totals changed | **0** |
| Snapshot columns added | quotations ×3, payment_requests ×1 |
| Consumers converted | approval threshold+discount, tax rate/code/rounding, downpayment rate/min/required |
| Consumers retained | `separation_of_duties` (security invariant), numbering (later phase) |
| Ambiguous records | 0 (dev); production run reports counts before any change |
