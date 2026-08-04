# Nationwide Lane & Coverage Model + Activation Gate

**Status:** PARTIAL — **implemented & CI-tested** (`backend/marketplace.py`, lane tests) ·
**Date:** 2026-08-03 · Increment 1.

Backed by working code. This increment delivers the lane record, the **serviceability promise
boundary**, and the **deterministic lane-activation gate**. Full province/city/barangay/port/ferry/
truck-ban geography and multi-leg journey legs are **not** yet modeled (next increments).

## Lane record (`mkt_lanes`)

`origin_group`/`dest_group` ∈ {LUZON, VISAYAS, MINDANAO}, `origin_zone`→`dest_zone` (unique),
`corridor`, `requires_sea_leg` (auto-set when island groups differ), `distance_km`, `min_carriers`,
the seven readiness inputs, serviceability `status`, and SoD fields (`assessed_by`, `approved_by`).

## Serviceability lifecycle

`DRAFT → ASSESSING → INTEREST_ONLY → PILOT → ACTIVE → (SUSPENDED / CLOSED)`

`serviceability(origin_zone, dest_zone)` is the **public promise boundary**:

| status | accepts_interest | promises_service |
|---|---|---|
| ASSESSING / INTEREST_ONLY | yes | **no** |
| PILOT / ACTIVE | yes | **yes** |
| SUSPENDED / CLOSED | no | no |
| unknown lane | **yes** (capture demand) | **no** |

This is the blueprint rule made executable: *"the platform may accept interest for inactive lanes but
must not promise immediate service."* Seeded pilot lanes (Metro Manila + CALABARZON + Bulacan/Pampanga)
are created **ASSESSING** — they promise nothing until they earn activation. A test asserts no seeded
lane promises service.

## Deterministic lane-activation gate (blueprint §14)

`lane_activation_status(lane_id)` returns `{ready, unmet[], criteria{}}` with **no side effects**. A lane
is `ready` only when **all seven** hold:

1. `verified_carriers >= min_carriers`
2. `backup_capacity`
3. `price_model_validated`
4. `ops_support`
5. `payment_capable`
6. `dispute_process`
7. `monitoring`

`activate_lane(actor, lane_id, target=PILOT|ACTIVE)` enforces: the `marketplace.lane.activate`
permission **AND** every criterion met **AND** the approver is **not** the assessor (separation of
duties). Any unmet criterion raises with the explicit `unmet` list; a self-approval raises
`PermissionError`. `set_lane_status()` can suspend/close but can **never** jump straight to PILOT/ACTIVE —
activation is the only path to a service promise.

## Not yet built (subsequent increments)

Province/city/barangay hierarchy; ports + ferry/RoRo routes; truck-ban / toll / restricted-route
overlays; multi-leg journey legs (custody owner, per-leg carrier/driver/vehicle/SLA/handover/exception);
lane-level pricing model; carrier supply counts sourced from real onboarding instead of assessment input.
