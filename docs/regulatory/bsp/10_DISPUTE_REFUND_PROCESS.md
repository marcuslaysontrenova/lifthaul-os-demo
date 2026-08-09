# 10 — Customer Dispute / Refund Process

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

Authoritative: dispute lifecycle in `marketplace_trust_closure.py`; refund path in
`protected_payment.py`. Proven by `DisputeE2E` (`backend/test_protected_payment_e2e.py`).

## Dispute lifecycle

1. **Window** — `DISPUTE_WINDOW`; release cannot complete while an open dispute exists.
2. **Open** — customer or carrier opens a dispute → `DISPUTED` → `DISPUTE_UNDER_REVIEW`.
3. **Adjudicate** — separation of duties: the opener may not resolve. Outcomes: release (full/partial),
   refund (full/partial), or legal hold.
4. **Resolve** — partial release + partial refund must **reconcile to zero** with remaining + fees.
5. **Escalate** — unresolved/complex → `LEGAL_HOLD`.

## Refunds

- Full and partial refunds via the provider (LiftHaul instructs).
- An **excessive refund fails reconciliation** (`difference != 0`) and is blocked.
- Refund events are immutable ledger entries.

## Consumer protection

Dispute windows, refund rights, and required disclosures are **counsel determinations**
(`COUNSEL_DECISION_CHECKLIST.md` §6). Defaults in code are configurable per that determination.

## Customer-facing language

The customer projection uses friendly labels and a step timeline; internal states are redacted. No
customer-facing use of "escrow" (enforced by `backend/test_terminology_guard.py`).
