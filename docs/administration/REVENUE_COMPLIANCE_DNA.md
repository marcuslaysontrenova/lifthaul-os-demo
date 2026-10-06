# LiftHaul Revenue & Compliance DNA

## Purpose

This control plane makes every proposed LiftHaul income stream answer four questions before activation:

1. What exactly is LiftHaul charging for?
2. Which existing engine calculates, invoices, settles, or reports it?
3. What Philippine legal, tax, licensing, partner, safety, privacy, and customer-disclosure controls apply?
4. Which current evidence proves those controls were independently reviewed?

The registry does **not** grant legal permission and is not legal advice. It records the evidence and accountable decisions supplied by Philippine counsel, tax advisers, regulators, licensed partners, security reviewers, and operations owners.

## Seeded earning schemes

| Scheme | Phase | Existing LiftHaul engine | Default posture |
|---|---:|---|---|
| Provider success fee | 1 | SaaS fee policies / platform-fee settlement | Assessment |
| Customer platform fee | 1 | SaaS / billing | Assessment |
| Fleet subscription | 1 | SaaS | Assessment |
| Enterprise subscription | 1 | SaaS / billing | Assessment |
| Heavy-equipment coordination and rental fee | 1 | Rental / industrial projects | Assessment |
| Accreditation administration fee | 1 | Accreditation | Assessment |
| Payment administration fee | 2 | Protected payment / payment gateway | Assessment; regulated-partner gate |
| Sponsored placement | 2 | Future module | Assessment; must not alter eligibility |
| API and white-label licence | 2 | API platform / SaaS | Assessment |
| Insurance referral income | 3 | Cargo insurance / goods protection | Assessment; licensing gate |
| Financing referral income | 3 | Future module | Assessment; licensed-partner gate |
| Managed project coordination fee | 2 | Industrial projects / billing | Assessment |
| Analytics and reporting add-on | 2 | Reporting / SaaS | Assessment |

No numeric fee is made live by the seed. Actual amounts remain in versioned commercial engines and require their existing approvals.

## Activation contract

`ASSESSMENT → READY_FOR_APPROVAL → APPROVED → ACTIVE`

- Every required checklist control must have a governed evidence reference.
- The evidence submitter cannot verify the same evidence.
- The scheme submitter cannot approve it.
- The approver cannot activate it.
- Expired evidence immediately makes readiness false.
- Active schemes cannot be silently edited; suspend and reassess first.
- Suspension requires a reason and is audit logged.
- Payment for document processing never implies provider accreditation or government approval.

## Runtime enforcement

`REVENUE_DNA_ENFORCEMENT=observe` records the outcome while existing deployments are migrated. The response explicitly remains blocked; observation is not an approval.

`REVENUE_DNA_ENFORCEMENT=enforce` rejects an earning transaction unless the scheme is `ACTIVE` and all evidence remains current. Production should use `enforce` after the initial evidence migration.

The first runtime integrations cover:

- provider success-fee transaction recording;
- enterprise subscription billing-evidence generation; and
- heavy-equipment rental revenue finalisation;
- production customer payment-session creation and refund requests;
- LiftHaul 10% administration-fee settlement; and
- live provider-protected-payment and payout submission.

### Payment & Settlement DNA

The `payment_admin_fee` scheme is the single live-money authorization record. It contains the five
base corporate/tax/consumer/privacy/accounting controls plus 21 payment-specific controls covering:

- the merchant/marketplace operating role, current BSP OPS evidence and applicable merchant-
  acquisition authority;
- the contracted regulated provider, approved fund flow, marketplace/sub-account/KYB capability,
  channel scope and per-channel certification;
- payment/refund/dispute/payout/reversal webhooks, replay protection, payout maker/checker,
  reconciliation, PostgreSQL recovery and independent penetration testing;
- KYC/KYB/AML/fraud procedures, payment terms/privacy agreements and BIR invoicing/tax treatment;
  and
- a controlled live pilot, real refund and verified-provider payout evidence, seven-day production
  reconciliation soak and cross-functional go-live sign-off.

For a real-money operation, `revenue_dna.guard_payment_operation()` always fails closed. The
`observe` migration setting cannot bypass it. Environment flags, provider credentials, channel
certification rows and direct calls to internal functions are never sufficient on their own.
Inbound dispute/reversal evidence, customer refunds of existing verified payments and read-only
reconciliation remain processable while a scheme is suspended so that LiftHaul can contain risk,
return money owed and account for existing transactions. This unwind exception cannot create a new
charge, LiftHaul revenue transfer or provider payout.

Additional earning engines should call `revenue_dna.guard()` at the point where a fee becomes chargeable—not merely when a page is displayed. Any new real-money edge must instead call the non-bypassable `guard_payment_operation()` immediately before the provider request.

## API

- `GET /admin/revenue-dna/summary`
- `GET /admin/revenue-dna/schemes`
- `GET /admin/revenue-dna/schemes/:code`
- `POST /admin/revenue-dna/schemes/:code/update`
- `POST /admin/revenue-dna/schemes/:code/controls/:control/evidence`
- `POST /admin/revenue-dna/evidence/:id/verify`
- `POST /admin/revenue-dna/schemes/:code/submit`
- `POST /admin/revenue-dna/schemes/:code/approve`
- `POST /admin/revenue-dna/schemes/:code/activate`
- `POST /admin/revenue-dna/schemes/:code/suspend`

Evidence references point to the controlled document repository. Secrets and full identity documents must not be pasted into this registry.

## Philippine primary-source register

- [DTI — Internet Transactions Act IRR](https://ecommerce.dti.gov.ph/implementing-rules-and-regulations/)
- [DTI — E-Commerce Philippine Trustmark guidance](https://trustmark.dti.gov.ph/guideline)
- [BIR — RMC 8-2024 clarifying RR 16-2023 marketplace withholding](https://bir-cdn.bir.gov.ph/BIR/pdf/RMC%20No.%208-2024%20%281%29.pdf)
- [BSP — Payment systems, OPS and merchant-acquisition framework](https://www.bsp.gov.ph/SitePages/PaymentsAndSettlements/PaymentsAndSettlements.aspx)
- [LTFRB](https://ltfrb.gov.ph/)
- [National Privacy Commission](https://privacy.gov.ph/)
- [DOLE OSHC — accreditation, including construction heavy-equipment testing organisations](https://oshc.dole.gov.ph/accreditation/)
- [Insurance Commission — 2025 legal opinion on compensated insurance referrals](https://www.insurance.gov.ph/wp-content/uploads/2025/10/IC-Legal-Opinion-No.-2025-03_Request-for-Opinion-on-Referral-Fee_LICD.pdf)

Requirements can change and their application depends on LiftHaul's final contracts and fund flow. Before production activation, the evidence should include a dated Philippine legal/tax assessment for the specific scheme—not only a link to a regulation.
