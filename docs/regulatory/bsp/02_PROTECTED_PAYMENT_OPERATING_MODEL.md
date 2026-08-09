# 02 — Protected Payment Operating Model

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

Canonical model. Terminology is **"Protected Payment"**, not "Escrow", until counsel + the regulated
provider confirm the legally correct term (see `docs/legal-payment/COUNSEL_DECISION_CHECKLIST.md`).

## Actors

- **Customer (shipper)** — pays the contract amount for a booking.
- **Licensed provider** — a BSP-regulated payment entity that **holds and settles** the funds.
- **LiftHaul OS** — orchestrates the transaction lifecycle and issues **release instructions**;
  never holds or moves funds.
- **Carrier** — receives settlement upon a valid, verified release.

## Lifecycle (business view)

1. Booking assigned → Protected Payment transaction created (`PAYMENT_REQUIRED`).
2. Customer funds the transaction **with the provider** (`CUSTOMER_FUNDED` → `FUNDING_CONFIRMED`).
3. Provider confirms funds are safeguarded (`FUNDS_PROTECTED`); trip authorized.
4. Service delivered; proof-of-delivery + milestone verification.
5. Dispute window elapses with no open dispute → release becomes eligible.
6. Release approved (maker/checker) → LiftHaul issues release **instruction** → provider settles to
   carrier → `SETTLED` once the ledger reconciles to zero difference.
7. Disputes/refunds handled inside the same state machine; excess refunds fail reconciliation and are
   blocked.

## Custody boundary (non-negotiable in this model)

- Funds live with the **provider** at all times. LiftHaul stores only references + evidence.
- LiftHaul cannot unilaterally move funds; it can only instruct the provider within the governed
  state machine, subject to release gates (fraud, dispute, driver/vehicle legality, payout security,
  transaction limits).

## Authoritative implementation

`backend/protected_payment.py` — 16 states + 11 exception states, guarded transitions, immutable
append-only ledger, reconciliation, provider interface + certification harness. Enforced by
`backend/test_protected_payment.py` and `backend/test_protected_payment_e2e.py`.

## Live-funds gate

`assert_live_allowed()` refuses any real-fund movement unless all three flags are true
(`legal_operating_model_approved`, `licensed_provider_active`, `live_protected_funds_enabled`).
Current state: **all false** → no live funds.
