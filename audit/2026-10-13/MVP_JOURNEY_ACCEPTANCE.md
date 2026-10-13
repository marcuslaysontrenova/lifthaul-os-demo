# LiftHaul MVP journey acceptance checkpoint — 13 October 2026

## Decision

**CONDITIONAL GO for the controlled no-live-funds MVP. NO-GO for live payment collection or payout.**

The currently deployed production version is healthy, but two independent changes remain undeployed:
PR #28 (customer booking detail/tracking) requires a new, exact merge authorization, and this
checkpoint's driver evidence remediation must pass its own pull-request gates and receive its own
release decision.

## Week-one MVP integration candidate

Branch `codex/mvp-sprint-week1` integrates the independently tested customer-booking and
driver-evidence changes from PRs #28 and #29, then closes the implementation gap for customer offer
comparison and governed selection requests. It is a review candidate only: it has not been merged,
deployed, or counted as hosted acceptance.

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

## Existing PR #28

- URL: https://github.com/marcuslaysontrenova/lifthaul-os-demo/pull/28
- State: OPEN
- Merge state: CLEAN
- Head: `0b7a24722f22733d1c21446f5662ee82ed3c973e`
- Base: `647f6e5d50e1ec26955778f264e210cf3c14d3d9`
- All reported checks: SUCCESS
- Reviews: none
- Gate: **not authorized for merge in this checkpoint**. The handover expressly requires a new,
  PR-specific authorization; earlier release approvals do not transfer.

## Remaining release gates

| Severity | Gate | Owner / required evidence |
|---|---|---|
| High | PR #28 exact merge/deploy authorization | Founder; authorize that PR/head explicitly |
| High | Driver-evidence remediation PR CI and release authorization | Engineering + founder after PR checks |
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

1. Publish this driver-evidence branch and require clean PR checks.
2. Obtain explicit PR-specific authorization before merging PR #28.
3. Obtain a separate exact authorization for the driver-evidence PR after its checks pass.
4. Deploy only the resulting tested merge SHA and record deployment identity.
5. Execute synthetic authenticated production UAT and recovery evidence collection.
6. Keep live funds off until the independent PSP/legal/finance gate is complete.

