# Cargo & Vehicle Taxonomy + Deterministic Eligibility

**Status:** PARTIAL — **implemented & CI-tested** (`backend/marketplace.py`, 31 tests) ·
**Date:** 2026-08-03 · Increment 1 of the Nationwide Marketplace program.

This document is backed by working code, not a plan. Insurance / inspection / live availability /
GPS location are **not** yet modeled (they belong to carrier-onboarding and driver-execution
workstreams) — this increment delivers the governed *catalog* + the *deterministic eligibility* logic.

## Vehicle taxonomy (`mkt_vehicle_categories`, seeded ACTIVE)

Every record carries: `payload_kg`, `volume_cbm`, internal dims, **opening dims**, `body_type`,
`axle_config`, `lifting_capable` + `lifting_capacity_kg`, `refrigerated`, `hazmat_allowed`,
`port_eligible`, `requires_special_permit`, status lifecycle (DRAFT→ACTIVE→RETIRED), immutable checksum.

| class_group | seeded categories |
|---|---|
| MOTORCYCLE_SMALL | motorcycle, motorcycle_box, sedan, mpv |
| LIGHT_COMMERCIAL | multicab, pickup, small_van, l300_van, ref_van_light, elf_4w |
| MEDIUM_HEAVY | truck_6w, truck_6w_wing, truck_6w_ref, truck_10w, truck_10w_wing, truck_12w, flatbed_10w, container_chassis |
| SPECIALIZED | lowbed_trailer, boom_truck, crane_truck |

## Cargo taxonomy (`mkt_cargo_types`, seeded ACTIVE)

Handling flags: `fragile, perishable, refrigerated, high_value, oversized, overweight, machinery,
hazardous, regulated, prohibited, default_permit_required`. Seeded classes include general,
packaged_goods, retail_stock, construction_material, agricultural, perishable_chilled, high_value,
machinery, vehicle_cargo, oversized_cargo, regulated_goods, hazardous, and **prohibited** (never bookable).

## Deterministic cargo→vehicle eligibility (the "before AI ranking" invariant)

`eligible_vehicles(conn, cargo_code, weight_kg, volume_cbm, dims)` returns the eligible pool using
**pure, deterministic rules** — computed *before* any AI ranking. AI may later re-rank this pool; it
may **never widen** it. Rules, in order:

1. cargo `prohibited` → empty pool, `blocked="cargo_prohibited"`.
2. cargo/vehicle must be `ACTIVE`.
3. `payload_kg >= weight_kg`; `volume_cbm >= volume_cbm`.
4. cargo `refrigerated` → vehicle must be `refrigerated`.
5. cargo `oversized` or `machinery` → vehicle `lifting_capable` OR body_type ∈ {flatbed, lowbed, container_chassis}.
6. cargo `hazardous` → vehicle `hazmat_allowed` (no seeded vehicle allows it yet → hazmat cargo is
   correctly un-servable until a compliant carrier is onboarded).
7. if item dims supplied → each dimension must fit the vehicle's **opening** dims.

`is_vehicle_eligible(...)` is the single-vehicle guard used by matching/assignment; a test proves it
agrees with the pool for every vehicle. Eligibility is proven **deterministic** (same inputs → same pool).

## Not yet built (subsequent increments)

Liquids/tanker body type; per-vehicle insurance/inspection/availability/location; tractor-head +
separate trailer modeling; AI re-ranking layer (must consume this pool, never bypass it).
