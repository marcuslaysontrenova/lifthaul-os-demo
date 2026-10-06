# LiftHaul Provider Earnings and Payout Policy

Evidence review date: 6 October 2026 (Asia/Manila)

## What the Philippine competitors publicly describe

### Transportify

Transportify publicly describes two materially different collection paths:

1. Cash bookings are paid directly by the customer at pickup or a designated drop-off point.
2. Business-account booking earnings become wallet credits and are disbursed to the driver's GCash
   account on a weekly schedule.

Its Philippine carrier terms also allow a broad set of payment methods, state that payment
facilitation is part of the carrier service, and place financial-institution disbursement costs on
the carrier. Transportify also publishes a direct customer-to-driver GCash option. These routes
reduce collection delay but create different proof, reconciliation, tax, dispute and support risks.

Transportify's requirements page, updated 15 May 2026, expressly says **No Initial Cash Deposit**
and that registration is free. It says payouts are primarily sent to GCash, with bank information
accepted where truck-driver payouts may exceed wallet limits. Its FAQ describes spot-cash payment
for some booking types and account payment every Tuesday for others.

Official sources:

- https://www.transportify.com.ph/vendor-terms-of-use/
- https://www.transportify.com.ph/transportify-rates/driver/truck-load-hauling-contract-delivery-jobs/
- https://www.transportify.com.ph/transportify-rates/driver/kumita-gamit-ang-toyota-lite-ace-suzuki-super-carry-at-isuzu-flexicube/
- https://www.transportify.com.ph/blog/pay-your-driver-using-gcash/
- https://www.transportify.com.ph/blog/settlements/
- https://www.transportify.com.ph/transportify-rates/driver/requirements-private-car-delivery-jobs/
- https://www.transportify.com.ph/faq/

### Lalamove Philippines

Lalamove publicly describes a driver wallet that records top-ups, cash-outs and balance changes.
Drivers enroll a KYC-verified GCash, Maya or UnionBank destination in the driver's own name. Its
published cash-out guidance says requests before 12 noon on a business day may be processed the
same day, requests after cutoff on the next business day, and weekend requests on Monday. Cash-out
may be rejected where there are pending orders, complaints or account-deletion requests. Its terms
allow fraud/illegal-activity holds and deductions authorized by its terms or software. Fleet owners
manage vehicles, drivers and driver/vehicle pairing through a separate fleet portal.

Lalamove's published Security Deposit Policy (last modified 25 June 2025) requires a minimum whose
amount varies by vehicle type and city, permits account restriction when the balance is deficient,
lists defined uses, and says a full refund may take 30 working days after termination. Its onboarding
page places deposit funding after applicant verification and before taking orders. LiftHaul must not
copy Lalamove's amounts, unilateral language or forfeiture mechanics.

There is also a public-policy inconsistency that must not be copied: the security-deposit page says
full-refund processing may take 30 working days, while the later offboarding page says the deposit is
credited to the driver wallet within three working days after the application is completed and must
be withdrawn within seven working days. LiftHaul must publish one unambiguous SLA and contractually
map it to the selected provider's actual refund process.

Official sources:

- https://www.lalamove.com/en-ph/blog/cash-out-process
- https://www.lalamove.com/en-ph/blog/driver-wallet-tagalog
- https://www.lalamove.com/en-ph/terms-and-conditions
- https://www.lalamove.com/en-ph/fleet-management
- https://www.lalamove.com/en-ph/driver-offboarding-policy
- https://www.lalamove.com/en-ph/driver-security-deposit
- https://www.lalamove.com/en-ph/driver-onboarding

## LiftHaul decision

LiftHaul will not copy competitor fee percentages, deposits, payout promises or contract language.
It will apply the useful operating controls to its own Protected Payment model:

1. **Separate states:** Earned, Available, Requested, Submitted, Processing and Paid are distinct.
2. **Paid requires evidence:** A provider reference is required before a payout becomes Paid.
3. **Independent drivers:** Earnings route only to a verified personal beneficiary account.
4. **Owner-operators:** Earnings route to the verified owner-operator account.
5. **Fleet owners:** Carrier earnings route to the verified fleet/business beneficiary. LiftHaul
   must not invent or silently deduct a driver's wage/share.
6. **Contractual splits:** Driver/fleet splits activate only with written allocation terms and a
   regulated provider that supports split disbursement.
7. **Payout choices:** On-demand business-day processing, a Transportify-like optional weekly
   Tuesday batch, or LiftHaul's existing optional weekly Friday batch. The selected PSP's cutoffs
   and holidays remain authoritative.
8. **Channels:** GCash, Maya and bank accounts are catalogue options; activation depends on the
   selected regulated provider's written capability and completed certification.
9. **Cutoff:** The default on-demand estimate is same-business-day processing before 12 noon and
   next-business-day processing after cutoff. It is an estimate, not a guarantee of receipt.
10. **Scoped holds:** Booking disputes block release of the affected funds. Fraud, beneficiary or
    critical account risks may block the account. LiftHaul avoids an indiscriminate hold on every
    unrelated earning merely because one booking is pending.
11. **No overdraft/double payout:** Requests reserve wallet balance and use idempotency keys.
12. **Destination security:** Beneficiary verification, MFA-backed maker/checker approval and a
    cooling period apply to destination changes and high-value withdrawals.
13. **Reconciliation:** A wallet balance or API success is not bank settlement. Provider reports
    and references must reconcile with LiftHaul's immutable ledger.
14. **Production fail-closed:** MOCK payouts are forbidden in production; live rails remain blocked
    until the legal operating model and licensed-provider gates are active.

## Refundable provider security deposit decision

LiftHaul adopts a **conditional, risk-based refundable provider security deposit**, not a universal
registration charge. Transportify demonstrates that a deposit is not operationally inevitable;
Lalamove demonstrates one possible risk-control model. LiftHaul's policy therefore supports both:

1. Deposit-required risk bands for services/vehicles with justified loss exposure.
2. Zero-deposit eligibility for a future approved low-risk band (for example, strongly verified,
   adequately insured fleet partners), if risk and legal review supports it.
3. Funding only after provider verification and acceptance of the applicable terms; never as an
   opaque application fee.
4. Funds safeguarded by a contracted regulated payment provider. LiftHaul keeps a sub-ledger but
   does not use deposits as working capital.
5. The balance is a refundable liability, never LiftHaul revenue and never mixed with provider
   earnings or customer Protected Payments.
6. Deductions only for a permitted use after an adjudicated claim/dispute, due notice, evidence,
   maker/checker review and provider confirmation. A complaint alone cannot trigger forfeiture.
7. A deduction below the active minimum pauses new work and creates a transparent top-up requirement;
   it does not silently seize unrelated earnings.
8. Offboarding refunds return the unapplied balance through the verified payment provider. An open
   case may pause refund only with a visible case reference, status and deadline.
9. The deposit is not cargo insurance, vehicle insurance, a guarantee of customer reimbursement or
   a cap on the provider's contractual/legal liability.
10. Required amounts must be set from claims frequency/severity, insurance deductibles, vehicle and
    service risk, fraud loss and provider limits—not copied from a competitor.

The implementation is intentionally **DRAFT/inactive** until LiftHaul records a Philippine legal
memo, BSP-regulated-provider contract/capability evidence, tax/accounting memo, accepted terms,
refund SLA and a risk-approved amount. Until then, no deposit is collected and onboarding is not
blocked.

Philippine regulatory note: BSP guidance requires relevant businesses to self-assess OPS status and
notes that linked e-money or money-service activities may require separate registration/licensing.
Giving users a stored peso balance can trigger additional analysis. This is why LiftHaul should use
provider-held funds and avoid creating its own cash wallet without counsel and BSP confirmation.

## Devil's-advocate findings

- Direct cash or customer-to-driver GCash is fast but weakens LiftHaul's ability to prove payment,
  retain its approved fee, perform refunds and reconcile disputes. It should be a separately
  governed payment class, not the default Protected Payment path.
- A universal fleet-owner/driver split would be unsafe. Fleet operators may use employees,
  contractors, rentals or revenue-sharing agreements. LiftHaul must store the agreed allocation
  model and never infer employment terms.
- A displayed wallet balance is a ledger claim, not safeguarded funds. Customer-money custody and
  stored-value functionality require counsel and regulated-provider confirmation.
- Same-day payout creates liquidity and fraud risk. Use transaction limits, destination cooling,
  risk holds and prefunding/provider confirmation.
- Competitor marketing pages can change and are not a substitute for their complete commercial
  agreements. LiftHaul's provider contract and Philippine legal advice remain authoritative.

## Implemented system surfaces

- `provider_payouts.py`: payout policies, profiles, earnings wallet, requests, schedules,
  idempotency, balance reservation, provider submission and FIFO allocation.
- `marketplace_payments.py`: an approved release creates `AVAILABLE` earnings; it no longer marks a
  beneficiary payout `PAID` before an actual payout request succeeds.
- Carrier portal finance: profile, wallet and request history are included in the carrier's own
  tenant-scoped projection.
- Carrier APIs: configure payout preference and request payout.
- Admin APIs: review/list and submit requests through the provider adapter.
- `provider_security_deposit.py`: draft/active policy governance, provider-verified funding,
  assignment/activation gate, separate liability statement, adjudicated deduction workflow,
  maker/checker control and provider-confirmed offboarding refunds.

## Still required before live payout

- Select and contract a BSP-regulated provider with written GCash, Maya, bank and marketplace payout
  capability.
- Map provider-specific asynchronous webhook states and verify signatures/replay protection.
- Certify beneficiary-name matching and destination-change procedures.
- Confirm fees, cutoff calendars, holidays, limits, failed-payout reversals and settlement reports.
- Complete counsel review of LiftHaul's marketplace role, custody model, tax/invoicing and provider
  terms.
- Run sandbox, controlled live-value payout, reversal/refund, reconciliation and PostgreSQL recovery
  tests.
- Build the provider/fleet-owner wallet UI and operational payout queue UI.
