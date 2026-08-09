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

## Errors

| HTTP | Meaning |
|---|---|
| 400 | validation error |
| 403 | invalid/revoked key, missing scope, wrong tenant, production not approved, ip not allowed |
| 404 | booking not found |
| 413 | payload too large |
| 429 | rate limit exceeded |

## E-commerce / ERP readiness

This API is designed to support future Shopify / WooCommerce / ERP / WMS / TMS / custom connectors
**without architectural change** — those connectors will call these same endpoints. They are not built
yet; the contract above is the integration surface they will use.
