# Payment, protected-payment, pricing, and tax report

## Required canonical equation

`Transport-service charge + 10% LiftHaul administration fee + applicable tax = total protected-payment amount`

Tolls, parking, ferry/RoRo, ports, permits, weighbridge, clearance, escort, out-of-quote labor/waiting, meals/accommodation, and incidental route expenses must remain outside the administration fee, tax calculation inside the booking, protected amount, provider payout, and LiftHaul revenue.

## Reconciliation results

| Test | Expected | Actual | Result |
|---|---|---|---|
| Canonical backend sample | ₱260 transport + ₱26 fee + ₱34 tax = ₱320 | Exactly ₱320 | Pass in local synthetic scope |
| Fee basis | 10% × pre-tax transport only | ₱26 = 10% × ₱260 | Pass |
| Exclusions | Informational, not in protected total | Returned separately by backend | Pass |
| Client acknowledgement | Required before disclosed booking proceeds | Browser checkbox exists; backend enforces only when caller sets `_client_disclosure_required` | Partial; caller-controlled enforcement is unsafe |
| Client demo `DEMO-PP-2001` arithmetic | Every displayed line reconciles | ₱38,500 + ₱3,850 + ₱847 is shown as ₱39,347 | **Critical fail** |
| Approved components in demo | Transport + 10% fee + tax only | Demo adds a processing-fee line | **Critical fail** |
| Protected amount | Equal the authorized protected-payment total/allocation model | Demo protects ₱38,500 while showing additional customer charges | **Critical fail** |
| Booking/payment state | One coherent state and allowed action | Payment Required + Funds Protected + Pay Now coexist | **High fail** |

The code cause is direct: `client-workspace.js` creates both a 10% platform fee and 2.2% processing fee, then computes total as contract + processing fee + tax, omitting the platform fee.

## Provider verification and release

Positive synthetic evidence:

- invalid/forged webhook does not change payment;
- duplicate webhook is idempotent;
- provider webhook plus provider API confirmation is required for paid status;
- wrong amount is placed under review;
- API-only delayed success is not enough until webhook evidence arrives;
- open dispute, missing POD, missing protection, fraud flags, unverified payout destination, and high-risk cooling period block release;
- refund requests wait for verified provider callback;
- live external funding defaults off.

These are unit/integration-test facts using fake providers. No live payment, bank settlement, refund, payout, chargeback, provider outage, reconciliation file, or finance dual-control exercise was performed.

## Tax

The backend separates transport tax and administration-fee tax and supports policy snapshots, inclusive/exclusive modes, VAT/non-VAT configuration, withholding, and effective-dated logic. That is good design evidence. It is **not tax approval**.

Required before production:

1. Philippine accountant/tax counsel signs the taxable-party, invoice issuer, VAT/non-VAT, withholding, rounding, credit/refund, and effective-date matrix.
2. Booking, payment-provider amount, protected ledger, statutory invoice, provider payout, platform revenue, withholding certificate, refund, and accounting export reconcile to the centavo from one immutable pricing snapshot.
3. The client demo and every receipt/export use the same canonical calculator; no independent JavaScript arithmetic.
4. Discounts and amendments allocate tax and refunds under signed policy, never by ad hoc percentage.

The BIR's invoicing rules under RR 7-2024 and the clarifications under RMC 77-2024 require production invoices and VAT treatment to be reviewed against the actual seller/platform arrangement: [RR 7-2024](https://bir-cdn.bir.gov.ph/BIR/pdf/RR%207-2024%20%28final%29.pdf), [RMC 77-2024 digest](https://bir-cdn.bir.gov.ph/BIR/pdf/RMC%20No.%2077-2024%20Digest.pdf).

## Payment report decision

**NO-GO.** Keep live funds disabled. A licensed/authorized provider and approved legal operating model are necessary but not sufficient; first fix the canonical ledger, status/action invariants, retry/atomicity, tax sign-off, invoices, provider reconciliation, and tested finance controls.

