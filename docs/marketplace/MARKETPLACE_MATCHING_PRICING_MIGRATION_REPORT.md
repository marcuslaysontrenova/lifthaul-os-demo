# Marketplace Matching & Pricing Migration Report

**Status:** VERIFIED (deterministic, read-only classifier) · **Date:** 2026-08-03 · Increment 3 ·
Backed by `marketplace_matching.classify_existing()` + `test_marketplace_matching.py`.

## Purpose

Classify existing bookings and quotations for marketplace candidacy **without** broadcasting, pricing,
offering, selecting, or assigning any record. Existing operational financials and statuses are untouched.

## Method

`classify_existing()` reads canonical tables and buckets records deterministically. It never creates a
marketplace booking, broadcast, offer, or assignment for a migrated record, and never moves funds.

## Classification buckets

| Bucket | Rule |
|---|---|
| `marketplace_candidate` | a booking tied to an active marketplace shipper on an eligible lane |
| `internal_operational` | existing operational-spine bookings (default for legacy `bookings`) |
| `historical` | closed/retired |
| `already_assigned` | operationally assigned already |
| `no_marketplace_shipper` | no marketplace shipper mapping |
| `no_eligible_lane` | no serviceable/assessable lane |
| `ambiguous` | insufficient signal → human review |
| `excluded` | explicitly excluded |

Existing operational bookings are classified `internal_operational` — they are **not** marketplace
records and are never broadcast/priced/offered/assigned by migration.

## Invariants (asserted by tests + PostgreSQL CI)

```
UNEXPECTED FINANCIAL DIFFERENCES        = 0
UNEXPECTED OPERATIONAL STATUS CHANGES   = 0
UNEXPECTED BROADCASTS                   = 0
UNEXPECTED OFFERS                       = 0
UNEXPECTED ASSIGNMENTS                  = 0
```

Verified locally (`test_migration_zero_unexpected`, `test_no_financial_drift` → freight 72000/672000
unchanged) and on real PostgreSQL (`matching migration zero drift`, `matching layer did not change
freight financials`).

## Guarantees

- No migrated record is broadcast, priced, offered, selected, or assigned.
- No funds move (Increment 3 never activates a trip or moves money).
- Pricing snapshots are immutable; a later rate change never alters an issued price.
- Historical records preserved; nothing deleted.
