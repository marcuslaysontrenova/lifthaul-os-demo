# Payment Provider Due-Diligence Checklist

Complete for each candidate protected-payment/safeguarding provider **before** it is configured or
certified. BSP regulates payment systems under the NPSA and maintains a listing/verifier of
regulated entities — provider selection must include formal regulatory-status verification, not
merely the easiest API.

| # | Item | Reviewed | Evidence / Notes |
|---|---|:---:|---|
| 1 | BSP / regulatory status (NPSA OPS registration; verify against the current dated BSP listing) | ☐ | |
| 2 | Merchant Acquisition License/authority for the proposed Philippine activity, where applicable | ☐ | |
| 3 | Legal entity (registration, ownership, standing) | ☐ | |
| 4 | Written approval of LiftHaul's marketplace role and proposed fund flow | ☐ | |
| 5 | Safeguarding arrangement for held funds (trust/segregation) | ☐ | |
| 6 | Settlement structure + timelines | ☐ | |
| 7 | API capability (matches `ProtectedPaymentProvider` interface) | ☐ | |
| 8 | QR Ph P2M, GCash, Maya, cards/3-D Secure and bank-transfer coverage | ☐ | |
| 9 | Sub-account/KYB, split settlement and conditional/delayed payout support | ☐ | |
| 10 | Partial release support | ☐ | |
| 11 | Refund support (full + partial) | ☐ | |
| 12 | Dispute / chargeback / reversal handling | ☐ | |
| 13 | Fees (per-transaction, refund, chargeback, FX, payout, settlement) | ☐ | |
| 14 | Transaction / velocity / reserve limits | ☐ | |
| 15 | Webhook security (signature, replay, idempotency) | ☐ | |
| 16 | Availability / SLA / uptime | ☐ | |
| 17 | Reconciliation feed + settlement statement access | ☐ | |
| 18 | Audit + transaction traceability | ☐ | |
| 19 | Data privacy, subprocessors, breach notice and DPA compliance | ☐ | |
| 20 | Incident handling + notification | ☐ | |
| 21 | Business continuity / DR | ☐ | |
| 22 | Contract termination terms | ☐ | |
| 23 | Fund-return scenario (provider or LiftHaul exit/insolvency) | ☐ | |
| 24 | Passes the LiftHaul provider certification harness (`certify_provider`) | ☐ | mandatory-tests PASS required before ACTIVE |

A provider may only be marked ACTIVE when: regulatory status verified **and** the certification
harness passes **and** counsel approves the operating model.
