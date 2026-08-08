# Operational Tenant Enforcement Matrix — LiftHaul OS

Scope: server-side tenant isolation for operational records. Tenant context is derived
**only** from the authenticated identity (`users.tenant_id` → actor), never from
client-supplied `tenant_id` (body/route/query/header). Enforcement primitives live in
`backend/tenant.py`:

| Primitive | Purpose |
|---|---|
| `tenant.guard(actor, row)` | Read guard — cross-tenant → `NotFoundError` (404, no existence leak) |
| `tenant.stamp(conn, actor, table, id)` | Server-derived ownership on create (ignores forged body tenant) |
| `tenant.assert_related(conn, actor, table, id)` | Relationship isolation — referenced record must be in actor's tenant (403) |
| `tenant.predicate(actor)` | `(sql, params)` scope fragment for list/search |
| `tenant.activate_cross_access` | Expiring (≤1h) platform cross-tenant grant, HIGH-severity audited |

**Backward-compatible rule:** deny only when *both* actor tenant and row tenant are set and
differ. A NULL tenant on either side = single-tenant/legacy → allowed. Isolation activates
the moment users and rows carry tenants (proven by the two-tenant suite).

## Core operational tables (this backend)

| Module | Function | Permission | Tenant source | Read enf. | Write enf. | Relationship | Test | Status |
|---|---|---|---|---|---|---|---|---|
| core | `create_customer` | `customer.create` | actor | — | `stamp` | — | tenant_isolation | ✅ VERIFIED |
| core | `_quote`/`get_quotation` | `quotation.read/view` | actor | `guard` | — | — | tenant_isolation, pricing | ✅ VERIFIED |
| core | `create_booking` | `booking.create` | actor | — | `stamp` | `assert_related(customers)` | tenant_isolation | ✅ VERIFIED |
| core | `_booking`/`get_booking` | `booking.*` | actor | `guard` | — | — | tenant_isolation | ✅ VERIFIED |
| core | `update_booking` | `booking.edit_*` | actor | `guard`(via `_booking`) | field-level | — | booking_access | ✅ VERIFIED |
| core | `create_quotation` | `quotation.create` | actor | `guard`(booking) | `stamp` | booking read-guard | tenant_isolation | ✅ VERIFIED |
| core | `quotation_lines` | (child of quotation) | inherited | via parent `guard` | via parent | parent read-guard | pricing | ✅ VERIFIED |
| core | `payment_request`/verify | `payment.*` | actor | `guard` | `stamp` | booking+quote read-guard | tenant_isolation | ✅ VERIFIED |
| core | `create_job`/confirm | `job.*` | actor | `guard`(via booking) | `stamp` | booking read-guard | tenant_isolation | ✅ VERIFIED |
| rates | `resolve_rate` | (pricing internal) | actor tenant param | tenant filter | — | — | pricing (rate tenant) | ✅ VERIFIED (closed this checkpoint) |
| rates | `list_rate_cards` | `crm.admin.pricing.view` | actor | `predicate` | — | — | pricing (rate tenant) | ✅ VERIFIED (closed this checkpoint) |
| rates | `create/update/archive_rate_card` | `crm.admin.pricing.manage` | actor | — | `stamp` on create | — | pricing | ✅ VERIFIED |
| server | `GET /rate-catalog` | `quotation.read/view` | actor | `predicate` | — | — | (route) | ✅ VERIFIED (closed this checkpoint) |
| server | `POST /quotations/price-preview` | `quotation.read/view` | actor | tenant param | — | — | (route) | ✅ VERIFIED (closed this checkpoint) |
| ops | `report_accepted_awaiting_payment` | (report) | actor | `predicate` | — | — | tenant_isolation | ✅ VERIFIED |
| core | quotation PDF / export | `quotation.print/export` | actor | `guard` | — | — | tenant_isolation (cross-tenant PDF → 404) | ✅ VERIFIED |

**Gap found and closed this checkpoint:** the pricing subsystem stamped `rate_cards` with a
tenant on create but `resolve_rate` / `list_rate_cards` / `/rate-catalog` / `/price-preview`
did not scope reads. A tenant could resolve another tenant's custom rate for the same
equipment code. Now tenant-scoped (own + global NULL only); covered by
`RateCardTenantIsolationTests`.

## Entities in the directive that are NOT backend tables here

Classified honestly so the matrix is not misleading:

- **Marketplace-owned** (own tenant/visibility model in `marketplace_*` engines):
  vehicles, drivers, carriers/subcontractors, trips/dispatch, offers/assignments,
  marketplace bookings, payouts/refunds, documents. Not re-scoped here.
- **Frontend-demo-only** (localStorage in `index.html`, no backend persistence):
  equipment/fleet, employees, suppliers, maintenance, inspections, safety/incidents,
  inventory & movements, reservations, invoices & lines, payment allocations, expenses,
  change orders, notifications. These have **no server-side multi-tenant surface** to
  enforce; when promoted to backend tables they must adopt `stamp`/`guard`/`predicate`.
- **Admin/config tables** (Platform-1): tenant/org-scoped via `org` + config cascade
  (separate from operational isolation).

## Verification

- `backend/test_tenant_isolation.py` — HTTP-router + persistent-file DB, two synthetic
  hauling companies with **overlapping names**: server-derived tenant, forged-body
  rejection, read 404-isolation, relationship isolation, full quotation→payment spine,
  cross-tenant PDF 404, report scoping, restart persistence.
- `backend/test_quotation_pricing.py::RateCardTenantIsolationTests` — rate-card resolve/list
  isolation + global-card sharing.

## Known residual (documented, not silently ignored)

- Literal browser E2E and a PostgreSQL-container restart require a Docker/PG/browser host —
  **environment-blocked**, reported separately, not claimed as verified.
- Pre-existing `test_server::test_full_lifecycle_over_api` lockout ordering flake — existing
  technical debt, tracked separately; not a regression of this work.
