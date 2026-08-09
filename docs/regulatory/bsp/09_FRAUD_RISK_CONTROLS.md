# 09 — Fraud / Risk Controls

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

Authoritative: `backend/marketplace_trust.py` (fraud flags, trust score) +
`marketplace_trust_closure.py` (release gate, risk limits).

## Controls

| Control | Behaviour |
|---|---|
| Fraud flags | HIGH/CRITICAL fraud flag blocks release; cleared only via independent review (`clear_fraud_flag`) |
| Trust score | Composed from KYB state, history, flags; feeds eligibility |
| Release gate | Fraud + dispute + driver/vehicle legality + payout security + transaction limits — all must pass |
| Progressive transaction limits | Per-carrier risk limit enforced at release |
| Payout security | Verified + active + not-cooling + not-fraud-blocked payout account; MFA + maker/checker |
| Velocity / dedup | Duplicate/rapid attempts flagged (platform + provider webhook idempotency) |
| Never-fabricate adapters | KYB/LTFRB verification returns MANUAL_VERIFICATION_REQUIRED when no legal live source |

## Evidence

- `FraudE2E` (`backend/test_protected_payment_e2e.py`): HIGH/CRITICAL denied → cleared → eligible.
- Release-gate composition tested across trust-closure suites.

## Money-laundering posture

AMLA/KYC obligations attach principally to the **regulated provider**; LiftHaul shares KYB/KYC data
and enforces business gates. Precise obligation split is a counsel determination (doc 01 §3.4).
