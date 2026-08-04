# Marketplace Protected-Payment Migration Report

**Status:** VERIFIED (deterministic, read-only classifier) · **Date:** 2026-08-03 · Increment 4 ·
Backed by `marketplace_payments.classify_existing()` + `test_marketplace_payments.py`.

## Purpose

Classify existing payments and assignments for the protected-payment layer **without** creating fake
protected-funds records, assigning fake provider transactions, altering existing payment/job status, or
creating any release/refund/funding record. No trips are activated. No funds move.

## Method

`classify_existing()` reads canonical tables and buckets records deterministically. It never fabricates
provider evidence and never marks anything protected/released/refunded. Any migrated record that enters
the marketplace protected-payment flow must go through the full governed lifecycle (funding evidence →
reconciliation → protected → gated release under SoD).

## Classification buckets

| Bucket | Rule |
|---|---|
| `provider_independent_payment` | existing operational `payment_requests` (default) |
| `manually_verified_payment` | manually verified, provider-independent |
| `marketplace_assignment_candidate` | tied to a confirmed marketplace assignment |
| `already_settled` | settled in the operational spine |
| `already_refunded` | refunded already |
| `ambiguous` | insufficient signal → human review |
| `historical` | closed/retired |
| `excluded` | explicitly excluded |

Existing operational payment records are classified `provider_independent_payment` — they are **not**
protected-payment records and are never funded/released/refunded by migration.

## Invariants (asserted by tests + PostgreSQL CI)

```
UNEXPECTED FINANCIAL DIFFERENCES     = 0
UNEXPECTED PAYMENT-STATUS CHANGES    = 0
UNEXPECTED JOB-STATUS CHANGES        = 0
UNEXPECTED FUNDING RECORDS           = 0
UNEXPECTED RELEASES                  = 0
UNEXPECTED REFUNDS                   = 0
```

Verified locally (`test_migration_zero_drift`, `test_no_financial_drift` → freight 72000/672000
unchanged) and on real PostgreSQL (`protected-payment migration zero drift`, `protected-payment layer
did not change freight financials`).

## Live-provider status

```
MOCK protected payment:            VERIFIED (deterministic, MOCK_ONLY / NOT_REAL_FUNDS)
LIVE protected payment:            BLOCKED
```

**Owner actions to unblock live settlement:** select + contract a licensed Philippine payment/
safeguarding partner (B3); confirm the legal operating model (B4); provision live provider credentials
and validate a sandbox/controlled-live funding + conditional-release + reconciliation flow (B1). Until
then the Wise/bank/e-wallet/licensed-partner adapters remain fail-closed and no funds move.
