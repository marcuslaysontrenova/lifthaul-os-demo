# Tenant & Organization Backfill Matrix (C-004 §10)

> **A plan, not an executed migration.** No destructive mass migration is performed
> here. This classifies every operational table by the organization scope it needs,
> and specifies the source, default rule, risk, validation query, and rollback for the
> eventual backfill (the recommended next Cap ID). Tenant Zero (RGO) is the default
> **only** for legacy records where evidence supports it. Version 0.1 (DRAFT).

## Scope classification

- **tenant-only** — needs `tenant_id` only.
- **branch / department / site / cost-center** — needs `tenant_id` **and** an org scope FK.
- **none** — platform/global or already scoped; no org column required.

## Backfill matrix

| Table | Scope class | Tenant source | Org source | Nullable | Default rule | Risk | Validation query | Rollback |
|---|---|---|---|---|---|---|---|---|
| `customers` | tenant-only | new `tenant_id` | — | NOT NULL after backfill | RGO (tenant 0) for all legacy | Low | `SELECT count(*) FROM customers WHERE tenant_id IS NULL` = 0 | drop column |
| `contacts`,`addresses` | tenant-only | via `customers.tenant_id` | — | NOT NULL | inherit from parent customer | Low | join-check no null | drop column |
| `bookings` | branch | new `tenant_id` | new `branch_id` (dispatch origin) | tenant NOT NULL, branch NULL-ok | RGO; branch NULL until assigned | Med (branch inference) | rows w/ tenant set, branch optional | drop columns |
| `booking_messages` | tenant-only | via `bookings` | — | NOT NULL | inherit from booking | Low | join-check | drop column |
| `quotations`,`quotation_lines` | tenant-only | via `bookings` | — | NOT NULL | inherit from booking | Low | join-check | drop column |
| `payment_requests` | tenant-only | via `bookings` | — | NOT NULL | inherit | Low | join-check | drop column |
| `jobs` | branch + site + cost-center | new `tenant_id` | `branch_id`,`operating_site_id`,`cost_center_id` | tenant NOT NULL, others NULL-ok | RGO; org FKs NULL until dispatch data mapped | **High** (financial/dispatch linkage) | tenant set for all; org FKs validated per job | drop columns |
| `job_stage_history` | tenant-only | via `jobs` | — | NOT NULL | inherit | Low | join-check | drop column |
| `reservations` | site | via `bookings` | `operating_site_id` | tenant NOT NULL, site NULL-ok | inherit tenant; site from resource map | Med | tenant set | drop columns |
| `site_assessments` | branch | via `bookings` | `branch_id` | tenant NOT NULL | inherit tenant | Low | join-check | drop column |
| `change_orders` | cost-center | via `jobs` | `cost_center_id` (via job) | tenant NOT NULL, cc NULL-ok | inherit from job | **High** (finance) | tenant set; cc matches job | drop columns |
| `expenses` | cost-center | via `jobs` | `cost_center_id` | tenant NOT NULL, cc NULL-ok | inherit from job; cc from job default | **High** (finance) | tenant set; sum(expenses) unchanged pre/post | drop columns |
| `invoices` | cost-center | via `jobs` | `cost_center_id` | tenant NOT NULL, cc NULL-ok | inherit | **High** (finance/billing) | tenant set; invoice totals unchanged | drop columns |
| `invoice_lines`,`payment_allocations` | tenant-only | via `invoices` | — | NOT NULL | inherit | Med | join-check; totals unchanged | drop column |
| `refunds` | tenant-only | via `bookings` | — | NOT NULL | inherit | Med | join-check | drop column |
| `equipment`,`vehicles` | branch | new `tenant_id` | `home_branch_id` | tenant NOT NULL, branch NULL-ok | RGO | Med | tenant set | drop columns |
| `employees` | branch/department | new `tenant_id` | `branch_id`,`department_id` | tenant NOT NULL | RGO | Med | tenant set | drop columns |
| `maintenance_work_orders`,`inspections` | tenant-only | via `equipment` | — | NOT NULL | inherit | Low | join-check | drop column |
| `subcontractors`,`suppliers`,`purchase_orders`,`supplier_invoices` | tenant-only | new `tenant_id` | — | NOT NULL | RGO | Low | tenant set | drop column |
| `inventory_items`,`inventory_movements` | branch | new `tenant_id` | `warehouse_id` | tenant NOT NULL, wh NULL-ok | RGO | Med | tenant set | drop columns |
| `safety_records`,`incidents` | site | via `jobs` | `operating_site_id` | tenant NOT NULL | inherit | Low | join-check | drop columns |
| `documents` | tenant-only | via owning entity | — | NOT NULL | inherit | Low | join-check | drop column |
| `master_data` | tenant-only | new `tenant_id` | — | NOT NULL | RGO (or platform-global rows tenant 0) | Low | tenant set | drop column |
| `notification_templates`,`notifications` | tenant-only | new `tenant_id` | — | NOT NULL | RGO | Low | tenant set | drop column |
| `audit_logs` | tenant-only | derive from actor/entity | — | NULL-ok (historical) | leave NULL for legacy; set going forward | Low | new rows have tenant | drop column |
| `users` | tenant-only | new `tenant_id` | — | NOT NULL | RGO for all legacy (single-tenant history) | Low | tenant set for all | drop column |
| `sessions`,`schema_version`,`system_config`,`platform_config`,`admin_*`,`org_*`,`tenants`,`login_history`,`mfa_enrollments` | none | — | — | — | already scoped/global | — | — | — |

## Migration protocol (when the backfill Cap ID is authorized)

1. **Additive, phase 1:** add nullable `tenant_id` (+ org FKs) columns; deploy; no behavior change.
2. **Backfill, phase 2:** set `tenant_id = RGO` for all legacy rows (single-tenant history is the evidence). Populate org FKs only where a defensible mapping exists; otherwise leave NULL.
3. **Validate:** run each row's validation query; assert financial invariants unchanged (sum of expenses/invoices/allocations pre == post) before/after.
4. **Constrain, phase 3:** add `NOT NULL` + FK + tenant-leading composite indexes only after validation passes.
5. **Enforce:** turn on the data-access tenant guard (every query tenant-filtered).

## Guardrails (from the directive's escalation triggers)

- No destructive mass migration without evidence — RGO default applies only to legacy
  single-tenant rows.
- Financial tables (`expenses`,`invoices`,`change_orders`,`payment_allocations`) are
  **High risk**: backfill must prove totals are unchanged and must not alter any
  financial calculation.
- Rollback for every table is "drop the added column(s)" — additive columns make the
  whole plan reversible until phase 3 constraints are applied.
