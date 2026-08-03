# PHASE 8 — Reporting Audit Register

**Product:** LiftHaul OS · **Tenant Zero:** RGO Machine Rigging · **Date:** 2026-08-02
**Scope:** governed Reporting & Dashboard Administration — approved datasets, safe declarative queries,
row/column security, KPIs, dashboards, schedules, delivery, cache, performance governance.
**Method:** full inspection of `ops.py` (report_* functions), `core.py`/`admin.py` (counts), the PMO
snapshot pattern, and `admin-console.html` (any frontend aggregates).

> Guiding rules (directive): **do not expose unrestricted SQL** to administrators; **never load all
> tenants and filter after retrieval** — the tenant predicate is injected into every query; row + column
> security, sensitivity masking, and resource limits apply to preview / execution / export / schedule /
> cache / API alike. Reuse the Phase-1 tenant predicate, Phase-5 field sensitivity, and Phase-6 org scope.

## Classification legend

`GOVERNED` admin-configurable + secured · `HARDCODED` fixed code metric · `FRONTEND ONLY` ·
`TENANT SAFE` scoped · `TENANT UNSAFE` · `PARTIALLY GOVERNED` · `LEGACY` · `DUPLICATED` · `UNUSED` ·
`SYSTEM CONTROLLED`.

---

## A. Existing report/metric functions (`ops.py`)

| Report | Code location | Data source | Aggregation | Tenant enforced? | Export? | Schedule? | Class | Migration recommendation |
|---|---|---|---|---|---|---|---|---|
| quotation conversion | `ops.report_quotation_conversion` | quotations | COUNT DISTINCT + ratio | YES (`tenant.predicate`) | no | no | PARTIALLY GOVERNED / TENANT SAFE | seed as governed standard report `quotation_conversion` |
| accepted awaiting payment | `ops.report_accepted_awaiting_payment` | bookings | COUNT | YES | no | no | PARTIALLY GOVERNED / TENANT SAFE | seed standard report |
| receivables | `ops.report_receivables` | invoices | SUM(balance) GROUP BY status | YES | no | no | PARTIALLY GOVERNED / TENANT SAFE | seed standard report `receivables` |
| confirmed jobs | `ops.report_confirmed_jobs` | jobs | COUNT | YES | no | no | PARTIALLY GOVERNED / TENANT SAFE | seed standard report |
| double-booking conflicts | `ops.py:523` | reservations | GROUP BY HAVING | YES | no | no | SYSTEM CONTROLLED (integrity) | keep as data-integrity, not a report |

## B. Counts embedded in write paths (numbering / sequencing)

| Item | Location | Class | Note |
|---|---|---|---|
| `COUNT(*) FROM quotations/change_orders/invoices` | `core.py`/`ops.py` (numbering) | SYSTEM CONTROLLED | sequence generation, NOT reporting — excluded |
| Phase-4 integrity data-integrity checks | `admin_platform` / server | SYSTEM CONTROLLED | governance checks, not reports |

## C. Frontend aggregates

| Surface | Location | Class | Recommendation |
|---|---|---|---|
| admin-console tables (users/org/etc.) | `admin-console.html` | FRONTEND ONLY (render of governed APIs) | already API-backed; no client-side aggregation of raw rows |
| PMO snapshot (TrenovaTech, separate product) | n/a to LiftHaul | NOT APPLICABLE | — |

## D. Direct SQL / tenantless analytics

| Concern | Present? | Note |
|---|---|---|
| Unrestricted SQL exposed to admins | **NO** | none; Phase 8 will keep it that way (declarative only) |
| Tenantless aggregate over all tenants | **NO** | existing report_* all use `tenant.predicate` |
| Frontend-only aggregate of raw rows | **NO** | console renders governed API results |

## Summary counts

- **Existing metrics (PARTIALLY GOVERNED, TENANT SAFE):** 4 `ops.report_*` — migrated as governed standard
  reports reproducing the SAME values (no business-meaning change).
- **SYSTEM CONTROLLED (excluded from reporting):** numbering counts, data-integrity checks.
- **FRONTEND ONLY:** console tables (already API-backed; no raw-row client aggregation).
- **NEW (Phase 8):** approved data-source registry, safe declarative query engine, report definitions +
  versions, row/column security, KPIs, dashboards + widgets, schedules, delivery/export, cache, perf
  governance, reporting integrity.

## Safety commitments

1. Every report execution injects the **tenant predicate** at query time (never load-all-filter-after)
   → Tenant A never receives Tenant B rows.
2. Column security masks/excludes **sensitive fields** by the actor's sensitivity permission across
   preview / export / schedule / cache / API.
3. Migrated standard reports reproduce the **same values** as the existing `ops.report_*` functions →
   **UNEXPECTED REPORT-VALUE DIFFERENCES = 0**.
4. Reporting reads only; it changes no financial value and no operational status →
   **UNEXPECTED FINANCIAL DIFFERENCES = 0**, **UNEXPECTED OPERATIONAL STATUS CHANGES = 0**.
5. No unrestricted SQL, no arbitrary expressions; only allowlisted datasets/fields/operators.
6. Cache keys include user + tenant + org + permissions + params; results are never shared across users
   or tenants.
