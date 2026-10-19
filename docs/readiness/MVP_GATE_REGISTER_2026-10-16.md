# LiftHaul controlled MVP gate register — 2026-10-16

This register is evidence-based. A gate is `COMPLETE` only when the required
production evidence exists. Code, a passing local test, or a visually complete
page alone does not close a production gate.

## Current measured position

- Completed gates: **4 of 9 (44.4%)**
- Remaining gates: **5 of 9 (55.6%)**
- Production application: `https://www.lifthaul.com.ph`
- Exact deployed commit: `1489a8f1451249fc11798595e640e052a9e8b5d7`
- Railway deployment: `00dd0d3b-28bf-488d-a9d9-7b19ed7708ff`
- Rollback target: `0c799269-22a2-4915-b920-63731e17e750`
- Public-money posture: **live collection, custody, transfer and payouts remain disabled**

## Gate checklist

| # | MVP gate | Status | Verified evidence | Exact remaining acceptance condition |
|---|---|---|---|---|
| 1 | Production platform and persistent database foundation | COMPLETE | Canonical `/readyz` reports `ready`, schema 27, exact SHA and deployment ID; Railway PostgreSQL is online | Continue health monitoring; do not change the recorded identity without a new release |
| 2 | Authentication and role-based access controls | COMPLETE | Full regression and release checks pass; unauthenticated admin access is denied; scoped `public_intake_qa` role and contract tests exist | Production persona acceptance remains Gate 5 and does not reopen this code-control gate |
| 3 | Core public discovery, booking and tracking journey | COMPLETE | Public pages, PSGC data, booking API, vehicle matching, tracking and browser UAT passed on the exact release candidate | Preserve the same journey in every subsequent exact-release regression |
| 4 | Hosted booking persistence, queue and tracking | COMPLETE | Production release run `39115836213` passed exact-SHA binding, protected QA credential check, public preflight, no-funds synthetic production journey and least-privilege persisted-record read. Artifact `regional-booking-acceptance-1489a8f1451249fc11798595e640e052a9e8b5d7` reports 20 passed, 0 failed for Luzon, Visayas and Mindanao, schema 27, with no live funds | Keep synthetic records identifiable and retain the immutable artifact; repeat this gate for any new production SHA |
| 5 | Authenticated production UAT for required personas | OPEN | Isolated persona workflow run `39115074502` passed all four role groups and the controlled matrix remains 11/11. Exact-release hosted run `39115781853` proved commit binding, fail-closed manifest handling and redacted evidence upload on live SHA `1489a8f1451249fc11798595e640e052a9e8b5d7`, but correctly failed before authentication because the protected persona manifest is absent | Provision eleven distinct synthetic persona accounts and fixture markers in protected secret `LH_PROD_PERSONA_CREDENTIALS_JSON`; rerun `.github/workflows/production-persona-uat.yml` against the exact live SHA and require 11/11, negative RBAC, fixture ownership and funds-off evidence |
| 6 | Production recovery, rollback, monitoring and ownership | OPEN | Exact rollback deployment is recorded; local backup/restore reconciled with 0.47 s restore; 40-client concurrency test passed with zero hard failures; health monitor workflow exists | Prove managed PostgreSQL backup/PITR policy, restore into an isolated database, reconciliation and measured RTO/RPO; execute or evidence rollback; assign alert and incident owners |
| 7 | Public support, privacy, complaint and navigation readiness | OPEN | `support.html` and `policies.html` exist; link/browser checks cover public navigation | Publish approved support channel and hours, complaint SLA/escalation, DPO/privacy contact, reviewed Terms/Privacy/Refund texts and valid routes; remove launch-gated placeholder language only after approval |
| 8 | Commercial fare rules and calculator approval | OPEN | One pricing engine, public fare calculator, versioned rate metadata, deterministic 10% fee tests and booking integration exist | Commercial owner must approve effective rate matrix, minimums, VAT treatment, waiting/stops/cancellation/toll rules and provider share; re-benchmark dated public Lalamove/Transportify scenarios and approve the version |
| 9 | Protected-payment boundary and production certification | OPEN | State machine, ledger, reconciliation, dispute, payout and fail-closed tests exist; customer copy does not claim escrow; live-funds flags are off | Contract a licensed payment provider, approve legal/fund-flow model, obtain production credentials, certify webhooks/reconciliation/refunds/payout controls and complete independent security/DR evidence before enabling any live-money flag |

## Gate 4 revalidation — 2026-10-18

- Deployed exact merged commit `1489a8f1451249fc11798595e640e052a9e8b5d7`
  to Railway as deployment `00dd0d3b-28bf-488d-a9d9-7b19ed7708ff`.
- Verified canonical `/readyz` reports schema 27 and those exact release and
  deployment identities.
- Completed GitHub Actions production release run `39115836213` successfully.
- Passed complete Python regression, static security/dependency audit and
  cross-browser UAT for the exact production commit.
- Passed backup/restore reconciliation and 40-client concurrency controls.
- Created and independently re-read three synthetic, no-funds production bookings:
  Luzon `LH-JJNFGO`, Visayas `LH-DJQVIA`, and Mindanao `LH-QADVK4`.
- Verified idempotency, public tracking, canonical queue presence, hosted persistence
  and least-privilege authenticated access. The evidence artifact records **20 passed,
  0 failed** and `live_funds_exercised: false`.
- Published the exact static frontend and stored immutable release manifest artifact
  `production-release-1489a8f1451249fc11798595e640e052a9e8b5d7`.

## Gate 5 movement — 2026-10-19

- Added and deployed the exact-release hosted persona workflow and its redacted
  evidence contract in PR #56.
- Revalidated the deployed commit through complete regression, static security,
  browser UAT, recovery/concurrency and no-funds production booking checks in run
  `39115836213`.
- Retained the isolated persona result from run `39115074502`, in which the four
  role groups and the controlled 11-persona matrix passed.
- Executed hosted Gate 5 run `39115781853` against the exact deployed SHA. It
  failed closed before authentication because `LH_PROD_PERSONA_CREDENTIALS_JSON`
  is absent. Artifact
  `production-persona-uat-1489a8f1451249fc11798595e640e052a9e8b5d7`
  records only `BLOCKED_OR_FAILED`, the non-secret reason, and
  `live_funds_enabled: false`.
- Verified on 2026-10-19 that the protected `production` environment still does
  not contain the persona-manifest secret. Existing shared QA credentials cannot
  substitute for eleven distinct least-privilege identities.

Gate 5 therefore remains **OPEN** and the measured MVP position remains **4/9
(44.4%)**. The next valid movement is provisioning and approving the eleven
synthetic accounts and unique fixture markers through a secure administrator path,
storing the manifest as the protected secret, and obtaining a successful exact-SHA
hosted rerun plus accountable UAT sign-off.

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

At the time of this 2026-10-16 entry these changes were **unreleased** and did not
change the then-current 3/9 production percentage. The 2026-10-18 evidence above
supersedes that historical status and closes Gate 4.

## Critical path to controlled MVP

1. Close Gate 5 only after every required persona has production sign-in and
   end-to-end evidence with separate, least-privilege identities.
2. Execute managed PostgreSQL backup/PITR restore, rollback evidence and monitoring
   ownership to close Gate 6.
3. Obtain approved public support/privacy/legal contacts and policy versions to
   close Gate 7.
4. Obtain dated commercial approval of the rate matrix to close Gate 8.
5. Keep Gate 9 fail-closed until external legal/provider/security conditions are
   evidenced. The application can be approved as **MVP GO — no live funds** only
   if the agreed MVP scope explicitly excludes live-money activation.

## Release rule

No percentage increase is allowed for partial work. A gate moves only from `OPEN`
to `COMPLETE` when its exact acceptance condition is attached to a commit, workflow
run, deployment or signed external decision. Until Gates 5–8 are closed and Gate 9
is either closed or formally excluded from the no-live-funds MVP scope, the overall
decision remains **NO-GO for a 100% MVP claim**.
