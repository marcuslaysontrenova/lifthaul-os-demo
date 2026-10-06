# LiftHaul Payment Production Certification Register

**Review date:** 2026-10-02
**Decision:** **NOT PRODUCTION-CERTIFIED — LIVE FUNDS MUST REMAIN DISABLED**
**Scope:** LiftHaul customer collections, refunds, provider earnings/payouts, payment recovery, webhooks and reconciliation.

This is the controlling evidence register for payment activation. Passing automated tests proves the
software controls; it does **not** prove a payment provider, legal operating model, production endpoint,
external security assessment or live-money transaction. No row may be marked complete without the
evidence described below.

## Current decision

| Certification gate | Repository control | Evidence required to close | Current status |
|---|---|---|---|
| Every payment channel certified in provider sandbox | `gateway_channel_certifications`; all 12 required scenarios per channel | Provider dashboard/API evidence for GCash, Maya, bank transfer, QR Ph, card and OTC; test IDs, timestamps and reviewer | **OPEN — external provider sandbox** |
| Independent security / penetration test | Fail-closed production flag plus internal dependency and static-security CI | Signed report from an independent assessor; scope, date, findings, remediation and clean retest | **OPEN — independent assessor** |
| Production webhook verification | Authenticated webhook, deduplication and server-to-server status verification | Real HTTPS callback evidence, signature failure test, replay test, duplicate test and provider/API match | **OPEN — hosted endpoint + provider** |
| PostgreSQL payment recovery | PostgreSQL CI workflow, idempotency keys, restart persistence and backup/restore drill | Successful workflow artifact or hosted restore drill showing transaction/refund/reconciliation recovery, RTO and RPO | **OPEN — execution evidence required** |
| Controlled low-value live payment pilot | Production pilot approval gate | Named approvers, capped PHP amount, exact channel/recipient, rollback plan, payment/provider references and recorded outcome | **OPEN — executive and provider approval** |
| Real provider payout and refund confirmation | Governed payout/refund ledgers, idempotency and separation of duties | One real payout and one eligible real refund with provider IDs, recipient confirmation, ledger match and two-person review | **OPEN — live provider evidence** |
| Daily production reconciliation | Idempotent UTC-day reconciliation worker and issue ledger | Seven consecutive production runs, zero unexplained variance, alert proof and named finance reviewer | **OPEN — production operations evidence** |
| Philippine payment-role / fund-flow approval | Regulatory and safeguarded-funds activation gates | Written Philippine counsel analysis and licensed-provider contract confirming LiftHaul's role and permitted fund flow | **OPEN — counsel/provider** |

## Evidence already established internally

- Live payment collection remains fail-closed by default.
- A channel cannot be certified unless all required success, failure, cancellation, expiry, duplicate,
  invalid-signature, amount-mismatch, delayed-confirmation, refund, partial-refund, reconciliation and
  end-to-end scenarios are recorded as passed.
- Production channel certification requires prior sandbox certification and all production approval
  gates.
- A payment becomes authoritative only after an authenticated provider webhook and a server-to-server
  provider status check.
- Payment, refund and reconciliation writes have database idempotency guards.
- Daily reconciliation uses a unique provider/environment/date run key and persists mismatches for
  review.
- Manual payment confirmation and sensitive refund/payout actions enforce permissions and separation
  of duties.
- The production PostgreSQL workflow covers migration, application startup, HTTP smoke, restart
  persistence, controlled marketplace lifecycle and backup/restore reconciliation.
- The payment security workflow performs dependency vulnerability audit, SBOM generation,
  high-confidence static security scanning and focused financial-control regression.

Internal controls are necessary but are not substitutes for the external evidence in the table above.

## Activation sequence

1. **Legal and provider model** — retain Philippine counsel; select and contract with a BSP-supervised
   payment provider for the intended collection, conditional-release, refund and payout model.
2. **Sandbox** — store credentials in the deployment/GitHub secret vault, never in source control or
   chat. Certify each enabled channel independently and attach provider evidence.
3. **Hosted pre-production** — deploy with PostgreSQL and HTTPS; verify inbound production-style
   webhooks, replay protection, recovery and reconciliation alerts.
4. **Independent security** — complete external application/API penetration testing, remediate all
   Critical/High findings and obtain a clean retest.
5. **Pilot authorization** — Marcus approves the exact capped transaction, channel, payer, provider,
   recipient and operating team. Approval to test is not approval for general availability.
6. **Live pilot** — execute one controlled payment, one payout and one eligible refund. Reconcile the
   provider records, LiftHaul ledger and bank settlement.
7. **Reconciliation soak** — obtain seven consecutive daily production reconciliation records and
   prove alert handling for a controlled mismatch.
8. **Go/no-go** — the accountable signatories below review the complete evidence pack. Only then may
   production flags be enabled for the specifically certified channels.

## Required sign-off

| Authority | Required decision | Name / evidence reference | Status |
|---|---|---|---|
| Founder / accountable executive | Controlled pilot and production activation | Not recorded | OPEN |
| Finance operations | Settlement, payout, refund and daily reconciliation acceptance | Not recorded | OPEN |
| Security assessor | Independent penetration-test closure | Not recorded | OPEN |
| Philippine legal / compliance counsel | Regulatory role, consumer terms, privacy and fund-flow approval | Not recorded | OPEN |
| Licensed payment provider | Commercial onboarding and per-channel certification | Not recorded | OPEN |
| Engineering / operations | PostgreSQL recovery, webhook, monitoring and rollback evidence | Not recorded | OPEN |

## Non-negotiable operating rules

- Do not set `PAYMENT_GATEWAY_MODE=production` or any production approval flag merely to make a test
  pass.
- Do not enable `LIVE_PROTECTED_FUNDS_ENABLED` until the complete evidence pack is signed.
- Enable only channels present in the certified-channel allowlist.
- Do not paste provider credentials, webhook secrets, bank details or personal data into tickets,
  documents, source control or chat.
- A successful redirect, screenshot or customer statement is not payment confirmation.
- Any unexplained reconciliation variance, webhook authentication failure, duplicate settlement,
  complaint or security finding is a stop condition.
- “Production-certified” applies only to the named provider, environment, channels, implementation
  version and evidence date. A material provider or fund-flow change requires recertification.

## Final certification record

The system may be labeled **Production-Certified for Payments** only when every row in the certification
table and sign-off table is complete, the complete repository regression is green, and the evidence pack
is immutable and reviewable. Until then, the truthful release label is:

> **Internally verified; externally gated; live payment collection disabled.**
