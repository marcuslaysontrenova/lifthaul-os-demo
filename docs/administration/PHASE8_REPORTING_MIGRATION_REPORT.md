# PHASE 8 — Reporting Migration Report

**Product:** LiftHaul OS · **Tenant Zero:** RGO Machine Rigging · **Date:** 2026-08-02
**Scope:** introduce governed Reporting & Dashboard Administration WITHOUT changing any historical
financial value or operational status, and reproducing existing metric values exactly.

## Migration strategy (additive, read-only, value-preserving)

Reporting is **read-only**. The Phase-8 engine adds a governed dataset registry + declarative query
model + row/column security on top of the existing data. The four existing `ops.report_*` metrics are
re-expressed as governed **standard reports** whose specs reproduce the SAME values (no business-meaning
change). No historical document is touched.

| Existing metric (`ops.py`) | Migration action | Governed standard report | Value equivalence |
|---|---|---|---|
| `report_quotation_conversion` | seeded as declarative report | `quotation_conversion` | accepted count reconciled == ops value |
| `report_receivables` | seeded as declarative report | `receivables` | per-status balance reconciled == ops value |
| `report_confirmed_jobs` / jobs-by-status | seeded | `jobs_by_status` | job counts reconciled |
| (new) Wise transfers | seeded | `wise_transfers` | provider-transfer counts |

## Classification of existing reporting surfaces

| Class | Items | Handling |
|---|---|---|
| deterministic migration | `ops.report_*` (4) | re-expressed as governed standard reports (same values) |
| standard report | the 4 seeded above | published ACTIVE |
| frontend-only metric | console tables | already API-backed; no client-side raw aggregation |
| hardcoded aggregate | none | — |
| duplicate | none | — |
| unsafe (tenantless / raw SQL) | **none** | existing metrics already tenant-scoped; no raw SQL exposed |
| legacy | none | — |
| excluded | numbering counts, data-integrity checks (SYSTEM CONTROLLED) | not reports |

## Migration results

| Metric | Result |
|---|---|
| Reports found | 4 (`ops.report_*`) |
| Reports migrated (as governed standard reports) | 4 |
| Dashboards found | 0 (new capability) |
| KPIs found | 0 (new capability) |
| Unsafe reports | **0** |
| Tenantless reports | **0** |
| Duplicate metrics | **0** |
| Ambiguous definitions | **0** |
| **Financial differences** | **0** |
| **Operational status differences** | **0** |
| **Report-value differences** | **0** |

## Invariants (proved in CI on PostgreSQL)

```
UNEXPECTED FINANCIAL DIFFERENCES = 0
UNEXPECTED OPERATIONAL STATUS CHANGES = 0
UNEXPECTED REPORT-VALUE DIFFERENCES = 0
```

- **Report-value:** `test_report_value_reconciliation` + `test_receivables_reconciliation` assert the
  governed reports return the SAME numbers as `ops.report_quotation_conversion` / `ops.report_receivables`.
- **Financial / operational:** reporting is read-only — `test_reporting_does_not_change_financials`
  runs + exports a report and asserts the quotation `tax`/`total` unchanged (72000/672000).
- **Security:** every execution injects the tenant predicate (Tenant A never sees Tenant B rows);
  financial/restricted columns are excluded without permission across execution + export.

## Reversibility

- Only additive DDL; no column drops; historical documents untouched.
- Report/KPI/dashboard/schedule definitions are additive and auditable; disabling them removes no data.
- Cache is derived and can be invalidated at any time without data loss.
