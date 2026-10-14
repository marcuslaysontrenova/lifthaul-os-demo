# LiftHaul MVP journey acceptance checkpoint — 13 October 2026

## Decision

**CONDITIONAL GO for the controlled no-live-funds MVP. NO-GO for live payment collection or payout.**

The currently deployed production version is healthy. One consolidated candidate, PR #30, remains
undeployed and requires a new authorization naming its exact current head. CI readiness is not
hosted acceptance; refresh the PR head and required checks immediately before authorization.

## Progress measurement

- **Formal MVP acceptance: 33.3% complete, 66.7% remaining.** Three of nine hard gates are closed.
- **Engineering maturity: 61.1% complete, 38.9% remaining.** Across 61 detailed items: 22 Done,
  5 Ready in tested PR, 23 Partial, 6 Open, 4 Blocked, and 1 Deferred. Score: `(22 + 5×0.75 +
  23×0.5) / 61 = 61.1%`.
- **Deployment delta ready for authorization:** A08, B12, C09, C10, and C11, plus safety improvements
  contributing to D04, D09, and D10.
- **Release decision:** NO-GO for unrestricted MVP; CONDITIONAL GO remains limited to the existing
  no-live-funds public runtime.

## Week-one MVP integration candidate

PR #30 from branch `codex/mvp-sprint-week1` integrates the independently tested customer-booking
and driver-evidence changes from PRs #28 and #29, then closes implementation gaps for customer
offer comparison, governed selection requests, and misleading inert payment-detail actions. It is a
review candidate only: it has not been merged, deployed, or counted as hosted acceptance.

Verified 14 October 2026 (Philippine time) before this audit-only update:

- URL: https://github.com/marcuslaysontrenova/lifthaul-os-demo/pull/30
- tested implementation head: `91e991a9869d9fbee561b109ed4f20afe9a3c8c0`
- exact base: `647f6e5d50e1ec26955778f264e210cf3c14d3d9`
- merge state: `CLEAN`
- GitHub checks: **54/54 successful**, zero failed, zero pending
- production remains on base SHA `647f6e5d50e1ec26955778f264e210cf3c14d3d9`, deployment
  `798eaded-7b98-42b0-b720-9a8c957b234a`, schema 27; `/healthz` and `/readyz` both return 200.

The customer workspace now:

- compares eligible offers using customer-safe provider, vehicle, amount, timing, validity,
  inclusion, exclusion, toll, ferry, waiting-rate, and note fields;
- excludes raw carrier, vehicle, driver, tenant, and internal-margin identifiers;
- escapes provider-supplied notes before rendering;
- records a persistent, owner-scoped, idempotent `PENDING_REVIEW` selection request;
- rejects unrelated, invalid, expired, malformed-validity, or already-selected offers; and
- does not directly assign a provider, create a trip, or move funds.

Integration-candidate evidence:

- 78/78 responsive browser tests passed across Chromium, Firefox, and WebKit;
- 1,522/1,527 backend tests passed in the managed sandbox, with the remaining five blocked only
  from creating temporary static-file fixtures;
- those five static-router tests passed 5/5 outside that filesystem restriction;
- the 31-step quote-to-job-to-invoice HTTP journey passed; and
- the offline SMTP transport tests are now hermetic while still proving TLS precedes credentials.

Gate 4 now also has a tested release-bound acceptance harness. It fails before its first write unless
the hosted backend reports the exact expected 40-character release SHA, then creates one explicitly
synthetic no-live-funds booking in each of Luzon, Visayas and Mindanao. For each exact record it proves
idempotent retry, public tracking, canonical authenticated intake-queue visibility and persistence
across independent HTTP requests. It stores only booking references and token fingerprints in the
evidence artifact, never opaque tracking tokens or credentials, and never calls a payment, assignment
or payout endpoint. The release workflow retains the artifact for 30 days. Offline contract, booking,
server and deploy coverage passed **114/114 tests**; all three edited workflow files parsed successfully.

This advances B12 from **Open** to **Ready in tested PR** and raises engineering maturity to 61.1%.
It does not close Gate 4: the exact candidate must still be authorized, merged, deployed and run
against the canonical hosted environment. Process-restart and database-restore evidence remain Gate 6.

This moves checklist item C10 to **Ready in review branch**. Formal MVP acceptance remains 3/9
(33.3%) until the exact candidate is authorized, merged, deployed, smoke-tested, and the hosted
acceptance gates are closed.

The same branch also moves C11 to **Ready in review branch** for the controlled MVP: the previously
inert receipt-download, delivery-confirmation, and problem-report buttons are now visibly disabled,
carry an accessible limitation explanation, and cannot send a write. Three focused browser tests
passed across Chromium, Firefox, and WebKit. These customer actions require a separately designed,
authorized, persisted workflow before they can be enabled; no fake receipt, release, or dispute is
claimed.

## Production evidence (read-only)

- Canonical public URL: https://www.lifthaul.com.ph/
- GitHub `main`: `647f6e5d50e1ec26955778f264e210cf3c14d3d9`
- Production `/healthz`: HTTP 200, status `ok`
- Production `/readyz`: HTTP 200, status `ready`, schema version 27
- Railway deployment identity reported by both probes:
  `798eaded-7b98-42b0-b720-9a8c957b234a`
- No production mutation, DNS change, account creation, secret creation, payment action, or payout
  occurred during this checkpoint.

## Completed in this checkpoint

### High-severity driver evidence controls

The live driver UI previously:

1. submitted hard-coded Manila coordinates when browser geolocation failed; and
2. declared photo and signature proof-of-delivery evidence without collecting either item.

The isolated branch now:

- fails closed when location permission is unavailable or denied;
- rejects missing, non-numeric, and out-of-range driver coordinates server-side;
- no longer sends fabricated coordinates;
- no longer claims uncollected photo or signature evidence;
- permits driver POD only after the trip is `DELIVERED`;
- requires the canonical recipient delivery code to be server-verified;
- records only `recipient_otp` as the evidence factor;
- advances the canonical trip to `POD_SUBMITTED`;
- makes a retry idempotent after POD submission;
- displays the actual `recipient_verification` state; and
- provides a direct “Apply as a LiftHaul driver” route from driver sign-in.

### Local acceptance evidence

- 30/30 targeted driver, registration, navigation, and integrity tests passed.
- 223/223 focused driver/fleet/public-booking/payment/payout/tenant suites passed before remediation.
- Full backend run: 1,516 application tests passed. Five static-router fixtures were prevented from
  writing by the managed Windows sandbox, not by application assertions.
- The same five static-router tests passed 5/5 when rerun with the required temporary-file permission.
- Responsive public UI release gate: 51/51 passed across Chromium, Firefox, and WebKit.
- `git diff --check`: passed.
- One transient Chromium navigation failure was reproduced as a local server connection interruption;
  the focused rerun passed, followed by a clean complete 51/51 cross-browser rerun.

## Consolidated PR #30

- URL: https://github.com/marcuslaysontrenova/lifthaul-os-demo/pull/30
- State: OPEN
- Merge state: CLEAN
- Head: resolve from GitHub immediately before authorization; never rely on a head embedded in the PR itself
- Base: `647f6e5d50e1ec26955778f264e210cf3c14d3d9`
- All reported checks: **54/54 SUCCESS**
- Reviews: none
- Scope: integrates PR #28 and PR #29 plus C10/C11 controlled-MVP remediation.
- Gate: **not authorized for merge or deployment in this checkpoint**. Earlier approvals do not
  transfer to this new PR and exact head.

## Remaining release gates

| Severity | Gate | Owner / required evidence |
|---|---|---|
| High | PR #30 exact merge/deploy authorization | Founder; authorize PR #30 and its freshly verified current head explicitly |
| High | Authenticated production UAT | Dedicated least-privilege synthetic accounts for booker, driver, fleet owner, staff/ops, finance, admin, and founder; no shared or real-customer credentials |
| High | Current recovery evidence | Named owner plus dated backup, isolated restore, rollback, monitor, and incident-response evidence for the exact release |
| High | Commercial/legal controls | Founder-approved fare/tax/admin-fee policy and trial limits; approved support, privacy, and DPO contacts |
| Critical for live funds | Licensed payment path | Executed PSP agreement, approved legal structure, production credentials, reconciliation ownership, refund/dispute controls, and finance sign-off |
| Medium | Email delivery | Deferred by owner; not an MVP blocker while UI and operating policy clearly use assisted/manual recovery |

## Funds posture

Live collection, escrow-like custody, bank transfer, e-wallet movement, and provider payout remain
disabled. The ledger, protected-payment state machine, segregation of duties, payout controls, and
reconciliation tests exist and pass, but they are readiness controls—not authority to move money.
LiftHaul must not claim BSP approval, licensed escrow status, LTFRB approval, or full regulatory
compliance without verified documentation.

## Next controlled sequence

1. Refresh PR #30 and obtain explicit authorization naming its exact current head.
2. Merge only that tested head, deploy only the resulting merge SHA, and record SHA, deployment ID,
   schema, health, readiness, PSGC and rollback version.
3. Close Gate 4 with synthetic Luzon, Visayas and Mindanao bookings that persist through intake queue,
   refresh/restart and public tracking.
4. Provision approved least-privilege synthetic identities and close Gate 5 through successful and
   denied persona UAT on desktop, tablet and mobile.
5. Close Gate 6 with dated PostgreSQL backup/restore, application rollback, alert-delivery and
   incident-owner evidence, plus approved hosted performance targets.
6. Obtain and test approved support, privacy and DPO contacts to close Gate 7.
7. Obtain Founder/Finance approval for fare, tax, 10% administration fee, provider share and trial
   limitations to close Gate 8; do not invent or activate unapproved money rules.
8. Approve and publish the controlled no-live-funds boundary and separate payment certification gate
   to close Gate 9. Keep live collections, custody, refunds and payouts off until PSP/legal/finance
   certification is independently complete.

