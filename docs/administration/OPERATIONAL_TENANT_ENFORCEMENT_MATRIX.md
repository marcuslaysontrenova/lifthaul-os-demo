# Operational Tenant Enforcement Matrix (Phase 1 Item 1.1)

> Server-side tenant enforcement status for every operational access path. Enforcement
> rule: deny when the actor tenant and the record tenant are both set and differ (null on
> either side = single-tenant/legacy, allowed) — proven backward-compatible (178 legacy
> tests stay green) while the two-tenant test proves isolation. Version 0.1 · 2026-07-30.
>
> Statuses: **TENANT ENFORCED · ENFORCED (ORG SCOPE PENDING) · FRONTEND FILTER ONLY ·
> TENANTLESS · PLATFORM ONLY · N/A.**

## Foundation (delivered this checkpoint)
- **Authoritative tenant context:** `users.tenant_id` → surfaced on the actor by
  `core.actor_for`; **never** read from client body/query/route/header (`test_tenant_isolation`
  proves a forged body `tenant_id` is ignored).
- **Enforcement primitives** (`tenant.py`): `guard` (404 no-leak read), `stamp` (server-derived
  ownership on create), `assert_related` (relationship isolation, 403), `predicate` (list scoping),
  `can_cross` (`platform.tenant.cross_access`), `bind_user_tenant`.
- **Columns:** `backfill.add_tenant_columns` runs on connect → 35 operational tables carry `tenant_id`.

## Route / access-path status

| Access path | Method | Service | Enforcement | Test |
|---|---|---|---|---|
| `/customers` create | POST | `core.create_customer` | **TENANT ENFORCED** (stamp) | `test_tenant_isolation` |
| `/bookings` create | POST | `core.create_booking` | **TENANT ENFORCED** (stamp + relationship check on customer) | ✓ |
| `/bookings/:id` read | GET | `core.get_booking` | **TENANT ENFORCED** (guard → 404) | ✓ |
| `/bookings/:id/quotation` create | POST | `core.create_quotation` | **TENANT ENFORCED** (guard on booking + stamp) | ✓ (spine) |
| `/quotations/:id/*` submit/approve/send/accept/revision | POST | `core` (via `_quote(actor)`) | **TENANT ENFORCED** (404 no-leak) | ✓ (deep-spine) |
| `/bookings/:id/payment-request` | POST | `core.create_payment_request` | **TENANT ENFORCED** (booking guard + stamp) | ✓ |
| `/payments/:id/link|evidence|verify` | POST | `core` (guard on payment_request) | **TENANT ENFORCED** (404 no-leak) | ✓ (verify) |
| `/bookings/:id/confirm` | POST | `core.confirm_job` | **TENANT ENFORCED** (booking guard + job stamp) | ✓ (spine) |
| `/jobs/:id/transition|change-order|expense|invoice` | POST | `ops` (via `_job(actor)` + stamp) | **TENANT ENFORCED** (404 no-leak) | ✓ (pattern) |
| `/jobs/:id/profitability` | GET | `ops.job_profitability(actor)` | **TENANT ENFORCED** | ✓ |
| `/invoices/:id/allocate` | POST | `ops.allocate_payment` (invoice guard) | **TENANT ENFORCED** | ✓ |
| `/bookings/:id/reserve` | POST | `ops.reserve_resource` (booking guard + stamp) | **TENANT ENFORCED** | ✓ |
| `/invoices/:id/lines` | GET | route-level invoice guard | **TENANT ENFORCED** | ✓ |
| `/jobs/:id/safety`, `/inventory/:id/move` | POST | `admin` | **ENFORCED (ORG SCOPE PENDING)** — parent job guard to add | — |
| `/quotations/:id/pdf`, `/calendar` | GET | `pdfgen`/`ops` | **PENDING** (document/export isolation) | — |
| `/reports` | GET | `ops` | **PENDING** (tenant-scoped aggregates) | — |
| `/admin/*` (Administration) | GET/POST | `server._admin_routes` | **PLATFORM/TENANT-SCOPED** (RGO context; per-tenant scoping to add) | `test_admin_api` |
| `/health`, `/ready`, `/login` | GET/POST | — | **N/A** (unauthenticated / auth) | — |

## Remaining gap (Item 1 completion)
Extend `guard`/`stamp`/`assert_related` into the **payment, job/ops, dispatch, document/PDF,
export, and report** paths (pattern established on the customer→booking→quotation spine), and
apply `predicate` to any operational **list/search** endpoints as they are added. Organization
scope on these records remains **AMBIGUOUS** and stays in the backfill remediation queue
(never auto-assigned). Then extend the two-tenant test to cover job/invoice/document/export/search.

## Indirect-exposure controls (Item 2.2) — status
`get_booking` returns **404** (not 403) across tenants, so existence is not leaked. Search /
autocomplete / export / PDF isolation are **PENDING** (no operational list/search/export endpoints
exist yet; when added they must use `tenant.predicate` + `guard`). Record counts and error
messages on the wired paths do not disclose other tenants' data.
