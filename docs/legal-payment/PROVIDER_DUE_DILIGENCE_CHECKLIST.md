# Payment Provider Due-Diligence Checklist

Complete for each candidate protected-payment/safeguarding provider **before** it is configured or
certified. BSP regulates payment systems under the NPSA and maintains a listing/verifier of
regulated entities — provider selection must include formal regulatory-status verification, not
merely the easiest API.

| # | Item | Reviewed | Evidence / Notes |
|---|---|:---:|---|
| 1 | BSP / regulatory status (NPSA registration/licence; verify against BSP listing) | ☐ | |
| 2 | Legal entity (registration, ownership, standing) | ☐ | |
| 3 | Safeguarding arrangement for held funds (trust/segregation) | ☐ | |
| 4 | Settlement structure + timelines | ☐ | |
| 5 | API capability (matches `ProtectedPaymentProvider` interface) | ☐ | |
| 6 | Partial release support | ☐ | |
| 7 | Refund support (full + partial) | ☐ | |
| 8 | Dispute / chargeback handling | ☐ | |
| 9 | Fees (per-transaction, FX, payout) | ☐ | |
| 10 | Transaction / velocity limits | ☐ | |
| 11 | Webhook security (signature, replay, idempotency) | ☐ | |
| 12 | Availability / SLA / uptime | ☐ | |
| 13 | Reconciliation feed + statement access | ☐ | |
| 14 | Audit + transaction traceability | ☐ | |
| 15 | Data privacy (DPA compliance) | ☐ | |
| 16 | Incident handling + notification | ☐ | |
| 17 | Business continuity / DR | ☐ | |
| 18 | Contract termination terms | ☐ | |
| 19 | Fund-return scenario (provider or LiftHaul exit) | ☐ | |
| 20 | Passes the LiftHaul provider certification harness (`certify_provider`) | ☐ | mandatory-tests PASS required before ACTIVE |

A provider may only be marked ACTIVE when: regulatory status verified **and** the certification
harness passes **and** counsel approves the operating model.
