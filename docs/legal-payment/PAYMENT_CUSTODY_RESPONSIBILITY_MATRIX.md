# Payment Custody Responsibility Matrix

Who is responsible for what under the current Protected Payment operating model. LiftHaul
orchestrates and evidences; the licensed provider holds and moves funds.

| Function | LiftHaul | Provider | Client | Carrier |
|---|:---:|:---:|:---:|:---:|
| Funding request (create protected-payment requirement) | ✓ | | | |
| **Custody of funds** | | ✓ | | |
| Actual fund movement (release/refund/payout) | | ✓ | | |
| Service evidence (GPS/geofence/POD/acceptance) | ✓ | | ✓ | ✓ |
| Release eligibility decision (gate + maker/checker) | ✓ | | | |
| Payout-account verification (maker/checker + MFA) | ✓ | provider-verified where applicable | | ✓ (submits) |
| Dispute handling / adjudication | ✓ | provider-dependent | ✓ | ✓ |
| Refund instruction | ✓ | | | |
| Refund execution | | ✓ | | |
| Reconciliation (funded = released + refunded + remaining + fees) | ✓ | ✓ | | |
| Immutable transaction ledger / audit | ✓ | | | |
| Regulatory/safeguarding compliance of held funds | | ✓ | | |
| KYB / carrier legality / vehicle & driver legality | ✓ | | | ✓ (submits) |
| Transaction risk limits | ✓ | | | |

Key: LiftHaul never appears in the "custody" or "fund movement" rows — those belong to the licensed
provider. This is the boundary counsel must confirm before any live activation.
