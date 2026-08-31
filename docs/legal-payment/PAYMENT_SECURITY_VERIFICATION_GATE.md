# LiftHaul Payment Security Verification Gate

Status: **engineering controls verified in automated tests; live payments remain fail-closed**.

This gate separates a secure implementation from external production authorization. It must never
be used to claim that LiftHaul is a bank, an e-money issuer, a licensed escrow provider, BSP
approved, or ready to receive live customer funds before every production evidence item passes.

## Implemented and test-verified controls

| Control | Enforced behavior |
|---|---|
| Customer credentials | Hosted provider checkout; LiftHaul does not collect raw card, bank, or wallet credentials. |
| Channel exposure | A method is hidden until credentials, secure URLs, provider approval, and every channel certification test pass. |
| Payment confirmation | A redirect, screenshot, HTTP 200, or API poll alone cannot mark a booking paid. An authenticated provider webhook **and** a server-to-server reference, amount, currency, provider-ID, and success-state match are required. |
| Uncertain success | Provider API success without a verified webhook is `UNDER_REVIEW`, never `PAID`. |
| Repeated clicks | Idempotency keys plus a database unique active-payment guard prevent a second active transaction for the same booking. |
| Webhook replay | Provider event IDs are unique and safely replayable. Invalid callback tokens cannot mutate records. |
| Transaction persistence | Processing state is committed before the external provider call and survives a database restart. |
| Provider failure | Failed or uncertain creates/refunds remain failed or under review; no false success is displayed. |
| Manual exception | Manual verification remains `UNDER_REVIEW`, requires an official provider/bank record, and requires a second authorized operator. |
| Tenant isolation | Finance actions are tenant-scoped; client views are identity-bound. |
| Refunds | Full and partial refunds remain pending until an authenticated provider result is received. |
| Reconciliation | Daily runs are idempotent and mismatches create review issues instead of silently rewriting money state. |
| Conditional release | Release requires protected-funds evidence, delivery evidence, dispute checks, payout verification, reconciliation, limits, and maker/checker approval. |
| Public terminology | Customer surfaces say **Protected Payment**. Legal “escrow” claims remain disabled. |

## Mandatory production evidence

All items below must have an owner, dated evidence, and approval. A boolean environment value is a
deployment lock, not evidence by itself.

1. Provider commercial and technical certification, including current BSP/regulatory-status due
   diligence for the contracted Philippine entity.
2. Written determination of LiftHaul's payment-system/merchant-acquisition role under the National
   Payment Systems Act and current BSP rules.
3. Executed safeguarded-funds or conditional-settlement model showing who holds money, segregation,
   release authority, refunds, chargebacks, insolvency handling, and reconciliation.
4. Sandbox certification for each enabled channel: success, failure, cancellation, expiry, duplicate
   callback, invalid authentication, wrong amount, delay, full refund, partial refund,
   reconciliation, and end-to-end operation.
5. Independent penetration/security assessment with critical and high findings closed.
6. Backup restoration and payment recovery exercise with evidence that idempotency, ledger,
   reconciliation, and webhook replay behavior remain correct after restore.
7. Controlled low-value production pilot and settlement confirmation for each enabled channel.
8. Production reconciliation automation, alert ownership, incident runbook, provider escalation
   contacts, and finance exception queue coverage.
9. Privacy/security review covering access control, encryption, retention, breach response, data
   processing agreements, and provider/vendor risk.
10. Approved customer disclosures for fees, release conditions, cancellation, refund, dispute,
    automatic-confirmation deadlines, and provider identity.

## Enforced production locks

`PAYMENT_GATEWAY_MODE=production` fails startup unless these controls are enabled:

- `PAYMENT_PROVIDER_CERTIFIED`
- `PAYMENT_PRODUCTION_PILOT_APPROVED`
- `PAYMENT_RECONCILIATION_AUTOMATION`
- `PAYMENT_REGULATORY_ROLE_APPROVED`
- `PAYMENT_SAFEGUARDED_FUNDS_APPROVED`
- `PAYMENT_INDEPENDENT_SECURITY_TEST_APPROVED`
- `PAYMENT_DR_RESTORE_APPROVED`

Per-channel production certification is also mandatory. The administrative endpoint
`GET /admin/payments/security-readiness` exposes a secret-free decision report and returns
`KEEP_LIVE_FUNDS_DISABLED` while any gate is missing.

The protected-payment domain has an additional independent three-part lock: approved legal
operating model, active licensed provider, and explicit live-protected-funds authorization. No live
provider adapter is registered in the current repository.

## Authoritative reference set

- BSP National Payment Systems Act / payments oversight:
  https://www.bsp.gov.ph/SitePages/PaymentsAndSettlements/PaymentsAndSettlements.aspx/1000
- BSP OPS registration FAQ:
  https://www.bsp.gov.ph/PaymentAndSettlement/FAQ_OPS_Registration.pdf
- BSP Circular No. 1198 merchant acquisition framework:
  https://www.bsp.gov.ph/Regulations/Issuances/2024/1198.pdf
- Xendit payment webhook:
  https://docs.xendit.co/apidocs/payment-webhook-notification
- Xendit webhook handling:
  https://docs.xendit.co/docs/handling-webhooks
- National Privacy Commission, Data Privacy Act:
  https://privacy.gov.ph/data-privacy-act/

## Decision

Current engineering decision: **sandbox and demonstration only; keep live funds disabled**.

The decision may change to **ACTIVATE** only after the administrative readiness report has no
blockers and the evidence package—not merely the configuration flags—has been independently
reviewed and approved.
