# 06 — Provider Interface Description

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

Formal contract a candidate provider must satisfy: `ProtectedPaymentProvider` in
`backend/protected_payment.py`, validated by `certify_provider()`.

## Required operations

| Method | Purpose |
|---|---|
| `create_payment` | Open a held payment for a transaction |
| `get_payment` | Fetch current provider-side state |
| `confirm_funding` | Confirm customer funds are received + safeguarded |
| `get_protected_balance` | Held balance for the transaction |
| `place_hold` | Hold/extend a hold |
| `release_partial` / `release_full` | Settle to carrier (on LiftHaul instruction) |
| `refund_partial` / `refund_full` | Refund to customer |
| `cancel` | Cancel an unfunded/permitted transaction |
| `get_settlement` | Settlement record for reconciliation |
| `reconcile` | Provider-side reconciliation feed |
| `verify_webhook` | Signature/replay/idempotency verification |
| `declare_capabilities()` | Machine-readable capability + regulatory-status declaration |

## Capability declaration

`declare_capabilities()` must return capability keys plus a regulatory-status block. The reference
`MockProtectedPaymentProvider` declares `regulated_status="NOT_A_LICENSED_PROVIDER"` and `live=False`
so it **conforms but is never active-eligible** (`certify_provider` returns `active_eligible=False`).

## Fail-closed rule

Any capability a provider does not declare/support raises `PROVIDER_CAPABILITY_NOT_SUPPORTED`. A
provider is only marked ACTIVE when: **regulatory status verified against the BSP listing** AND
**certification harness passes** AND **counsel approves the operating model**.

## Certification harness output

`certify_provider(adapter)` → `PROVIDER_CERTIFICATION_REPORT` with per-capability conformance and an
`active_eligible` verdict. Run against each candidate before configuration (see doc 17).
