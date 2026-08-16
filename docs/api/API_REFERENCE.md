# LiftHaul Enterprise — B2B API Reference (v1)

> Platform Control → Integrations. The B2B API exposes the **same canonical booking, quotation and
> tracking engines** used everywhere in LiftHaul — there is no separate booking system. Public
> anonymous intake (`POST /public/bookings`) remains for individual/manual bookings; this API is for
> **authenticated enterprise integrations** (ERP / WMS / TMS / e-commerce / custom apps).

Base URL (when hosted): `https://<your-lifthaul-host>`
Only the endpoints below are public. Internal `/admin/*` endpoints are **not** part of the API.

## Authentication

Every request carries an API key issued from the Developer Portal:

```
Authorization: Bearer <client_id>:<secret>
# or
X-API-Key: <client_id>:<secret>
```

- Secrets are shown **once** at creation and stored **hashed** — they are never returned again.
- Rotate a secret from the Developer Portal; the previous secret stops working immediately.
- `PRODUCTION` clients must be explicitly approved before their keys authenticate.
- `SANDBOX` clients use synthetic data and can never move real funds or enable live Protected Payment.

## Scopes (server-enforced; no wildcard)

`bookings:create` · `bookings:read` · `bookings:update` · `quotations:read` · `tracking:read` ·
`marketplace:read` · `payments:read` · `jobs:read`

A request without the required scope returns `403 { "error": "missing scope: ..." }`.

## Rate limits

Per client: `rate_per_min` (default 120) and `rate_per_day` (default 20000). Exceeding either returns
`429`. Tune per client in the Developer Portal.

## Idempotency

Send `Idempotency-Key: <unique>` on create/bulk requests. Replaying the same key returns the original
result and never creates a duplicate booking.

---

## Endpoints

### Create a booking — `POST /api/v1/bookings` (scope `bookings:create`)

Reuses multi-stop, scheduling, service levels, nationwide geography (Luzon/Visayas/Mindanao),
inter-island detection and the standardized quote engine. Server owns pricing — caller totals are ignored.

```json
{
  "contact_name": "Acme Logistics",
  "contact_phone": "09171234567",
  "origin_island": "Luzon",
  "dest_island": "Visayas",
  "origin_city": "Cavite",
  "dest_city": "Cebu",
  "vehicle": "6w",
  "km": 300,
  "service_level": "STANDARD",
  "schedule_type": "SCHEDULED",
  "scheduled_at": "2026-09-01",
  "stops": [
    {"type": "PICKUP", "address": "Warehouse A"},
    {"type": "DROP", "address": "Store 1"}
  ],
  "payment": "protected"
}
```

Response:

```json
{
  "ref": "LH-II1A2B3C", "tracking_token": "pbk_...", "status": "REQUEST_RECEIVED",
  "service": "Inter-Island", "inter_island": true, "service_class": "STANDARD",
  "service_level": "STANDARD", "schedule_type": "SCHEDULED", "stops": 2,
  "estimate": 25200, "estimate_status": "QUOTED_INDICATIVE",
  "protected_payment": {"eligible": true, "live_funds_enabled": false, "intended_method": "protected"}
}
```

Engineered classes (`crane`, `lowbed`) never receive an instant price — `estimate_status` is
`ESTIMATE_REQUIRED` and the request routes to the estimator queue.

### Bulk create — `POST /api/v1/bookings/bulk` (scope `bookings:create`)

```json
{ "rows": [ { ...booking... }, { ...booking... } ] }   // up to 500
```

Response: `{ "batch_id": "batch_...", "created_count": N, "error_count": M, "created": [...], "errors": [{"index":i,"error":"..."}] }`.
Each row is validated + priced independently; errors don't fail the batch.

### Read a booking — `GET /api/v1/bookings/:ref` (scope `bookings:read`)
### Track a booking — `GET /api/v1/bookings/:ref/tracking` (scope `tracking:read`)

Returns the **customer-safe projection** only: reference, normalized status, service type, origin,
destination, requested date, vehicle class, service level, schedule, quotation status, Protected
Payment state, milestones (domestic vs inter-island), multi-stop legs, POD availability, next action.
Never returns internal cost, margin, competing offers, fraud details, bank data or internal notes.

### Estimate a quote — `POST /api/v1/quotes/estimate` (scope `quotations:read`)

```json
{ "origin_island": "Luzon", "dest_island": "Luzon", "vehicle": "6w", "km": 100, "service_level": "STANDARD" }
```

Response: `{ "result": "INSTANT_ESTIMATE", "estimate": 6700, "service_class": "STANDARD", ... }`
or `{ "result": "ESTIMATE_REQUIRED", "estimate": null, "note": "Engineering estimate required..." }`
for crane/rigging/lowbed/engineered services.

---

## Webhooks

Subscribe endpoints (from the Developer Portal) to receive events. Delivery is **out-of-band** — a
failing endpoint never blocks core bookings.

**Events:** `booking.created`, `booking.reviewed`, `quotation.ready`, `quotation.accepted`,
`payment.required`, `payment.confirmed`, `marketplace.matching`, `carrier.assigned`, `trip.started`,
`trip.at_port`, `trip.in_transit`, `trip.delivered`, `pod.available`, `dispute.opened`,
`settlement.completed`.

**Delivery payload** (HTTP POST to your URL):

```
Headers:
  X-LiftHaul-Event: booking.created
  X-LiftHaul-Event-Id: evt_...
  X-LiftHaul-Signature: sha256=<hex>
Body:
  { "id": "evt_...", "type": "booking.created", "timestamp": "...", "tenant_safe": true, "data": { ... } }
```

**Verify the signature** — recompute `HMAC-SHA256(secret, "<event_id>.<timestamp>.<raw_body>")` and
compare to `X-LiftHaul-Signature`. The signing secret is shown once at webhook creation and is
rotatable.

**Delivery lifecycle:** `PENDING → DELIVERING → DELIVERED`, or on failure
`RETRYING` (exponential backoff) → `DEAD_LETTER` after 5 attempts. Administrators can **replay** a
failed delivery (same event id, incremented attempt, full audit trail).

## Goods Protection (cargo insurance) & Claims

LiftHaul orchestrates coverage; the policy sits with a **licensed insurer/broker** — LiftHaul is not
the insurer. Live coverage isn't advertised until an insurer is configured.

### Quote coverage — `POST /api/v1/insurance/quote` (scope `insurance:quote`)

```json
{ "booking_ref": "LH-...", "declared_value": 300000, "cargo_category": "MACHINERY" }
```

Result is one of: `ELIGIBLE` (with `coverage_limit`, `premium`, `deductible`, `provider`,
`validity_days`, `exclusions`), `MANUAL_UNDERWRITING_REQUIRED` (high-value / engineered / heavy),
`MANUAL_INSURANCE_REVIEW_REQUIRED` (no insurer connected), or `NOT_ELIGIBLE` (excluded cargo). No
instant premium is ever fabricated for engineered/heavy risks.

### Read coverage — `GET /api/v1/insurance/:ref` (scope `insurance:read`)
### Open a claim — `POST /api/v1/claims` (scope `claims:create`)

```json
{ "booking_ref": "LH-...", "incident_ref": "INC-1", "claimed_amount": 50000 }
```

A claim can be opened only against **BOUND** coverage; it references the policy and insured amount.
Insurer decisions (approve/deny/settle) require a recorded adjuster reference — LiftHaul never
fabricates them.

### Read a claim — `GET /api/v1/claims/:id` (scope `claims:read`)

### Additional webhook events

`insurance.quote_ready`, `insurance.bound`, `insurance.rejected`, `claim.created`, `claim.submitted`,
`claim.approved`, `claim.denied`, `claim.settled`.

## Errors

| HTTP | Meaning |
|---|---|
| 400 | validation error |
| 403 | invalid/revoked key, missing scope, wrong tenant, production not approved, ip not allowed |
| 404 | booking not found |
| 413 | payload too large |
| 429 | rate limit exceeded |

## Automated Customer & Operational Notifications

An event-driven notification layer sits over the canonical lifecycle. It **extends the existing
notification domain** — it is not a parallel messaging system. The B2B event bus (`emit_event`) fans
each platform event out to both outbound webhooks **and** this notification engine, so a single
lifecycle event (booking → quote → payment → carrier → pickup → delivery → OTP → POD → claim →
settlement) can reach the customer over email / SMS / push (WhatsApp later) according to policy.

**Honest delivery.** When no live provider adapter is configured for a channel, a send is **never**
marked DELIVERED. It fails as `provider_unavailable`; mandatory transactional notices retry with
exponential backoff to a dead-letter state, optional ones fail. Internal states: `CREATED · QUEUED ·
PROVIDER_ACCEPTED · DELIVERED · FAILED · RETRYING · DEAD_LETTER · SUPPRESSED`.

**Notification Policy Matrix** — per-event × channel modes (`REQUIRED` / `OPTIONAL` / `OFF`) decide
which channels fire; not every event blindly sends every channel. `REQUIRED` events in the mandatory
set (payment, carrier assignment, OTP issuance, recipient verified, delivered, claim status,
settlement) **cannot be suppressed** by an opt-out; `OPTIONAL` channels honor recipient opt-in/opt-out.

**Safety.** Duplicate prevention via a per-(tenant,event,recipient,channel,correlation) dedup key;
sensitive values (OTP/code/secret/password/card/bank/PIN) are **stripped from notification bodies** —
the OTP plaintext is delivered only through the authorized delivery-verification path, never logged,
returned, or placed in a notification. Recipients are masked in all history views. Every send emits an
audit trail; templates are tenant-scoped and versioned (activating a new version deactivates the prior).

Admin endpoints (session-authenticated, RBAC `integration.catalog.view` / `integration.profile.manage`):

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/notifications/history` | Notification history (recipient omitted; tenant-scoped) |
| GET | `/admin/notifications/provider-health` | Per-channel adapter status |
| GET | `/admin/notifications/policy/:event` | Resolved policy matrix row for an event |
| POST | `/admin/notifications/templates` | Upsert a tenant/event/channel/locale template (versioned) |
| POST | `/admin/notifications/preferences` | Set a recipient communication preference / opt-out |
| POST | `/admin/notifications/deliver` | Run one delivery pass (honest — no fabricated sends) |

## Carrier / Fleet Owner Portal

A secure **self-service surface over the existing carrier ecosystem** — it introduces no parallel
carrier, vehicle, driver, compliance, payment, trust or marketplace domain. A portal login is bound
to exactly one carrier via a `carrier_principals` record (identity-derived, **never** client-supplied);
a bound principal can only ever read or act on its **own** carrier_id, and a spoofed `carrier_id` in a
request is ignored.

**Governance invariant — a carrier manages its own fleet but never self-verifies regulated compliance.**
Registering a vehicle lands it `DRAFT`; a driver lands `APPLICATION`; an uploaded document lands
`UPLOADED/SUBMITTED`; a payout account lands `PENDING_APPROVAL`. Only a LiftHaul reviewer can move any
of these to VERIFIED/ACTIVE/APPROVED. The `carrier_principal` role holds none of the operational
`marketplace.*` permissions and no verify/activate/approve/override permission, so a carrier token
against any `/admin/*` route is `403`. Internally the portal elevates the carrier's own actor by the
single minimal permission needed for one self-service call — a closed allow-list that can never include
a verification permission (a hard assertion guards it).

The **operational-eligibility panel** (`GET /portal/carrier/overview`) composes KYB, business-permit
compliance, LTFRB/CPC authority, fleet + driver eligibility (via the existing compliance and legality
gates) and the hard marketplace gate, so a carrier sees exactly *why* a company, vehicle or driver
cannot accept work.

Carrier-facing (session actor = a bound carrier principal):

| Method | Path | Purpose |
|---|---|---|
| GET | `/portal/carrier/overview` | Eligibility summary panel (company / fleet / drivers / marketplace status) |
| GET | `/portal/carrier/profile` · `/compliance` · `/fleet` · `/drivers` | Profile, document/CPC status + expiry watch, fleet, drivers (each with per-item eligibility reasons) |
| GET | `/portal/carrier/invitations` · `/assignments` · `/trips` | Offers, active assignments, trips (scoped to this carrier) |
| GET | `/portal/carrier/finance` · `/cases` · `/notifications` · `/performance` | Earnings + Protected Payment + payout status; disputes/claims/Goods-Protection; masked comms; trust score |
| POST | `/portal/carrier/vehicles` · `/drivers` | Register a vehicle (→DRAFT) / driver (→APPLICATION) |
| POST | `/portal/carrier/vehicles/:id/maintenance` | Toggle own vehicle maintenance hold (cannot mark ACTIVE) |
| POST | `/portal/carrier/documents` | Upload a compliance document (→SUBMITTED, never self-verified) |
| POST | `/portal/carrier/payout-account` | Submit a payout account (→pending approval; masked at rest) |
| POST | `/portal/carrier/offers` · `/offers/:id/withdraw` | Submit / withdraw a marketplace offer |
| POST | `/portal/carrier/assignments/:id/respond` | Accept / decline an assignment |
| POST | `/portal/carrier/trips/:id/pod` | Submit proof-of-delivery evidence (OTP never entered here) |

Operator-side principal administration (requires `marketplace.carrier.application.manage`):

| Method | Path | Purpose |
|---|---|---|
| POST | `/admin/carrier-portal/bind` | Bind a user login to a carrier |
| POST | `/admin/carrier-portal/principals/:id/revoke` | Revoke a binding |
| GET | `/admin/carrier-portal/overview/:carrier_id` | Operator support read of a carrier's overview |

Front-end: `portal.html` (carrier-facing) + a **Carrier Access** tab in the operator console for
binding principals and previewing a carrier's operating picture.

## Driver Reassignment / Re-matching

A governed orchestration over the **existing** matching primitives — no new carrier/vehicle/driver/
matching/offer/assignment/payment domain. When an assigned resource falls through, a reassignment case
(`mkt_reassignments`, the only new table) records why and what moved, and drives one of two paths:

- **Intra-carrier substitution** — swap in another driver/vehicle from the **same carrier** via the
  existing `request_substitution`, which deterministically re-runs every eligibility gate (carrier
  active + compliant, vehicle ACTIVE + eligible, driver assignable). **Fail-closed:** an ineligible
  substitute is rejected and the case stays OPEN. The substitute must belong to the same carrier.
- **Inter-carrier re-match** (ops authority) — release the current carrier, set the assignment
  `REASSIGNMENT_REQUIRED`, return the booking to matching and re-open the broadcast to other eligible
  carriers via the existing candidate-generation + broadcast.

**Protected Payment continuity is absolute:** a reassignment asserts `funds_moved: false`, is **refused**
once the protected transaction is releasing/settled (`RELEASE_APPROVED/REQUESTED/CONFIRMED/SETTLED`), and
never mutates the protected ledger — settlement stays governed by the existing release gate. Mid-trip
reassignment (an active trip exists) is **HIGH severity and requires evidence**. Terminal assignments
cannot be reassigned. Reason codes: `DRIVER_UNAVAILABLE, DRIVER_NO_SHOW, VEHICLE_BREAKDOWN, LICENSE_EXPIRED,
COMPLIANCE_LAPSED, CARRIER_SUSPENDED, SHIPPER_REQUESTED, OPS_FORCED`.

Operator endpoints (RBAC `marketplace.reassignment.*`):

| Method | Path | Purpose |
|---|---|---|
| POST | `/admin/marketplace/reassignments` | Open a case (`marketplace.reassignment.open`) |
| POST | `/admin/marketplace/reassignments/:id/substitute` | Intra-carrier substitution (`…substitute`) |
| POST | `/admin/marketplace/reassignments/:id/rematch` | Inter-carrier re-match (`…rematch`) |
| POST | `/admin/marketplace/reassignments/:id/cancel` | Cancel an open case |
| GET | `/admin/marketplace/reassignments` · `/:id` · `/:id/timeline` | List / detail / audit timeline |
| GET | `/admin/marketplace/reassignment-queues` | Open / high-severity / substituted / re-matched counts |

Carrier-portal (intra-carrier only — a carrier can never re-match its own work away):

| Method | Path | Purpose |
|---|---|---|
| POST | `/portal/carrier/reassignments` | Open an intra-carrier reassignment on an own assignment |
| POST | `/portal/carrier/reassignments/:id/substitute` | Propose a same-carrier substitute |

Front-end: a **Reassignment** operator console tab (queues + open/substitute/re-match/cancel) and a
"Reassign driver/vehicle" action on the carrier portal's Assignments tab.

## Hourly / Daily / Project Rental

A duration-and-usage revenue model over the **existing** spine — LiftHaul's heavy-equipment roots
(crane/rigging rental), where the unit of sale is a resource rented for a duration, not a point-to-point
haul. It reuses carriers, vehicles, drivers, tax policy, the platform-fee split and **Protected Payment**
(`create_transaction`, MOCK provider) — rental money is protected and released by the **same governed
gate** as freight, and is never fabricated.

New, structurally-distinct pieces: a rental **rate model** and **agreement lifecycle**:
- `rental_rate_cards` — governed, **effective-dated + versioned** rates per (vehicle_category, rate_unit
  ∈ HOURLY/DAILY/WEEKLY/MONTHLY/PROJECT) with a **minimum-billing quantity**, overtime multiplier,
  standby rate, mobilization fee, and operator/fuel inclusion flags. Superseding a rate closes the prior
  one (audit trail, never an in-place mutation).
- `rental_agreements` — `QUOTED → CONFIRMED → ACTIVE → COMPLETED → SETTLED` (or `CANCELLED`). Confirm
  requires an assigned carrier+vehicle; activate re-runs the **same** driver/vehicle eligibility gate the
  marketplace uses.
- `rental_usage` — **honest** capture of actual hours/days + overtime + standby (non-negative,
  meter-ordered; never inferred).
- `rental_invoices` — an immutable, checksummed billing snapshot: `billed = max(actual, min_billing)`;
  `+ overtime×rate×multiplier + standby + mobilization − discount`; tax + 10% platform fee; carrier
  payout derived. **Overtime above ₱50,000 requires `marketplace.rental.overtime.approve`.** One invoice
  per agreement.

Operator endpoints (RBAC `marketplace.rental.*`):

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/admin/marketplace/rental/rates` | List / set (versioned) a rental rate (`…rate.manage`) |
| POST | `/admin/marketplace/rental/agreements` | Quote a rental (returns an estimate) |
| POST | `/admin/marketplace/rental/agreements/:id/confirm` · `/activate` | Advance the agreement |
| POST | `/admin/marketplace/rental/agreements/:id/usage` | Record actual usage (`…usage.record`) |
| POST | `/admin/marketplace/rental/agreements/:id/finalize` | Billing snapshot + Protected Payment (`…billing.finalize`) |
| POST | `/admin/marketplace/rental/agreements/:id/settle` · `/cancel` | Settle / cancel |
| GET | `/admin/marketplace/rental/agreements` · `/:id` · `/queues` | List / detail (+usage+invoice) / counts |

Front-end: a **Rental** operator console tab (queues, quote form, agreement lifecycle actions, and the
effective rate catalog).

## Corporate Billing & Statements

Consolidated accounts-receivable over the **existing** revenue streams — a corporate customer running
both freight and rental gets ONE account, ONE running balance, and periodic statements, without a
parallel payment domain and without coupling billing to each source's schema.

- `billing_accounts` — one account per customer, with credit limit + payment terms + billing cycle.
  Credit governance **reuses `crm_admin.evaluate_credit`** (evidence-only by default), never a second
  credit engine.
- `billing_items` — a normalized, source-agnostic charge/credit **ledger**. Any revenue source posts a
  charge via `post_charge(source_type, source_id, …)`, **idempotent per source**, so statements read
  one ledger. Rental finalize posts here automatically; freight/ERP use the same call.
- `billing_payments` — A/R payment **records**. Recording a payment posts a `CREDIT` and moves the
  balance; **it never moves real money** — live custody stays behind the Protected Payment funds gate
  (`funds_moved: false`).
- `billing_statements` — an immutable, checksummed period snapshot: opening balance (carried from the
  prior statement), charges, payments, closing balance, **aging** (current / 1-30 / 31-60 / 61-90 /
  90+), due date (`period_end + terms`), and a credit-limit evaluation. Generation **sweeps every still-
  open item up to `period_end`** — a charge posted late (dated before the period but after the last
  statement) is never lost — and stamps swept items to the statement so they are not double-counted.

Operator endpoints (RBAC `marketplace.billing.*`):

| Method | Path | Purpose |
|---|---|---|
| POST/GET | `/admin/marketplace/billing/accounts` | Open (`…manage`) / list accounts |
| POST | `/admin/marketplace/billing/accounts/:id/terms` | Update credit limit / terms / status |
| GET | `/admin/marketplace/billing/accounts/:id/balance` | Running balance (charges − credits) |
| POST | `/admin/marketplace/billing/accounts/:id/charges` | Post a charge (idempotent per source) |
| POST | `/admin/marketplace/billing/accounts/:id/payments` | Record an A/R payment (`…payment`; no fund movement) |
| POST | `/admin/marketplace/billing/accounts/:id/statements` | Generate a period statement (`…statement`) |
| GET | `/admin/marketplace/billing/statements` · `/:id` | List / detail (with lines + aging) |
| POST | `/admin/marketplace/billing/statements/:id/paid` | Mark a statement (and its charges) paid |
| GET | `/admin/marketplace/billing/queues` | Account / over-limit / issued-statement counts |

Integration: `rental.finalize_rental` calls `billing.post_charge_for_customer` so a corporate
customer's rental invoices accrue to their statement automatically (no account → the source invoice
simply stands alone). Front-end: a **Corporate Billing** operator console tab (accounts, charges,
payments, statement generation, and a statement detail view with aging).

## Preferred Carriers / Dedicated Capacity

A shipper preference layer over the **existing** matching — steer work to trusted carriers, exclude
unwanted ones, and reserve guaranteed capacity — without forking matching, ranking, or the
carrier/vehicle domains.

- `carrier_preferences` — a shipper's tiered carrier list:
  `DEDICATED` (reserved capacity, highest) > `EXCLUSIVE` (when any exists, the pool is restricted to the
  shipper's preferred carriers only) > `PREFERRED` (ranking boost; others still compete);
  `BLOCKED` (excluded from this shipper's matches — a **business choice, never a compliance action**).
  One preference per shipper-carrier pair (upsert).
- `dedicated_capacity` — a commitment of N units of a vehicle category from a carrier to a shipper over
  a period. Reserving capacity implies a `DEDICATED` preference. **Usage is counted honestly** from real
  assignments (carrier + matching vehicle category + shipper, within the period) — never asserted.

`apply_preferences` runs **after** the deterministic `rank_candidates` step and only **reorders/filters
an already-eligible list** — it can never override a hard eligibility or compliance gate (those ran in
`candidate_pool`), and never introduces a carrier that wasn't already eligible. The adjustment is
transparent: each surviving candidate is annotated with `preference_tier`, `preference_bonus`, and
`adjusted_score`. `generate_candidates` consults this layer through a guarded hook, so preferences take
effect without matching needing to know how they're stored.

Operator endpoints (RBAC `marketplace.preference.*` / `marketplace.capacity.*`):

| Method | Path | Purpose |
|---|---|---|
| POST | `/admin/marketplace/preferences` | Set/upsert a shipper-carrier preference (`…preference.manage`) |
| POST | `/admin/marketplace/preferences/:id/remove` | Remove a preference |
| GET | `/admin/marketplace/preferences` | List preferences (optionally by shipper) |
| POST | `/admin/marketplace/dedicated-capacity` | Reserve capacity (`…capacity.manage`; implies DEDICATED) |
| POST | `/admin/marketplace/dedicated-capacity/:id/cancel` | Cancel a commitment |
| GET | `/admin/marketplace/dedicated-capacity/:id/status` | Committed / used (from real assignments) / available |
| GET | `/admin/marketplace/dedicated-capacity` | List commitments |
| GET | `/admin/marketplace/preference-queues` | Tier + active-capacity counts |

Front-end: a **Preferred Carriers** operator console tab (tier preferences, capacity reservations, and
live usage).

## Dynamic Surcharge Engine

Governed, effective-dated surcharge rules applied **transparently** inside the existing
`price_booking` — without forking the pricing engine and **without silently changing any price**.

- `surcharge_rules` — effective-dated, versioned rules. Each has a type (FUEL / PEAK / HOLIDAY / ZONE /
  CONGESTION / DEMAND / SPECIAL), a basis (`PERCENT` of subtotal / `FLAT` / `PER_KM`), a rate, an
  `applies_when` condition set (origin/dest zones, vehicle categories, cargo codes, weekdays, a date
  window, a distance band), a priority, and a `stackable` flag. Superseding a rule closes the prior one.
- `surcharge_applications` — an immutable record of exactly which rules hit which pricing snapshot and
  for how much (full transparency + audit).

**Config-gated, fail-safe by default.** The pricing hook fires only when `marketplace.surcharge_enabled`
is `true` (default `false`), so existing prices and pricing-snapshot checksums are **unchanged** until an
operator deliberately switches surcharging on — the same fail-closed discipline as LTFRB enforcement and
live funds. Rates are operator-set, never fabricated from a live feed. `evaluate` is pure and
deterministic; stackable rules all apply, and among non-stackable matched rules only the single
highest-priority one applies.

Operator endpoints (RBAC `marketplace.surcharge.*`):

| Method | Path | Purpose |
|---|---|---|
| POST/GET | `/admin/marketplace/surcharge/rules` | Set/upsert (versioned) / list rules (`…manage` / `…view`) |
| POST | `/admin/marketplace/surcharge/rules/:id/deactivate` | Deactivate a rule |
| POST | `/admin/marketplace/surcharge/quote` | Preview which surcharges would apply to a context (no persist) |
| GET | `/admin/marketplace/surcharge/bookings/:id/applications` | The immutable applied-surcharge audit for a booking |
| GET | `/admin/marketplace/surcharge/queues` | Active-rule / application counts + engine on/off |

When enabled, surcharges appear as `surcharge_<CODE>` line components in the pricing snapshot and are
recorded in `surcharge_applications`. Front-end: a **Surcharges** operator console tab (rule catalog,
a "what would apply" preview, and the engine on/off indicator).

## Driver Mobile App

A driver-facing operating surface over the **existing** trip / POD / OTP domains — no parallel trip,
POD, or verification domain. It mirrors the Carrier Portal governance pattern: a login is bound to
exactly one driver (`driver_principals`, identity-derived, never client-supplied), and every read/write
is forced to that driver's **own** trips and delegates to the canonical `marketplace_trips` /
`delivery_verification` functions via a minimal, auditable elevation.

**Two hard safety rules, preserved by construction:**
- **A driver never self-verifies compliance.** The `driver_principal` role holds no operational
  `marketplace.*` permission and no verify/activate/approve, so any `/admin/*` route is `403`; the
  elevation allow-list can never include a verify/approve permission (hard-asserted).
- **A driver never issues or sees a delivery OTP.** The app can only **verify** a code the recipient
  provides (`delivery.verification.verify`); it cannot issue, resend, or read the plaintext (separate
  permissions the role lacks, and the verify path returns a result, never the code).

Driver-facing (session actor = a bound driver principal):

| Method | Path | Purpose |
|---|---|---|
| GET | `/driver/profile` · `/driver/trips` · `/driver/trips/:id` | Own profile, own trips, trip detail + timeline + delivery-verification status |
| POST | `/driver/trips/:id/start` | Activate the trip |
| POST | `/driver/trips/:id/advance` | Advance status (EN_ROUTE → … → DELIVERED) |
| POST | `/driver/trips/:id/ping` | Send a GPS position |
| POST | `/driver/trips/:id/pod` | Submit proof-of-delivery evidence |
| POST | `/driver/trips/:id/accept` | Accept delivery |
| POST | `/driver/trips/:id/verify-otp` | Verify the recipient's delivery code (never issued/seen by the driver) |
| POST | `/driver/trips/:id/exception` | Report a field exception |

Operator-side (requires `marketplace.driver.manage`):

| Method | Path | Purpose |
|---|---|---|
| POST | `/admin/driver-app/bind` | Bind a login to a driver |
| POST | `/admin/driver-app/principals/:id/revoke` | Revoke a binding |

Front-end: `driver.html` — a mobile-first driver app (assigned trips, one-tap status advance, GPS from
the device, POD capture, and a recipient-code entry field). Operators bind driver logins from the
console **Carrier Access** tab.

## Service Provider & Fleet Registration Workspace

A dynamic, master-data-driven registration layer over the **existing** carrier / vehicle / driver /
compliance domains. A provider registers once (the `mkt_carriers` record) and then adds unlimited
individual units — without forking any of those domains.

- `vehicle_variants` — a **master-data taxonomy** (category → variant → class) that maps a rich variant
  ("6-Wheeler Closed Van") onto an existing marketplace `category_code`, so pricing/matching keep
  working unchanged. **Admin-extendable** (`set_variant`) — new variants without a code change.
- **Classification engine** (`classify`) — the provider supplies physical specs (wheels / axles / body /
  payload / refrigerated / lifting) and LiftHaul **deterministically** resolves the canonical variant +
  tonnage class, e.g. `{TRUCK, 6 wheels, closed_van, 4000kg}` → **"6-Wheeler Closed Van - 4T Class"**.
  Both provider-entered specs and the canonical classification are stored (`vehicle_specs`).
  Unclassifiable specs are rejected (the engine never guesses beyond the governed rules).
- `register_unit` classifies then delegates to the canonical `register_vehicle`; the unit lands **DRAFT**
  (a reviewer verifies — a provider never self-verifies).
- **Service areas + capabilities** (`provider_service_areas` / `provider_capabilities`) influence
  eligibility.
- **Per-unit eligibility** returns **specific coded reasons** composed from the existing gates:
  `ELIGIBLE / REGISTRATION_EXPIRED / INSURANCE_EXPIRED / CPC_INVALID / MAINTENANCE_HOLD /
  DRIVER_UNQUALIFIED / DRIVER_UNAVAILABLE / OUTSIDE_SERVICE_AREA / PROVIDER_SUSPENDED / COMPLIANCE_HOLD /
  NOT_ACTIVATED`. Server-side rules stay authoritative — a front-end selection never determines
  eligibility.
- **Bulk import** classifies → validates → creates, isolating bad rows (dry-run supported).

Operator endpoints (RBAC `marketplace.fleet.*` / `marketplace.fleet.variant.manage`):

| Method | Path | Purpose |
|---|---|---|
| POST/GET | `/admin/marketplace/fleet/variants` | Add/upsert (admin) / list the variant taxonomy |
| POST | `/admin/marketplace/fleet/classify` | Classify specs → canonical variant (preview) |
| POST | `/admin/marketplace/fleet/units` | Register a unit from specs (→DRAFT) |
| GET | `/admin/marketplace/fleet/units/:id/spec` | Unit spec profile (provider + canonical) |
| POST | `/admin/marketplace/fleet/units/:id/eligibility` | Per-unit eligibility with coded reasons |
| POST/GET | `/admin/marketplace/fleet/service-areas` · `…/carriers/:id/service-areas` | Set / list coverage |
| POST/GET | `/admin/marketplace/fleet/capabilities` · `…/carriers/:id/capabilities` | Set / list capabilities |
| GET | `/admin/marketplace/fleet/carriers/:carrier_id/dashboard` | Fleet dashboard (counts by variant/status/eligibility) |
| POST | `/admin/marketplace/fleet/bulk-import` | Bulk register (dry-run + real; isolates errors) |

Carrier-portal (a provider self-registers its OWN fleet): `POST /portal/carrier/fleet/units`,
`/fleet/classify`, `/fleet/service-areas`, `/fleet/capabilities`, `GET /portal/carrier/fleet-dashboard`.
Front-end: a console **Fleet Registration** tab (classification preview, spec-based registration, variant
master data). New equipment categories (forklift, telehandler) are added to the canonical catalog so the
full taxonomy — motorcycle → truck configs → heavy-haul → crane → forklift — is registrable.

## E-commerce / ERP readiness

This API is designed to support future Shopify / WooCommerce / ERP / WMS / TMS / custom connectors
**without architectural change** — those connectors will call these same endpoints. They are not built
yet; the contract above is the integration surface they will use.
