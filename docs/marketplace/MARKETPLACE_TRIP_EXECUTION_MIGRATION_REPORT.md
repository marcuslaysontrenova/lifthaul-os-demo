# Marketplace Trip-Execution Migration Report

**Status:** VERIFIED (deterministic, read-only classifier) · **Date:** 2026-08-04 · Increment 5 (core) ·
Backed by `marketplace_trips.classify_existing()` + `test_marketplace_trips.py`.

## Scope honesty

Increment 5 (core) delivers the **execution engine** — Trip Execution state machine, provider-neutral
GPS + geofencing, and Proof of Delivery — wired to Increment-4 protected payment. The full driver mobile
app, customer tracking portal, dispatch control-center UI, fleet/wallet/comms/analytics surfaces, and the
AI operational copilot from the 12-workstream Increment-5 vision are **not** built in this increment; they
are staged on top of this governed backend spine.

## Purpose

Classify existing operational jobs for the marketplace trip-execution layer **without** activating any
trip, fabricating any Proof of Delivery, or altering any existing job/payment status. No trip goes active
and no execution evidence is invented by migration.

## Classification buckets

| Bucket | Rule |
|---|---|
| `marketplace_trip_candidate` | tied to a confirmed marketplace assignment with protected funding |
| `internal_operational_job` | existing operational-spine `jobs` (default) |
| `historical` | closed/retired |
| `excluded` | explicitly excluded |

Existing operational jobs are classified `internal_operational_job` — they are never activated as
marketplace trips by migration.

## Invariants (asserted by tests + PostgreSQL CI)

```
UNEXPECTED FINANCIAL DIFFERENCES     = 0
UNEXPECTED PAYMENT-STATUS CHANGES    = 0
UNEXPECTED JOB-STATUS CHANGES        = 0
UNEXPECTED TRIP ACTIVATIONS          = 0
UNEXPECTED POD RECORDS               = 0
```

Verified locally (`test_migration_zero_drift`, `test_no_financial_drift` → freight 72000/672000
unchanged) and on real PostgreSQL (`trip migration zero drift`, `trip layer did not change freight
financials`).

## Key execution invariants (proven)

- A trip **activates only when the Increment-4 protected-funding gate is eligible** — no trip goes
  active before payment authorization.
- Proof of Delivery is **required** before `POD_SUBMITTED`; delivery evidence bridges to Increment-4
  release (execution evidence, not manual input, unlocks payout).
- **Live GPS/maps providers are fail-closed** (Google/Mapbox/OSM/HERE raise until owner-provisioned
  credentials + validation); the deterministic mock is labelled MOCK_ONLY.

## Live-provider status

```
MOCK GPS / execution:   VERIFIED (deterministic, MOCK_ONLY)
LIVE GPS / maps:        BLOCKED  (owner: provision a maps/GPS provider key + validate live ingestion)
```
