# LiftHaul controlled MVP gate register — 2026-10-16

This register is evidence-based. A gate is `COMPLETE` only when the required
production evidence exists. Code, a passing local test, or a visually complete
page alone does not close a production gate.

## Current measured position

- Completed gates: **3 of 9 (33.3%)**
- Remaining gates: **6 of 9 (66.7%)**
- Production application: `https://www.lifthaul.com.ph`
- Exact deployed commit: `dfea0b9df01824a73bb1a39a0f611f5e3f6c0ba1`
- Railway deployment: `0ac737cf-5b1f-4709-8450-ba3e5631182b`
- Rollback target: `a4f1cf95-bb5b-4bda-9ec2-38f270cd5794`
- Public-money posture: **live collection, custody, transfer and payouts remain disabled**

## Gate checklist

| # | MVP gate | Status | Verified evidence | Exact remaining acceptance condition |
|---|---|---|---|---|
| 1 | Production platform and persistent database foundation | COMPLETE | Canonical `/readyz` reports `ready`, schema 27, exact SHA and deployment ID; Railway PostgreSQL is online | Continue health monitoring; do not change the recorded identity without a new release |
| 2 | Authentication and role-based access controls | COMPLETE | Full regression and release checks pass; unauthenticated admin access is denied; scoped `public_intake_qa` role and contract tests exist | Production persona acceptance remains Gate 5 and does not reopen this code-control gate |
| 3 | Core public discovery, booking and tracking journey | COMPLETE | Public pages, PSGC data, booking API, vehicle matching, tracking and browser UAT passed on the exact release candidate | Gate 4 must still prove newly written records persist on the hosted production database |
| 4 | Hosted booking persistence, queue and tracking | OPEN | Exact-release regional test harness is implemented; prior production run stopped before the first write because protected QA credentials were absent | Configure a dedicated `public_intake_qa` account and protected secrets; run the exact-release workflow; require all three island-group bookings to be created, idempotent, trackable, persisted and present in the canonical queue |
| 5 | Authenticated production UAT for required personas | OPEN | Least-privilege QA harness and offline contract tests pass | Execute production sign-in and journey evidence for booker, driver applicant, vehicle owner, fleet owner, specialized provider, operations, finance and administrator; record pass/fail without shared credentials or real funds |
| 6 | Production recovery, rollback, monitoring and ownership | OPEN | Exact rollback deployment is recorded; local backup/restore reconciled with 0.47 s restore; 40-client concurrency test passed with zero hard failures; health monitor workflow exists | Prove managed PostgreSQL backup/PITR policy, restore into an isolated database, reconciliation and measured RTO/RPO; execute or evidence rollback; assign alert and incident owners |
| 7 | Public support, privacy, complaint and navigation readiness | OPEN | `support.html` and `policies.html` exist; link/browser checks cover public navigation | Publish approved support channel and hours, complaint SLA/escalation, DPO/privacy contact, reviewed Terms/Privacy/Refund texts and valid routes; remove launch-gated placeholder language only after approval |
| 8 | Commercial fare rules and calculator approval | OPEN | One pricing engine, public fare calculator, versioned rate metadata, deterministic 10% fee tests and booking integration exist | Commercial owner must approve effective rate matrix, minimums, VAT treatment, waiting/stops/cancellation/toll rules and provider share; re-benchmark dated public Lalamove/Transportify scenarios and approve the version |
| 9 | Protected-payment boundary and production certification | OPEN | State machine, ledger, reconciliation, dispute, payout and fail-closed tests exist; customer copy does not claim escrow; live-funds flags are off | Contract a licensed payment provider, approve legal/fund-flow model, obtain production credentials, certify webhooks/reconciliation/refunds/payout controls and complete independent security/DR evidence before enabling any live-money flag |

## Work completed on 2026-10-16

- Reproduced the failed production acceptance run and identified the exact cause:
  missing `LH_PROD_TEST_ADMIN_EMAIL` and `LH_PROD_TEST_ADMIN_PASSWORD` in the
  protected GitHub `production` environment.
- Verified Railway already contains masked bootstrap-administrator variables; no
  secret value was exposed or copied.
- Added a release-workflow reliability gate for backup/restore reconciliation and
  40-client concurrent-registration isolation.
- Added an explicit fail-closed credential check with an actionable error.
- Connected the regional synthetic journey to the read-only authenticated QA test
  using the newly created booking reference instead of a stale hard-coded reference.
- Added immutable `reliability_result` release evidence.
- Added a deployment contract test for these controls.
- Re-ran local controls: backup/restore PASS, concurrency PASS, nine production-QA
  contract tests PASS, targeted deployment-workflow test PASS.

These changes remain **unreleased** until committed, reviewed, merged and deployed.
They do not change the 3/9 production percentage by themselves.

## Critical path to controlled MVP

1. Create a dedicated `public_intake_qa` production principal and place its email
   and password only in GitHub's protected production secrets.
2. Commit and review the release-workflow improvements, then run all required CI.
3. Deploy the exact merged commit and run the production release workflow in
   `verify_existing` or authorized `deploy` mode.
4. Close Gate 4 from the regional-booking artifact and Gate 5 only after every
   required persona has production evidence.
5. Execute managed PostgreSQL backup/PITR restore, rollback evidence and monitoring
   ownership to close Gate 6.
6. Obtain approved public support/privacy/legal contacts and policy versions to
   close Gate 7.
7. Obtain dated commercial approval of the rate matrix to close Gate 8.
8. Keep Gate 9 fail-closed until external legal/provider/security conditions are
   evidenced. The application can be approved as **MVP GO — no live funds** only
   if the agreed MVP scope explicitly excludes live-money activation.

## Release rule

No percentage increase is allowed for partial work. A gate moves only from `OPEN`
to `COMPLETE` when its exact acceptance condition is attached to a commit, workflow
run, deployment or signed external decision. Until Gates 4–8 are closed and Gate 9
is either closed or formally excluded from the no-live-funds MVP scope, the overall
decision remains **NO-GO for a 100% MVP claim**.
