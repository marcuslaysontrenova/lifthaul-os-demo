# Marketplace Onboarding Migration Report

**Status:** VERIFIED (deterministic, read-only classifier) · **Date:** 2026-08-03 · Increment 2 ·
Backed by `marketplace_onboarding.classify_existing()` + `test_marketplace_onboarding.py`.

## Purpose

Classify existing operational records into marketplace-candidacy buckets **without** activating any
participant, expanding any eligibility, or fabricating verification evidence. Existing platform
financials and operational statuses are untouched — this is a read-only classification.

## Method

`classify_existing()` reads canonical operational tables and buckets records deterministically. It
never writes an activation, never sets a `VERIFIED`/`ACTIVE` marketplace status, and never invents a
document. Migrated participants (if later imported) enter at `APPLICATION` and must pass the full
governed lifecycle — verification (no self-verify), fail-closed activation (no self-activate),
compliance evidence — exactly like a fresh applicant.

## Classification buckets

| Bucket | Rule | Auto-activated? |
|---|---|---|
| `shipper_candidate` | existing `customers` | **No** — candidate only |
| `carrier_candidate` | existing suppliers/subcontractors acting as haulers | **No** |
| `internal_operational` | internal ops records not marketplace participants | n/a |
| `marketplace_ineligible` | records that cannot be a shipper/carrier | n/a |
| `ambiguous` | insufficient signal → human review | **No** |
| `excluded` | explicitly excluded | n/a |
| `historical` | closed/retired records | n/a |

Existing customers are classified as **shipper candidates only**. Supplier/subcontractor → carrier
mapping is deferred until a dedicated import path exists (kept at 0/ambiguous rather than guessed).

## Invariants (asserted by tests + PostgreSQL CI)

```
UNEXPECTED FINANCIAL DIFFERENCES        = 0
UNEXPECTED OPERATIONAL STATUS CHANGES   = 0
UNEXPECTED PARTICIPANT ACTIVATIONS      = 0
UNEXPECTED ELIGIBILITY EXPANSION        = 0
```

Verified locally (`test_migration_no_auto_activation`, `test_no_marketplace_financial_drift` →
freight 72000/672000 unchanged) and on real PostgreSQL (`marketplace onboarding migration zero drift`,
`marketplace layer did not change freight financials`).

## Guarantees

- No participant is activated by migration.
- No eligibility pool is widened by migration (hard compliance gates still apply at
  `candidate_pool()` time).
- No verification evidence is fabricated; documents must be uploaded and independently verified.
- Historical records are preserved; nothing is deleted.
