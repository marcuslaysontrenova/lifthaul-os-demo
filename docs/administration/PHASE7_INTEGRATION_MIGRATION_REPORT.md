# PHASE 7 — Integration Migration Report

**Product:** LiftHaul OS · **Tenant Zero:** RGO Machine Rigging · **Date:** 2026-08-02
**Scope:** introduce governed Integration Administration + Wise (mock/sandbox) WITHOUT altering any
existing verified payment, WITHOUT assigning fake Wise transaction IDs, and WITHOUT changing any
financial value, payment status, or job status.

## Migration strategy (additive, provider-independent, no fabricated settlement)

Phase 7 adds a governed integration layer (definitions/profiles/idempotency/webhooks/reconciliation/
dead-letter/health) and a Wise adapter **alongside** the existing provider-independent payment domain.
Existing payment records are untouched; no real or fake Wise ID is assigned to a historical payment.
The payment amount continues to come from the **stored accepted-quotation downpayment snapshot**
(Phase-2), never recomputed. A provider `CREATED`/200 is never treated as settlement.

| Existing surface | Migration action | Impact |
|---|---|---|
| `payment_requests` (provider-independent) | retained; Wise transfers reference them via `payment_request_id` | none — records unchanged |
| verified payments | **never altered**; no Wise ID backfilled | none |
| `verify_payment` / `confirm_job` gate | reused; Wise verification feeds the same path with reconciled evidence + SoD | none — same control |
| MockWiseProvider (Phase-1) | superseded by `wise.MockWiseAdapter` (deterministic, all scenarios) | none |
| new integration objects | additive tables | additive only |

## Classification of existing payment/integration records

| Class | Handling |
|---|---|
| provider independent | existing payment_requests keep working without a provider |
| Wise candidate | future/open payment requests may create a Wise transfer (opt-in) |
| manually verified payment | preserved; not re-verified, not re-provider-stamped |
| external reference present / absent | left as-is |
| ambiguous | routed to manual review at reconciliation time (never auto-reconciled) |
| historical | excluded from any change |

## Migration results

| Metric | Result |
|---|---|
| Payment requests analyzed | all (read-only) |
| Verified payments altered | **0** |
| Fake Wise transaction IDs assigned | **0** |
| Wise transfers created | only via governed opt-in flow (mock) |
| **Financial differences** | **0** |
| **Payment-status changes** | **0** (from migration/wiring) |
| **Job-status changes** | **0** |

## Invariants (proved in CI on PostgreSQL)

```
UNEXPECTED FINANCIAL DIFFERENCES = 0
UNEXPECTED PAYMENT-STATUS CHANGES = 0
UNEXPECTED JOB-STATUS CHANGES = 0
```

- **Financial:** `test_wise_does_not_change_snapshot_financials` drives a Wise payment and asserts the
  quotation `tax`/`total`/`dp_amount` unchanged (72000/672000/201600); the payment amount equals the
  stored snapshot (201600).
- **Payment status:** verification requires a reconciled MATCHED item + an authorized verifier who is
  NOT the transfer creator (SoD); a provider `CREATED`/`PENDING` never verifies.
- **Job status:** job activation still requires a VERIFIED payment (`confirm_job`); mock settlement in a
  non-production environment cannot activate real production jobs.

## Live Wise boundary (§27)

Live Wise is **BLOCKED**: the `RealWiseAdapter` reads `WISE_API_KEY` server-side only and, absent
owner-controlled credentials, reports BLOCKED without fabricating success. All non-secret capability is
proven with the deterministic mock. Owner actions to unblock:

1. Provision a Wise **business** API token (sandbox first) and store it as the env-backed secret
   referenced by the connection profile (`WISE_API_KEY`).
2. Validate the connection (retrieves permitted profiles) and **select the authorized business profile**.
3. Run a sandbox quote + transfer + status retrieval; confirm reconciliation evidence.
4. Only then may LIVE WISE PRODUCTION READINESS be marked VERIFIED (reported separately from mock).

## Reversibility

- Only additive DDL; no column drops; historical payments untouched.
- Connection profiles can be suspended/killed (fail-safe); dead-letter + reconciliation items are
  additive and auditable; no destructive change to financial documents.
