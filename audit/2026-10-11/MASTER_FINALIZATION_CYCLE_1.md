# LiftHaul master finalization — cycle 1 evidence

Date: 2026-10-11 (Asia/Manila)  
Branch: `codex/master-finalization-cycle-1`  
Production baseline: `3919680f25f2f9cd3d1c2325923dc1f9a43fb70b`

## Decision

**NO-GO for controlled commercial launch.** This cycle removes specific public-release defects and prepares a reviewable development candidate. It does not authorize a production deployment, live payments, provider payouts, or a compliance claim.

## Completed in this cycle

- Removed the public demo/prototype badge and demo footer labels from the landing, booking, and tracking journeys.
- Replaced the landing-page assurance pills with statements that do not imply verification or payment status before evidence exists.
- Reworded registration password guidance so account access is not inaccurately tied to sponsorship.
- Bounded driver-registration fields and added desktop, tablet, and mobile layout regression coverage.
- Replaced the booking failure fallback with a fail-closed message that confirms no booking, payment, or dispatch record was created.
- Replaced the fake `support@lifthaul.demo` address with the governed support route.
- Packaged the Philippine geographic dataset in the release image and restricted static serving to JSON files under `data/psgc/` only.
- Added automated release-copy, packaging, static-routing, booking-failure, geography, and responsive-field checks.

## Test evidence

- Targeted Python regression: 17/17 passed.
- Complete Python discovery suite after the final static-route repair: 1,491/1,491 passed in 609.088 seconds, including the 31-step HTTP end-to-end journey.
- Cross-browser public UI: the first authoritative run produced 45/48 passes and exposed the PSGC static-route defect in Chromium, Firefox, and WebKit. After correction, 48/48 passed across Chromium, Firefox, and WebKit.
- Diff hygiene: `git diff --check` passed.

## Active defects and blockers

| Severity | Item | Status / control |
|---|---|---|
| P1 | Production booking cannot load `/data/psgc/manifest.json` | Fixed in development; production remains affected until an approved, tested deployment. |
| P1 | Live payment collection, settlement, and payout certification | Blocked by licensed provider agreement, production credentials, webhook/reconciliation evidence, legal approval, and founder activation approval. Fail-closed controls remain required. |
| P1 | Business-owner UAT across booker, driver, owner, fleet, finance, operations, and admin roles | Not yet signed off. Synthetic technical tests are not a substitute for accountable UAT approval. |
| P2 | Demo/synthetic guidance still exists in internal or authenticated workspace code | Must be classified and either confined to test-only builds or replaced before commercial launch. |
| P2 | Public support and privacy contact details | A real approved support route and privacy/DPO contact must be supplied; fabricated addresses are prohibited. |
| P2 | Fare, 10% administrative fee, membership, deposit, cancellation, and payout policy | Technical controls exist in parts of the system, but commercial/legal approval and a versioned effective policy are still required. |
| P3 | Missing favicon requests and noisy client-abort stack traces in local browser runs | Does not invalidate tested journeys, but should be cleaned up for operational signal quality. |

## Payment posture

Payment architecture must not be represented as live merely because UI or ledger records exist. Live collection, bank/e-wallet transfer, escrow-like custody, and automated provider payout remain disabled until the external provider, legal structure, production secrets, reconciliation, dispute, refund, and operational approval gates are evidenced. Sandbox and synthetic transaction testing may continue.

## Next three controlled milestones

1. Finish the complete Python and three-browser regression reruns, then commit and open a pull request containing the exact evidence-backed candidate.
2. Audit and remove or isolate remaining demo/synthetic behavior in authenticated workspaces; execute role-based synthetic UAT with saved evidence and a defect register.
3. Obtain accountable approvals and credentials for commercial policy, support/privacy contacts, and payment-provider certification before any live financial activation or public production claim.
