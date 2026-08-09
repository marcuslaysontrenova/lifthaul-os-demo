# 04 — Custody Responsibility Matrix

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

Authoritative version: `docs/legal-payment/PAYMENT_CUSTODY_RESPONSIBILITY_MATRIX.md`. Summary below.

| Responsibility | LiftHaul OS | Licensed provider | Customer | Carrier |
|---|:---:|:---:|:---:|:---:|
| Hold / safeguard funds | — | ✔ | — | — |
| Move / settle funds | — | ✔ | — | — |
| Fund the transaction | — | receives | ✔ | — |
| Receive settlement | — | pays out | — | ✔ |
| Decide release (business rules) | ✔ | executes | — | — |
| Maker/checker on release | ✔ | — | — | — |
| KYC of payer | shares data | ✔ (regulated) | provides | — |
| KYB of carrier | ✔ | — | — | provides |
| Store payment references / evidence | ✔ | ✔ | — | — |
| Immutable transaction ledger | ✔ | ✔ (own books) | — | — |
| Reconciliation (LiftHaul view) | ✔ | ✔ (statements) | — | — |
| Dispute intake / adjudication | ✔ (platform) | ✔ (chargeback) | initiates | responds |
| Refund execution | instructs | ✔ | receives | — |
| Regulatory reporting (payment) | per determination | ✔ | — | — |

**Rule:** any cell that would place *fund custody or movement* on LiftHaul is a design violation and
must be redesigned to the provider. Enforced in code by the live-funds gate + release gates.
