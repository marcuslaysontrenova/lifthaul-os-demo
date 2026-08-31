# Protected Payment — Operating Model

This describes the LiftHaul Protected Payment operating model as currently built. It is written so
Philippine counsel can evaluate it. **LiftHaul does not represent itself as directly holding escrow
funds under the current model.** The regulated funds layer is provided by a licensed financial
partner; LiftHaul orchestrates the transaction and holds only references + evidence.

## Fund flow

```
Customer
   │  (funds a protected-payment requirement)
   ▼
Licensed Financial Provider           ← holds/safeguards the funds (custody)
   │
   ▼
Protected Funds                       ← recorded by LiftHaul as references + immutable ledger
   │
   ▼
LiftHaul Transaction Orchestration    ← state machine + controls (no custody)
   │
   ▼
Verified Service Milestones           ← GPS/geofence/POD/customer acceptance evidence
   │
   ▼
Release Instruction                   ← maker/checker + release gate; LiftHaul instructs, does not move funds
   │
   ▼
Provider                              ← executes the actual fund movement
   │
   ▼
Carrier                               ← receives payout to a verified payout account
```

## What LiftHaul does / does not do

- **Does:** create the protected-payment requirement; record provider references + an immutable
  ledger; evaluate milestone/delivery evidence; run the release gate (fraud, dispute, payout
  verification, cooling, transaction limits, reconciliation); issue release/refund **instructions**;
  reconcile; audit.
- **Does NOT (under this model):** take custody of customer funds; move funds itself; act as a bank
  or an e-money issuer; guarantee funds; represent the service as legal "escrow".

## Terminology

Customer-facing wording is **Protected Payment / Protected Funds / Payment Protection**. The term
"Escrow" is **not** used in any customer-facing surface until counsel + a regulated provider
approve that terminology and the corresponding operating/custody structure. The architecture may be
described internally as "escrow-ready" (a technical readiness statement, not a legal status).

## Activation control

Live fund movement is technically disabled: `LIVE_PROTECTED_FUNDS_ENABLED=false`. It can only turn on
when **all three** are documented as true: an approved PH legal operating model, an active licensed
provider, and the flag. Missing any one → LIVE FUND MOVEMENT DENIED (enforced centrally in code).

The customer payment gateway applies a separate production lock for provider certification,
per-channel certification, a controlled pilot, automated reconciliation, regulatory-role review,
safeguarded-funds approval, an independent security test, and DR/restore approval. See
`PAYMENT_SECURITY_VERIFICATION_GATE.md`. These are cumulative controls: passing the gateway gate
does not by itself authorize protected-funds custody or conditional release.
