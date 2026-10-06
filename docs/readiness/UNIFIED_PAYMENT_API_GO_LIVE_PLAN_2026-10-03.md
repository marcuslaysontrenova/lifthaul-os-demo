# LiftHaul Unified Payment API and Go-Live Plan

**Decision date:** 2026-10-03
**Current release decision:** **LIVE PAYMENTS DISABLED**
**Controlling product term:** **LiftHaul Protected Payment** — not “escrow” unless Philippine
legal counsel and the regulated funds provider approve both the term and the legal structure.

This plan converts the founder directive into an implementation and certification backlog. It is
not a legal opinion and does not certify any provider. Provider capabilities, regulatory status,
commercial approval and permitted fund flows must be confirmed in writing before activation.

## 1. Integration decision

LiftHaul will maintain **one internal Payment API** and connect it to one initial BSP-supervised
payment provider through a provider adapter. LiftHaul will not build or maintain a separate
first-release integration for every bank or e-wallet.

The initial provider is expected to aggregate the launch channels:

- QR Ph;
- GCash;
- Maya;
- credit and debit cards with 3-D Secure;
- at least one online-bank-transfer or virtual-account method; and
- provider payouts to verified service providers, where commercially and legally approved.

Over-the-counter channels remain in the architecture but should be enabled only after their own
sandbox certification. A second provider or a direct bank/e-wallet connection is a later resilience
or commercial optimization decision, not a launch prerequisite.

### When a direct bank or wallet API may be justified later

A separate direct integration may be considered only when there is written evidence that it solves
one of these needs better than the primary provider:

1. a required rail, virtual account, payout destination or conditional-settlement capability is
   unavailable through the primary provider;
2. transaction volume supports materially better commercial terms;
3. concentration-risk policy requires a second live rail;
4. treasury/reconciliation requires a direct banking service; or
5. an enterprise client contract requires a named bank.

Every additional provider increases certification, webhook, reconciliation, refund, chargeback,
incident-response and operational-support scope. It must use the same normalized internal contract.

## 2. Target architecture

```text
Booker / Staff UI
       |
       v
LiftHaul Payment API
  - booking and amount authority
  - idempotency
  - normalized statuses
  - immutable references and ledger
  - refund/dispute/release controls
  - reconciliation
       |
       v
Provider Adapter (initially one)
       |
       +-- QR Ph
       +-- GCash / Maya
       +-- Cards / 3-D Secure
       +-- Bank transfer / virtual account
       +-- Approved OTC channels
       +-- Payout rails
       |
       v
BSP-supervised provider and participating banks/e-wallets
```

QR Ph is an interoperable Philippine standard: a participating bank or non-bank e-money issuer can
scan a compliant QR Ph code. LiftHaul therefore needs a provider integration that supports QR Ph,
not a separate QR integration for each participating institution.

## 3. Normalized internal API contract

The provider adapter must normalize, at minimum:

| Operation | Required behavior |
|---|---|
| Create payment request | Bind booking, payer, amount, currency and immutable idempotency key. |
| Create checkout/payment link | Return only a provider-hosted or approved payment URL. |
| Generate dynamic QR Ph | Return QR payload/image reference, expiry and provider reference. |
| Retrieve payment | Server-to-server status inquiry; redirects and screenshots are not evidence. |
| Verify webhook | Authenticate source/signature, reject replay and deduplicate event IDs. |
| Refund / partial refund | Enforce balance, approval, idempotency and status constraints. |
| Record chargeback | Freeze release eligibility and open finance/risk handling. |
| Create payout | Pay only a verified payee account after release gates and maker/checker approval. |
| Retrieve payout/settlement | Normalize pending, succeeded, failed and reversed outcomes. |
| Reconcile | Compare provider report/API, LiftHaul ledger and actual bank settlement. |

### Public payment statuses

Provider-specific statuses must map to the following customer/operations vocabulary without losing
the original provider value:

- Awaiting Payment
- Processing
- Paid
- Payment Secured
- Partially Paid
- Failed
- Expired
- Cancelled
- Under Dispute
- Refund Pending
- Refunded
- Payout Pending
- Released to Provider
- Payout Failed

The protected-payment state machine may keep more detailed internal states. A successful checkout
redirect is never `Paid` or `Payment Secured`; those states require an authenticated webhook plus a
server-to-server provider check and ledger match.

## 4. Protected Payment operating models to submit to counsel

These are candidate operating models, not blanket legal approvals:

1. **Provider-managed conditional settlement / marketplace payout — preferred.** The regulated
   provider receives and safeguards funds, then executes release or refund under the contracted
   workflow. LiftHaul stores references, evidence and ledger entries rather than taking custody.
2. **Card authorization then capture.** For card transactions only, reserve the amount and capture
   it after a defined service milestone if the provider enables the feature and its hold window fits
   the booking. This is not escrow and does not cover wallet/bank-transfer channels.
3. **Direct-to-service-provider collection plus separate LiftHaul fee.** The customer pays the
   transport provider directly and pays LiftHaul's platform/admin fee separately. This lowers
   custody risk but weakens the unified checkout, refund and service-protection experience.
4. **Corporate postpaid account.** Approved business customers receive invoiced credit terms after
   underwriting, limits and collection controls. This is accounts receivable, not safeguarded
   customer funds.
5. **Milestone billing.** Separate provider-processed charges are collected for booking, pickup and
   delivery milestones. This reduces the amount exposed at each stage but adds payment and refund
   complexity.
6. **Controlled manual bank transfer or cash/OTC.** Use only with a unique reference, official
   provider/bank verification, manual review, immutable audit and explicit refund process. A
   screenshot is never proof of payment.

True escrow should be considered only through a bank, trust entity or regulated provider whose
agreement and counsel opinion explicitly authorize that structure and terminology.

## 5. Current repository assessment

### Already implemented internally

- Unified collection edge for GCash, Maya, bank transfer, QR Ph, cards and OTC channel definitions.
- Xendit-targeted payment-session adapter with credentials read from environment variables.
- Idempotent payment and refund records.
- Authenticated webhook processing with deduplication and server-to-server verification.
- Provider dispute webhook ingestion with idempotent records, amount/currency/reference mismatch
  review, customer-payment freeze and administration-fee recovery hold.
- Amount, currency, booking and provider-reference matching before authoritative payment state.
- Protected Payment state machine, immutable ledger, disputes, release gates and reconciliation.
- Daily idempotent reconciliation worker and issue ledger.
- Fail-closed per-channel certification and production activation gates.
- A database-backed Payment & Settlement DNA with four-person evidence verification, approval and
  activation; production flags and credentials cannot bypass it.
- Direct runtime gates on production collection, LiftHaul's 10% administration-fee transfer and
  non-mock protected-payment/provider payout submission. Governed refunds remain available as a
  risk-reducing unwind for existing verified payments even if revenue activation is suspended.
- Separation of duties for sensitive manual verification, refund, release and payout operations.
- Automated financial-control, PostgreSQL portability/recovery and security workflows.

### Must still be completed before live money

- Select, contract and commercially onboard one provider for the exact marketplace fund flow.
- Obtain written evidence of current BSP status and, where applicable, merchant-acquisition
  authority; a directory listing alone does not prove every required service is approved.
- Implement and certify the real **protected-funds / split-settlement / payout adapter**. The current
  protected-payment live adapter remains intentionally blocked and mock-only.
- Implement an explicit dynamic QR Ph response contract and expiry handling.
- Connect the now-normalized gateway dispute/chargeback records to the selected provider's real
  protected-funds adapter and provider-payout freeze workflow.
- Import and reconcile the provider's settlement report, not only individual transaction status.
- Support multiple governed payment attempts or installments per booking without allowing duplicate
  active collection or overpayment. The current gateway permits only one active payment record per
  booking.
- Normalize the public statuses above across collection, protected funds, refunds and payouts.
- Complete provider sandbox evidence, production-style webhook tests, independent penetration test,
  PostgreSQL restore drill, controlled live pilot, real refund/payout and reconciliation soak.

The truthful current label is: **internally verified; externally gated; live payment collection
disabled.**

## 6. Provider request-for-proposal gate

Send the same written RFP to at least three candidates found in the current BSP directory. Do not
select on channel count alone. Require a signed response for:

- legal entity, BSP registration and applicable Merchant Acquisition License/authority;
- GCash, Maya, QR Ph P2M, cards/3-D Secure, bank transfer/virtual accounts and OTC coverage;
- marketplace/sub-account onboarding and KYB for truck/fleet/equipment providers;
- split settlement, conditional/delayed payout and partial release;
- bank and e-wallet payout coverage, limits and name/account validation;
- refunds, partial refunds, reversals, chargebacks and disputes;
- webhook authentication, event replay policy, idempotency and status inquiry API;
- settlement reports, reconciliation APIs/files and bank settlement proof;
- sandbox scenarios, production certification, uptime and incident SLA;
- transaction, refund, chargeback, payout and settlement fees;
- reserve, rolling hold, settlement delay, termination and insolvency/fund-return terms;
- privacy, subprocessors, data location, retention, breach notice and DPA terms; and
- whether the proposed LiftHaul fund flow and the phrase “Protected Payment” are contractually
  acceptable.

Provider marketing pages are discovery evidence only. Commercial activation and marketplace
capabilities must be confirmed for LiftHaul's specific merchant account.

## 7. Production critical path

| Order | Owner | Task | Exit evidence | Status |
|---:|---|---|---|---|
| 1 | Founder + Product + Finance | Approve fund-flow decision matrix: no-show, cancellation, damage, non-confirmation, disputes, partial service, chargeback and provider failure. | Signed business rules and authority matrix | OPEN |
| 2 | Philippine counsel | Decide whether LiftHaul is merchant of record, e-marketplace/marketplace operator, intermediary and/or potentially an OPS/merchant acquirer under the implemented fund flow. | Written legal opinion and required registrations/contracts | OPEN |
| 3 | Finance + Tax counsel | Confirm 10% admin-fee basis, VAT/withholding, invoice responsibility and provider-payout accounting. | Tax memo and approved invoice examples | OPEN |
| 4 | Procurement + Compliance | RFP and due diligence for at least three provider candidates. | Completed comparison and regulatory evidence | OPEN |
| 5 | Founder | Select provider and approve commercial contract; do not enable live mode. | Executed agreement and named accountable contacts | OPEN |
| 6 | Engineering | Implement the certified provider adapter for collection, QR, status, refunds, chargebacks, payouts and settlement reports. | Adapter tests and reviewed API mapping | OPEN |
| 7 | Engineering + Security | Deploy isolated sandbox/pre-production on HTTPS + PostgreSQL with managed secrets, queues, monitoring, backups and alerting. | Hosted environment and operational evidence | OPEN |
| 8 | Engineering + Provider | Certify every enabled launch channel against all required success/failure/replay/mismatch/refund/reconciliation cases. | Provider test IDs and channel certification records | OPEN |
| 9 | Legal + Operations | Publish reviewed Terms, Protected Payment terms, cancellation/refund/dispute policies, privacy notice and provider rules. | Approved versioned policies | OPEN |
| 10 | Compliance + Operations | Complete booker/provider KYC/KYB, payout-account verification, fraud/AML escalation and support runbooks. | Approved procedures and trained owners | OPEN |
| 11 | Independent assessor | Perform application/API penetration test and clean retest. | Signed report; no open Critical/High findings | OPEN |
| 12 | Engineering + Operations | Execute PostgreSQL backup/restore, delayed/duplicate webhook, outage/retry and duplicate-payout drills. | RTO/RPO, reconciliation and alert evidence | OPEN |
| 13 | Founder + Provider + Finance | Authorize exact low-value live pilot with capped channels, routes, users and fleet owners. | Signed pilot plan and rollback criteria | OPEN |
| 14 | Pilot team | Execute payment, eligible refund and provider payout; reconcile provider, LiftHaul and bank records. | Matching IDs, amounts, timestamps and two-person review | OPEN |
| 15 | Finance operations | Run seven consecutive daily production reconciliations and one controlled mismatch alert. | Seven clean records and incident-response proof | OPEN |
| 16 | Go-live board | Review the immutable evidence pack and activate only certified channels. | Signed go/no-go record and channel allowlist | OPEN |

## 8. Minimum responsible launch scope

The fastest responsible pilot is intentionally narrow:

- selected routes and verified fleet owners only;
- QR Ph, GCash, Maya, cards and one bank-transfer option only when each is certified;
- low booking and transaction limits;
- provider-managed collection and either provider payout or tightly controlled manual payout;
- one named finance reviewer and one independent approver per sensitive movement;
- manual operational oversight and 24/7 ability to disable a channel;
- no public claim of “escrow”; and
- no general paid-booking launch until refunds, disputes, reconciliation and provider payouts pass
  end-to-end production testing.

## 9. Primary regulatory references

- BSP Payments and Settlements / NPSA / OPS and Merchant Acquisition framework:
  https://www.bsp.gov.ph/SitePages/PaymentsAndSettlements/PaymentsAndSettlements.aspx
- BSP list of registered Operators of Payment Systems (verify the current dated version at review):
  https://www.bsp.gov.ph/PaymentAndSettlement/COR.pdf
- BSP Circular No. 1198, regulatory framework for Merchant Payment Acceptance Activities:
  https://www.bsp.gov.ph/Regulations/Published%20Issuances/Images/Circular_1198.pdf
- BSP QR Ph overview and interoperability:
  https://www.bsp.gov.ph/SitePages/MediaAndResearch/Multimedia_QRPh.aspx
- DTI Internet Transactions Act and implementing rules:
  https://ecommerce.dti.gov.ph/implementing-rules-and-regulations/
- National Privacy Commission, Data Privacy Act and IRR:
  https://privacy.gov.ph/the-data-privacy-act-and-its-irr/
