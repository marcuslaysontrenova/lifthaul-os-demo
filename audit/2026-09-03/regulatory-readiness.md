# Regulatory-readiness gaps

This is a product-control assessment, not legal or tax advice. Philippine counsel, a tax adviser, privacy leadership, transport compliance specialists, and the selected payment provider must approve the production model.

## Payments and protected settlement — not ready

The BSP's payment-system framework includes registration/licensing materials for operators of payment systems and merchant-acquisition activities. Circular 1198's FAQ describes governance, pricing, payment-cycle, dispute/recourse, and risk-management expectations for merchant payment acceptance: [BSP Payments and Settlements](https://www.bsp.gov.ph/SitePages/PaymentsAndSettlements/PaymentsAndSettlements.aspx), [Circular 1198 FAQ](https://www.bsp.gov.ph/Regulations/Issuances/2024/1198%20-%20FAQ.pdf).

Required gap closure:

- documented legal characterization of LiftHaul's exact role and money flow;
- approved provider contracts, settlement accounts, safeguarding/reconciliation model, complaints/refunds/disputes, and customer disclosures;
- no “escrow,” “BSP compliant,” licensed, or funds-held claim without exact documentary support;
- immutable payment, refund, payout, hold, dispute, and operator audit evidence;
- production dual control, segregation of duties, limits, monitoring, and incident response.

Current status: live funds correctly disabled, but public “Protected Payment” copy still risks being understood as an operational financial safeguard.

## Transport authority — not ready

Truck-for-Hire authority and vehicle/operator eligibility require auditable review against current official records and applicable permits. LTFRB publishes CPC requirements and a vehicle-confirmation facility: [Truck-for-Hire CPC requirements](https://www.ltfrb.gov.ph/wp-content/uploads/2017/10/A.-NEW-CERTIFICATE-OF-PUBLIC-CONVENIENCE-CPC.pdf), [LTFRB YVC](https://yvc.ltfrb.gov.ph/).

Required gap closure:

- official-source verification workflow with source, reviewer, timestamp, expiry, scope, and adverse-result reason;
- plate/vehicle identity uniqueness and ownership dispute process;
- driver/vehicle/route/permit compatibility enforced at assignment and dispatch;
- expiry and suspension handling that does not strand an active safe trip;
- marketing language limited to what was actually verified.

Current status: verification models/tests exist; production official-source operation does not.

## Tax and invoicing — not ready

BIR RR 7-2024 implements invoicing requirements under the Ease of Paying Taxes Act, and RMC 77-2024 clarifies them: [RR 7-2024](https://bir-cdn.bir.gov.ph/BIR/pdf/RR%207-2024%20%28final%29.pdf), [RMC 77-2024 digest](https://bir-cdn.bir.gov.ph/BIR/pdf/RMC%20No.%2077-2024%20Digest.pdf).

Required gap closure:

- identify seller/invoice issuer for transport and platform service;
- accountant-approved VAT/non-VAT, inclusive/exclusive, withholding, refund/credit, and rounding rules;
- separately stated and immutable taxable bases;
- registered invoice output and reconciliation to payment and accounting exports;
- never tax or fee excluded third-party expenses inside the booking.

Current status: the backend tax architecture is promising; the client demo is arithmetically wrong and no statutory invoice chain is proven.

## Privacy and breach response — not ready

The NPC requires security-incident management, documented incident/breach handling, and applicable notification. Its guidance discusses a 72-hour notification window for qualifying breaches and annual incident reporting: [Breach reporting](https://privacy.gov.ph/pips-and-pics/breach-reporting/), [Data Privacy Act IRR](https://privacy.gov.ph/implementing-rules-regulations-data-privacy-act-2012/).

Required gap closure:

- approved privacy notice, lawful bases/consents, data inventory, retention/deletion schedule, data-subject request process, processor agreements, and cross-border assessment;
- named and reachable DPO/privacy contact;
- privacy impact assessment for identity, licence, vehicle, financial, GPS, photo, signature, and dispute evidence;
- least-privilege access, audit, secure binary document storage, breach detection/response, tabletop exercise, and reporting process.

Current status: policy text explicitly leaves retention, deletion, breach procedures, and DPO contact launch-gated.

## Claims and release language

The site must not state or imply current approval, insurance, protection, official verification, real-time tracking, or minute-level matching beyond the evidence. All pre-launch pages should lead with the operating limitation, not rely on a footer disclaimer to qualify an operational headline.

## Regulatory release gate

**Not ready.** Required sign-offs are artifacts, not verbal approvals: signed legal memo, payment/provider contracts, transport verification SOP and evidence, tax matrix/sample invoices, privacy impact assessment and incident plan, approved customer/provider terms, and a production control test witnessed by the accountable owners.

