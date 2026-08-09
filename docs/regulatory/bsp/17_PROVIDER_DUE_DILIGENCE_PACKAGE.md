# 17 — Provider Due-Diligence Package

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

Master checklist: `docs/legal-payment/PROVIDER_DUE_DILIGENCE_CHECKLIST.md` (20 items). This document
frames it for the BSP submission and ties it to the certification harness.

## Selection rule

A provider may be marked **ACTIVE** only when **all three** hold:

1. **Regulatory status verified** against the BSP listing (NPSA registration/licence; EMI if applicable).
2. **Certification harness passes** — `certify_provider(adapter)` returns conformance for every
   mandatory capability and `active_eligible=true`.
3. **Counsel approves** the operating model (`COUNSEL_DECISION_CHECKLIST.md`).

## Due-diligence dimensions (summary)

- BSP/regulatory status + legal entity standing
- Safeguarding/segregation of held funds
- Settlement structure + timelines
- API conformance to `ProtectedPaymentProvider`
- Partial release, full+partial refund, dispute/chargeback handling
- Fees (per-txn, FX, payout), velocity/transaction limits
- Webhook security (signature, replay, idempotency)
- SLA/uptime, reconciliation feed, audit traceability
- DPA compliance, incident handling, BC/DR
- Contract termination + **fund-return scenario**

## Candidate register (to be completed)

| Provider | Regulated status verified | Harness passed | Counsel approved | Verdict |
|---|:---:|:---:|:---:|---|
| _(candidate 1)_ | ☐ | ☐ | ☐ | PENDING |
| _(candidate 2)_ | ☐ | ☐ | ☐ | PENDING |

The reference `MockProtectedPaymentProvider` conforms to the interface but declares
`NOT_A_LICENSED_PROVIDER` and is **never** active-eligible — it exists only to test the harness.
