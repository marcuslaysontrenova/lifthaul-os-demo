# 07 — Transaction State Machine

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

Authoritative: `_STATES`, `_EXCEPTION_STATES`, `_TRANSITIONS` in `backend/protected_payment.py`.
Only declared transitions are permitted; anything else raises. Guarded by
`backend/test_protected_payment.py`.

## Core states (16)

```
PAYMENT_REQUIRED → PAYMENT_INTENT_CREATED → AWAITING_CUSTOMER_FUNDS → CUSTOMER_FUNDED
→ FUNDING_CONFIRMED → FUNDS_PROTECTED → TRIP_AUTHORIZED → SERVICE_IN_PROGRESS
→ DELIVERY_EVIDENCE_PENDING → DISPUTE_WINDOW → RELEASE_ELIGIBLE → RELEASE_APPROVAL_PENDING
→ RELEASE_APPROVED → RELEASE_REQUESTED → RELEASE_CONFIRMED → SETTLED
```

## Exception states (11)

```
PAYMENT_FAILED, FUNDING_TIMEOUT, DISPUTED, DISPUTE_UNDER_REVIEW, REFUND_PENDING,
REFUNDED, PARTIALLY_REFUNDED, CANCELLED, FROZEN, RECONCILIATION_HOLD, LEGAL_HOLD
```

## Guard highlights

- Transitions into `RELEASE_*` compose the **release gate** (fraud, dispute, driver/vehicle legality,
  payout security, transaction limits). Any failing gate blocks the transition.
- `DISPUTED` may move to `DISPUTE_WINDOW`, `RELEASE_ELIGIBLE`, `REFUND_PENDING`, `LEGAL_HOLD`, or back
  to `FUNDS_HELD`/protected — never straight to `SETTLED`.
- `SETTLED` is only reachable when `reconcile()` shows `difference == 0`.
- Corrections never mutate history — they are reversing ledger entries (see doc 08).

## Idempotency & recovery

Duplicate provider webhooks are idempotent; state persists across process restarts (proven by
`FailureRecoveryE2E` in `backend/test_protected_payment_e2e.py`).
