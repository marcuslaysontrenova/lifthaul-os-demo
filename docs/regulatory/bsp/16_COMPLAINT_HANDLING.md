# 16 — Complaint Handling

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

Aligned to BSP's **Financial Consumer Protection** expectations (R.A. 11765) — final scope per counsel.

## Channels

- In-platform dispute intake (see doc 10) for payment/service complaints.
- A monitored support contact (email/phone) — owner to publish.

## Process

1. **Log** — every complaint recorded with timestamp, category, correlation id.
2. **Acknowledge** — within a target SLA (to be set; BSP FCP suggests prompt acknowledgement).
3. **Investigate** — trace via audit ledger; involve provider for fund-side issues.
4. **Resolve** — outcome communicated; payment outcomes flow through the state machine (release/refund).
5. **Escalate** — unresolved → management → provider/regulator per counsel.
6. **Report** — periodic complaint metrics (surfaced via the platform's reporting layer).

## Consumer-facing clarity

- Plain-language customer projection (no "escrow"); status timeline visible.
- Fees and release conditions disclosed (disclosure specifics per counsel, doc 10).

## To be completed with the owner

- Published SLAs, support hours, and the regulator-escalation path (BSP FCP / provider).
