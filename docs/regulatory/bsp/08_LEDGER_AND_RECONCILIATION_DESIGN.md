# 08 — Ledger & Reconciliation Design

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

Authoritative: ledger + `reconcile()` in `backend/protected_payment.py`.

## Immutable ledger

- **Append-only.** Entry types: `funding`, `protected`, `platform_fee`, `provider_fee`, `release`,
  `refund`, `adjustment`.
- **No mutation / no deletion.** A correction is a new **reversing entry** (`reverses_entry_id`),
  preserving the full history.
- Every entry carries actor, timestamp, amount, reason, correlation id.
- Immutability proven by `ImmutabilityE2E` — finance cannot bypass reconciliation or edit history.

## Reconciliation identity

```
funded == released + refunded + remaining + fees
difference = funded - (released + refunded + remaining + fees)
```

- `SETTLED` requires `difference == 0`.
- Excess refunds or releases drive `difference != 0` → `SETTLEMENT BLOCKED` / `RECONCILIATION_HOLD`.
- `daily_reconciliation()` produces a finance-facing exception list.

## Provider reconciliation

The provider's `reconcile()` feed + `get_settlement()` are cross-checked against the platform ledger.
Discrepancies raise reconciliation exceptions surfaced in the Finance control center and the
Regulatory Compliance dashboard.

## Retention / auditability

The immutable ledger + `audit_ledger` events satisfy who-changed-what-when. Final retention periods
are a counsel/DPA determination (doc 15, doc 01 §"Records/audit").
